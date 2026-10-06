"""持久重试、当前授权复查及原 TaskRun 的正常恢复接续。"""

import asyncio
import json
from pathlib import Path

from agentcrew_core.cron.recovery import retry_decision, permission_refused, side_effect_safety
from agentcrew_core.events import RunEventType as T
from agentcrew_core.governance import RequestIdentity
from agentcrew_core.tools import build_default_registry, ToolInvocation
from agentcrew_core.tools.judgment import bash_readonly
from ..bus import Topic
from ..db.projections import _row_to_event
from ..governance.resources import canonical, now
from ..db.audit import append_audit, SNAPSHOT_EVERY, snapshot_chain_head
from .store import now_ms


class CronRecovery:
    def __init__(self, runtime):
        self.runtime, self.db, self.events = runtime, runtime.db, runtime.event_store
        self.executor, self.store = runtime.cron_executor, runtime.cron_store
        self._timers = {}
        self._closed = False
        self._subscription = self._listener = None

    def decision(self, conn, bound, reason, stamp):
        task_id = conn.execute("SELECT task_run_id FROM cron_job_runs WHERE id=?", (bound["occurrence_id"],)).fetchone()[0]
        rows = conn.execute("SELECT c.*,json_extract(e.payload,'$.read_only_verdict') AS readonly FROM tool_calls c JOIN run_events e ON e.task_run_id=c.task_run_id AND e.type='tool.prepared' AND json_extract(e.payload,'$.call_id')=c.call_id WHERE c.task_run_id=?", (task_id,)).fetchall()
        registry = build_default_registry()
        def readonly(row):
            tool = registry.get(row["tool_name"])
            return bool(row["readonly"]) or tool is not None and tool.metadata.read_only
        safety = side_effect_safety([{**dict(row), "read_only": readonly(row)} for row in rows])
        text = reason or ""
        failed_outputs = [value[0] or "" for value in conn.execute("SELECT json_extract(payload,'$.details.stderr') FROM run_events WHERE task_run_id=? AND type='tool.failed'", (task_id,))]
        denied = permission_refused(text, failed_outputs) or any(code in text for code in (
            "SOUL_", "CONTEXT_", "TOKENIZER_", "token_budget", "max_steps", "doom_loop", "unparseable", "LengthFinishReason"))
        return retry_decision(enabled=bool(bound["enabled"] and not bound["deleted_at"]),
            revision_matches=bound["revision"] == bound["occurrence_revision"], retry_no=bound["occurrence_retry"],
            **safety, denied=denied, failed=True, now_ms=stamp)

    def permitted(self, conn, occurrence_id, *, automatic, check_due=True):
        occurrence = self.store.occurrence(occurrence_id, conn)
        bound = self.executor.task(occurrence["task_run_id"], conn) if occurrence["task_run_id"] else None
        if bound is None:
            return False, "计划发生没有可恢复的原任务"
        if automatic and (occurrence["status"] != "retry_wait" or occurrence["retry_at"] is None or check_due and occurrence["retry_at"] > now_ms() or occurrence["retry_count"] >= 3):
            return False, "当前发生没有到期的可用自动重试"
        if not bound["enabled"] or bound["deleted_at"] or bound["revision"] != bound["occurrence_revision"]:
            return False, "PLAN_DISABLED：计划已经停用或修订改变"
        actor = RequestIdentity(bound["credential_owner_id"], bound["owner_id"], bound["credential_owner_id"] != bound["owner_id"])
        if not self.runtime.identities.visible_scope(actor, bound["workspace_id"], bound["agent_id"]):
            return False, "AUTHORIZATION_REVOKED：当前角色、员工或Grant已经失效"
        task = conn.execute("SELECT conversation_id FROM task_runs WHERE id=?", (occurrence["task_run_id"],)).fetchone()
        if not self.runtime.identities.visible_conversation(actor, task[0]):
            return False, "OUT_OF_SCOPE：原会话失去权限"
        conversation = conn.execute("SELECT queue_paused,pending_queue FROM conversations WHERE id=?", (task[0],)).fetchone()
        if conversation[0] or any(item.get("state") == "queued" for item in json.loads(conversation[1])):
            return False, "原会话的队列已暂停或存在排队指令"
        if conn.execute("SELECT 1 FROM task_runs WHERE conversation_id=? AND id<>? AND status IN ('queued','running','waiting_user','waiting_verification','interrupted') LIMIT 1", (task[0], occurrence["task_run_id"])).fetchone():
            return False, "原会话存在其他未结束任务"
        if conn.execute("SELECT 1 FROM tool_calls WHERE task_run_id=? AND status IN ('pending_verification','outcome_unknown') LIMIT 1", (occurrence["task_run_id"],)).fetchone():
            return False, "存在未知副作用或待核验调用"
        context = self.runtime.sessions.build_work_context(task[0], occurrence["task_run_id"])
        if any(Path(root).resolve() != root for root in (*context.filesystem.read_roots, *context.filesystem.write_roots)):
            return False, "OUT_OF_SCOPE：原文件范围的规范化目录已经改变"
        rules = self.runtime.approvals._load_rules(bound["agent_id"], conn)
        view = self.runtime.grants.view(occurrence["task_run_id"], conn)
        for connector in view["connectors"]:
            if connector["type"] != "mcp":
                continue
            if conn.execute("SELECT 1 FROM connector_tools WHERE connector_id=? AND connector_revision=?", (connector["id"], connector["revision"])).fetchone() is None:
                return False, "AUTHORIZATION_REVOKED：当前MCP连接器缺少有效工具目录"
            _resources, reason = self.runtime.connectors.startup_resource_state(connector, conn)
            if reason:
                return False, "AUTHORIZATION_REVOKED：" + reason
        registry = self.runtime.connectors.registry(occurrence["task_run_id"], build_default_registry())
        for call in conn.execute("SELECT c.call_id,c.tool_name,c.input,json_extract(e.payload,'$.connector_id') AS connector_id,json_extract(e.payload,'$.connector_revision') AS connector_revision FROM tool_calls c JOIN run_events e ON e.task_run_id=c.task_run_id AND e.type='tool.prepared' AND json_extract(e.payload,'$.call_id')=c.call_id WHERE c.task_run_id=?", (occurrence["task_run_id"],)):
            if call["connector_id"] is not None and not any(item["id"] == call["connector_id"] and item["revision"] == call["connector_revision"] for item in view["connectors"]):
                return False, "AUTHORIZATION_REVOKED：原连接器授权失效或配置已经改变"
            inputs = json.loads(call["input"])
            tool = registry.get(call["tool_name"])
            if tool is None:
                return False, "AUTHORIZATION_REVOKED：原调用已不在当前工具目录"
            if call["tool_name"] in {"skill_view", "skill_patch"}:
                skill = conn.execute("SELECT id FROM skills WHERE workspace_id=? AND name=?", (bound["workspace_id"], inputs.get("name"))).fetchone()
                if skill is not None and not any(item["type"] == "skill" and item["id"] == skill[0] for item in view["capabilities"]):
                    return False, "AUTHORIZATION_REVOKED：原技能Grant已经失效"
            boundary = self.runtime.approvals._file_boundary(ToolInvocation(call["call_id"], call["tool_name"], inputs), context)
            readonly = bash_readonly(inputs["command"], context.cwd)[0] if call["tool_name"] == "bash" else tool.metadata.read_only
            gate = self.executor.current_gate(occurrence["task_run_id"], tool.metadata, inputs, readonly, rules, context.cwd, conn)
            if boundary or gate.action != "allow":
                return False, boundary or gate.reason
        if automatic:
            decision = self.decision(conn, bound, occurrence["note"], now_ms())
            if decision["status"] != "retry_wait":
                return False, decision["reason"] or "原任务不具备安全自动重试条件"
        return True, None

    async def recover(self):
        def tx(conn):
            followups = []
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute("SELECT * FROM cron_job_runs WHERE status IN ('fired','retry_wait','interrupted','pending_verification') ORDER BY created_at").fetchall()
                for row in rows:
                    if row["task_run_id"] is None:
                        if row["status"] != "interrupted":
                            followups.extend(self.transition_in_tx(conn, row["id"], "interrupted", "登记后缺少派发任务，需真人核查，禁止重发"))
                        continue
                    task = conn.execute("SELECT status FROM task_runs WHERE id=?", (row["task_run_id"],)).fetchone()[0]
                    if task in {"completed", "failed", "cancelled", "interrupted", "waiting_verification"} and row["status"] != "retry_wait":
                        event = conn.execute("SELECT * FROM run_events WHERE task_run_id=? AND type IN ('run.completed','run.failed','run.cancelled','run.interrupted','tool.pending_verification') ORDER BY global_seq DESC LIMIT 1", (row["task_run_id"],)).fetchone()
                        if event:
                            followups.extend(self.executor.observe_in_tx(conn, _row_to_event(event)))
            for event in followups:
                self.events.publish(event)
        await self.events.channel.execute(tx)

    async def start(self):
        self._subscription = self.runtime.bus.subscribe(Topic("all"), internal=True)
        await self.refresh()
        self._listener = asyncio.create_task(self._listen(), name="cron:retry-events")

    async def _listen(self):
        self._subscription.bind_consumer()
        while not self._closed:
            event = self._subscription.take_nowait()
            if event is None:
                await self._subscription.wait_for_data()
                continue
            if event.type in {T.CRON_JOB_CHANGED, T.CRON_JOB_STATUS, T.CRON_JOB_FAILED, T.GOVERNANCE_GRANT_CHANGED,
                              T.GOVERNANCE_ROLE_CHANGED, T.GOVERNANCE_RESOURCE_CHANGED, T.GOVERNANCE_RULE_CHANGED}:
                await self.refresh()

    async def refresh(self):
        disabled = self.db.read_conn.execute("SELECT r.id FROM cron_job_runs r JOIN cron_jobs j ON j.id=r.job_id WHERE r.status='retry_wait' AND (j.enabled=0 OR j.deleted_at IS NOT NULL OR j.revision<>r.revision)").fetchall()
        for row in disabled:
            await self._stop_retry(row[0], "PLAN_DISABLED：计划已停用或修订已经改变")
        waiting = self.db.read_conn.execute("SELECT id FROM cron_job_runs WHERE status='retry_wait'").fetchall()
        for row in waiting:
            allowed, reason = await self.events.channel.execute(lambda conn: self.permitted(conn, row[0], automatic=True, check_due=False))
            if not allowed:
                await self._stop_retry(row[0], reason)
        rows = self.db.read_conn.execute("SELECT id,retry_at FROM cron_job_runs WHERE status='retry_wait' AND retry_at IS NOT NULL").fetchall()
        active = {row[0] for row in rows}
        for occurrence_id, task in list(self._timers.items()):
            if occurrence_id not in active:
                task.cancel()
                del self._timers[occurrence_id]
        for occurrence_id, retry_at in rows:
            if occurrence_id not in self._timers or self._timers[occurrence_id].done():
                task = asyncio.create_task(self._retry(occurrence_id, retry_at), name="cron:retry:" + occurrence_id)
                task.add_done_callback(self.runtime.cron_scheduler._finished)
                self._timers[occurrence_id] = task

    async def _retry(self, occurrence_id, retry_at):
        while not self._closed and now_ms() < retry_at:
            await asyncio.sleep(min((retry_at - now_ms()) / 1000, 60))
        if self._closed:
            return
        allowed, reason = await self.events.channel.execute(lambda conn: self.permitted(conn, occurrence_id, automatic=True))
        if not allowed:
            await self._stop_retry(occurrence_id, reason)
            return
        occurrence = self.store.occurrence(occurrence_id)
        origin = self.executor.task(occurrence["task_run_id"])
        actor = RequestIdentity(origin["credential_owner_id"], origin["owner_id"], origin["credential_owner_id"] != origin["owner_id"])
        await self.runtime.recovery.resume(occurrence["task_run_id"], "automatic_cron_retry:" + occurrence_id,
            request_identity=actor, automatic_occurrence_id=occurrence_id)

    async def _stop_retry(self, occurrence_id, reason):
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                prior = self.store.occurrence(occurrence_id, conn)
                events = self.transition_in_tx(conn, occurrence_id, "failed", reason) if prior["status"] == "retry_wait" else []
            for event in events:
                self.events.publish(event)
            sequence = conn.execute("SELECT max(seq) FROM audit_log").fetchone()[0]
            if events and sequence % SNAPSHOT_EVERY == 0:
                snapshot_chain_head(conn, self.runtime.data_dir / "chain-head.txt")
        await self.events.channel.execute(tx)

    def transition_in_tx(self, conn, occurrence_id, status, reason):
        occurrence = self.store.occurrence(occurrence_id, conn)
        timestamp = now()
        conn.execute("UPDATE cron_job_runs SET status=?,retry_at=NULL,note=?,updated_at=? WHERE id=?", (status, reason, timestamp, occurrence_id))
        conn.execute("UPDATE cron_jobs SET last_status=?,updated_at=? WHERE id=?", (status, timestamp, occurrence["job_id"]))
        job = self.store.decode(conn.execute("SELECT * FROM cron_jobs WHERE id=?", (occurrence["job_id"],)).fetchone())
        payload = {"job_id": job["id"], "occurrence_id": occurrence_id, "revision": occurrence["revision"], "trigger": occurrence["trigger"],
            "scheduled_at": occurrence["scheduled_at"], "triggered_at": occurrence["triggered_at"], "source_task_run_id": occurrence["task_run_id"],
            "retry_no": occurrence["retry_count"], "status": status, "reason": reason, "retry_at": None,
            "scope": self.store.scope(job, conn), "notification_id": "cron-" + occurrence_id + "-" + status + "-" + str(occurrence["retry_count"])}
        payload["audit_seq"] = append_audit(conn, ts=timestamp, actor_type="system", actor_id="cron", action="cron.reconciled",
            resource_type="cron_job", resource_id=job["id"], detail=canonical(payload))
        event = self.events.append_in_tx(conn, task_run_id=None, conversation_id=None, type=T.CRON_JOB_FAILED if status == "failed" else T.CRON_JOB_STATUS, payload=payload)
        events = [event]
        current = self.store.occurrence(occurrence_id, conn)
        if conn.execute("SELECT 1 FROM runtime_notifications WHERE id=?", (payload["notification_id"],)).fetchone() is None:
            events.append(self.executor.notify_in_tx(conn, current, job, event))
        return events

    async def shutdown(self):
        self._closed = True
        tasks = list(self._timers.values())
        if self._listener is not None:
            tasks.append(self._listener)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks)
        if self._subscription is not None:
            self.runtime.bus.unsubscribe(self._subscription)
