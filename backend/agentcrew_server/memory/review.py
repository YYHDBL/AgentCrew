"""持久化提炼触发、后台白名单、独立审批与真实辅助循环。"""

import asyncio
import json
import sys
import uuid
from pathlib import Path

from anyio import CancelScope

from agentcrew_core.events import RunEventType as T
from agentcrew_core.loop import LoopDeps, LoopGates, run_task, user_text_message
from agentcrew_core.memory import failure, sha256
from agentcrew_core.memory.budget import ContextBudgetError
from agentcrew_core.memory.review import REVIEW_SYSTEM, REVIEW_TOOLS, safe_review_messages
from agentcrew_core.recovery import replay_messages
from agentcrew_core.tools.builtin import build_default_registry
from agentcrew_core.tools.metadata import ToolInvocation
from agentcrew_core.tools.scheduler import ToolRegistry, ToolScheduler, input_hash

from ..db.audit import append_audit
from ..providers import ConfiguredProvider, bind_slot
from ..secrets import redact
from ..sessions import SessionService
from .jobs import now
from .search import MemorySearch
from .skills import MemorySkills
from .store import MemoryIdentity, canonical
from .tokenizer import load_counter


class MemoryReview:
    def __init__(self, jobs):
        self.jobs, self.store, self.db = jobs, jobs.store, jobs.db
        self.sessions = SessionService(self.db, jobs.events, self.store.data_dir, jobs.settings)
        self._pending = {}
        self._tool_locks = {}

    def _configuration(self):
        entry = dict(self.jobs.settings.config.values["models"]["aux"])
        safe = {key: value for key, value in entry.items() if key != "api_key"}
        safe["api_key_sha256"] = sha256(entry.get("api_key", "").encode())
        safe["write_approval"] = self.jobs.settings.config.values["memory"]["write_approval"]
        return entry, safe, self.jobs.settings.get_view()["config_version"]

    def _enqueue_tx(self, conn, kind, trigger_key, source, task_id, config):
        existing = conn.execute("SELECT id FROM memory_jobs WHERE kind=? AND trigger_key=?", (kind, trigger_key)).fetchone()
        if existing:
            return existing[0], []
        row = conn.execute("SELECT t.conversation_id,c.workspace_id,c.agent_id,t.status FROM task_runs t "
            "JOIN conversations c ON c.id=t.conversation_id WHERE t.id=?", (task_id,)).fetchone()
        if row is None or row[3] != "completed":
            raise ValueError("提炼只允许引用已经交付的任务")
        entry, safe, version = config
        job_id = uuid.uuid4().hex
        conn.execute("INSERT INTO memory_jobs(id,kind,trigger_key,trigger_global_seq,conversation_id,task_run_id,"
            "workspace_id,agent_id,model,config_version,config_snapshot,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued',?)", (job_id, kind, trigger_key, source, row[0],
            task_id, row[1], row[2], entry.get("model"), version, canonical(safe), now()))
        events, audit = self.jobs._record_tx(conn, job_id)
        return job_id, [(events, audit)]

    async def enqueue(self, kind, trigger_key, source, task_id):
        if kind not in {"memory_review", "skill_review"}:
            raise ValueError("后台提炼类型无效")
        config = self._configuration()
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                job_id, publications = self._enqueue_tx(conn, kind, trigger_key, source, task_id, config)
            for events, audit in publications:
                self.jobs._publish(conn, events, audit)
            return job_id
        job_id = await self.jobs.events.channel.execute(tx)
        if self.jobs.get(job_id)["status"] == "queued":
            self.jobs._slots.setdefault(job_id, config[0])
            self.jobs._ready.set()
        return job_id

    async def consume(self, global_seq):
        config = self._configuration()
        def tx(conn):
            row = conn.execute("SELECT type,payload,conversation_id,task_run_id FROM run_events WHERE global_seq=? "
                "AND global_seq>(SELECT start_global_seq FROM memory_review_state WHERE id=1)", (global_seq,)).fetchone()
            if row is None or row[2] is None:
                return []
            kind, payload, conversation, task_id = row[0], json.loads(row[1]), row[2], row[3]
            counted = "user" if kind == "queue.item_enqueued" or kind == "run.queued" and not payload.get("queue_item_id") else \
                "tool" if kind == "llm.request_done" and payload.get("tool_uses") else None
            if counted is None and kind != "run.completed":
                return []
            if kind == "run.completed" and conn.execute(
                    "SELECT 1 FROM task_runs WHERE id=? AND status='completed'", (task_id,)).fetchone() is None:
                return []
            publications, created = [], []
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO memory_trigger_state(conversation_id) VALUES(?) ON CONFLICT DO NOTHING", (conversation,))
                if counted:
                    inserted = conn.execute("INSERT INTO memory_counted_events VALUES(?,?,?) ON CONFLICT DO NOTHING",
                                            (global_seq, conversation, counted)).rowcount
                    if inserted:
                        column, source = ("user_turns", "user_event_global_seq") if counted == "user" else ("tool_iterations", "tool_event_global_seq")
                        conn.execute(f"UPDATE memory_trigger_state SET {column}={column}+1,{source}=? WHERE conversation_id=?", (global_seq, conversation))
                if kind == "run.completed":
                    state = conn.execute("SELECT user_turns,tool_iterations,memory_watermark,skill_watermark,"
                                         "user_event_global_seq,tool_event_global_seq FROM memory_trigger_state WHERE conversation_id=?", (conversation,)).fetchone()
                    for review_kind, count, watermark, source, column, threshold in (
                        ("memory_review", state[0], state[2], state[4], "memory_watermark", 10),
                        ("skill_review", state[1], state[3], state[5], "skill_watermark", 15)):
                        while watermark + threshold <= count:
                            watermark += threshold
                            trigger_source = conn.execute("SELECT global_seq FROM memory_counted_events "
                                "WHERE conversation_id=? AND kind=? ORDER BY global_seq LIMIT 1 OFFSET ?",
                                (conversation, "user" if review_kind == "memory_review" else "tool", watermark - 1)).fetchone()[0]
                            job_id, records = self._enqueue_tx(conn, review_kind, f"{conversation}:{watermark}", trigger_source, task_id, config)
                            created.append(job_id)
                            publications.extend(records)
                        conn.execute(f"UPDATE memory_trigger_state SET {column}=? WHERE conversation_id=?", (watermark, conversation))
            for events, audit in publications:
                self.jobs._publish(conn, events, audit)
            return created
        created = await self.jobs.events.channel.execute(tx)
        for job_id in created:
            if self.jobs.get(job_id)["status"] == "queued":
                self.jobs._slots.setdefault(job_id, config[0])
        if created:
            self.jobs._ready.set()

    @staticmethod
    def registry():
        default, registry = build_default_registry(), ToolRegistry()
        for name in REVIEW_TOOLS:
            registry.register(default.get(name))
        return registry

    def context(self, job):
        ctx = self.sessions.build_work_context(job["conversation_id"], task_run_id=job["task_run_id"])
        ctx.job_id = job["id"]
        ctx.artifacts_dir = self.store.data_dir / "artifacts" / "memory-jobs" / job["id"]
        ctx.memory_writer = self.store.run_tool
        ctx.memory_skills = MemorySkills(self.store).run_tool
        ctx.memory_search = MemorySearch(self.store).run_tool
        return ctx

    def approvals(self, job_id):
        rows = self.db.read_conn.execute("SELECT * FROM memory_job_approvals WHERE job_id=? ORDER BY requested_at,id", (job_id,))
        return [{**dict(row), "input": json.loads(row["input"])} for row in rows]

    def scheduler(self, job):
        async def gate(invocation, metadata, readonly):
            if self.jobs.get(job["id"])["status"] != "running":
                raise ValueError("JOB_NOT_RUNNING：作业已经停止")
            if hasattr(self.jobs, "rules") and self.jobs.rules.gate(job["agent_id"], invocation, self.context(job)).action == "deny":
                return "deny"
            if invocation.name not in {"memory_write", "skill_patch"} or invocation.input.get("action") == "read" or \
                    not json.loads(job["config_snapshot"])["write_approval"]:
                return "allow"
            approval_id = await self.request_approval(job["id"], invocation)
            future = asyncio.get_running_loop().create_future()
            self._pending[approval_id] = future
            try:
                row = next(row for row in self.approvals(job["id"]) if row["id"] == approval_id)
                decision = row["decision"] or await future
                return "allow" if decision == "allow_once" else "deny"
            finally:
                self._pending.pop(approval_id, None)
        return ToolScheduler(self.registry(), gate=gate)

    async def execute(self, job_id, invocation, context, scheduler):
        async with self._tool_locks.setdefault(job_id, asyncio.Lock()):
            if hasattr(self.jobs, "identities"):
                self.jobs.check_identity(job_id)
            result = await scheduler.run(invocation, context)
            if result.details.get("record_failed") or result.error == "EVENT_PERSIST_FAILED":
                raise RuntimeError("EVENT_PERSIST_FAILED：后台工具事件未持久化，作业立即停止")
            return result

    async def request_approval(self, job_id, invocation):
        if invocation.name not in {"memory_write", "skill_patch"} or invocation.input.get("action") == "read":
            raise ValueError("后台审批仅允许记忆及技能写入")
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                job = self.jobs._job(conn, job_id)
                if job is None or job["status"] != "running":
                    raise ValueError("只有运行中的后台作业能够请求审批")
                old = conn.execute("SELECT id,input_hash FROM memory_job_approvals WHERE job_id=? AND call_id=?",
                    (job_id, invocation.call_id)).fetchone()
                digest = input_hash(invocation.input)
                if old:
                    if old[1] != digest:
                        raise ValueError("APPROVAL_STALE：调用参数已经变化")
                    return old[0]
                approval_id = uuid.uuid4().hex
                conn.execute("INSERT INTO memory_job_approvals VALUES(?,?,?,?,?,?,'pending',?,NULL,NULL)",
                    (approval_id, job_id, invocation.call_id, digest, invocation.name, redact(canonical(invocation.input)), now()))
                conn.execute("UPDATE memory_jobs SET status='waiting_approval' WHERE id=?", (job_id,))
                event = self.jobs.events.append_in_tx(conn, task_run_id=None, conversation_id=job["conversation_id"],
                    type=T.MEMORY_APPROVAL_REQUESTED, payload={"job_id": job_id, "approval_id": approval_id,
                    "call_id": invocation.call_id, "tool": invocation.name, "input": json.loads(redact(canonical(invocation.input))),
                    "input_hash": digest, "scope": self.jobs._scope(job)})
                requested_audit = append_audit(conn, ts=event.ts, actor_type="system", actor_id=job_id,
                    action=event.type.value, resource_type="memory_approval", resource_id=approval_id, detail=canonical(event.payload))
                status_events, audit = self.jobs._record_tx(conn, job_id)
            self.jobs._publish(conn, [event, *status_events], audit, previous_audit_seq=requested_audit - 1)
            return approval_id
        return await self.jobs.events.channel.execute(tx)

    async def decide(self, job_id, approval_id, decision, digest, request_identity=None):
        if decision not in {"allow_once", "reject_once"}:
            return failure("VALIDATION_ERROR", "后台审批决定无效")
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                job = self.jobs._job(conn, job_id)
                if request_identity is not None:
                    self.jobs.identities.job(request_identity, job_id, conn)
                cursor = conn.execute("SELECT * FROM memory_job_approvals WHERE id=? AND job_id=?", (approval_id, job_id))
                values = cursor.fetchone()
                if values is None:
                    return failure("NOT_FOUND", "后台审批不存在")
                row = dict(zip((column[0] for column in cursor.description), values))
                if hasattr(self.jobs, "grants"):
                    self.jobs.grants.check_tool(job["task_run_id"], row["tool"], json.loads(row["input"]), conn)
                if row["input_hash"] != digest or row["status"] == "expired" or job["status"] in {"cancelled", "interrupted", "failed"}:
                    return failure("APPROVAL_STALE", "后台审批已经失效或参数不一致")
                if row["decision"]:
                    return {"decision": decision} if row["decision"] == decision else failure("APPROVAL_STALE", "审批已经按其他决定处理")
                conn.execute("UPDATE memory_job_approvals SET status=?,decision=?,resolved_at=? WHERE id=?",
                    ("allowed" if decision == "allow_once" else "rejected", decision, now(), approval_id))
                conn.execute("UPDATE memory_jobs SET status='running' WHERE id=?", (job_id,))
                event, resolved_audit = self._resolved_tx(conn, job, approval_id, decision,
                    request_identity.effective_user_id if request_identity else "owner", actor_type="user",
                    credential_owner_id=request_identity.credential_owner_id if request_identity else "owner")
                status_events, audit = self.jobs._record_tx(conn, job_id)
            self.jobs._publish(conn, [event, *status_events], audit, previous_audit_seq=resolved_audit - 1)
            return {"decision": decision}
        result = await self.jobs.events.channel.execute(tx)
        future = self._pending.get(approval_id)
        if "error" not in result and future is not None and not future.done():
            future.set_result(decision)
        return result

    async def cancel(self, job_id, *, reason="人类用户取消后台作业"):
        async with self.jobs._gate:
            job = self.jobs.get(job_id)
            if job is None:
                return failure("NOT_FOUND", "后台作业不存在")
            current = self.jobs._current
            if current is not None and current.get_name() == f"memory-job:{job_id}" and not current.done():
                self.jobs._reason = reason
                self.jobs._cancel_scope.cancel()
                _done, pending = await asyncio.wait({current}, timeout=2)
                if pending:
                    raise TimeoutError("后台作业未在两秒内完成取消")
            await self.jobs._status(job_id, "cancelled", reason)
            self.jobs._slots.pop(job_id, None)
            return self.view(job_id)

    def view(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            return failure("NOT_FOUND", "后台作业不存在")
        return {**{key: job[key] for key in ("id", "kind", "status", "conversation_id", "task_run_id",
            "trigger_global_seq", "model", "config_version", "error", "created_at", "finished_at")},
            "usage": json.loads(job["usage"]), "report": json.loads(job["report"]) if job["report"] else None,
            "trigger_global_seq": job["trigger_global_seq"] or 0, "approvals": self.approvals(job_id),
            "calls": [{**dict(row), "payload": json.loads(row["payload"])} for row in self.db.read_conn.execute(
                "SELECT ordinal,type,payload,created_at FROM memory_job_calls WHERE job_id=? ORDER BY ordinal", (job_id,))]}

    def _resolved_tx(self, conn, job, approval_id, decision, actor, *, actor_type="system", credential_owner_id=None):
        event = self.jobs.events.append_in_tx(conn, task_run_id=None, conversation_id=job["conversation_id"],
            type=T.MEMORY_APPROVAL_RESOLVED, payload={"job_id": job["id"], "approval_id": approval_id,
                "decision": decision, "actor": actor, "scope": self.jobs._scope(job),
                **({"credential_owner_id": credential_owner_id} if credential_owner_id else {})})
        audit = append_audit(conn, ts=event.ts, actor_type=actor_type, actor_id=actor,
            action=event.type.value, resource_type="memory_approval", resource_id=approval_id, detail=canonical(event.payload))
        return event, audit

    def expire_tx(self, conn, job_id):
        job, events = self.jobs._job(conn, job_id), []
        for row in conn.execute("SELECT id FROM memory_job_approvals WHERE job_id=? AND status='pending'", (job_id,)).fetchall():
            conn.execute("UPDATE memory_job_approvals SET status='expired',decision='expired',resolved_at=? WHERE id=?", (now(), row[0]))
            event, _audit = self._resolved_tx(conn, job, row[0], "expired", "system")
            events.append(event)
        return events

    def material(self, job):
        rows = self.db.read_conn.execute("SELECT e.global_seq,e.type,e.payload,e.task_run_id FROM run_events e "
            "JOIN task_runs t ON t.id=e.task_run_id WHERE e.conversation_id=? AND t.status='completed' "
            "AND e.global_seq<=? ORDER BY e.global_seq", (job["conversation_id"], self.db.read_conn.execute(
                "SELECT MAX(global_seq) FROM run_events WHERE task_run_id=? AND type='run.completed'", (job["task_run_id"],)).fetchone()[0])).fetchall()
        history = []
        for task_id in dict.fromkeys(row[3] for row in rows):
            replay = replay_messages([tuple(row[:3]) for row in rows if row[3] == task_id],
                instruction_task_id=task_id, include_event_sources=True)
            if replay.needs_manual_review:
                raise ValueError("REPLAY_CORRUPT：后台资料事件不完整")
            for message in replay.messages:
                message["_source_task_run_id"] = task_id
            history.extend(replay.messages)
            completion = next(row for row in reversed(rows) if row[3] == task_id and row[1] == "run.completed")
            final_text = json.loads(completion[2]).get("final_text", "")
            last_text = "\n".join(block["text"] for block in history[-1]["content"] if block["type"] == "text")
            if final_text and (history[-1]["role"] != "assistant" or last_text != final_text):
                history.append({"role": "assistant", "content": [{"type": "text", "text": final_text}],
                    "_source_task_run_id": task_id, "_event_global_seqs": [completion[0]]})
        recent = safe_review_messages(history[-24:])
        older_tasks = {message["_source_task_run_id"] for message in history[:-24]}
        summaries = [dict(row) for row in self.db.read_conn.execute("SELECT task_run_id,text FROM session_summaries "
            "WHERE conversation_id=? ORDER BY created_at,id", (job["conversation_id"],)) if row["task_run_id"] in older_tasks]
        missing = sorted(older_tasks - {summary["task_run_id"] for summary in summaries})
        safe_summaries = safe_review_messages([user_text_message(row["text"]) for row in summaries])
        text = canonical({"kind": job["kind"], "source_task_run_id": job["task_run_id"],
            "scope": self.jobs._scope(job), "recent_raw_messages": recent,
            "older_single_line_summaries": [{**row, "text": " ".join(message["content"][0]["text"].splitlines())}
                for row, message in zip(summaries, safe_summaries)],
            "missing_summary_task_ids": missing})
        return [user_text_message(redact(text))], {"raw_messages": len(recent), "older_summaries": len(summaries),
            "missing_summary_task_ids": missing, "input_sha256": sha256(text.encode())}

    async def run(self, job_id):
        result_text = None
        try:
            if hasattr(self.jobs, "identities"):
                self.jobs.check_identity(job_id)
            slot = bind_slot(self.jobs._slots.pop(job_id))
            await self.jobs._status(job_id, "running")
            job = self.jobs.get(job_id)
            messages, material_report = await asyncio.to_thread(self.material, job)
            index = await MemorySkills(self.store).index(MemoryIdentity(job["workspace_id"], job["agent_id"],
                "agent", job["agent_id"], job["conversation_id"], job["task_run_id"], job_id=job_id, user_turn_id=job_id))
            if "error" in index:
                raise ValueError(f"后台 Skill 索引读取失败：{index['error']}")
            messages.append(user_text_message("【当前范围的 Skill 索引；正文通过 skill_patch read 按需读取】\n" + canonical(index)))
            material_report.update(skill_index_items=len(index["items"]), skill_index_sha256=sha256(canonical(index).encode()))
            context, registry = self.context(job), self.registry()
            async def sink(event_type, payload):
                await self.jobs.events.channel.execute(lambda conn: self.jobs._call_tx(conn, job_id, event_type, payload))
            context.emit = sink
            provider = ConfiguredProvider({"aux": slot}, client=self.jobs._http, session_id=job["conversation_id"])
            provider.budget_sink = sink
            scheduler = self.scheduler(job)
            async def budget():
                if hasattr(self.jobs, "identities"):
                    self.jobs.check_identity(job_id)
                counter = await asyncio.to_thread(load_counter, slot)
                measured = await asyncio.to_thread(counter.measure, slot, messages, registry.schemas(),
                    system=REVIEW_SYSTEM, thinking={"type": "disabled"})
                if measured.total_input_tokens > slot.context_window * 75 // 100:
                    raise ContextBudgetError("CONTEXT_BUDGET_EXCEEDED：后台完整输入超过 aux 窗口 75%")
                measured.validate()
            result = await run_task(messages, LoopDeps(request=lambda: provider.stream("aux", messages, registry.schemas(),
                    thinking={"type": "disabled"}, system=REVIEW_SYSTEM),
                execute=lambda call: self.execute(job_id, ToolInvocation(call.id, call.name, call.input), context, scheduler),
                emit=sink, on_progress=lambda: None, before_request=budget,
                gates=LoopGates(max_steps=40), model=slot.model, model_slot="aux"))
            result_text = redact(result.final_text)
            if result.status != "completed":
                raise ValueError(f"后台提炼失败：{result.reason}")
            with CancelScope(shield=True):
                await self.jobs._status(job_id, "completed", report={**material_report, "result": result_text})
        finally:
            self.jobs._slots.pop(job_id, None)
            self._tool_locks.pop(job_id, None)
            error = sys.exception()
            if error is not None:
                cancelled = isinstance(error, asyncio.CancelledError)
                status = "interrupted" if cancelled and self.jobs._closed else "cancelled" if cancelled else "failed"
                with CancelScope(shield=True):
                    await self.jobs._status(job_id, status, self.jobs._reason if cancelled else redact(f"{type(error).__name__}: {error}"),
                        report={"result": result_text} if result_text else None)
