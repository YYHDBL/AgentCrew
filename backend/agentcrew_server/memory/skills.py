"""Skill 渐进披露与本回合读取修订，持久化复用记忆账本。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath

from agentcrew_core.memory import context_entries, failure, sha256, skill_content, suspected_injection

from .store import MemoryIdentity, MemoryStore


class MemorySkills:
    def __init__(self, store: MemoryStore):
        self.store = store
        self.db = store.db
        self._names: dict[tuple[str, str, str], asyncio.Lock] = {}

    def _find(self, identity, name):
        return self.db.read_conn.execute("SELECT * FROM memory_skills WHERE workspace_id=? AND agent_id=? AND name=?",
            (identity.workspace_id, identity.agent_id, name)).fetchone()

    @staticmethod
    def _name(name):
        return isinstance(name, str) and 1 <= len(name) <= 80 and name == name.strip() and not any(ord(c) < 32 for c in name)

    def _file(self, store_id, relative):
        if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
            return failure("OUT_OF_SCOPE", "支撑文件路径无效")
        parts = relative.split("/")
        if len(parts) < 2 or parts[0] not in {"references", "templates", "assets", "scripts"} or any(p in {"", ".", ".."} for p in parts):
            return failure("OUT_OF_SCOPE", "支撑文件必须位于技能的合法目录内")
        path = self.store.path("skill", store_id).parent / PurePosixPath(relative)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            return failure("OUT_OF_SCOPE", "Skill 文件路径不能使用符号链接")
        return path

    async def index(self, identity, *, archived=False):
        denied = await asyncio.to_thread(self.store._authorize, identity, "workspace", identity.workspace_id)
        if denied:
            return denied
        if identity.actor_type == "agent" and hasattr(self.store, "skill_versions"):
            return self.store.skill_versions.task_index(identity)
        rows = self.db.read_conn.execute("SELECT c.*,s.revision,s.metadata FROM memory_skills c JOIN memory_stores s ON s.store_type='skill' AND s.store_id=c.id WHERE c.workspace_id=? AND c.agent_id=? ORDER BY c.name,c.id",
            (identity.workspace_id, identity.agent_id)).fetchall()
        items = []
        for row in rows:
            value = await self.store.read(identity, "skill", row["id"])
            if "error" in value:
                return value
            visible = context_entries(value["entries"], include_archived=archived) if identity.actor_type == "agent" else value["entries"]
            if not visible or (visible[0]["state"] == "archived" and not archived):
                continue
            entry = visible[0]
            if identity.actor_type == "agent" and any(suspected_injection(t) for t in [value["metadata"]["name"], value["metadata"]["description"], *value["metadata"].get("files", {})]):
                continue
            items.append({"id": row["id"], "workspace_id": row["workspace_id"], "agent_id": row["agent_id"],
                "name": value["metadata"]["name"], "description": value["metadata"]["description"], "revision": value["revision"],
                "state": entry["state"], "hits": entry["hits"], "last_hit_at": entry["last_hit_at"]})
        return {"items": items}

    async def _record_read(self, identity, name, revision, call_id):
        execution = identity.job_id or identity.task_run_id
        if execution is None:
            return
        turn = identity.user_turn_id or identity.task_run_id
        def tx(conn):
            with conn:
                conn.execute("INSERT INTO skill_reads VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(execution_id,user_turn_id,workspace_id,agent_id,name) DO UPDATE SET revision=excluded.revision,call_id=excluded.call_id,created_at=excluded.created_at",
                    (execution, turn, identity.workspace_id, identity.agent_id, name, revision, call_id, datetime.now(timezone.utc).isoformat()))
        await self.store.events.channel.execute(tx)

    async def view(self, identity, name, *, file=None, call_id=None):
        if not self._name(name):
            return failure("VALIDATION_ERROR", "Skill 名称必须为 1 至 80 字符")
        denied = await asyncio.to_thread(self.store._authorize, identity, "workspace", identity.workspace_id)
        if denied:
            return denied
        call_id = call_id or uuid.uuid4().hex
        if identity.actor_type == "agent" and hasattr(self.store, "skill_versions"):
            return await self.store.skill_versions.task_view(identity, name, file, call_id, self)
        async with self._names.setdefault((identity.workspace_id, identity.agent_id, name), asyncio.Lock()):
            row = self._find(identity, name)
            if row is None:
                if file is not None:
                    return failure("NOT_FOUND", "Skill 创建目标不存在")
                await self._record_read(identity, name, 0, call_id)
                return {"name": name, "exists": False, "revision": 0}
            store_id = row["id"]
            lock = self.store._locks.setdefault(("skill", store_id), asyncio.Lock())
            if lock.locked():
                return failure("STORE_RECOVERING", "Skill 正在写入")
            async with lock:
                value = await asyncio.to_thread(self.store._load, "skill", store_id)
                if "error" in value:
                    return value
                entries = value["metadata"]["entries"]
                if identity.actor_type == "agent":
                    visible = context_entries(entries)
                    if (entries and not visible) or any("[BLOCKED:" in e.get(field, "") for e in visible for field in ("text", "basis", "review_basis")):
                        return failure("REVIEW_REQUIRED", "Skill 正文需要人工审核或含疑似注入")
                    if any(suspected_injection(t) for t in [value["metadata"]["name"], value["metadata"]["description"], *value["metadata"].get("files", {})]):
                        return failure("REVIEW_REQUIRED", "Skill 名称、描述或支撑文件路径含疑似注入")
                if file is not None:
                    path = self._file(store_id, file)
                    if isinstance(path, dict):
                        return path
                    if file not in value["metadata"].get("files", {}):
                        return failure("NOT_FOUND", "支撑文件未登记在当前 Skill 修订中")
                    text = await asyncio.to_thread(self.store._file_text, path)
                    if identity.actor_type == "agent" and suspected_injection(text):
                        return failure("REVIEW_REQUIRED", "Skill 支撑文件含疑似注入")
                else:
                    text = entries[0]["text"] if entries else ""
                    await self._record_read(identity, name, value["revision"], call_id)
                    if entries:
                        await self.store.record_hits([entries[0]["entry_id"]], use_id=call_id)
                return {"id": store_id, "name": value["metadata"]["name"], "description": value["metadata"]["description"], "exists": True,
                    "revision": value["revision"], "text": text, "sha256": sha256(text.encode()),
                    "files": sorted(value["metadata"].get("files", {})), **({"file": file} if file is not None else {})}

    async def change(self, identity, name, *, action, change_id, expected_revision, basis,
                     description=None, text=None, old_text=None, new_text=None, files=None,
                     entry_hash=None, management_request=None):
        if not self._name(name) or action not in {"create", "patch", "edit"} or type(expected_revision) is not int or expected_revision < 0:
            return failure("VALIDATION_ERROR", "Skill 名称、动作及预期修订无效")
        denied = await asyncio.to_thread(self.store._authorize, identity, "workspace", identity.workspace_id)
        if denied:
            return denied
        if description is not None and (not isinstance(description, str) or not 1 <= len(description) <= 60):
            return failure("VALIDATION_ERROR", "Skill 描述必须为 1 至 60 字符")
        if not isinstance(basis, str) or not basis.strip() or (files is not None and not isinstance(files, dict)):
            return failure("VALIDATION_ERROR", "Skill 依据和支撑文件必须有效")
        async with self._names.setdefault((identity.workspace_id, identity.agent_id, name), asyncio.Lock()):
            row = self._find(identity, name)
            previous = self.db.read_conn.execute("SELECT store_id FROM memory_changes WHERE change_id=? AND store_type='skill'", (change_id,)).fetchone()
            assigned_id = uuid.uuid5(uuid.NAMESPACE_URL, f"agentcrew:skill:{change_id}").hex
            store_id = previous[0] if previous else assigned_id if action == "create" else row["id"] if row else assigned_id
            request_context = {"name": name, "action": action, "text": text, "old_text": old_text, "new_text": new_text,
                               "description": description, "files": files}
            if management_request is not None:
                request_context.update(entry_hash=entry_hash, management_request=management_request)
            request_hash = self.store.request_hash(identity, "skill", store_id, expected_revision, basis, request_context=request_context)
            replayed = self.store._replayed(change_id, request_hash)
            if replayed is not None:
                return replayed
            execution = identity.job_id or identity.task_run_id
            if identity.actor_type == "agent" and not previous:
                read = self.db.read_conn.execute("SELECT revision FROM skill_reads WHERE execution_id=? AND user_turn_id=? AND workspace_id=? AND agent_id=? AND name=?",
                    (execution, identity.user_turn_id or identity.task_run_id, identity.workspace_id, identity.agent_id, name)).fetchone()
                if read is None:
                    return failure("READ_REQUIRED", "必须在本回合先读取 Skill 正文或创建目标状态")
                if read[0] != expected_revision:
                    return failure("REVISION_CONFLICT", "预期修订与本回合读取修订不一致", read_revision=read[0])
            before = await asyncio.to_thread(self.store._load, "skill", store_id)
            if "error" in before:
                return before
            if before["revision"] != expected_revision:
                return await self.store._record_failure(identity, change_id, request_hash,
                    failure("REVISION_CONFLICT", "预期修订已经陈旧", current_revision=before["revision"]))
            if entry_hash is not None and not any(entry["entry_hash"] == entry_hash for entry in before["metadata"]["entries"]):
                return await self.store._record_failure(identity, change_id, request_hash,
                    failure("NOT_FOUND", "Skill 条目哈希已经不存在"))
            if action == "create" and (expected_revision != 0 or (row and not previous)):
                return failure("REVISION_CONFLICT", "创建目标已经存在")
            if action != "create" and not row:
                return failure("NOT_FOUND", "需要修改的 Skill 不存在")
            description = description if description is not None else before["metadata"].get("description")
            if description is None:
                return failure("VALIDATION_ERROR", "创建 Skill 必须提供有效描述")
            now = datetime.now(timezone.utc).isoformat()
            entries = skill_content(before["metadata"]["entries"], action=action, text=text, old_text=old_text, new_text=new_text,
                source=identity.source(change_id), basis=basis, now=now)
            if isinstance(entries, dict):
                return await self.store._record_failure(identity, change_id, request_hash, entries)
            for relative, content in (files or {}).items():
                path = self._file(store_id, relative)
                if isinstance(path, dict):
                    return path
                if content is not None and not isinstance(content, str):
                    return failure("VALIDATION_ERROR", "支撑文件正文必须为字符串，删除使用 null")
            metadata = {"name": name, "description": description}
            operation = {"action": "edit" if before["metadata"]["entries"] else "add", "text": entries[0]["text"]}
            if before["metadata"]["entries"]:
                operation["entry_hash"] = before["metadata"]["entries"][0]["entry_hash"]
            return await self.store.change(identity, "skill", store_id, change_id=change_id, expected_revision=expected_revision,
                basis=basis, operations=[operation], skill=metadata, support_files=files,
                request_context=request_context)

    async def run_tool(self, invocation, context):
        row = self.db.read_conn.execute("SELECT workspace_id,agent_id,conversation_id FROM task_runs JOIN conversations ON conversations.id=task_runs.conversation_id WHERE task_runs.id=?", (context.task_run_id,)).fetchone()
        if row is None:
            return failure("OUT_OF_SCOPE", "Skill 工具缺少真实任务身份")
        identity = MemoryIdentity(row[0], row[1], "agent", row[1], row[2], context.task_run_id,
            job_id=context.job_id, user_turn_id=context.job_id or context.task_run_id)
        value = invocation.input
        if invocation.name == "skill_view" or value.get("action") == "read":
            return await self.view(identity, value.get("name"), file=value.get("file"), call_id=invocation.call_id)
        return await self.change(identity, value.get("name"), action=value.get("action"), change_id=invocation.call_id,
            expected_revision=value.get("expected_revision"), basis=value.get("basis"), description=value.get("description"),
            text=value.get("text"), old_text=value.get("old_text"), new_text=value.get("new_text"), files=value.get("files"))
