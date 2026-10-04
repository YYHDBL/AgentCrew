"""经过验证的服务备份、持久化恢复计划与启动恢复。"""

import hashlib
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentcrew_core.events import RunEventType
from ..db.audit import append_audit, snapshot_chain_head, verify_with_anchor
from .resources import GovernanceError, canonical, identifier, now


DATA_ENTRIES = ("config.json", "USER.md", "USER.meta.json", "soul.md", "soul.meta.json",
    "agents", "archive", "skills", "workspaces", "conversations", "artifacts")
DATABASE_ENTRIES = ("agentcrew.db", "agentcrew.db-wal", "agentcrew.db-shm", "chain-head.txt")


class BackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["database", "directory"]
    change_id: str
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: str
    verified: bool
    database_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    anchor_seq: int = Field(ge=1)
    anchor_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    files: dict[str, str]

    def public(self):
        return self.model_dump(exclude={"change_id", "input_hash", "files"})


class RestorePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backup_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    change_id: str
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    preserved_path: str
    original_entries: list[str]
    actor_id: str
    credential_owner_id: str


class RestoreResult(RestorePlan):
    restored_at: str
    verified: bool


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path, value):
    if path.is_symlink() or path.with_suffix(".pending").is_symlink():
        raise GovernanceError("OUT_OF_SCOPE", "持久化计划及暂存文件不能是符号链接", 403)
    temporary = path.with_suffix(".pending")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def data_files(root):
    paths = []
    for name in DATA_ENTRIES:
        entry = root / name
        if entry.is_symlink():
            raise GovernanceError("OUT_OF_SCOPE", "备份数据入口不能是符号链接", 403)
        if not entry.exists():
            continue
        entries = [entry, *entry.rglob("*")] if entry.is_dir() else [entry]
        for path in entries:
            if path.is_symlink():
                raise GovernanceError("OUT_OF_SCOPE", "备份中的符号链接需要先由所有者处理", 403)
            if path.is_file():
                paths.append(path)
    return {str(path.relative_to(root)): digest(path) for path in sorted(paths)}


def managed_store(root):
    store = root / "backups" / "governance"
    if (root / "backups").is_symlink() or store.is_symlink() or store.resolve() != root.resolve() / "backups" / "governance":
        raise GovernanceError("OUT_OF_SCOPE", "备份目录超出服务管理范围", 403)
    return store


def entry_digest(path):
    if path.is_symlink():
        raise GovernanceError("OUT_OF_SCOPE", "恢复入口不能是符号链接", 403)
    if path.is_file():
        return digest(path)
    files = sorted(path.rglob("*"))
    if any(file.is_symlink() for file in files):
        raise GovernanceError("OUT_OF_SCOPE", "恢复目录不能包含符号链接", 403)
    return {str(file.relative_to(path)): digest(file) for file in files if file.is_file()}


def validate_backup(root, backup_id):
    if not isinstance(backup_id, str) or len(backup_id) != 64 or any(char not in "0123456789abcdef" for char in backup_id):
        raise GovernanceError("VALIDATION_ERROR", "备份标识无效", 422)
    folder = managed_store(root) / backup_id
    if any(path.is_symlink() for path in (root / "backups", root / "backups" / "governance", folder, folder / "manifest.json")):
        raise GovernanceError("OUT_OF_SCOPE", "备份路径不能是符号链接", 403)
    if not (folder / "manifest.json").is_file():
        raise GovernanceError("NOT_FOUND", "服务管理的备份不存在", 404)
    manifest = BackupManifest.model_validate_json((folder / "manifest.json").read_text())
    payload = folder / "payload"
    if manifest.id != backup_id or hashlib.sha256(manifest.change_id.encode()).hexdigest() != backup_id or payload.is_symlink() or (payload / "agentcrew.db").is_symlink():
        raise GovernanceError("OUT_OF_SCOPE", "备份身份或目录无效", 403)
    if digest(payload / "agentcrew.db") != manifest.database_sha256:
        raise GovernanceError("REPLAY_CORRUPT", "备份数据库SHA不一致")
    names = ("agentcrew.db", "chain-head.txt") if manifest.kind == "database" else ("agentcrew.db", "chain-head.txt", *DATA_ENTRIES)
    if any(path.name not in names or path.is_symlink() for path in payload.iterdir()):
        raise GovernanceError("OUT_OF_SCOPE", "备份包含未管理路径", 403)
    actual_files = data_files(payload)
    if manifest.kind == "directory" and actual_files != manifest.files:
        raise GovernanceError("REPLAY_CORRUPT", "备份文件SHA或清单不一致")
    if manifest.kind == "database" and actual_files:
        raise GovernanceError("REPLAY_CORRUPT", "数据库备份包含未登记文件")
    connection = sqlite3.connect(f"file:{payload / 'agentcrew.db'}?mode=ro", uri=True)
    integrity = connection.execute("PRAGMA integrity_check").fetchall()
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    audit = verify_with_anchor(connection, payload / "chain-head.txt")
    anchor = connection.execute("SELECT hash FROM audit_log WHERE seq=?", (manifest.anchor_seq,)).fetchone()
    connection.close()
    if integrity != [("ok",)] or foreign_keys or not audit.ok or anchor is None or anchor[0] != manifest.anchor_hash:
        raise GovernanceError("REPLAY_CORRUPT", "备份数据库完整性、外键或两级审计验证失败")
    return manifest, payload


