"""辅助作业生命周期：独立身份、提交后摘要、前台优先及模型流收尾。"""

import asyncio
import json
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx

from agentcrew_core.events import RunEventType
from agentcrew_core.loop import LoopDeps, LoopGates, run_task, user_text_message
from agentcrew_core.memory import SUMMARY_SYSTEM, contains_credentials, sha256

from ..bus import Topic
from ..db.audit import SNAPSHOT_EVERY, append_audit, snapshot_chain_head
from ..providers import ConfiguredProvider, bind_slot
from ..secrets import redact
from .store import canonical

_log = logging.getLogger("agentcrew.memory.jobs")
_TERMINAL = {"completed", "cancelled", "interrupted", "failed"}


def now():
    return datetime.now(timezone.utc).isoformat()


class MemoryJobs:
    def __init__(self, store, bus, settings):
        self.store, self.db, self.events = store, store.db, store.events
        self.bus, self.settings = bus, settings
        self._gate = asyncio.Lock()
        self._ready = asyncio.Event()
        self._closed = False
        self._current = None
        self._reason = "前台指令到达，辅助作业已取消"
        self._slots = {}
        self._http = httpx.AsyncClient(timeout=180)
        self._sub = self._dispatch_task = self._worker_task = None

    async def start(self):
        self._sub = self.bus.subscribe(Topic("all"), internal=True)
        self._dispatch_task = asyncio.create_task(self._dispatch(), name="memory-jobs:dispatch")
        self._worker_task = asyncio.create_task(self._worker(), name="memory-jobs:worker")

    def get(self, job_id):
        return self._job(self.db.read_conn, job_id)

    @staticmethod
    def _job(conn, job_id):
        cursor = conn.execute("SELECT * FROM memory_jobs WHERE id=?", (job_id,))
        row = cursor.fetchone()
        return dict(zip((column[0] for column in cursor.description), row)) if row else None

    def _idle(self):
        return self.db.read_conn.execute(
            "SELECT 1 FROM task_runs WHERE status IN ('queued','running','waiting_user','waiting_verification') LIMIT 1"
        ).fetchone() is None and self.db.read_conn.execute(
            "SELECT 1 FROM conversations c,json_each(c.pending_queue) q "
            "WHERE c.status='active' AND c.queue_paused=0 AND json_extract(q.value,'$.state')='queued' LIMIT 1"
        ).fetchone() is None

    async def _dispatch(self):
        self._sub.bind_consumer()
        while not self._closed:
            event = self._sub.take_nowait()
            if event is None:
                await self._sub.wait_for_data()
                continue
            if event.type == RunEventType.RUN_COMPLETED:
                await self.enqueue(event.global_seq)
            if event.type in {RunEventType.RUN_QUEUED, RunEventType.RUN_RESUMED, RunEventType.QUEUE_ITEM_ENQUEUED}:
                async with self._gate:
                    await self._cancel("cancelled", "前台任务已经接收，辅助作业已取消")
            if event.type in {RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED,
                              RunEventType.RUN_CANCELLED, RunEventType.RUN_INTERRUPTED,
                              RunEventType.QUEUE_PAUSED, RunEventType.QUEUE_ITEM_CANCELLED}:
                self._ready.set()

    async def enqueue(self, global_seq):
        async with self._gate:
            if self._closed:
                return None
            entry = dict(self.settings.config.values["models"]["aux"])
            safe = {key: value for key, value in entry.items() if key != "api_key"}
            safe["api_key_sha256"] = sha256(entry.get("api_key", "").encode())
            version = self.settings.get_view()["config_version"]
            def tx(conn):
                source = conn.execute(
                    "SELECT e.task_run_id,e.conversation_id,c.workspace_id,c.agent_id FROM run_events e "
                    "JOIN conversations c ON c.id=e.conversation_id JOIN task_runs t ON t.id=e.task_run_id "
                    "WHERE e.global_seq=? AND e.type='run.completed' AND t.status='completed'",
                    (global_seq,)).fetchone()
                if source is None:
                    raise ValueError("摘要触发必须引用真实的已完成任务事件")
                existing = conn.execute("SELECT id FROM memory_jobs WHERE kind='summary' AND trigger_key=?", (source[0],)).fetchone()
                if existing:
                    return existing[0]
                job_id = uuid.uuid4().hex
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute("INSERT INTO memory_jobs(id,kind,trigger_key,trigger_global_seq,conversation_id,task_run_id,"
                                 "workspace_id,agent_id,model,config_version,config_snapshot,status,created_at) "
                                 "VALUES(?,'summary',?,?,?,?,?,?,?,?,?,'queued',?)",
                                 (job_id, source[0], global_seq, source[1], source[0], *source[2:], entry.get("model"), version, canonical(safe), now()))
                    events, audit_seq = self._record_tx(conn, job_id)
                self._publish(conn, events, audit_seq)
                return job_id
            job_id = await self.events.channel.execute(tx)
            if (await asyncio.to_thread(self.get, job_id))["status"] == "queued":
                self._slots.setdefault(job_id, entry)
                self._ready.set()
            return job_id

    async def _worker(self):
        while not self._closed:
            await self._ready.wait()
            self._ready.clear()
            while not self._closed:
                async with self._gate:
                    if not await asyncio.to_thread(self._idle):
                        break
                    row = await asyncio.to_thread(lambda: self.db.read_conn.execute(
                        "SELECT id FROM memory_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone())
                    if row is None:
                        break
                    self._current = asyncio.create_task(self._run(row[0]), name=f"memory-job:{row[0]}")
                    current = self._current
                await asyncio.wait({current})
                if not current.cancelled() and current.exception() is not None:
                    _log.error("memory.job.failed 作业执行失败 job=%s error=%s", row[0], redact(str(current.exception())))
                async with self._gate:
                    if self._current is current:
                        self._current = None

    async def _run(self, job_id):
        result_text = None
        try:
            slot = bind_slot(self._slots.pop(job_id))
            await self._status(job_id, "running")
            job = await asyncio.to_thread(self.get, job_id)
            provider = ConfiguredProvider({"aux": slot}, client=self._http, session_id=job["conversation_id"])
            source = await asyncio.to_thread(lambda: self.db.read_conn.execute(
                "SELECT t.instruction,e.payload FROM task_runs t JOIN run_events e ON e.task_run_id=t.id "
                "WHERE e.global_seq=? AND e.type='run.completed'", (job["trigger_global_seq"],)).fetchone())
            material = {"task_run_id": job["task_run_id"], "instruction": source[0],
                        "reply": json.loads(source[1])["final_text"]}
            messages = [user_text_message(redact(canonical(material)))]
            async def sink(event_type, payload):
                await self.events.channel.execute(lambda conn: self._call_tx(conn, job_id, event_type, payload))
            provider.budget_sink = sink
            async def forbidden(call):
                raise ValueError(f"任务摘要禁止调用工具：{call.name}")
            result = await run_task(messages, LoopDeps(
                request=lambda: provider.stream("aux", messages, [], thinking={"type": "disabled"}, system=SUMMARY_SYSTEM),
                execute=forbidden, emit=sink, on_progress=lambda: None,
                gates=LoopGates(max_steps=1), model=slot.model, model_slot="aux"))
            result_text = redact(result.final_text.strip())
            if result.status != "completed" or not 1 <= len(result_text) <= 200 or contains_credentials(result_text):
                raise ValueError(f"真实 aux 摘要不符合契约：status={result.status},characters={len(result_text)},reason={result.reason}")
            commit = asyncio.create_task(self.complete(job_id, result_text))
            try:
                await asyncio.shield(commit)
            finally:
                await commit
        finally:
            self._slots.pop(job_id, None)
            error = sys.exception()
            if error is not None:
                cancelling = asyncio.current_task().cancelling()
                status = "interrupted" if cancelling and self._closed else "cancelled" if cancelling else "failed"
                await self._status(job_id, status, self._reason if cancelling else redact(f"{type(error).__name__}: {error}"),
                                   report={"result": result_text} if result_text is not None else None)

    def _call_tx(self, conn, job_id, event_type, payload):
        with conn:
            ordinal = conn.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM memory_job_calls WHERE job_id=?", (job_id,)).fetchone()[0]
            conn.execute("INSERT INTO memory_job_calls VALUES(?,?,?,?,?)", (job_id, ordinal, str(event_type), redact(canonical(payload)), now()))
            if event_type == RunEventType.LLM_REQUEST_DONE:
                conn.execute("UPDATE memory_jobs SET usage=? WHERE id=?", (canonical({"input_tokens": payload["prompt_tokens"],
                    "output_tokens": payload["completion_tokens"]}), job_id))

    async def complete(self, job_id, text):
        if not isinstance(text, str) or not 1 <= len(text) <= 200 or contains_credentials(text):
            raise ValueError("任务摘要必须包含 1 至 200 个字符")
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                job = self._job(conn, job_id)
                if job is None:
                    raise LookupError("摘要作业不存在")
                if job["status"] == "completed":
                    return conn.execute("SELECT id FROM session_summaries WHERE job_id=?", (job_id,)).fetchone()[0]
                if job["status"] != "running":
                    raise ValueError("只有正在运行的作业能够提交摘要")
                if conn.execute("SELECT status FROM task_runs WHERE id=?", (job["task_run_id"],)).fetchone()[0] != "completed":
                    raise ValueError("任务尚未完成，不能提交摘要")
                summary_id, ts = uuid.uuid4().hex, now()
                conn.execute("INSERT INTO session_summaries VALUES(?,?,?,?,?,?)", (summary_id, job["task_run_id"], job_id, job["conversation_id"], text, ts))
                conn.execute("UPDATE memory_jobs SET status='completed',finished_at=? WHERE id=?", (ts, job_id))
                created = self.events.append_in_tx(conn, task_run_id=None, conversation_id=job["conversation_id"],
                    type=RunEventType.MEMORY_SUMMARY_CREATED, payload={"job_id": job_id, "source_task_run_id": job["task_run_id"],
                        "summary_id": summary_id, "summary": text, "scope": self._scope(job)})
                events, audit_seq = self._record_tx(conn, job_id)
            self._publish(conn, [created, *events], audit_seq)
            return summary_id
        return await self.events.channel.execute(tx)

    @staticmethod
    def _scope(job):
        return {"owner_id": "owner", "workspace_id": job["workspace_id"], "agent_id": job["agent_id"]}

    def _record_tx(self, conn, job_id):
        job = self._job(conn, job_id)
        payload = {"job_id": job_id, "kind": job["kind"], "status": job["status"], "scope": self._scope(job),
                   "trigger_global_seq": job["trigger_global_seq"], "source_task_run_id": job["task_run_id"],
                   "model": job["model"], "config_version": job["config_version"], "usage": json.loads(job["usage"]), "error": job["error"]}
        event = self.events.append_in_tx(conn, task_run_id=None, conversation_id=job["conversation_id"],
                                        type=RunEventType.MEMORY_JOB_STATUS, payload=payload)
        audit_seq = append_audit(conn, ts=event.ts, actor_type="system", actor_id=job_id, action=event.type.value,
                                resource_type="memory_job", resource_id=job_id, detail=canonical(payload))
        return [event], audit_seq

    def _publish(self, conn, events, audit_seq):
        if audit_seq % SNAPSHOT_EVERY == 0:
            snapshot_chain_head(conn, self.store.data_dir / "chain-head.txt")
        for event in events:
            self.events.publish(event)

    async def _status(self, job_id, status, error=None, report=None):
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                old = conn.execute("SELECT status FROM memory_jobs WHERE id=?", (job_id,)).fetchone()
                if old is None:
                    raise LookupError("辅助作业不存在")
                if old[0] in _TERMINAL:
                    return
                conn.execute("UPDATE memory_jobs SET status=?,error=?,report=?,finished_at=? WHERE id=?",
                    (status, error, canonical(report) if report is not None else None, now() if status in _TERMINAL else None, job_id))
                events, audit_seq = self._record_tx(conn, job_id)
            self._publish(conn, events, audit_seq)
        await self.events.channel.execute(tx)

    async def _cancel(self, status, reason):
        self._reason = reason
        if self._current is not None and not self._current.done():
            self._current.cancel()
            done, pending = await asyncio.wait({self._current}, timeout=2)
            if pending:
                raise TimeoutError("辅助模型流未在两秒内完成取消")
            for task in done:
                if not task.cancelled() and task.exception() is not None:
                    _log.error("memory.job.cancel 作业收尾失败：%s", redact(str(task.exception())))
        queued = await asyncio.to_thread(lambda: self.db.read_conn.execute("SELECT id FROM memory_jobs WHERE status='queued'").fetchall())
        for row in queued:
            await self._status(row[0], status, reason)
            self._slots.pop(row[0], None)

    @asynccontextmanager
    async def foreground(self):
        async with self._gate:
            await self._cancel("cancelled", "前台指令到达，辅助作业已取消")
            yield

    async def recover(self):
        unfinished = self.db.read_conn.execute("SELECT id FROM memory_jobs WHERE status IN ('queued','running','waiting_approval') ORDER BY created_at,id").fetchall()
        for row in unfinished:
            await self._status(row[0], "interrupted", "进程终止期间辅助作业未完成")
        missing = self.db.read_conn.execute(
            "SELECT global_seq FROM run_events e WHERE type='run.completed' "
            "AND global_seq>(SELECT start_global_seq FROM memory_job_state WHERE id=1) "
            "AND NOT EXISTS(SELECT 1 FROM memory_jobs j WHERE j.kind='summary' AND j.trigger_key=e.task_run_id) ORDER BY global_seq").fetchall()
        for row in missing:
            job_id = await self.enqueue(row[0])
            await self._status(job_id, "interrupted", "任务完成已经提交，进程终止时摘要尚未开始")
            self._slots.pop(job_id, None)

    async def shutdown(self):
        self._closed = True
        async with self._gate:
            await self._cancel("interrupted", "后端关闭，辅助作业执行终止")
        for task in (self._dispatch_task, self._worker_task):
            if task is not None:
                task.cancel()
        pending = {task for task in (self._dispatch_task, self._worker_task) if task is not None}
        if pending:
            await asyncio.wait(pending)
        if self._sub is not None:
            self.bus.unsubscribe(self._sub)
        await self._http.aclose()
