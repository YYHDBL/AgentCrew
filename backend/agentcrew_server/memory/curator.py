"""启动与人工确定性治理：串行变更、完整归档和成功水位。"""

import asyncio
import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

from anyio import CancelScope

from agentcrew_core.events import RunEventType as T
from agentcrew_core.memory import failure, sha256
from agentcrew_core.memory.curation import curation_plan

from ..db.audit import append_audit
from ..secrets import redact
from .jobs import now
from .store import MemoryIdentity, canonical


class MemoryCurator:
    def __init__(self, jobs):
        self.jobs, self.store, self.db = jobs, jobs.store, jobs.db

    def due(self, workspace, agent):
        row = self.db.read_conn.execute("SELECT last_curate_at FROM memory_governance WHERE workspace_id=? AND agent_id=?", (workspace, agent)).fetchone()
        if row is None:
            return True
        previous, current = datetime.fromisoformat(row[0]), datetime.now(timezone.utc)
        if previous.tzinfo is None or previous > current:
            raise ValueError("成功治理时间缺少时区或位于未来")
        return current - previous >= timedelta(days=7)

    async def enqueue(self, workspace, agent, client_request_id, *, startup=False, identity=None):
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) for value in (workspace, agent)) or \
                not isinstance(client_request_id, str) or not 1 <= len(client_request_id) <= 128:
            return failure("VALIDATION_ERROR", "治理范围与请求标识无效")
        key = f"startup:{client_request_id}:{workspace}:{agent}" if startup else f"manual:{client_request_id}"
        async with self.jobs._gate:
            existing = self.db.read_conn.execute("SELECT id,workspace_id,agent_id FROM memory_jobs WHERE kind='curate' AND trigger_key=?", (key,)).fetchone()
            if existing:
                return existing[0] if tuple(existing[1:]) == (workspace, agent) else failure("IDEMPOTENCY_CONFLICT", "请求标识已用于其他治理范围")
            auxiliary = self.db.read_conn.execute("SELECT 1 FROM memory_jobs WHERE status IN ('queued','running','waiting_approval') "
                + ("AND kind<>'curate' " if startup else "") + "LIMIT 1").fetchone()
            if not self.jobs._idle() or auxiliary:
                return failure("SYSTEM_BUSY", "系统存在前台任务或辅助作业")
            version = self.jobs.settings.get_view()["config_version"]
            def tx(conn):
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    source = conn.execute("SELECT MAX(global_seq) FROM run_events").fetchone()[0]
                    job_id = uuid.uuid4().hex
                    conn.execute("INSERT INTO memory_jobs(id,kind,trigger_key,trigger_global_seq,conversation_id,task_run_id,"
                        "workspace_id,agent_id,model,config_version,config_snapshot,status,created_at) "
                        "VALUES(?,'curate',?,?,NULL,NULL,?,?,NULL,?,?,'queued',?)",
                        (job_id, key, source, workspace, agent, version, canonical({"mode": "deterministic", "time_basis": "UTC"}), now()))
                    events, audit = self.jobs._record_tx(conn, job_id, identity)
                self.jobs._publish(conn, events, audit)
                return job_id
            job_id = await self.jobs.events.channel.execute(tx)
            self.jobs._ready.set()
            return job_id

    async def startup(self):
        if not await asyncio.to_thread(self.jobs._idle):
            return []
        if hasattr(self.jobs, "identities"):
            scopes = self.db.read_conn.execute("""SELECT a.workspace_id,a.id FROM agents a JOIN workspaces w ON w.id=a.workspace_id
                WHERE a.status='active' AND w.status='active' AND (
                    EXISTS(SELECT 1 FROM conversations c WHERE c.agent_id=a.id) OR
                    EXISTS(SELECT 1 FROM memory_skills s WHERE s.agent_id=a.id) OR
                    EXISTS(SELECT 1 FROM memory_stores s WHERE s.store_type='soul' AND s.store_id=a.id) OR
                    EXISTS(SELECT 1 FROM memory_stores s WHERE s.store_type='workspace' AND s.store_id=a.workspace_id))
                ORDER BY a.workspace_id,a.id""").fetchall()
        else:
            scopes = self.db.read_conn.execute("SELECT workspace_id,agent_id FROM conversations UNION "
                "SELECT workspace_id,agent_id FROM memory_skills UNION SELECT store_id,'default' FROM memory_stores WHERE store_type='workspace' "
                "UNION SELECT 'default',store_id FROM memory_stores WHERE store_type='soul'").fetchall()
        if not scopes and self.db.read_conn.execute("SELECT 1 FROM memory_stores WHERE store_type='user'").fetchone():
            scopes = [("default", "default")]
        created, boot = [], uuid.uuid4().hex
        for workspace, agent in scopes:
            if self.due(workspace, agent):
                job_id = await self.enqueue(workspace, agent, boot, startup=True)
                if isinstance(job_id, dict):
                    if job_id["error"] != "SYSTEM_BUSY":
                        raise ValueError(f"启动治理受理失败：{job_id['error']}")
                    break
                created.append(job_id)
        return created

    def references(self, skill_id, workspace, agent):
        return [dict(row) for row in self.db.read_conn.execute("SELECT r.* FROM skill_references r JOIN memory_skills s ON s.id=r.skill_id "
            "WHERE r.skill_id=? AND s.workspace_id=? AND s.agent_id=? AND r.active=1 AND length(r.resource_type)>0 AND length(r.resource_id)>0",
            (skill_id, workspace, agent))]

    async def run(self, job_id):
        job = self.jobs.get(job_id)
        if job is None or job["kind"] != "curate":
            raise ValueError("治理执行必须绑定真实治理作业")
        report = {"checked": 0, "stale": [], "archived": [], "exempt": [], "changes": []}
        try:
            if hasattr(self.jobs, "identities"):
                self.jobs.check_identity(job_id)
            await self.jobs._status(job_id, "running")
            job = self.jobs.get(job_id)
            identity = MemoryIdentity(job["workspace_id"], job["agent_id"], "curator", job_id, job_id=job_id)
            targets = [("user", "owner"), ("workspace", job["workspace_id"]), ("soul", job["agent_id"])]
            targets += [("skill", row[0]) for row in self.db.read_conn.execute("SELECT id FROM memory_skills WHERE workspace_id=? AND agent_id=? ORDER BY id",
                (job["workspace_id"], job["agent_id"]))]
            stamp = datetime.now(timezone.utc)
            for kind, store_id in targets:
                if not await asyncio.to_thread(self.jobs._idle):
                    await self.jobs._status(job_id, "cancelled", "前台任务到达，治理停止发起变更", report=report)
                    return
                value = await self.store.read(identity, kind, store_id)
                if "error" in value:
                    raise ValueError(f"治理读取失败：{value['error']}")
                entries = value["entries"]
                refs = self.references(store_id, job["workspace_id"], job["agent_id"]) if kind == "skill" else []
                actions, exempt = curation_plan(entries, now=stamp, quota=self.store.quota(kind), referenced=bool(refs))
                report["checked"] += len(entries)
                report["exempt"].extend({"store_type": kind, "store_id": store_id, **row, "references": refs} for row in exempt)
                if not actions:
                    continue
                result = await self.store.change(identity, kind, store_id,
                    change_id="curate-" + sha256(f"{job_id}:{kind}:{store_id}".encode()), expected_revision=value["revision"],
                    basis="依据真实 UTC 时间、使用统计、固定状态及有效引用执行确定性治理",
                    operations=[{"action": row["action"], "entry_hash": row["entry_hash"]} for row in actions])
                if "error" in result:
                    raise ValueError(f"治理写入失败：{result['error']}")
                report["changes"].append(result)
                for action in actions:
                    item = {"store_type": kind, "store_id": store_id, **action}
                    if action["action"] == "archive":
                        item["archive_path"] = f"agents/{job['agent_id']}/archive/{kind}/{store_id}/{action['entry_id']}"
                    report["archived" if action["action"] == "archive" else "stale"].append(item)
            with CancelScope(shield=True):
                await self.complete(job_id, report)
        finally:
            error = sys.exception()
            if error is not None:
                cancelling = isinstance(error, asyncio.CancelledError)
                status = "interrupted" if cancelling and self.jobs._closed else "cancelled" if cancelling else "failed"
                with CancelScope(shield=True):
                    await self.jobs._status(job_id, status, self.jobs._reason if cancelling else redact(f"{type(error).__name__}: {error}"), report=report)

    async def complete(self, job_id, report):
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                job = self.jobs._job(conn, job_id)
                if job["status"] == "completed":
                    return
                if job["status"] != "running":
                    raise ValueError("只有完整运行的治理能够提交成功水位")
                previous = conn.execute("SELECT COALESCE(MAX(seq),0) FROM audit_log").fetchone()[0]
                completed_at = now()
                conn.execute("UPDATE memory_jobs SET status='completed',report=?,finished_at=? WHERE id=?", (canonical(report), completed_at, job_id))
                conn.execute("INSERT INTO memory_governance VALUES(?,?,?,?,?) ON CONFLICT(workspace_id,agent_id) "
                    "DO UPDATE SET last_curate_at=excluded.last_curate_at,job_id=excluded.job_id,report=excluded.report",
                    (job["workspace_id"], job["agent_id"], completed_at, job_id, canonical(report)))
                event = self.jobs.events.append_in_tx(conn, task_run_id=None, conversation_id=None, type=T.MEMORY_CURATED,
                    payload={"job_id": job_id, "report": report, "last_curate_at": completed_at, "scope": self.jobs._scope(job)})
                append_audit(conn, ts=event.ts, actor_type="curator", actor_id=job_id, action=event.type.value,
                    resource_type="memory_job", resource_id=job_id, detail=canonical(event.payload))
                events, audit = self.jobs._record_tx(conn, job_id)
            self.jobs._publish(conn, [event, *events], audit, previous_audit_seq=previous)
        await self.jobs.events.channel.execute(tx)