class DiagnosticBackups:
    def __init__(self, runtime):
        self.runtime = runtime
        self.root = runtime.data_dir
        self.folder = self.root / "backups" / "governance"

    def list(self, identity):
        self.runtime.identities.require(identity, "owner")
        managed_store(self.root)
        items = []
        for path in sorted(self.folder.glob("*/manifest.json")):
            manifest, _ = validate_backup(self.root, path.parent.name)
            items.append(manifest.public())
        return items

    async def record_restores(self):
        for path in sorted(self.folder.glob("preserved-*/restore-result.json")):
            if path.parent.is_symlink() or path.is_symlink():
                raise GovernanceError("OUT_OF_SCOPE", "恢复结果目录与文件不能是符号链接", 403)
            result = RestoreResult.model_validate_json(path.read_text())
            if path.parent.name != "preserved-" + result.input_hash or not result.verified:
                raise GovernanceError("REPLAY_CORRUPT", "恢复结果身份或验证状态无效")
            def operation(conn):
                org = conn.execute("SELECT org_id FROM memberships WHERE user_id=?", (result.credential_owner_id,)).fetchone()
                if org is None:
                    raise GovernanceError("REPLAY_CORRUPT", "恢复结果的本地凭证身份不存在")
                value = {"id": result.backup_id, "revision": 1, "preserved_path": result.preserved_path, "verified": True}
                return value, {"org_id": org[0], "workspace_id": None, "agent_id": None, "owner_id": result.actor_id}, {"preserved_path": result.preserved_path}
            await self.runtime.governance.mutate(change_id=result.change_id, actor_id=result.actor_id,
                credential_owner_id=result.credential_owner_id, request={"backup_id": result.backup_id, "input_hash": result.input_hash},
                action=RunEventType.GOVERNANCE_BACKUP_RESTORED.value, kind="diagnostic_backup", resource_id=result.backup_id,
                operation=operation, event_type=RunEventType.GOVERNANCE_BACKUP_RESTORED)

    async def create(self, identity, change_id, kind):
        identifier(change_id)
        if kind not in {"database", "directory"}:
            raise GovernanceError("VALIDATION_ERROR", "备份类型无效", 422)
        signature = hashlib.sha256(canonical({"actor_id": identity.effective_user_id,
            "credential_owner_id": identity.credential_owner_id, "kind": kind, "change_id": change_id}).encode()).hexdigest()
        backup_id = hashlib.sha256(change_id.encode()).hexdigest()
        def tx(conn):
            self.runtime.identities.require(identity, "owner", conn=conn)
            managed_store(self.root)
            if self.runtime.diagnostic is not None:
                raise GovernanceError("DIAGNOSTIC_MODE", "诊断期间禁止创建备份", 503)
            prior = conn.execute("SELECT input_hash,result FROM governance_changes WHERE change_id=?", (change_id,)).fetchone()
            if prior is not None:
                if prior[0] != signature:
                    raise GovernanceError("IDEMPOTENCY_CONFLICT", "change_id已经绑定不同请求")
                manifest, _ = validate_backup(self.root, backup_id)
                return manifest.public()
            busy = conn.execute("SELECT 1 FROM task_runs WHERE status IN ('queued','running','waiting_user','waiting_verification') LIMIT 1").fetchone()
            jobs = conn.execute("SELECT 1 FROM memory_jobs WHERE status IN ('queued','running','waiting_approval') LIMIT 1").fetchone()
            queue = conn.execute("SELECT 1 FROM conversations c,json_each(c.pending_queue) q WHERE json_extract(q.value,'$.state')='queued' LIMIT 1").fetchone()
            changes = conn.execute("SELECT 1 FROM memory_changes WHERE status!='committed' LIMIT 1").fetchone()
            if busy or jobs or queue or changes:
                raise GovernanceError("SYSTEM_BUSY", "任务、作业、队列及持久化意图全部停止后才能创建自洽备份")
            audit = verify_with_anchor(conn, self.root / "chain-head.txt")
            if not audit.ok:
                raise GovernanceError("REPLAY_CORRUPT", "审计链未通过验证，禁止创建备份")
            folder = self.folder / backup_id
            if folder.is_symlink():
                raise GovernanceError("OUT_OF_SCOPE", "备份入口不能是符号链接", 403)
            if not (folder / "manifest.json").exists():
                if folder.exists():
                    incomplete = self.folder / ("incomplete-" + backup_id + "-" + hashlib.sha256(now().encode()).hexdigest()[:12])
                    os.replace(folder, incomplete)
                payload = folder / "payload"
                payload.mkdir(parents=True)
                atomic_json(folder / "intent.json", {"change_id": change_id, "input_hash": signature, "kind": kind})
                seq = snapshot_chain_head(conn, self.root / "chain-head.txt")
                if seq is None:
                    raise GovernanceError("REPLAY_CORRUPT", "备份必须具有真实审计锚点")
                conn.execute("VACUUM INTO ?", (str(payload / "agentcrew.db"),))
                shutil.copy2(self.root / "chain-head.txt", payload / "chain-head.txt")
                files = data_files(self.root)
                if kind == "directory":
                    for name in DATA_ENTRIES:
                        source = self.root / name
                        if source.is_dir():
                            shutil.copytree(source, payload / name)
                        elif source.is_file():
                            shutil.copy2(source, payload / name)
                head = conn.execute("SELECT hash FROM audit_log WHERE seq=?", (seq,)).fetchone()[0]
                manifest = BackupManifest(id=backup_id, kind=kind, change_id=change_id, input_hash=signature,
                    created_at=now(), verified=True, database_sha256=digest(payload / "agentcrew.db"), anchor_seq=seq, anchor_hash=head, files=files)
                atomic_json(folder / "manifest.json", manifest.model_dump())
            manifest, _ = validate_backup(self.root, backup_id)
            if manifest.input_hash != signature:
                raise GovernanceError("IDEMPOTENCY_CONFLICT", "change_id已经绑定不同备份请求")
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                detail = {"change_id": change_id, "resource_type": "diagnostic_backup", "resource_id": backup_id,
                    "revision": 1, "actor_id": identity.effective_user_id, "credential_owner_id": identity.credential_owner_id,
                    "kind": kind, "scope": {"org_id": self.runtime.identities.current(identity, conn)["org_id"], "workspace_id": None, "agent_id": None, "owner_id": identity.effective_user_id}}
                audit_seq = append_audit(conn, ts=now(), actor_type="user", actor_id=identity.effective_user_id,
                    action="governance.backup_created", resource_type="diagnostic_backup", resource_id=backup_id, detail=canonical(detail))
                detail["audit_seq"] = audit_seq
                event = self.runtime.event_store.append_in_tx(conn, task_run_id=None, conversation_id=None,
                    type=RunEventType.GOVERNANCE_BACKUP_CREATED, payload=detail)
                conn.execute("INSERT INTO governance_changes VALUES(?,?,?,?,?,?)", (change_id, signature,
                    identity.effective_user_id, canonical(manifest.public()), audit_seq, event.global_seq))
            if audit_seq % 100 == 0:
                snapshot_chain_head(conn, self.root / "chain-head.txt")
            self.runtime.event_store.publish(event)
            return manifest.public()
        return await self.runtime.write_channel.execute(tx)

    async def restore(self, identity, backup_id, change_id):
        identifier(change_id)
        signature = hashlib.sha256(canonical({"actor_id": identity.effective_user_id,
            "credential_owner_id": identity.credential_owner_id, "backup_id": backup_id, "change_id": change_id}).encode()).hexdigest()
        def stage(conn):
            self.runtime.identities.require(identity, "owner", conn=conn)
            if self.runtime.diagnostic is None:
                raise GovernanceError("INVALID_TRANSITION", "受控恢复需要先进入只读诊断")
            manifest, _ = validate_backup(self.root, backup_id)
            result_path = self.folder / ("preserved-" + signature) / "restore-result.json"
            if result_path.parent.is_symlink() or result_path.is_symlink():
                raise GovernanceError("OUT_OF_SCOPE", "恢复结果路径不能是符号链接", 403)
            if result_path.exists():
                completed = RestoreResult.model_validate_json(result_path.read_text())
                if completed.input_hash != signature or not completed.verified:
                    raise GovernanceError("IDEMPOTENCY_CONFLICT", "change_id恢复结果不一致")
                return {"backup_id": backup_id, "restart_required": True, "preserved_path": completed.preserved_path}
            if conn.execute("SELECT 1 FROM governance_changes WHERE change_id=?", (change_id,)).fetchone():
                raise GovernanceError("IDEMPOTENCY_CONFLICT", "change_id已经用于其他治理变更")
            pending = self.root / "pending-restore.json"
            if pending.exists():
                plan = RestorePlan.model_validate_json(pending.read_text())
                if plan.input_hash != signature:
                    raise GovernanceError("IDEMPOTENCY_CONFLICT", "已有不同的恢复计划等待重启")
            else:
                if manifest.kind == "database" and data_files(self.root) != manifest.files:
                    raise GovernanceError("REPLAY_CORRUPT", "数据库备份关联文件已经变化，请选择包含文件的整目录备份")
                names = DATABASE_ENTRIES if manifest.kind == "database" else (*DATABASE_ENTRIES, *DATA_ENTRIES)
                original = [name for name in names if (self.root / name).exists() or (self.root / name).is_symlink()]
                plan = RestorePlan(backup_id=backup_id, change_id=change_id, input_hash=signature,
                    preserved_path="backups/governance/preserved-" + signature, original_entries=original,
                    actor_id=identity.effective_user_id, credential_owner_id=identity.credential_owner_id)
                atomic_json(pending, plan.model_dump())
            return {"backup_id": backup_id, "restart_required": True, "preserved_path": plan.preserved_path}
        return await self.runtime.write_channel.execute(stage, diagnostic=True)


