"""三库持久化：意图、原子文件替换和账本/审计/事件事务。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from anyio import CancelScope

from agentcrew_core.events import RunEventType
from agentcrew_core.memory import QUOTAS, contains_credentials, context_entries, failure, material_risk, render_entries, sha256, transform
from agentcrew_core.tools.builtin.write_file import _open_dir_nofollow

from ..db.audit import SNAPSHOT_EVERY, append_audit, snapshot_chain_head
from ..db.database import Database
from ..db.event_store import EventStore
from ..secrets import known_secrets, redact


def canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class MemoryIdentity:
    workspace_id: str
    agent_id: str
    actor_type: str = "user"
    actor_id: str = "owner"
    conversation_id: str | None = None
    task_run_id: str | None = None
    job_id: str | None = None
    user_turn_id: str | None = None

    def source(self, change_id: str | None = None) -> dict:
        value = {k: v for k, v in asdict(self).items()
                if v is not None and k not in {"workspace_id", "agent_id", "user_turn_id"}}
        if self.actor_type == "user" and change_id:
            value["manual_edit_id"] = change_id
        return value


class MemoryStore:
    def __init__(self, db: Database, events: EventStore, data_dir: Path, settings=None):
        self.db = db
        self.events = events
        self.data_dir = data_dir.absolute()
        self.settings = settings
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def quota(self, store_type: str) -> int | None:
        if store_type == "skill":
            return None
        configured = self.settings.config.values["memory"] if self.settings else {}
        return configured.get(f"{store_type}_quota", QUOTAS[store_type])

    def path(self, store_type: str, store_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", store_id):
            raise ValueError("库身份包含非法路径字符")
        if store_type == "user" and store_id == "owner":
            return self.data_dir / "USER.md"
        if store_type == "workspace":
            return self.data_dir / "workspaces" / store_id / "MEMORY.md"
        if store_type == "soul":
            return self.data_dir / "agents" / store_id / "soul.md"
        if store_type == "skill":
            return self.data_dir / "skills" / store_id / "SKILL.md"
        raise ValueError("库类型或身份无效")

    def _authorize(self, identity: MemoryIdentity, store_type: str, store_id: str) -> dict | None:
        if hasattr(self, "identities"):
            caller = self.identities.memory_actor(identity)
            self.identities.agent(caller, identity.agent_id, identity.workspace_id)
            if identity.conversation_id:
                self.identities.conversation(caller, identity.conversation_id)
            if store_type == "skill" and hasattr(self, "grants"):
                current = self.identities.current(caller)
                if identity.actor_type != "user" or current["role"] == "member":
                    if not self.skill_versions.permitted(identity, store_id):
                        return failure("OUT_OF_SCOPE", "技能当前授权或资源状态已经失效")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) for value in (identity.workspace_id, identity.agent_id)):
            return failure("OUT_OF_SCOPE", "工作区和员工身份包含非法路径字符")
        expected = {"user": "owner", "workspace": identity.workspace_id, "soul": identity.agent_id}
        if store_type == "skill":
            skill = self.db.read_conn.execute("SELECT workspace_id,agent_id FROM memory_skills WHERE id=?", (store_id,)).fetchone()
            if skill and tuple(skill) == (identity.workspace_id, identity.agent_id):
                expected["skill"] = store_id
        if store_type not in expected or store_id != expected[store_type]:
            return failure("OUT_OF_SCOPE", "记忆库不属于当前执行范围")
        if identity.actor_type == "user":
            if (not hasattr(self, "identities") and identity.actor_id != "owner") or identity.task_run_id or identity.job_id:
                return failure("OUT_OF_SCOPE", "人工操作身份无效")
        elif identity.actor_type == "agent":
            if identity.actor_id != identity.agent_id or not identity.conversation_id:
                return failure("OUT_OF_SCOPE", "员工操作缺少真实会话身份")
        elif identity.actor_type == "curator":
            job = self.db.read_conn.execute("SELECT kind FROM memory_jobs WHERE id=?", (identity.job_id,)).fetchone()
            if not identity.job_id or identity.actor_id != identity.job_id or identity.conversation_id or identity.task_run_id or job is None or job[0] != "curate":
                return failure("OUT_OF_SCOPE", "治理身份缺少真实独立作业")
        else:
            return failure("OUT_OF_SCOPE", "记忆操作身份无效")
        if identity.conversation_id:
            row = self.db.read_conn.execute(
                "SELECT workspace_id,agent_id FROM conversations WHERE id=?", (identity.conversation_id,)).fetchone()
            if row is None or tuple(row) != (identity.workspace_id, identity.agent_id):
                return failure("OUT_OF_SCOPE", "记忆来源与会话身份不一致")
        if identity.task_run_id:
            row = self.db.read_conn.execute("SELECT conversation_id FROM task_runs WHERE id=?", (identity.task_run_id,)).fetchone()
            if row is None or row[0] != identity.conversation_id:
                return failure("OUT_OF_SCOPE", "记忆来源任务不属于当前会话")
        if identity.job_id:
            job = self.db.read_conn.execute("SELECT conversation_id,task_run_id,workspace_id,agent_id,status FROM memory_jobs WHERE id=?", (identity.job_id,)).fetchone()
            if job is None or tuple(job[:4]) != (identity.conversation_id, identity.task_run_id, identity.workspace_id, identity.agent_id):
                return failure("OUT_OF_SCOPE", "后台作业身份与记忆来源不一致")
            if job[4] != "running":
                return failure("JOB_NOT_RUNNING", "后台作业已经停止，禁止继续读写")
        return None

    def _pending(self, store_type: str, store_id: str):
        return self.db.read_conn.execute(
            "SELECT change_id FROM memory_changes WHERE store_type=? AND store_id=? AND status='prepared'",
            (store_type, store_id)).fetchone()

    def _file_text(self, path: Path) -> str | None:
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError("记忆文件不能使用符号链接")
        if not path.exists():
            return None
        directory = _open_dir_nofollow(path)
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            with os.fdopen(descriptor, "rb") as stream:
                return stream.read().decode("utf-8")
        finally:
            os.close(directory)

    def _load(self, store_type: str, store_id: str) -> dict:
        if self._pending(store_type, store_id):
            return failure("STORE_RECOVERING", "记忆库存在未完成写入意图")
        path = self.path(store_type, store_id)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            return failure("OUT_OF_SCOPE", "记忆路径不能使用符号链接")
        metadata_path = path.with_suffix(".meta.json")
        if metadata_path.is_symlink() or any(parent.is_symlink() for parent in metadata_path.parents) or \
                metadata_path.exists() and not metadata_path.is_file() or path.exists() and not path.is_file():
            return failure("EXTERNAL_MODIFICATION", "记忆正文或 metadata 路径已被外部替换")
        text = self._file_text(path)
        metadata = self._file_text(metadata_path)
        row = self.db.read_conn.execute("SELECT * FROM memory_stores WHERE store_type=? AND store_id=?", (store_type, store_id)).fetchone()
        if row is None:
            if text is not None or metadata is not None:
                return failure("EXTERNAL_MODIFICATION", "记忆文件没有对应的持久化版本")
            return {"store_type": store_type, "store_id": store_id, "revision": 0,
                    "text": "", "metadata": {"revision": 0, "entries": []},
                    "text_sha256": None, "metadata_sha256": None}
        if text is None or metadata is None or sha256(text.encode()) != row["text_sha256"] or sha256(metadata.encode()) != row["metadata_sha256"]:
            return failure("EXTERNAL_MODIFICATION", "文件与持久化版本的校验值不一致")
        value = {**dict(row), "metadata": json.loads(metadata)}
        if store_type == "skill":
            for relative, digest in value["metadata"].get("files", {}).items():
                target = path.parent / relative
                if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
                    return failure("OUT_OF_SCOPE", "Skill 支撑文件不能使用符号链接", file=relative)
                if target.exists() and not target.is_file():
                    return failure("EXTERNAL_MODIFICATION", "Skill 支撑文件已被外部替换", file=relative)
                content = self._file_text(target)
                if content is None or sha256(content.encode()) != digest:
                    return failure("EXTERNAL_MODIFICATION", "Skill 支撑文件与持久化校验值不一致", file=relative)
        return value

    async def read(self, identity: MemoryIdentity, store_type: str, store_id: str) -> dict:
        denied = await asyncio.to_thread(self._authorize, identity, store_type, store_id)
        if denied:
            return denied
        lock = self._locks.setdefault((store_type, store_id), asyncio.Lock())
        if lock.locked():
            return failure("STORE_RECOVERING", "记忆库正在写入")
        async with lock:
            value = await asyncio.to_thread(self._load, store_type, store_id)
        if "error" in value:
            return value
        used_characters = len(value["text"])
        entries = value["metadata"]["entries"]
        usage = {r["entry_id"]: dict(r) for r in self.db.read_conn.execute(
            "SELECT u.* FROM memory_usage u JOIN memory_entries e ON e.entry_id=u.entry_id WHERE e.store_type=? AND e.store_id=?", (store_type, store_id))}
        entries = [{**e, **{k: usage[e["entry_id"]][k] for k in ("hits", "last_hit_at")}}
                   if e["entry_id"] in usage else dict(e) for e in entries]
        if identity.actor_type == "agent":
            entries = context_entries(entries)
            value = {**value, "text": render_entries(entries)}
        entries = [{**e, "reviewed_by": e.get("approved_by"), "reviewed_at": e.get("approved_at")} for e in entries]
        watermark = self.db.read_conn.execute("SELECT COALESCE(MAX(global_seq),0) FROM run_events").fetchone()[0]
        return {**value, "metadata": {**value["metadata"], "entries": entries}, "entries": entries,
                "sha256": value["text_sha256"] or sha256(b""),
                "metadata_sha256": value["metadata_sha256"] or sha256(canonical(value["metadata"]).encode()),
                "used_characters": used_characters, "quota": self.quota(store_type), "at_global_seq": watermark}

    def _replayed(self, change_id: str, request_hash: str, conn=None) -> dict | None:
        conn = conn if conn is not None else self.db.read_conn
        row = conn.execute("SELECT input_hash,status,result FROM memory_changes WHERE change_id=?", (change_id,)).fetchone()
        if row:
            if row[0] != request_hash:
                return failure("IDEMPOTENCY_CONFLICT", "change_id 已绑定不同输入")
            if row[1] != "committed":
                return failure("STORE_RECOVERING", "该写入正在恢复")
            return {**json.loads(row[2]), "idempotent_replay": True}
        row = conn.execute("SELECT input_hash,result FROM memory_failed_attempts WHERE change_id=?", (change_id,)).fetchone()
        if row:
            return json.loads(row[1]) if row[0] == request_hash else failure("IDEMPOTENCY_CONFLICT", "change_id 已绑定不同输入")
        return None

    async def _record_failure(self, identity, change_id, request_hash, result) -> dict:
        execution = identity.job_id or identity.task_run_id
        turn = identity.user_turn_id or identity.task_run_id
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                replayed = self._replayed(change_id, request_hash, conn)
                if replayed is not None:
                    return replayed
                recorded = dict(result)
                if execution:
                    row = conn.execute("SELECT failures FROM memory_write_turns WHERE execution_id=? AND user_turn_id=?", (execution, turn)).fetchone()
                    count = min(3, (row[0] if row else 0) + 1)
                    conn.execute("INSERT INTO memory_write_turns VALUES(?,?,?) ON CONFLICT(execution_id,user_turn_id) DO UPDATE SET failures=excluded.failures", (execution, turn, count))
                    recorded.update(save_failures=count, skipped=count >= 3)
                    if count >= 3:
                        recorded["message"] += "；本回合跳过保存"
                conn.execute("INSERT INTO memory_failed_attempts VALUES(?,?,?)", (change_id, request_hash, canonical(recorded)))
            return recorded
        return await self.events.channel.execute(tx)

    async def change(self, identity: MemoryIdentity, store_type: str, store_id: str, *,
                     change_id: str, expected_revision: int, basis: str,
                     operations: list[dict] | None = None, restored_ledger_id: int | None = None,
                     skill: dict | None = None, support_files: dict | None = None,
                     request_context: dict | None = None) -> dict:
        if hasattr(self, "identities") and identity.actor_type == "user":
            self.identities.require(self.identities.memory_actor(identity), "manage", identity.workspace_id)
        denied = await asyncio.to_thread(self._authorize, identity, "workspace" if skill and expected_revision == 0 else store_type,
                                        identity.workspace_id if skill and expected_revision == 0 else store_id)
        if denied:
            return denied
        if not isinstance(change_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", change_id) or type(expected_revision) is not int or expected_revision < 0 or not isinstance(basis, str) or not basis.strip():
            return failure("VALIDATION_ERROR", "change_id、预期修订及依据必须有效")
        if not operations and not restored_ledger_id:
            return failure("VALIDATION_ERROR", "缺少记忆修改动作")
        request_hash = self.request_hash(identity, store_type, store_id, expected_revision, basis, operations=operations,
            restored_ledger_id=restored_ledger_id, skill=skill, support_files=support_files, request_context=request_context)
        async with self._locks.setdefault((store_type, store_id), asyncio.Lock()):
            replayed = await asyncio.to_thread(self._replayed, change_id, request_hash)
            if replayed is not None:
                return replayed
            supplied = canonical({"operations": operations, "basis": basis, "skill": skill, "support_files": support_files})
            if any(secret in supplied for secret in known_secrets()):
                return await self._record_failure(identity, change_id, request_hash,
                    failure("CREDENTIAL_REJECTED", "记忆正文或依据包含已登记凭据"))
            execution = identity.job_id or identity.task_run_id
            if execution:
                row = self.db.read_conn.execute("SELECT failures FROM memory_write_turns WHERE execution_id=? AND user_turn_id=?", (execution, identity.user_turn_id or identity.task_run_id)).fetchone()
                if row and row[0] >= 3:
                    return failure("SAVE_SKIPPED", "本回合已失败三次，跳过保存", skipped=True)
            before = await asyncio.to_thread(self._load, store_type, store_id)
            if "error" in before:
                return before if before["error"] == "STORE_RECOVERING" else await self._record_failure(identity, change_id, request_hash, before)
            if before["revision"] != expected_revision:
                return await self._record_failure(identity, change_id, request_hash, failure("REVISION_CONFLICT", "预期修订已经陈旧", current_revision=before["revision"]))
            now = datetime.now(timezone.utc).isoformat()
            restored_files = []
            metadata_updates = skill
            if restored_ledger_id:
                row = self.db.read_conn.execute("SELECT store_type,store_id,before_metadata,before_files FROM memory_ledger WHERE id=?", (restored_ledger_id,)).fetchone()
                if row is None or tuple(row[:2]) != (store_type, store_id):
                    return await self._record_failure(identity, change_id, request_hash, failure("NOT_FOUND", "该库的账本记录不存在"))
                restored_metadata = json.loads(row[2])
                entries = restored_metadata["entries"]
                metadata_updates = {k: v for k, v in restored_metadata.items() if k not in {"entries", "revision"}}
                restored_files = json.loads(row[3])
            else:
                entries = transform(before["metadata"]["entries"], operations, identity.source(change_id), basis, now,
                                    whole_document=store_type == "skill")
            if isinstance(entries, dict):
                return await self._record_failure(identity, change_id, request_hash, entries)
            if store_type == "skill" and not restored_ledger_id and skill:
                materials = dict(before["metadata"].get("files", {}))
                materials = {relative: self._file_text(self.path(store_type, store_id).parent / relative) for relative in materials}
                materials.update(support_files or {})
                combined = "\n".join([skill["name"], skill["description"], *materials, *[v for v in materials.values() if v is not None]])
                if contains_credentials(combined) or any(secret in combined for secret in known_secrets()):
                    return await self._record_failure(identity, change_id, request_hash, failure("CREDENTIAL_REJECTED", "Skill 描述或支撑文件包含凭据"))
                if material_risk(combined):
                    entries[0].update(needs_review=True, approved_by=None, approved_at=None)
            text = render_entries(entries)
            quota = self.quota(store_type)
            if quota is not None and len(text) > quota:
                visible = context_entries(before["metadata"]["entries"]) if identity.actor_type == "agent" else before["metadata"]["entries"]
                return await self._record_failure(identity, change_id, request_hash, failure("QUOTA_EXCEEDED", "记忆配额不足，请整合条目", current_entries=visible, used_characters=len(before["text"]), quota=quota, available_characters=max(0, quota-len(before["text"]))))
            action = "rollback" if restored_ledger_id else operations[0]["action"] if len(operations) == 1 else "batch"
            action = {"add": "create", "edit": "update"}.get(action, action)
            plan = await asyncio.to_thread(self._plan, identity, before, entries, text, basis, now, change_id, restored_ledger_id, action, restored_files,
                                           metadata_updates, support_files)
            plan["request_context"] = request_context
            task = asyncio.create_task(self._prepare_and_finish(change_id, request_hash, plan))
            with CancelScope(shield=True):
                try:
                    return await asyncio.shield(task)
                finally:
                    await task

    @staticmethod
    def request_hash(identity, store_type, store_id, expected_revision, basis, *, operations=None,
                     restored_ledger_id=None, skill=None, support_files=None, request_context=None):
        request = {"identity": asdict(identity), "store_type": store_type, "store_id": store_id,
                   "expected_revision": expected_revision, "basis": basis}
        if request_context is not None:
            request["request_context"] = request_context
        else:
            request.update(operations=operations, restored_ledger_id=restored_ledger_id)
            if skill is not None or support_files is not None:
                request.update(skill=skill, support_files=support_files)
        return sha256(canonical(request).encode())

    async def _prepare_and_finish(self, change_id, request_hash, plan):
        prepared = await self.events.channel.execute(lambda conn: self._prepare_tx(conn, change_id, request_hash, plan))
        if prepared:
            return prepared
        return await self._finish_change(change_id, plan)

    def _plan(self, identity, before, entries, text, basis, now, change_id, restored_id, action, restored_files,
              metadata_updates=None, support_files=None):
        store_type, store_id = before["store_type"], before["store_id"]
        metadata = {**(metadata_updates or {})} if restored_id else {**before["metadata"], **(metadata_updates or {})}
        metadata.update(revision=before["revision"] + 1, entries=entries)
        path = self.path(store_type, store_id)
        files = []
        if store_type == "skill":
            if restored_id and "name" not in metadata:
                metadata.update(name=before["metadata"]["name"], description=before["metadata"]["description"])
            manifest = dict(metadata.get("files", {}))
            if restored_id:
                for relative in before["metadata"].get("files", {}):
                    if relative not in manifest:
                        content = self._file_text(path.parent / relative)
                        files.append({"path": str((path.parent / relative).relative_to(self.data_dir)), "before": content, "after": None})
            for relative, content in (support_files or {}).items():
                previous = self._file_text(path.parent / relative)
                files.append({"path": str((path.parent / relative).relative_to(self.data_dir)), "before": previous, "after": content})
                if content is None:
                    manifest.pop(relative, None)
                else:
                    manifest[relative] = sha256(content.encode())
            metadata["files"] = manifest
            for relative in manifest:
                if relative not in (support_files or {}):
                    content = self._file_text(path.parent / relative)
                    files.append({"path": str((path.parent / relative).relative_to(self.data_dir)), "before": content, "after": content})
        for target, content in ((path, text), (path.with_suffix(".meta.json"), canonical(metadata))):
            previous = (before["text"] if target == path else canonical(before["metadata"])) if before["revision"] else None
            files.append({"path": str(target.relative_to(self.data_dir)), "before": previous, "after": content})
        old = {e["entry_id"]: e for e in before["metadata"]["entries"]}
        for entry in entries:
            if entry["state"] == "archived" and old.get(entry["entry_id"], {}).get("state") != "archived":
                root = (self.data_dir / "agents" / identity.agent_id / "archive" if identity.actor_type in {"agent", "curator"} else self.data_dir / "archive") / store_type / store_id / entry["entry_id"]
                archive_meta = {**entry, "original_path": str(path.relative_to(self.data_dir)), "store_type": store_type, "store_id": store_id}
                archived_files = []
                if store_type == "skill":
                    archive_meta["skill_metadata"] = metadata
                    archive_meta["original_files"] = {relative: str((path.parent / relative).relative_to(self.data_dir)) for relative in metadata["files"]}
                    archived_files = [(root / relative, self._file_text(path.parent / relative)) for relative in metadata["files"]]
                for target, content in [(root / "entry.md", entry["text"]), (root / "entry.meta.json", canonical(archive_meta)), *archived_files]:
                    relative = str(target.relative_to(self.data_dir))
                    tracked = self.db.read_conn.execute("SELECT json_extract(f.value,'$.content') FROM memory_ledger l,json_each(l.after_files) f WHERE json_extract(f.value,'$.path')=? ORDER BY l.global_seq DESC LIMIT 1", (relative,)).fetchone()
                    files.append({"path": relative, "before": tracked[0] if tracked else None, "after": content})
        primary_paths = {str(path.relative_to(self.data_dir)), str(path.with_suffix(".meta.json").relative_to(self.data_dir))}
        for restored in restored_files:
            if restored["path"] in primary_paths:
                continue
            self._intent_path(restored["path"])
            tracked = self.db.read_conn.execute("SELECT json_extract(f.value,'$.content') FROM memory_ledger l,json_each(l.after_files) f WHERE json_extract(f.value,'$.path')=? ORDER BY l.global_seq DESC LIMIT 1", (restored["path"],)).fetchone()
            files = [f for f in files if f["path"] != restored["path"]]
            files.append({"path": restored["path"], "before": tracked[0] if tracked else None, "after": restored["content"]})
        for item in files:
            item["before_sha256"] = sha256(item["before"].encode()) if item["before"] is not None else None
            item["after_sha256"] = sha256(item["after"].encode()) if item["after"] is not None else None
        return {"store_type": store_type, "store_id": store_id, "before": before, "after_text": text,
                "after_metadata": metadata, "files": files, "identity": asdict(identity), "source": identity.source(change_id),
                "basis": basis, "created_at": now, "revision": metadata["revision"],
                "change_id": change_id, "restored_ledger_id": restored_id,
                "action": action}

    def _prepare_tx(self, conn, change_id, request_hash, plan):
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if hasattr(self, "identities"):
                identity = MemoryIdentity(**plan["identity"])
                caller = self.identities.memory_actor(identity)
                self.identities.agent(caller, identity.agent_id, identity.workspace_id, conn)
                if identity.actor_type == "user":
                    self.identities.require(caller, "manage", identity.workspace_id, conn)
                if plan["store_type"] == "skill" and hasattr(self, "grants"):
                    self.grants.check_skill_change(conn, identity, plan["store_id"])
            replayed = self._replayed(change_id, request_hash, conn)
            if replayed is not None:
                return replayed
            row = conn.execute("SELECT revision FROM memory_stores WHERE store_type=? AND store_id=?", (plan["store_type"], plan["store_id"])).fetchone()
            if (row[0] if row else 0) != plan["before"]["revision"]:
                return failure("REVISION_CONFLICT", "准备意图时修订发生变化")
            if plan["store_type"] == "skill":
                occupied = conn.execute("SELECT id FROM memory_skills WHERE workspace_id=? AND name=? AND id<>?",
                    (plan["identity"]["workspace_id"], plan["after_metadata"]["name"], plan["store_id"])).fetchone()
                if occupied:
                    return failure("REVISION_CONFLICT", "同一工作区的技能名称已经存在")
            if plan["store_type"] == "skill" and plan["before"]["revision"] == 0:
                metadata, actor = plan["after_metadata"], plan["identity"]
                existing = conn.execute("SELECT id FROM memory_skills WHERE workspace_id=? AND agent_id=? AND name=?",
                    (actor["workspace_id"], actor["agent_id"], metadata["name"])).fetchone()
                if existing:
                    return failure("REVISION_CONFLICT", "创建目标已经存在")
                conn.execute("INSERT INTO memory_skills VALUES(?,?,?,?,?,?)", (plan["store_id"], actor["workspace_id"], actor["agent_id"],
                    metadata["name"], metadata["description"], plan["created_at"]))
            plan["ledger_id"] = conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM (SELECT id FROM memory_ledger UNION ALL SELECT json_extract(plan,'$.ledger_id') AS id FROM memory_changes)").fetchone()[0]
            conn.execute("INSERT INTO memory_changes(change_id,input_hash,store_type,store_id,expected_revision,plan,status,created_at) VALUES(?,?,?,?,?,?,'prepared',?)", (change_id, request_hash, plan["store_type"], plan["store_id"], plan["before"]["revision"], canonical(plan), plan["created_at"]))

    def _replace_files(self, plan) -> dict | None:
        for item in plan["files"]:
            target = self._intent_path(item["path"])
            current = self._file_text(target)
            digest = sha256(current.encode()) if current is not None else None
            if digest not in (item["before_sha256"], item["after_sha256"]):
                return failure("EXTERNAL_MODIFICATION", "意图文件出现第三种状态", path=item["path"])
        for item in plan["files"]:
            content = item["after"].encode() if item["after"] is not None else None
            self._atomic_replace(self._intent_path(item["path"]), content, item["before_sha256"], item["after_sha256"], plan["change_id"])

    def _intent_path(self, relative: str) -> Path:
        path = Path(relative)
        target = self.data_dir / path
        if path.is_absolute() or ".." in path.parts or not target.resolve().is_relative_to(self.data_dir.resolve()):
            raise ValueError("记忆意图中的文件路径超出数据目录")
        return target

    def _atomic_replace(self, target, content, before_sha, after_sha, change_id):
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = _open_dir_nofollow(target)
        try:
            current = self._file_text(target)
            digest = sha256(current.encode()) if current is not None else None
            if digest == after_sha:
                return
            if digest != before_sha:
                raise ValueError("EXTERNAL_MODIFICATION：文件在替换前发生变化")
            if content is None:
                os.unlink(target.name, dir_fd=fd)
                os.fsync(fd)
                return
            temporary = f".{target.name}.{change_id}.pending"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target.name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
            for parent in target.parent.parents:
                if parent == self.data_dir.parent:
                    break
                descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            os.close(fd)

    async def _finish_change(self, change_id, plan):
        error = await asyncio.to_thread(self._replace_files, plan)
        if error:
            return error
        return await self.events.channel.execute(lambda conn: self._commit_tx(conn, change_id, plan))

    def _commit_tx(self, conn, change_id, plan):
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            state = conn.execute("SELECT status,result FROM memory_changes WHERE change_id=?", (change_id,)).fetchone()
            if state[0] == "committed":
                return {**json.loads(state[1]), "idempotent_replay": True}
            previous_audit_seq = conn.execute("SELECT coalesce(max(seq),0) FROM audit_log").fetchone()[0]
            kind, store_id = plan["store_type"], plan["store_id"]
            text_sha = sha256(plan["after_text"].encode())
            meta_sha = sha256(canonical(plan["after_metadata"]).encode())
            conn.execute("INSERT INTO memory_stores VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(store_type,store_id) DO UPDATE SET revision=excluded.revision,text=excluded.text,metadata=excluded.metadata,text_sha256=excluded.text_sha256,metadata_sha256=excluded.metadata_sha256,updated_at=excluded.updated_at", (kind, store_id, plan["revision"], plan["after_text"], canonical(plan["after_metadata"]), text_sha, meta_sha, plan["created_at"]))
            conn.execute("DELETE FROM memory_entries WHERE store_type=? AND store_id=?", (kind, store_id))
            for entry in plan["after_metadata"]["entries"]:
                conn.execute("INSERT INTO memory_entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (entry["entry_id"], kind, store_id, entry["entry_hash"], entry["text"], entry["state"], entry["hits"], entry["last_hit_at"], entry["created_at"], canonical(entry["source"]), entry["basis"], int(entry["needs_review"]), entry["approved_by"], entry["approved_at"]))
            old = {e["entry_id"]: e["state"] for e in plan["before"]["metadata"]["entries"]}
            archived = any(e["state"] == "archived" and old.get(e["entry_id"]) != "archived" for e in plan["after_metadata"]["entries"])
            event_type = RunEventType.MEMORY_ARCHIVED if archived else RunEventType.MEMORY_UPDATED
            if kind == "skill" and not archived:
                event_type = RunEventType.SKILL_PATCHED
            if kind == "skill":
                conn.execute("UPDATE memory_skills SET name=?,description=? WHERE id=?", (plan["after_metadata"]["name"], plan["after_metadata"]["description"], store_id))
            prior = {e["entry_id"]: e for e in plan["before"]["metadata"]["entries"]}
            changed = [e for e in plan["after_metadata"]["entries"] if prior.get(e["entry_id"]) != e]
            single = changed[0] if len(changed) == 1 else None
            payload = {"change_id": change_id, "ledger_id": plan["ledger_id"], "store_type": kind, "store_id": store_id,
                "credential_owner_id": "owner",
                "revision": plan["revision"], "action": plan["action"], "source": plan["source"],
                "entry_id": single["entry_id"] if single else None, "entry_hash": single["entry_hash"] if single else None,
                "summary": redact(single["text"] if single else f"记忆库完成 {plan['action']}，修改 {len(changed)} 条记忆")[:200],
                "source_task_run_id": plan["identity"]["task_run_id"], "job_id": plan["identity"]["job_id"],
                "scope": {"owner_id": "owner", "workspace_id": plan["identity"]["workspace_id"], "agent_id": plan["identity"]["agent_id"]}}
            if kind == "skill":
                payload.update(name=plan["after_metadata"]["name"], description=plan["after_metadata"]["description"],
                               files=sorted(plan["after_metadata"].get("files", {})))
            if archived:
                payload["archive_path"] = str(Path(next(f["path"] for f in plan["files"] if f["path"].endswith("/entry.md"))).parent)
            event = self.events.append_in_tx(conn, task_run_id=None, conversation_id=plan["identity"]["conversation_id"], type=event_type, payload=payload)
            audit_seq = append_audit(conn, ts=plan["created_at"], actor_type=plan["source"]["actor_type"], actor_id=plan["source"]["actor_id"], action=event_type.value, resource_type="memory_store", resource_id=f"{kind}:{store_id}", detail=canonical(payload))
            before_files = [{"path": f["path"], "content": f["before"], "sha256": f["before_sha256"]} for f in plan["files"]]
            after_files = [{"path": f["path"], "content": f["after"], "sha256": f["after_sha256"]} for f in plan["files"]]
            conn.execute("INSERT INTO memory_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (plan["ledger_id"], change_id, kind, store_id, plan["action"], plan["revision"], plan["before"]["text"], plan["after_text"], canonical(plan["before"]["metadata"]), canonical(plan["after_metadata"]), canonical(before_files), canonical(after_files), canonical(plan["source"]), plan["basis"], plan["restored_ledger_id"], audit_seq, event.global_seq, plan["created_at"]))
            version_result, version_event = {}, None
            if kind == "skill" and hasattr(self, "skill_versions"):
                version_result, version_event, audit_seq = self.skill_versions.publish_in_tx(conn, plan)
            result = {"change_id": change_id, "ledger_id": plan["ledger_id"], "store_type": kind, "store_id": store_id,
                      "entry_id": payload["entry_id"], "entry_hash": payload["entry_hash"],
                      "revision": plan["revision"], "global_seq": event.global_seq, "idempotent_replay": False, **version_result}
            conn.execute("UPDATE memory_changes SET status='committed',result=?,committed_at=? WHERE change_id=?", (canonical(result), datetime.now(timezone.utc).isoformat(), change_id))
        self.events.publish(event)
        if version_event is not None:
            self.events.publish(version_event)
        if audit_seq // SNAPSHOT_EVERY > previous_audit_seq // SNAPSHOT_EVERY:
            snapshot_chain_head(conn, self.data_dir / "chain-head.txt")
        return result

    async def recover(self) -> list[dict]:
        rows = self.db.read_conn.execute("SELECT change_id,plan FROM memory_changes WHERE status='prepared' ORDER BY created_at,change_id").fetchall()
        results = []
        for row in rows:
            plan = json.loads(row[1])
            async with self._locks.setdefault((plan["store_type"], plan["store_id"]), asyncio.Lock()):
                results.append(await self._finish_change(row[0], plan))
        return results

    async def record_hits(self, entry_ids: list[str], *, use_id: str | None = None) -> None:
        """显式检索使用单独统计，冻结快照及业务修订均保持不变。"""
        now = datetime.now(timezone.utc).isoformat()
        use_id = use_id or uuid.uuid4().hex
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                for entry_id in sorted(set(entry_ids)):
                    inserted = conn.execute("INSERT OR IGNORE INTO memory_usage_hits VALUES(?,?,?)", (use_id, entry_id, now))
                    if inserted.rowcount:
                        conn.execute("INSERT INTO memory_usage VALUES(?,1,?) ON CONFLICT(entry_id) DO UPDATE SET hits=hits+1,last_hit_at=excluded.last_hit_at", (entry_id, now))
        await self.events.channel.execute(tx)

    async def run_tool(self, invocation, context) -> dict:
        row = self.db.read_conn.execute("SELECT workspace_id,agent_id,conversation_id FROM task_runs JOIN conversations ON conversations.id=task_runs.conversation_id WHERE task_runs.id=?", (context.task_run_id,)).fetchone()
        if row is None:
            return failure("OUT_OF_SCOPE", "记忆工具缺少真实任务身份")
        identity = MemoryIdentity(row[0], row[1], "agent", row[1], row[2], context.task_run_id,
            job_id=context.job_id, user_turn_id=context.job_id or context.task_run_id)
        target = invocation.input.get("target")
        store_id = {"user": "owner", "workspace": row[0], "soul": row[1]}.get(target)
        if store_id is None:
            return failure("VALIDATION_ERROR", "target 必须为 user、workspace 或 soul")
        if invocation.input.get("action") == "read":
            return await self.read(identity, target, store_id)
        operations = invocation.input.get("operations")
        if operations is None:
            operations = [{k: invocation.input[k] for k in ("action", "entry_hash", "text") if k in invocation.input}]
        return await self.change(identity, target, store_id, change_id=invocation.call_id,
            expected_revision=invocation.input.get("expected_revision"), basis=invocation.input.get("basis"), operations=operations)
