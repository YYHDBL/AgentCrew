"""不可变员工提案、真人范围决定及原审批事务中的唯一计划创建。"""

import hashlib
import json

from agentcrew_core.cron.models import CreateBody, ScheduleTaskInput
from agentcrew_core.cron.authorization import authorization_contains
from agentcrew_core.events import RunEventType as T
from agentcrew_core.tools.scheduler import input_hash
from ..db.audit import append_audit, snapshot_chain_head
from ..governance.resources import GovernanceError, canonical, now
from ..runs import display_value


class CronProposals:
    def __init__(self, runtime):
        self.runtime, self.db, self.events = runtime, runtime.db, runtime.event_store
        self.store, self.identities = runtime.cron_store, runtime.identities

    def row(self, proposal_id, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        row = connection.execute("SELECT * FROM cron_proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "计划提案不存在", 404)
        return dict(row)

    @staticmethod
    def public(row):
        return display_value({**{key: row[key] for key in ("id", "task_run_id", "attempt_no", "call_id", "input_hash", "revision", "status", "job_id", "decided_by", "decided_at")},
            **{key: json.loads(row[key]) for key in ("proposal", "candidates", "selected")}})

    def scope(self, row, conn):
        task = conn.execute("SELECT c.workspace_id,c.agent_id,g.effective_user_id FROM task_runs t JOIN conversations c ON c.id=t.conversation_id JOIN task_governance g ON g.task_run_id=t.id WHERE t.id=?", (row["task_run_id"],)).fetchone()
        workspace = self.runtime.governance.get("workspace", task[0], conn)
        return {"org_id": workspace["org_id"], "workspace_id": task[0], "agent_id": task[1], "owner_id": task[2]}

    def get(self, identity, proposal_id):
        row = self.row(proposal_id)
        task = self.db.read_conn.execute("SELECT conversation_id FROM task_runs WHERE id=?", (row["task_run_id"],)).fetchone()
        self.identities.conversation(identity, task[0])
        return self.public(row)

    def prepare(self, pending, inputs):
        data = ScheduleTaskInput.model_validate(inputs).model_dump()
        task = self.db.read_conn.execute("SELECT c.workspace_id,c.agent_id FROM task_runs t JOIN conversations c ON c.id=t.conversation_id WHERE t.id=?", (pending.task_run_id,)).fetchone()
        proposal = CreateBody.model_validate({"change_id": "proposal-" + pending.ctx.task_run_id + "-" + pending.input_hash[:16],
            "workspace_id": task[0], "agent_id": task[1], "name": data["name"], "schedule": data["schedule"],
            "target": {"instruction": data["instruction"], "execution_mode": data["execution_mode"],
                "conversation_id": pending.conversation_id if data["execution_mode"] == "existing" else None},
            "pre_authorized": data["pre_authorized"]}).model_dump()
        actor = self.identities.task(pending.task_run_id)
        self.store.preview(actor, {**proposal, "pre_authorized": []}, manage=False)
        return proposal

    def register_in_tx(self, conn, pending, call_id, proposal):
        prior = conn.execute("SELECT input_hash,task_run_id,attempt_no FROM cron_proposals WHERE id=?", (call_id,)).fetchone()
        if prior is not None:
            if tuple(prior) != (pending.input_hash, pending.task_run_id, pending.attempt_no):
                raise GovernanceError("APPROVAL_STALE", "提案调用身份已经绑定其他参数或尝试")
            return
        origin = self.identities.task(pending.task_run_id, conn)
        preview = self.store.preview(origin, {**proposal, "pre_authorized": []}, conn, manage=False)
        stamp = now()
        audit_seq = append_audit(conn, ts=stamp, actor_type="agent", actor_id=pending.agent_id, action="cron.proposal_created",
            resource_type="cron_proposal", resource_id=call_id, detail=canonical({"call_id": call_id, "input_hash": pending.input_hash,
                "source_task_run_id": pending.task_run_id, "attempt_no": pending.attempt_no, "candidates": preview["candidates"]}))
        conn.execute("INSERT INTO cron_proposals(id,task_run_id,attempt_no,call_id,input_hash,proposal,candidates,status,created_at,created_audit_seq) VALUES(?,?,?,?,?,?,?,'pending',?,?)",
            (call_id, pending.task_run_id, pending.attempt_no, call_id, pending.input_hash, canonical(proposal), canonical(preview["candidates"]), stamp, audit_seq))

    def prior_decision(self, pending, call_id, inputs):
        row = self.db.read_conn.execute("SELECT * FROM cron_proposals WHERE id=?", (call_id,)).fetchone()
        if row is None or row["status"] == "pending":
            return None
        if (row["task_run_id"], row["attempt_no"], row["input_hash"]) != (pending.task_run_id, pending.attempt_no, input_hash(inputs)):
            raise GovernanceError("APPROVAL_STALE", "原提案参数或执行尝试已经改变")
        return "allow" if row["status"] == "approved" else "deny"

    @staticmethod
    def decision_hash(identity, body):
        return hashlib.sha256(canonical({"actor": identity.effective_user_id, "credential_owner": identity.credential_owner_id, "body": body}).encode()).hexdigest()

    def validate_decision(self, conn, call_id, decision, actor, body):
        if body is None or actor is None or decision not in {"allow_once", "reject_once"}:
            raise GovernanceError("APPROVAL_STALE", "计划创建必须使用真人提案决定与范围选择")
        row = self.row(call_id, conn)
        proposal = json.loads(row["proposal"])
        self.identities.require(actor, "manage", proposal["workspace_id"], conn)
        digest = self.decision_hash(actor, body)
        if row["input_hash"] != body["input_hash"]:
            raise GovernanceError("APPROVAL_STALE", "批准参数哈希与原提案不符")
        if row["status"] != "pending":
            if row["status"] in {"approved", "rejected"} and row["decision_hash"] == digest:
                return None
            raise GovernanceError("APPROVAL_STALE", "提案决定已经保存或执行方已经失效")
        if row["revision"] != body["expected_revision"]:
            raise GovernanceError("APPROVAL_STALE", "提案修订已经改变")
        self.identities.task(row["task_run_id"], conn)
        if decision == "reject_once":
            if body["selected"]:
                raise GovernanceError("VALIDATION_ERROR", "拒绝计划不选择预授权", 422)
            return []
        preview = self.store.preview(actor, {**proposal, "pre_authorized": body["selected"]}, conn)
        candidates = json.loads(row["candidates"])
        if any(not any(authorization_contains(candidate, selected) for candidate in candidates) for selected in preview["selected"]):
            raise GovernanceError("OUT_OF_SCOPE", "所选范围超出持久提案的原候选", 403)
        return preview["selected"]

    def resolve_in_tx(self, conn, call_id, decision, actor, body, selected):
        row = self.row(call_id, conn)
        job, events = None, []
        if decision == "allow_once":
            proposal = {**json.loads(row["proposal"]), "change_id": "approved-proposal-" + call_id, "pre_authorized": selected}
            job = self.store.create_in_tx(conn, actor, proposal, source_task_id=row["task_run_id"], proposal_id=call_id)
            payload = {"job_id": job["id"], "resource_type": "cron_job", "resource_id": job["id"], "revision": 1,
                "change_id": proposal["change_id"], "enabled": True, "deleted_at": None, "actor_id": actor.effective_user_id,
                "credential_owner_id": actor.credential_owner_id, "proposal_id": call_id, "scope": self.store.scope(job, conn)}
            payload["audit_seq"] = append_audit(conn, ts=now(), actor_type="user", actor_id=actor.effective_user_id,
                action="cron.job_created", resource_type="cron_job", resource_id=job["id"], detail=canonical(payload))
            events.append(self.events.append_in_tx(conn, task_run_id=None, conversation_id=None, type=T.CRON_JOB_CHANGED, payload=payload))
        stamp = now()
        conn.execute("UPDATE cron_proposals SET status=?,revision=revision+1,selected=?,job_id=?,decided_by=?,credential_owner_id=?,decided_at=?,decision_hash=? WHERE id=?",
            ("approved" if job else "rejected", canonical(selected), job["id"] if job else None, actor.effective_user_id,
             actor.credential_owner_id, stamp, self.decision_hash(actor, body), call_id))
        events.append(self.resolved_event(conn, self.row(call_id, conn), decision, actor.effective_user_id))
        return events

    def resolved_event(self, conn, row, decision, actor_id):
        payload = {"proposal_id": row["id"], "call_id": row["call_id"], "input_hash": row["input_hash"], "revision": row["revision"],
            "decision": decision, "selected": json.loads(row["selected"]), "actor_id": actor_id, "job_id": row["job_id"], "scope": self.scope(row, conn)}
        payload["audit_seq"] = append_audit(conn, ts=now(), actor_type="system" if decision == "expired" else "user", actor_id=actor_id,
            action="cron.proposal_" + row["status"], resource_type="cron_proposal", resource_id=row["id"], detail=canonical(payload))
        return self.events.append_in_tx(conn, task_run_id=None, conversation_id=None, type=T.CRON_PROPOSAL_RESOLVED, payload=payload)

    def expire_in_tx(self, conn, row):
        conn.execute("UPDATE cron_proposals SET status='expired',revision=revision+1,decided_at=? WHERE id=? AND status='pending'", (now(), row["id"]))
        return self.resolved_event(conn, self.row(row["id"], conn), "expired", "cron")

    def observe_in_tx(self, conn, event):
        events = self.runtime.cron_executor.observe_in_tx(conn, event)
        if event.type == T.PERMISSION_REQUESTED and event.payload.get("tool") == "schedule_task":
            row = self.row(event.payload["tool_call_id"], conn)
            events.append(self.events.append_in_tx(conn, task_run_id=None, conversation_id=None, type=T.CRON_PROPOSAL_REQUESTED,
                payload={"proposal_id": row["id"], "call_id": row["call_id"], "input_hash": row["input_hash"], "revision": row["revision"],
                    "source_task_run_id": row["task_run_id"], "scope": self.scope(row, conn)}))
        elif event.task_run_id and event.type in {T.RUN_COMPLETED, T.RUN_FAILED, T.RUN_CANCELLED, T.RUN_INTERRUPTED, T.TOOL_PENDING_VERIFICATION}:
            for row in conn.execute("SELECT * FROM cron_proposals WHERE task_run_id=? AND status='pending'", (event.task_run_id,)).fetchall():
                events.append(self.expire_in_tx(conn, dict(row)))
        return events

    async def decide(self, actor, proposal_id, body):
        row = self.row(proposal_id)
        await self.runtime.approvals.submit(row["call_id"], body["decision"], body["input_hash"], request_identity=actor, proposal_decision=body)
        return self.get(actor, proposal_id)

    async def execute(self, invocation, context):
        row = self.row(invocation.call_id)
        self.identities.task(context.task_run_id)
        if row["status"] != "approved" or row["task_run_id"] != context.task_run_id or row["input_hash"] != input_hash(invocation.input):
            raise GovernanceError("APPROVAL_STALE", "实际工具调用缺少相同参数的真人批准")
        return self.store.decode(self.db.read_conn.execute("SELECT * FROM cron_jobs WHERE id=?", (row["job_id"],)).fetchone())

    async def recover(self):
        def tx(conn):
            events = []
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                for row in conn.execute("SELECT * FROM cron_proposals WHERE status='pending'").fetchall():
                    events.append(self.expire_in_tx(conn, dict(row)))
                for row in conn.execute("SELECT p.*,t.conversation_id FROM cron_proposals p JOIN task_runs t ON t.id=p.task_run_id JOIN tool_calls c ON c.call_id=p.call_id WHERE p.status='approved' AND c.status IN ('prepared','dispatched')").fetchall():
                    job = conn.execute("SELECT id FROM cron_jobs WHERE id=?", (row["job_id"],)).fetchone()
                    if job is None:
                        raise RuntimeError("已批准提案缺少持久计划，禁止继续恢复")
                    events.append(self.events.append_in_tx(conn, task_run_id=row["task_run_id"], conversation_id=row["conversation_id"],
                        type=T.TOOL_COMPLETED, attempt_no=row["attempt_no"], payload={"call_id": row["call_id"], "output": canonical({"job_id": job[0], "proposal_id": row["id"]}),
                            "output_summary": "真实计划及批准已经提交，重启核对完成", "details": {"job_id": job[0]}}))
            for event in events:
                self.events.publish(event)
            if events:
                snapshot_chain_head(conn, self.runtime.data_dir / "chain-head.txt")
        await self.events.channel.execute(tx)
