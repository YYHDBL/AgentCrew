"""计划发生与正常 Harness 的事务接入及终态收敛。"""

import json
import uuid

from agentcrew_core.events import RunEventType as T
from agentcrew_core.governance import RequestIdentity
from agentcrew_core.cron.authorization import unattended_gate
from agentcrew_core.tools.gate import GateResult
from ..db.audit import append_audit
from ..governance.resources import canonical, now
from ..governance.resources import GovernanceError
from .store import now_ms


class CronExecutor:
    def __init__(self, runtime):
        self.runtime, self.store, self.db = runtime, runtime.cron_store, runtime.db
        self.events, self.identities = runtime.event_store, runtime.identities

    def task(self, task_id, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        row = connection.execute("SELECT j.*,r.id AS occurrence_id,r.revision AS occurrence_revision,r.retry_count AS occurrence_retry FROM task_runs t JOIN cron_job_runs r ON r.task_run_id=t.id JOIN cron_jobs j ON j.id=r.job_id WHERE t.id=?", (task_id,)).fetchone()
        return dict(row) if row is not None else None

    def current_gate(self, task_id, metadata, inputs, readonly, rules, cwd, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        row = self.task(task_id, connection)
        if row is None:
            return None
        if not row["enabled"] or row["deleted_at"] or row["revision"] != row["occurrence_revision"]:
            return GateResult("deny", "PLAN_DISABLED：计划已停用或修订已经改变")
        job = self.store.decode(row)
        origin = RequestIdentity(row["credential_owner_id"], row["owner_id"], row["owner_id"] != row["credential_owner_id"])
        self.identities.agent(origin, row["agent_id"], row["workspace_id"], connection)
        return unattended_gate(metadata, inputs, readonly, rules, job["metadata"]["pre_authorized"], row["agent_id"], cwd)

    def check_task(self, task_id, conn):
        row = self.task(task_id, conn)
        if row is not None and (not row["enabled"] or row["deleted_at"] or row["revision"] != row["occurrence_revision"]):
            raise GovernanceError("OUT_OF_SCOPE", "PLAN_DISABLED：计划已停用或修订已经改变", 403)

    def prepare_in_tx(self, conn, job, occurrence_id):
        occurrence = self.store.occurrence(occurrence_id, conn)
        if occurrence["status"] != "fired" or occurrence["task_run_id"] is not None:
            return []
        origin = RequestIdentity(job["metadata"]["credential_owner_id"], job["metadata"]["owner_id"],
                                 job["metadata"]["credential_owner_id"] != job["metadata"]["owner_id"])
        if not self.identities.visible_scope(origin, job["workspace_id"], job["metadata"]["agent_id"]):
            conn.execute("UPDATE cron_job_runs SET status='failed',note='AUTHORIZATION_REVOKED：当前角色、员工或Grant已失效' WHERE id=?", (occurrence_id,))
            return []
        if job["target"]["execution_mode"] == "existing":
            conversation_id = job["target"]["conversation_id"]
            if not self.identities.visible_conversation(origin, conversation_id):
                conn.execute("UPDATE cron_job_runs SET status='failed',note='OUT_OF_SCOPE：目标会话已失去读取或执行权限' WHERE id=?", (occurrence_id,))
                return []
        else:
            conversation_id = uuid.uuid4().hex
            timestamp = now()
            conn.execute("INSERT INTO conversations(id,workspace_id,agent_id,agent_spec_snapshot,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (conversation_id, job["workspace_id"], job["metadata"]["agent_id"], canonical(job["metadata"]["agent_spec_snapshot"]), timestamp, timestamp))
            conn.execute("INSERT INTO governance_conversations VALUES(?,?,?,?,?)", (conversation_id, job["workspace_id"], job["metadata"]["agent_id"], origin.credential_owner_id, origin.effective_user_id))
        task_id = uuid.uuid4().hex
        event = self.events.append_in_tx(conn, task_run_id=task_id, conversation_id=conversation_id, type=T.RUN_QUEUED,
            payload={"instruction": job["target"]["instruction"], "cron_job_id": job["id"], "occurrence_id": occurrence_id,
                "trigger": occurrence["trigger"], "request_identity": {"credential_owner_id": origin.credential_owner_id, "effective_user_id": origin.effective_user_id}})
        conn.execute("UPDATE task_governance SET agent_revision=?,agent_spec=?,skill_versions=? WHERE task_run_id=?",
            (job["metadata"]["agent_revision"], canonical(job["metadata"]["agent_spec_snapshot"]), canonical(job["metadata"]["skill_versions"]), task_id))
        conn.execute("UPDATE cron_job_runs SET task_run_id=?,updated_at=? WHERE id=?", (task_id, event.ts, occurrence_id))
        conn.execute("INSERT INTO cron_run_attempts(occurrence_id,retry_no,task_run_id,attempt_no,status) VALUES(?,0,?,1,'queued')", (occurrence_id, task_id))
        conn.execute("UPDATE cron_jobs SET run_count=run_count+1,last_run_at=?,last_status='fired',updated_at=? WHERE id=?", (now_ms(), event.ts, job["id"]))
        return [event]

    async def dispatch(self, occurrence_id):
        row = await self.events.channel.execute(lambda conn: self.store.occurrence(occurrence_id, conn))
        if row["status"] == "fired" and row["task_run_id"] is None:
            raise RuntimeError("计划发生缺少同事务登记的真实任务，禁止派发")

    def notify_in_tx(self, conn, occurrence, job, domain_event):
        notification_id = "cron-" + occurrence["id"] + "-" + occurrence["status"] + "-" + str(occurrence["retry_count"])
        severity = "badge" if occurrence["status"] == "completed" else "warning" if occurrence["status"] in {"missed", "skipped"} else "error"
        conn.execute("INSERT INTO runtime_notifications(id,global_seq,owner_id,workspace_id,agent_id,job_id,task_run_id,kind,severity,message,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (notification_id, domain_event.global_seq, job["metadata"]["owner_id"], job["workspace_id"], job["metadata"]["agent_id"], job["id"],
             occurrence["task_run_id"], "cron." + occurrence["status"], severity, job["name"] + "：" + occurrence["status"] + ("，" + occurrence["note"] if occurrence["note"] else ""), domain_event.ts))
        return self.events.append_in_tx(conn, task_run_id=None, conversation_id=None, type=T.NOTIFICATION_CREATED,
            payload={"notification_id": notification_id, "source_global_seq": domain_event.global_seq, "job_id": job["id"],
                "source_task_run_id": occurrence["task_run_id"], "severity": severity, "scope": self.store.scope(job, conn)})

    def observe_in_tx(self, conn, event):
        if event.task_run_id is None or event.type not in {T.RUN_STARTED, T.RUN_RESUMED, T.RUN_COMPLETED, T.RUN_FAILED, T.RUN_CANCELLED, T.RUN_INTERRUPTED, T.TOOL_VERIFICATION_SUBMITTED}:
            return []
        bound = self.task(event.task_run_id, conn)
        if bound is None:
            return []
        job = self.store.decode(bound)
        occurrence_id = bound["occurrence_id"]
        if event.type in {T.RUN_STARTED, T.RUN_RESUMED}:
            conn.execute("UPDATE cron_run_attempts SET status='running',started_at=? WHERE occurrence_id=? AND attempt_no=?", (event.ts, occurrence_id, event.attempt_no))
            return []
        task = conn.execute("SELECT status FROM task_runs WHERE id=?", (event.task_run_id,)).fetchone()[0]
        status = "pending_verification" if task == "waiting_verification" else task
        if event.type == T.TOOL_VERIFICATION_SUBMITTED and task not in {"completed", "interrupted"}:
            return []
        denied = conn.execute("SELECT payload FROM run_events WHERE task_run_id=? AND type='tool.failed' AND (json_extract(payload,'$.error') LIKE '%PERMISSION_DENIED%' OR json_extract(payload,'$.error') LIKE '%UNATTENDED_%' OR json_extract(payload,'$.error') LIKE '%PRE_AUTH_%') ORDER BY seq DESC LIMIT 1", (event.task_run_id,)).fetchone()
        note = event.payload.get("reason")
        if denied is not None and status not in {"pending_verification", "interrupted"}:
            status, note = "failed", json.loads(denied[0])["error"]
        prior = self.store.occurrence(occurrence_id, conn)
        if prior["status"] == status:
            return []
        conn.execute("UPDATE cron_job_runs SET status=?,note=?,updated_at=? WHERE id=?", (status, note, event.ts, occurrence_id))
        conn.execute("UPDATE cron_run_attempts SET status=?,finished_at=?,error=? WHERE occurrence_id=? AND attempt_no=?", (status, event.ts, note, occurrence_id, event.attempt_no or 1))
        conn.execute("UPDATE cron_jobs SET last_status=?,updated_at=? WHERE id=?", (status, event.ts, job["id"]))
        occurrence = self.store.occurrence(occurrence_id, conn)
        payload = {"job_id": job["id"], "occurrence_id": occurrence_id, "revision": bound["occurrence_revision"], "trigger": occurrence["trigger"],
            "scheduled_at": occurrence["scheduled_at"], "triggered_at": occurrence["triggered_at"], "source_task_run_id": event.task_run_id,
            "retry_no": occurrence["retry_count"], "status": status, "reason": note, "retry_at": None,
            "scope": self.store.scope(job, conn), "notification_id": "cron-" + occurrence_id + "-" + status + "-" + str(occurrence["retry_count"])}
        payload["audit_seq"] = append_audit(conn, ts=event.ts, actor_type="system", actor_id="cron", action="cron.job_" + status,
            resource_type="cron_job", resource_id=job["id"], detail=canonical(payload))
        domain = self.events.append_in_tx(conn, task_run_id=None, conversation_id=None, type=T.CRON_JOB_FAILED if status == "failed" else T.CRON_JOB_STATUS, payload=payload)
        notification = self.notify_in_tx(conn, occurrence, job, domain)
        return [domain, notification]