def apply_pending_restore(root):
    root = Path(root)
    pending = root / "pending-restore.json"
    if not pending.exists():
        return None
    if pending.is_symlink():
        raise GovernanceError("OUT_OF_SCOPE", "恢复计划不能是符号链接", 403)
    plan = RestorePlan.model_validate_json(pending.read_text())
    manifest, payload = validate_backup(root, plan.backup_id)
    if plan.preserved_path != "backups/governance/preserved-" + plan.input_hash:
        raise GovernanceError("OUT_OF_SCOPE", "恢复保存路径无效", 403)
    names = DATABASE_ENTRIES if manifest.kind == "database" else (*DATABASE_ENTRIES, *DATA_ENTRIES)
    if any(name not in names for name in plan.original_entries):
        raise GovernanceError("OUT_OF_SCOPE", "恢复计划包含未管理路径", 403)
    install = root / "backups" / "governance" / ("install-" + plan.input_hash)
    if install.is_symlink() or any((install / source.name).is_symlink() for source in payload.iterdir()):
        raise GovernanceError("OUT_OF_SCOPE", "恢复暂存目录或文件不能是符号链接", 403)
    if install.resolve() != (root.resolve() / "backups" / "governance" / install.name):
        raise GovernanceError("OUT_OF_SCOPE", "恢复暂存路径超出服务管理目录", 403)
    preserved = root / plan.preserved_path
    if preserved.is_symlink() or pending.is_symlink():
        raise GovernanceError("OUT_OF_SCOPE", "恢复计划与保存目录不能是符号链接", 403)
    preserved.mkdir(parents=True, exist_ok=True)
    for name in plan.original_entries:
        destination = preserved / name
        if not destination.exists() and not destination.is_symlink():
            if name in {"agentcrew.db-wal", "agentcrew.db-shm"} and not (root / name).exists():
                continue
            os.replace(root / name, destination)
    for source in payload.iterdir():
        destination = root / source.name
        if source.name not in names:
            raise GovernanceError("OUT_OF_SCOPE", "备份包含未管理的入口", 403)
        if destination.exists():
            expected = entry_digest(source)
            actual = entry_digest(destination)
            if expected != actual:
                raise GovernanceError("REPLAY_CORRUPT", "恢复重启期间目标内容与验证备份不一致")
            continue
        stage = install / source.name
        stage.parent.mkdir(parents=True, exist_ok=True)
        if stage.exists() and entry_digest(stage) != entry_digest(source):
            incomplete = stage.parent / ("incomplete-" + source.name + "-" + hashlib.sha256(now().encode()).hexdigest()[:12])
            os.replace(stage, incomplete)
        if not stage.exists():
            if source.is_dir():
                shutil.copytree(source, stage)
            else:
                shutil.copy2(source, stage)
        os.replace(stage, destination)
    actual = sqlite3.connect(f"file:{root / 'agentcrew.db'}?mode=ro", uri=True)
    check = verify_with_anchor(actual, root / "chain-head.txt")
    actual.close()
    if not check.ok or manifest.kind == "directory" and data_files(root) != manifest.files:
        raise GovernanceError("REPLAY_CORRUPT", "恢复后的数据库或文件验证失败")
    atomic_json(preserved / "restore-result.json", {**plan.model_dump(), "restored_at": now(), "verified": True})
    pending.unlink()
    return {"backup_id": plan.backup_id, "preserved_path": plan.preserved_path}
