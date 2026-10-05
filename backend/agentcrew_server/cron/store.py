"""计划及发生的 SQLite 权威存储，复用串行事务、身份与审计。"""

import hashlib
import json
import time
import uuid
from pathlib import Path

from agentcrew_core.cron.schedule import next_run_at
from agentcrew_core.cron.authorization import authorization_contains, path_candidates, conflict_reason
from agentcrew_core.events import RunEventType as T
from agentcrew_core.memory.pagination import memory_page
from ..db.audit import append_audit, SNAPSHOT_EVERY, snapshot_chain_head
from ..governance.resources import GovernanceError, canonical, now
from ..runs import display_value, Runs


def now_ms():
    return time.time_ns() // 1_000_000


class CronStore:
    def __init__(self, runtime):
        self.runtime, self.db = runtime, runtime.db
        self.resources, self.identities = runtime.governance, runtime.identities
        self.events, self.rules = runtime.event_store, runtime.approvals.rules

    @staticmethod
    def decode(row):
        value = dict(row)
        return display_value({"id": value["id"], "workspace_id": value["workspace_id"], "name": value["name"],
            "revision": value["revision"], "schedule": json.loads(value["schedule"]), "target": json.loads(value["target"]),
            "metadata": json.loads(value["metadata"]), "state": {"enabled": bool(value["enabled"]),
                **{key: value[key] for key in ("next_run_at", "last_run_at", "last_status", "run_count", "retry_count", "max_retries")}},
            "created_at": value["created_at"], "updated_at": value["updated_at"], "deleted_at": value["deleted_at"]})

    def get(self, identity, job_id, conn=None, manage=False):
        connection = conn if conn is not None else self.db.read_conn
        row = connection.execute("SELECT * FROM cron_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "计划不存在", 404)
        self.identities.require(identity, "manage" if manage else "use", row["workspace_id"], connection)
        current = self.identities.current(identity, connection)
        if current["role"] == "member":
            self.identities.agent(identity, row["agent_id"], row["workspace_id"], connection)
        if current["role"] == "member" and row["owner_id"] != identity.effective_user_id:
            raise GovernanceError("OUT_OF_SCOPE", "计划属于其他成员", 403)
        value = self.decode(row)
        if value["target"]["execution_mode"] == "existing":
            origin = connection.execute("SELECT effective_user_id FROM governance_conversations WHERE conversation_id=?", (value["target"]["conversation_id"],)).fetchone()
            if origin is None or current["role"] != "owner" and origin[0] != identity.effective_user_id:
                raise GovernanceError("OUT_OF_SCOPE", "目标会话属于其他成员", 403)
        return value

    def scope(self, job, conn):
        workspace = self.resources.get("workspace", job["workspace_id"], conn)
        return {"org_id": workspace["org_id"], "workspace_id": job["workspace_id"],
                "agent_id": job["metadata"]["agent_id"], "owner_id": job["metadata"]["owner_id"]}

    def roots(self, identity, workspace_id, agent_id, target, conn):
        workspace = self.resources.get("workspace", workspace_id, conn)
        roots = [str(Path(workspace["data_dir"]).resolve())]
        if target["execution_mode"] == "existing":
            conversation_id = target["conversation_id"]
            if not conversation_id:
                raise GovernanceError("VALIDATION_ERROR", "existing 必须指定会话", 422)
            row = self.identities.conversation(identity, conversation_id, conn)
            if (row[0], row[1]) != (workspace_id, agent_id):
                raise GovernanceError("OUT_OF_SCOPE", "目标会话的员工或工作区不匹配", 403)
            conversation = self.runtime.sessions.conversation_or_404(conversation_id)
            if conversation["status"] != "active":
                raise GovernanceError("OUT_OF_SCOPE", "目标会话已归档", 403)
            scope = self.runtime.sessions.scope_of(conversation)
            roots.append(str(Path(scope["materials_dir"]).resolve()))
            roots.extend(str(Path(folder["path"]).resolve()) for folder in scope["folders"] if folder["access"] == "read_write")
        elif target["conversation_id"] is not None:
            raise GovernanceError("VALIDATION_ERROR", "new_conversation 不接受会话标识", 422)
        return roots

    def preview(self, identity, request, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        self.identities.require(identity, "manage", request["workspace_id"], connection)
        agent = self.identities.agent(identity, request["agent_id"], request["workspace_id"], connection)
        roots = self.roots(identity, request["workspace_id"], request["agent_id"], request["target"], connection)
        candidates = []
        rows = connection.execute("SELECT tool_name,pattern FROM agent_permission_rules WHERE agent_id=? AND effect='allow' AND revoked_at IS NULL", (agent["id"],)).fetchall()
        for row in rows:
            tool, pattern = row
            if tool in {"write_file", "read_file"}:
                candidates.extend(path_candidates(tool, str(Path(pattern).resolve()), roots))
            elif tool == "http_request":
                granted = connection.execute("SELECT c.config FROM connectors c JOIN grants g ON g.resource_type='connector' AND g.resource_id=c.id WHERE c.type='http' AND c.status='active' AND c.workspace_id=? AND g.grantee_type='agent' AND g.grantee_id=? AND g.revoked_at IS NULL", (agent["workspace_id"], agent["id"])).fetchall()
                rule = {"tool": tool, "pattern": pattern}
                for connector in granted:
                    for host in json.loads(connector[0])["allowed_hosts"]:
                        allowed = {"tool": tool, "pattern": host}
                        candidate = allowed if authorization_contains(rule, allowed) else rule if authorization_contains(allowed, rule) else None
                        if candidate is not None and candidate not in candidates:
                            candidates.append(candidate)
            elif tool.startswith("mcp_"):
                available = connection.execute("SELECT 1 FROM connector_tools t JOIN connectors c ON c.id=t.connector_id AND c.revision=t.connector_revision JOIN grants g ON g.resource_type='connector' AND g.resource_id=c.id WHERE t.stable_name=? AND c.workspace_id=? AND c.status='active' AND g.grantee_type='agent' AND g.grantee_id=? AND g.revoked_at IS NULL", (tool, agent["workspace_id"], agent["id"])).fetchone()
                if available is not None:
                    candidates.append({"tool": tool, "pattern": self.rules.normalize(agent["id"], tool, pattern, "allow", connection)})
            else:
                normalized = self.rules.normalize(agent["id"], tool, pattern, "allow", connection)
                candidates.append({"tool": tool, "pattern": normalized})
        normalized_choices = []
        for choice in request["pre_authorized"]:
            pattern = self.rules.normalize(agent["id"], choice["tool"], choice["pattern"], "allow", connection)
            normalized = {"tool": choice["tool"], "pattern": pattern}
            if not any(authorization_contains(candidate, normalized) for candidate in candidates):
                raise GovernanceError("OUT_OF_SCOPE", "计划预授权超出当前员工允许候选", 403)
            if normalized not in normalized_choices:
                normalized_choices.append(normalized)
        stamp = now_ms()
        digest = hashlib.sha256(canonical({"actor": identity.effective_user_id, "agent": agent, "roots": roots, "candidates": candidates}).encode()).hexdigest()
        return {"candidates": candidates, "next_run_at": next_run_at(request["schedule"], stamp, stamp),
                "authorization_sha256": digest, "selected": normalized_choices}

    @staticmethod
    def references(conn, job):
        enabled = job["state"]["enabled"] and job["deleted_at"] is None
        conn.execute("UPDATE skill_references SET active=0 WHERE resource_type='cron' AND resource_id=?", (job["id"],))
        for skill_id in job["metadata"]["skill_versions"]:
            conn.execute("INSERT INTO skill_references VALUES('cron',?,?,?,?) ON CONFLICT(resource_type,resource_id,skill_id) DO UPDATE SET active=excluded.active",
                         (job["id"], skill_id, int(enabled), now()))

    async def create(self, identity, request):
        job_id = uuid.uuid5(uuid.NAMESPACE_URL, "agentcrew:cron:" + identity.effective_user_id + ":" + request["change_id"]).hex
        def operation(conn):
            preview = self.preview(identity, request, conn)
            agent = self.identities.agent(identity, request["agent_id"], request["workspace_id"], conn)
            stamp, timestamp = now_ms(), now()
            metadata = {"agent_id": agent["id"], "agent_spec_snapshot": agent["spec"],
                "agent_revision": agent["revision"], "skill_versions": self.runtime.skill_versions.task_snapshot(conn, agent["id"], agent["spec"]),
                "pre_authorized": preview["selected"], "created_by": "user", "owner_id": identity.effective_user_id,
                "credential_owner_id": identity.credential_owner_id, "created_via_task_run_id": None, "proposal_id": None}
            conn.execute("INSERT INTO cron_jobs(id,workspace_id,agent_id,owner_id,credential_owner_id,name,revision,schedule,target,metadata,enabled,anchor_at,next_run_at,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?,?,?)",
                (job_id, request["workspace_id"], agent["id"], identity.effective_user_id, identity.credential_owner_id,
                 request["name"], canonical(request["schedule"]), canonical(request["target"]), canonical(metadata),
                 int(request.get("enabled", True)), stamp, next_run_at(request["schedule"], stamp, stamp), timestamp, timestamp))
            job = self.get(identity, job_id, conn)
            self.references(conn, job)
            return job, self.scope(job, conn), {"job_id": job_id, "enabled": job["state"]["enabled"], "deleted_at": None}
        return await self.resources.mutate(change_id=request["change_id"], actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request=request, action="cron.job_created", kind="cron_job", resource_id=job_id,
            operation=operation, event_type=T.CRON_JOB_CHANGED, authorize=lambda conn: self.identities.require(identity, "manage", request["workspace_id"], conn))

    async def edit(self, identity, job_id, request, delete=False):
        def operation(conn):
            job = self.get(identity, job_id, conn, manage=True)
            if job["revision"] != request["expected_revision"]:
                raise GovernanceError("REVISION_CONFLICT", "计划修订已经改变")
            if job["deleted_at"]:
                raise GovernanceError("REVISION_CONFLICT", "计划已删除")
            configuration = {"workspace_id": job["workspace_id"], "agent_id": job["metadata"]["agent_id"],
                "schedule": request.get("schedule", job["schedule"]), "target": request.get("target", job["target"]),
                "pre_authorized": request.get("pre_authorized", job["metadata"]["pre_authorized"])}
            metadata = job["metadata"]
            if set(request) & {"schedule", "target", "pre_authorized"} or request.get("enabled") is True:
                preview = self.preview(identity, configuration, conn)
                metadata = {**metadata, "pre_authorized": preview["selected"]}
            stamp, timestamp = now_ms(), now()
            prior = conn.execute("SELECT anchor_at,next_run_at FROM cron_jobs WHERE id=?", (job_id,)).fetchone()
            changed_schedule = "schedule" in request
            conn.execute("UPDATE cron_jobs SET name=?,revision=revision+1,schedule=?,target=?,metadata=?,enabled=?,anchor_at=?,next_run_at=?,updated_at=?,deleted_at=? WHERE id=?",
                (request.get("name", job["name"]), canonical(configuration["schedule"]), canonical(configuration["target"]), canonical(metadata),
                 int(False if delete else request.get("enabled", job["state"]["enabled"])), stamp if changed_schedule else prior[0],
                 next_run_at(configuration["schedule"], stamp, stamp) if changed_schedule else prior[1], timestamp, timestamp if delete else None, job_id))
            value = self.get(identity, job_id, conn)
            self.references(conn, value)
            return value, self.scope(value, conn), {"job_id": job_id, "enabled": value["state"]["enabled"], "deleted_at": value["deleted_at"]}
        return await self.resources.mutate(change_id=request["change_id"], actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request=request, action="cron.job_deleted" if delete else "cron.job_changed",
            kind="cron_job", resource_id=job_id, operation=operation, event_type=T.CRON_JOB_CHANGED,
            authorize=lambda conn: self.get(identity, job_id, conn, manage=True))

    def list(self, identity, workspace, limit, after):
        with Runs(self.runtime).snapshot(identity) as conn:
            current = self.identities.require(identity, "use", workspace, conn)
            rows = conn.execute("SELECT * FROM cron_jobs WHERE deleted_at IS NULL AND (? IS NULL OR workspace_id=?) ORDER BY created_at DESC,id DESC", (workspace, workspace)).fetchall()
            selected = []
            for row in rows:
                if row["workspace_id"] not in current["workspace_ids"] or current["role"] == "member" and row["owner_id"] != identity.effective_user_id:
                    continue
                if current["role"] == "member" and not self.identities.visible_scope(identity, row["workspace_id"], row["agent_id"]):
                    continue
                target = json.loads(row["target"])
                if target["execution_mode"] == "existing" and current["role"] != "owner":
                    origin = conn.execute("SELECT effective_user_id FROM governance_conversations WHERE conversation_id=?", (target["conversation_id"],)).fetchone()
                    if origin is None or origin[0] != identity.effective_user_id:
                        continue
                selected.append(self.get(identity, row["id"], conn))
            result = {**memory_page(selected, {"actor": identity.effective_user_id, "workspace": workspace, "order": "created-desc"}, limit, after, key="id"),
                      "at_global_seq": Runs.head(conn)}
        return result

    def occurrence(self, occurrence_id, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        row = connection.execute("SELECT * FROM cron_job_runs WHERE id=?", (occurrence_id,)).fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "计划发生记录不存在", 404)
        value = dict(row)
        value["attempts"] = [dict(attempt) for attempt in connection.execute("SELECT retry_no,task_run_id,attempt_no,status,started_at,finished_at FROM cron_run_attempts WHERE occurrence_id=? ORDER BY retry_no", (occurrence_id,))]
        return value

    def conflict(self, conn, job):
        active = conn.execute("SELECT 1 FROM cron_job_runs WHERE job_id=? AND status IN ('fired','retry_wait','interrupted','pending_verification') LIMIT 1", (job["id"],)).fetchone() is not None
        if job["target"]["execution_mode"] != "existing":
            return conflict_reason(active_occurrence=active)
        conversation = job["target"]["conversation_id"]
        row = conn.execute("SELECT status,queue_paused,pending_queue FROM conversations WHERE id=?", (conversation,)).fetchone()
        task = conn.execute("SELECT 1 FROM task_runs WHERE conversation_id=? AND status IN ('queued','running','waiting_user','waiting_verification','interrupted') LIMIT 1", (conversation,)).fetchone() is not None
        pending = conn.execute("SELECT 1 FROM tool_calls c JOIN task_runs t ON t.id=c.task_run_id WHERE t.conversation_id=? AND c.status='pending_verification' LIMIT 1", (conversation,)).fetchone() is not None
        return conflict_reason(active_occurrence=active, conversation_active=row is not None and row[0] == "active",
            queue_paused=bool(row[1]) if row else False,
            queued_items=any(item.get("state") == "queued" for item in json.loads(row[2])) if row else False,
            active_task=task, pending_verification=pending)

    def history(self, identity, job_id, limit, after):
        with Runs(self.runtime).snapshot(identity) as conn:
            self.get(identity, job_id, conn)
            rows = conn.execute("SELECT id FROM cron_job_runs WHERE job_id=? ORDER BY triggered_at DESC,id DESC", (job_id,)).fetchall()
            result = {**memory_page([self.occurrence(row[0], conn) for row in rows], {"actor": identity.effective_user_id, "job": job_id, "order": "trigger-desc"}, limit, after, key="id"),
                      "at_global_seq": Runs.head(conn)}
        return result

    async def run_now(self, identity, job_id, request):
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                job = self.get(identity, job_id, conn, manage=True)
                prior = conn.execute("SELECT id,revision FROM cron_job_runs WHERE job_id=? AND actor_id=? AND client_request_id=?", (job_id, identity.effective_user_id, request["client_request_id"])).fetchone()
                if prior:
                    if prior[1] != request["expected_revision"]:
                        raise GovernanceError("IDEMPOTENCY_CONFLICT", "手动运行标识已绑定其他修订")
                    return self.occurrence(prior[0], conn)
                if job["revision"] != request["expected_revision"]:
                    raise GovernanceError("REVISION_CONFLICT", "计划修订已经改变")
                if not job["state"]["enabled"] or job["deleted_at"]:
                    raise GovernanceError("REVISION_CONFLICT", "计划已停用或删除")
                stamp, timestamp, occurrence_id = now_ms(), now(), uuid.uuid4().hex
                note = self.conflict(conn, job)
                busy = note is not None
                status = "skipped" if busy else "fired"
                conn.execute("INSERT INTO cron_job_runs(id,job_id,revision,trigger,scheduled_at,triggered_at,actor_id,client_request_id,status,note,created_at,updated_at) VALUES(?,?,?,'manual',NULL,?,?,?,?,?,?,?)",
                    (occurrence_id, job_id, job["revision"], stamp, identity.effective_user_id, request["client_request_id"], status, note, timestamp, timestamp))
                payload = {"job_id": job_id, "occurrence_id": occurrence_id, "revision": job["revision"], "trigger": "manual",
                    "scheduled_at": None, "triggered_at": stamp, "source_task_run_id": None, "retry_no": 0,
                    "status": status, "reason": note, "scope": self.scope(job, conn), "notification_id": None}
                payload["audit_seq"] = append_audit(conn, ts=timestamp, actor_type="user", actor_id=identity.effective_user_id,
                    action="cron.manual_requested", resource_type="cron_job", resource_id=job_id, detail=canonical(payload))
                event = self.events.append_in_tx(conn, task_run_id=None, conversation_id=None,
                    type=T.CRON_JOB_SKIPPED if busy else T.CRON_JOB_FIRED, payload=payload)
                result = self.occurrence(occurrence_id, conn)
            self.events.publish(event)
            if payload["audit_seq"] % SNAPSHOT_EVERY == 0:
                snapshot_chain_head(conn, self.resources.data_dir / "chain-head.txt")
            return result
        result = await self.events.channel.execute(tx)
        if self.runtime.cron_scheduler is not None and result["status"] == "fired":
            await self.runtime.cron_scheduler.dispatch_registered(result["id"])
        return result
