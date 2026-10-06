"""复用辅助队列、预算及取消握手的只读轨迹审查。"""

import asyncio
import json
import sys
import uuid

from anyio import CancelScope

from agentcrew_core.events import RunEventType as T
from agentcrew_core.events.run_projection import project_run_state
from agentcrew_core.governance import RequestIdentity
from agentcrew_core.loop import LoopDeps, LoopGates, run_task, user_text_message
from agentcrew_core.memory.budget import ContextBudgetError
from agentcrew_core.reviews import ReviewResult, TRACE_REVIEW_SYSTEM, validate_reference
from agentcrew_core.tools import build_default_registry, ToolRegistry, ToolInvocation, ToolScheduler
from ..governance.resources import GovernanceError, canonical, now
from ..db.projections import _row_to_event
from ..memory.search import MemorySearch
from ..memory.tokenizer import load_counter
from ..providers import ConfiguredProvider, bind_slot
from ..runs import display_value
from ..secrets import redact
from .trace_read import TraceReader


class TraceAudit:
    def __init__(self, runtime):
        self.runtime, self.jobs, self.db = runtime, runtime.memory_jobs, runtime.db
        self.events, self.reader = runtime.event_store, TraceReader(runtime)

    @staticmethod
    def registry():
        default, registry = build_default_registry(), ToolRegistry()
        for name in ("trace_read", "session_search"):
            registry.register(default.get(name))
        return registry

    @staticmethod
    def target(conn, task_id, watermark=None, attempt_no=None):
        row = conn.execute("SELECT t.*,c.workspace_id,c.agent_id,g.effective_user_id,g.credential_owner_id FROM task_runs t JOIN conversations c ON c.id=t.conversation_id JOIN task_governance g ON g.task_run_id=t.id WHERE t.id=?", (task_id,)).fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "审查目标任务不存在", 404)
        head = watermark if watermark is not None else conn.execute("SELECT max(global_seq) FROM run_events WHERE task_run_id=?", (task_id,)).fetchone()[0]
        if watermark is not None and attempt_no is None:
            history = [_row_to_event(event) for event in conn.execute("SELECT * FROM run_events WHERE task_run_id=? AND global_seq<=? ORDER BY seq", (task_id, head))]
            attempt_no = project_run_state(history)["current_attempt_no"] or None
        return {"task_run_id": task_id, "attempt_no": attempt_no if watermark is not None else row["current_attempt_no"] or None,
            "source_global_seq": head, "workspace_id": row["workspace_id"], "agent_id": row["agent_id"], "conversation_id": row["conversation_id"],
            "effective_user_id": row["effective_user_id"], "credential_owner_id": row["credential_owner_id"]}

    def enqueue_in_tx(self, conn, targets, trigger_key, trigger_seq, config, priority):
        prior = conn.execute("SELECT id FROM memory_jobs WHERE kind='trace_audit' AND trigger_key=?", (trigger_key,)).fetchone()
        if prior:
            return prior[0], []
        anchor = targets[-1]
        actor = RequestIdentity(anchor["credential_owner_id"], anchor["effective_user_id"], anchor["credential_owner_id"] != anchor["effective_user_id"])
        readable = all(self.runtime.identities.visible_conversation(actor, target["conversation_id"]) for target in targets)
        entry, safe, version = config
        job_id = uuid.uuid4().hex
        conn.execute("INSERT INTO memory_jobs(id,kind,trigger_key,trigger_global_seq,conversation_id,task_run_id,workspace_id,agent_id,model,config_version,config_snapshot,status,error,created_at,finished_at,priority) VALUES(?,'trace_audit',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, trigger_key, trigger_seq, anchor["conversation_id"], anchor["task_run_id"], anchor["workspace_id"], anchor["agent_id"],
             entry["model"], version, canonical(safe), "queued" if readable else "skipped",
             None if readable else "AUTHORIZATION_REVOKED：目标失去当前读取授权", now(), None if readable else now(), priority))
        for target in targets:
            conn.execute("INSERT INTO trace_targets(job_id,task_run_id,attempt_no,source_global_seq) VALUES(?,?,?,?)",
                (job_id, target["task_run_id"], target["attempt_no"], target["source_global_seq"]))
        conn.execute("INSERT INTO job_governance VALUES(?,?,?)", (job_id, actor.effective_user_id, actor.credential_owner_id))
        events, audit = self.jobs._record_tx(conn, job_id)
        return job_id, [(events, audit)]

    async def consume(self, global_seq):
        config = self.jobs.review._configuration()
        def tx(conn):
            event = conn.execute("SELECT * FROM run_events WHERE global_seq=? AND type IN ('run.completed','run.failed','tool.verification_submitted') AND global_seq>(SELECT start_global_seq FROM trace_review_state WHERE id=1)", (global_seq,)).fetchone()
            if event is None or event["task_run_id"] is None:
                return []
            failed = event["type"] == "run.failed"
            status = conn.execute("SELECT status FROM task_runs WHERE id=?", (event["task_run_id"],)).fetchone()[0]
            if not failed and status != "completed":
                return []
            if event["type"] == "tool.verification_submitted" and conn.execute("SELECT 1 FROM run_events WHERE task_run_id=? AND type='run.completed'", (event["task_run_id"],)).fetchone() is None:
                return []
            if not failed and conn.execute("SELECT 1 FROM trace_counted_events WHERE task_run_id=? AND kind='completed'", (event["task_run_id"],)).fetchone():
                return []
            target = self.target(conn, event["task_run_id"], event["global_seq"], event["attempt_no"])
            bucket = canonical({key: target[key] for key in ("workspace_id", "agent_id", "effective_user_id", "credential_owner_id")})
            created, publications = [], []
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                inserted = conn.execute("INSERT INTO trace_counted_events VALUES(?,?,?,?) ON CONFLICT DO NOTHING", (global_seq, target["task_run_id"], bucket, "failed" if failed else "completed")).rowcount
                if not inserted:
                    return []
                if failed:
                    job_id, records = self.enqueue_in_tx(conn, [target], "failure:" + str(global_seq), global_seq, config, 100)
                    created.append(job_id)
                    publications.extend(records)
                else:
                    conn.execute("INSERT INTO trace_trigger_state(bucket) VALUES(?) ON CONFLICT DO NOTHING", (bucket,))
                    conn.execute("UPDATE trace_trigger_state SET completed_count=completed_count+1 WHERE bucket=?", (bucket,))
                    count, watermark = conn.execute("SELECT completed_count,completed_watermark FROM trace_trigger_state WHERE bucket=?", (bucket,)).fetchone()
                    if count >= watermark + 5:
                        rows = conn.execute("SELECT e.* FROM trace_counted_events c JOIN run_events e ON e.global_seq=c.global_seq WHERE c.bucket=? AND c.kind='completed' ORDER BY c.global_seq LIMIT 5 OFFSET ?", (bucket, watermark)).fetchall()
                        targets = [self.target(conn, row["task_run_id"], row["global_seq"], row["attempt_no"]) for row in rows]
                        job_id, records = self.enqueue_in_tx(conn, targets, "batch:" + bucket + ":" + str(watermark + 5), global_seq, config, 10)
                        created.append(job_id)
                        publications.extend(records)
                        conn.execute("UPDATE trace_trigger_state SET completed_watermark=completed_watermark+5 WHERE bucket=?", (bucket,))
            for events, audit in publications:
                self.jobs._publish(conn, events, audit)
            return created
        created = await self.events.channel.execute(tx)
        for job_id in created:
            if self.jobs.get(job_id)["status"] == "queued":
                self.jobs._slots.setdefault(job_id, config[0])
        if created:
            self.jobs._ready.set()

    async def enqueue(self, actor, task_id, client_request_id):
        conn = self.db.read_conn
        source = self.target(conn, task_id)
        self.runtime.identities.conversation(actor, source["conversation_id"])
        if (actor.effective_user_id, actor.credential_owner_id) != (source["effective_user_id"], source["credential_owner_id"]):
            raise GovernanceError("OUT_OF_SCOPE", "审查只能引用当前请求者的任务", 403)
        config = self.jobs.review._configuration()
        def tx(connection):
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                self.runtime.identities.conversation(actor, source["conversation_id"], connection)
                job_id, publications = self.enqueue_in_tx(connection, [source], "manual:" + actor.effective_user_id + ":" + task_id + ":" + client_request_id,
                    source["source_global_seq"], config, 90)
            for events, audit in publications:
                self.jobs._publish(connection, events, audit)
            return job_id
        job_id = await self.events.channel.execute(tx)
        if self.jobs.get(job_id)["status"] == "queued":
            self.jobs._slots.setdefault(job_id, config[0])
            self.jobs._ready.set()
        return self.view(actor, job_id)

    def context(self, job):
        context = self.runtime.sessions.build_work_context(job["conversation_id"], job["task_run_id"])
        context.job_id = job["id"]
        context.artifacts_dir = self.runtime.data_dir / "artifacts" / "reviews" / job["id"]
        context.trace_reader = self.reader.run_tool
        context.memory_search = MemorySearch(self.runtime.memory).run_tool
        context.search_owner_only = True
        return context

    def material(self, job, task_id):
        targets = [dict(row) for row in self.db.read_conn.execute("SELECT * FROM trace_targets WHERE job_id=? ORDER BY source_global_seq", (job["id"],))]
        materials = []
        context = self.context(job)
        for target in targets:
            if target["task_run_id"] != task_id:
                continue
            tail = self.db.read_conn.execute("SELECT seq FROM run_events WHERE task_run_id=? AND global_seq<=? ORDER BY seq DESC LIMIT 40", (target["task_run_id"], target["source_global_seq"])).fetchall()
            after = tail[-1][0] - 1 if tail else 0
            data = self.reader.read(ToolInvocation("material", "trace_read", {"task_run_id": target["task_run_id"], "limit": 40, "after_seq": after}), context)
            items = [{**event, "payload": {key: value for key, value in event["payload"].items() if key != "tool_declarations"}} for event in data["items"]]
            materials.append({"task_run_id": target["task_run_id"], "attempt_no": target["attempt_no"], "source_global_seq": target["source_global_seq"],
                "task": data["task"], "summaries": data["summaries"], "events": items, "earlier_events_before_seq": after,
                "missing_earlier_summary": after > 0 and not data["summaries"], "has_more": data["has_more"], "next_after_seq": data["next_after_seq"]})
        return [user_text_message(canonical({"targets": materials, "review_instruction": "依据实际事件分析失败和流程。如存在可复用机理，请提出有依据的技能建议；不能确认的部分使用null。"}))]

    async def run(self, job_id):
        result_text = None
        try:
            self.jobs.check_identity(job_id)
            slot = bind_slot(self.jobs._slots.pop(job_id))
            await self.jobs._status(job_id, "running")
            job = self.jobs.get(job_id)
            registry, context = self.registry(), self.context(job)
            system = TRACE_REVIEW_SYSTEM + "\n严格遵循此JSON schema，不增加字段；anomalies和improvement_suggestions必须是字符串数组：\n" + canonical(ReviewResult.model_json_schema())
            async def sink(kind, payload):
                await self.events.channel.execute(lambda conn: self.jobs._call_tx(conn, job_id, kind, payload))
            context.emit = sink
            async def gate(invocation, metadata, readonly):
                if invocation.name not in {"trace_read", "session_search"} or not readonly:
                    raise ValueError("审查工具白名单拒绝调用")
                self.jobs.check_identity(job_id)
                if self.jobs.get(job_id)["status"] != "running":
                    raise ValueError("审查作业已经停止")
                return "allow"
            scheduler = ToolScheduler(registry, gate=gate)
            async def execute(call):
                if call.name not in registry.names():
                    raise ValueError("审查禁止调用：" + call.name)
                return await scheduler.run(ToolInvocation(call.id, call.name, call.input), context)
            provider = ConfiguredProvider({"aux": slot}, client=self.jobs._http, session_id=job["conversation_id"])
            provider.budget_sink = sink
            formatting = {"response_format": {"type": "json_object"}, "temperature": 0} if slot.provider == "openai-compatible" else {}
            targets = [row[0] for row in self.db.read_conn.execute("SELECT task_run_id FROM trace_targets WHERE job_id=? ORDER BY source_global_seq", (job_id,))]
            reports, skipped, actual_results = [], [], []
            for task_id in targets:
                messages = await asyncio.to_thread(self.material, job, task_id)
                async def budget():
                    self.jobs.check_identity(job_id)
                    count = self.db.read_conn.execute("SELECT count(*) FROM memory_job_calls WHERE job_id=? AND type='llm.request_started'", (job_id,)).fetchone()[0]
                    if count >= 20:
                        raise ContextBudgetError("CONTEXT_BUDGET_EXCEEDED：整个审查作业达到20次模型调用上限")
                    counter = await asyncio.to_thread(load_counter, slot)
                    measured = await asyncio.to_thread(counter.measure, slot, messages, registry.schemas(), system=system, thinking={"type": "disabled"})
                    measured.validate()
                    if measured.total_input_tokens > slot.context_window * 75 // 100:
                        raise ContextBudgetError("CONTEXT_BUDGET_EXCEEDED：审查输入超过aux窗口75%")
                result = await run_task(messages, LoopDeps(request=lambda: provider.stream("aux", messages, registry.schemas(), system=system, thinking={"type": "disabled"}, **formatting),
                    execute=execute, emit=sink, on_progress=lambda: None, before_request=budget, gates=LoopGates(max_steps=20), model=slot.model, model_slot="aux"))
                actual_results.append({"task_run_id": task_id, "actual_result": redact(result.final_text)})
                result_text = canonical(actual_results)
                if result.status != "completed":
                    raise ValueError("真实审查未完成：" + str(result.reason))
                outcome = ReviewResult.model_validate_json(actual_results[-1]["actual_result"])
                if any(report.task_run_id != task_id for report in outcome.reports):
                    raise ValueError("单个目标审查引用了其他任务")
                reports.extend(outcome.reports)
                if outcome.nothing_to_report:
                    skipped.append({"task_run_id": task_id, "reason": outcome.reason})
            outcome = ReviewResult(nothing_to_report=not reports, reason=canonical(skipped) if skipped else None, reports=reports)
            with CancelScope(shield=True):
                await self.complete(job_id, outcome)
        finally:
            self.jobs._slots.pop(job_id, None)
            error = sys.exception()
            if error is not None:
                cancelled = isinstance(error, asyncio.CancelledError)
                status = "interrupted" if cancelled and self.jobs._closed else "cancelled" if cancelled else "failed"
                with CancelScope(shield=True):
                    await self.jobs._status(job_id, status, self.jobs._reason if cancelled else redact(f"{type(error).__name__}: {error}"), report={"actual_result": result_text} if result_text else None)

    async def complete(self, job_id, outcome):
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                job = self.jobs._job(conn, job_id)
                if job["status"] != "running":
                    raise ValueError("已结束审查不能提交报告")
                origin = conn.execute("SELECT credential_owner_id,effective_user_id FROM job_governance WHERE job_id=?", (job_id,)).fetchone()
                actor = RequestIdentity(origin[0], origin[1], origin[0] != origin[1])
                self.runtime.identities.job(actor, job_id, conn)
                targets = {row["task_run_id"]: dict(row) for row in conn.execute("SELECT * FROM trace_targets WHERE job_id=?", (job_id,))}
                for target in targets.values():
                    conversation = conn.execute("SELECT conversation_id FROM task_runs WHERE id=?", (target["task_run_id"],)).fetchone()[0]
                    self.runtime.identities.conversation(actor, conversation, conn)
                events = []
                for report in outcome.reports:
                    target = targets.get(report.task_run_id)
                    if target is None or target["attempt_no"] != report.attempt_no:
                        raise ValueError("报告任务或尝试不属于输入目标")
                    reference = report.report.root_cause_event
                    actual = conn.execute("SELECT task_run_id,attempt_no,seq,global_seq FROM run_events WHERE task_run_id=? AND seq=?", (report.task_run_id, reference.seq)).fetchone() if reference else None
                    validate_reference(reference, report.task_run_id, report.attempt_no, target["source_global_seq"], actual)
                    report_id, stamp = uuid.uuid4().hex, now()
                    conn.execute("INSERT INTO trace_reports VALUES(?,?,?,?,?,?,?,?)", (report_id, job_id, report.task_run_id, report.attempt_no,
                        target["source_global_seq"], job["model"], stamp, canonical(report.report.model_dump())))
                    conn.execute("UPDATE trace_targets SET result='reported' WHERE job_id=? AND task_run_id=?", (job_id, report.task_run_id))
                    events.append(self.events.append_in_tx(conn, task_run_id=None, conversation_id=job["conversation_id"], type=T.AUDIT_REPORTED,
                        payload={"report_id": report_id, "job_id": job_id, "source_task_run_id": report.task_run_id, "attempt_no": report.attempt_no,
                            "source_global_seq": target["source_global_seq"], "model": job["model"], "root_cause_event": report.report.root_cause_event.model_dump() if reference else None,
                            "scope": self.scope(conn, job_id)}))
                conn.execute("UPDATE trace_targets SET result=? WHERE job_id=? AND result IS NULL", ("skipped：" + (outcome.reason or "模型没有为该目标生成报告"), job_id))
                conn.execute("UPDATE memory_jobs SET status=?,error=?,report=?,finished_at=? WHERE id=?", ("skipped" if outcome.nothing_to_report else "completed", outcome.reason,
                    canonical(outcome.model_dump()), now(), job_id))
                status_events, audit = self.jobs._record_tx(conn, job_id)
            self.jobs._publish(conn, [*events, *status_events], audit)
        await self.events.channel.execute(tx)

    def scope(self, conn, job_id):
        job = self.jobs._job(conn, job_id)
        actor = conn.execute("SELECT effective_user_id FROM job_governance WHERE job_id=?", (job_id,)).fetchone()[0]
        return {"workspace_id": job["workspace_id"], "agent_id": job["agent_id"], "owner_id": actor}

    def event_payload(self, conn, job_id):
        job = self.jobs._job(conn, job_id)
        target = conn.execute("SELECT attempt_no FROM trace_targets WHERE job_id=? AND task_run_id=?", (job_id, job["task_run_id"])).fetchone()
        report = conn.execute("SELECT id FROM trace_reports WHERE job_id=? ORDER BY created_at,id LIMIT 1", (job_id,)).fetchone()
        usage = json.loads(job["usage"])
        return {"job_id": job_id, "kind": job["kind"], "status": job["status"], "source_task_run_id": job["task_run_id"], "attempt_no": target[0] if target else None,
            "trigger_global_seq": job["trigger_global_seq"], "model": job["model"], "config_version": job["config_version"], "reason": job["error"],
            "report_id": report[0] if report else None, "skill_id": None, "usage": None if not usage.get("complete", True) else usage, "scope": self.scope(conn, job_id)}

    def view(self, actor, job_id):
        self.runtime.identities.job(actor, job_id)
        job = self.jobs.get(job_id)
        if job["kind"] != "trace_audit":
            raise GovernanceError("NOT_FOUND", "轨迹审查作业不存在", 404)
        payload = self.event_payload(self.db.read_conn, job_id)
        return display_value({**payload, "id": job_id, "task_run_id": job["task_run_id"], "version_id": None,
            "targets": [dict(row) for row in self.db.read_conn.execute("SELECT * FROM trace_targets WHERE job_id=?", (job_id,))],
            "report_ids": [row[0] for row in self.db.read_conn.execute("SELECT id FROM trace_reports WHERE job_id=?", (job_id,))]})

    def report(self, actor, task_id):
        task = self.db.read_conn.execute("SELECT conversation_id FROM task_runs WHERE id=?", (task_id,)).fetchone()
        if task is None:
            raise GovernanceError("NOT_FOUND", "任务不存在", 404)
        self.runtime.identities.conversation(actor, task[0])
        row = self.db.read_conn.execute("SELECT * FROM trace_reports WHERE task_run_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (task_id,)).fetchone()
        return display_value({**dict(row), "report": json.loads(row["report"])}) if row else None
