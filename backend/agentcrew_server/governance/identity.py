"""本地认证后的请求身份、当前角色及旧接口访问边界。"""

import hashlib
import json
import secrets

from agentcrew_core.events import RunEventType
from agentcrew_core.governance import RequestIdentity, role_allows
from ..secrets import register_secret
from .resources import GovernanceError, canonical, now


class Identities:
    def __init__(self, resources):
        self.resources, self.db = resources, resources.db

    def resolve(self, identity_token=None):
        if identity_token:
            row = self.db.read_conn.execute("SELECT credential_owner_id,user_id FROM demo_identities WHERE token_hash=? AND revoked_at IS NULL",
                (hashlib.sha256(identity_token.encode()).hexdigest(),)).fetchone()
            if row is None:
                raise GovernanceError("UNAUTHORIZED", "演示身份标识无效或已撤销", 401)
            identity = RequestIdentity(row[0], row[1], row[0] != row[1])
        else:
            identity = RequestIdentity("owner", "owner")
        self.current(identity)
        return identity

    def current(self, identity, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        credential = connection.execute("SELECT u.status,m.status,m.role FROM users u JOIN memberships m ON m.user_id=u.id WHERE u.id=?", (identity.credential_owner_id,)).fetchone()
        if credential is None or tuple(credential[:2]) != ("active", "active") or identity.demo and credential[2] != "owner":
            raise GovernanceError("UNAUTHORIZED", "真实凭证所属者资格已失效", 401)
        row = connection.execute("SELECT m.org_id,m.role,m.status,u.status,u.name FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.user_id=?",
            (identity.effective_user_id,)).fetchone()
        if row is None or row[2] != "active" or row[3] != "active":
            raise GovernanceError("UNAUTHORIZED", "当前成员资格已失效", 401)
        workspaces = [r[0] for r in connection.execute("SELECT w.id FROM workspaces w LEFT JOIN workspace_members m ON m.workspace_id=w.id AND m.user_id=? WHERE w.org_id=? AND w.status='active' AND (?='owner' OR m.enabled=1) ORDER BY w.id",
            (identity.effective_user_id, row[0], row[1]))]
        return {"credential_owner_id": identity.credential_owner_id, "effective_user_id": identity.effective_user_id,
                "name": row[4], "org_id": row[0], "role": row[1], "demo": identity.demo, "workspace_ids": workspaces}

    def require(self, identity, operation, workspace_id=None, conn=None):
        value = self.current(identity, conn)
        if self.resources.get("organization", value["org_id"], conn)["status"] != "active":
            raise GovernanceError("OUT_OF_SCOPE", "组织已禁用", 403)
        if not role_allows(value["role"], operation):
            raise GovernanceError("OUT_OF_SCOPE", "当前角色无权执行该操作", 403)
        if workspace_id is not None and workspace_id not in value["workspace_ids"]:
            raise GovernanceError("OUT_OF_SCOPE", "当前身份无权访问该工作区", 403)
        return value

    def task(self, task_id, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        row = connection.execute("SELECT g.credential_owner_id,g.effective_user_id,t.conversation_id FROM task_governance g JOIN task_runs t ON t.id=g.task_run_id WHERE t.id=?", (task_id,)).fetchone()
        if row is None:
            raise GovernanceError("OUT_OF_SCOPE", "任务缺少发起身份", 403)
        identity = RequestIdentity(row[0], row[1], row[0] != row[1])
        self.conversation(identity, row[2], connection)
        return identity

    def check_dispatch(self, conn, kind, task_id, payload):
        if kind in {RunEventType.TOOL_DISPATCHED, RunEventType.LLM_REQUEST_STARTED} and task_id is not None:
            self.task(task_id, conn)

    def agent(self, identity, agent_id, workspace_id=None, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        resource = self.resources.get("agent", agent_id, connection)
        value = self.require(identity, "use", resource["workspace_id"], connection)
        if workspace_id is not None and workspace_id != resource["workspace_id"]:
            raise GovernanceError("OUT_OF_SCOPE", "员工不属于请求工作区", 403)
        if resource["status"] != "active":
            raise GovernanceError("OUT_OF_SCOPE", "员工已禁用", 403)
        if value["role"] == "member" and connection.execute("SELECT 1 FROM grants WHERE resource_type='agent' AND resource_id=? AND grantee_type='user' AND grantee_id=? AND revoked_at IS NULL",
            (agent_id, identity.effective_user_id)).fetchone() is None:
            raise GovernanceError("OUT_OF_SCOPE", "当前成员没有员工使用授权", 403)
        return resource

    def conversation(self, identity, conversation_id, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        row = connection.execute("SELECT workspace_id,agent_id,effective_user_id FROM governance_conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "会话身份不存在", 404)
        self.agent(identity, row[1], row[0], connection)
        value = self.current(identity, connection)
        if value["role"] != "owner" and row[2] != identity.effective_user_id:
            raise GovernanceError("OUT_OF_SCOPE", "当前身份不能访问其他成员的会话", 403)
        return row

    def visible_conversation(self, identity, conversation_id):
        if not self.credential_active(identity):
            return False
        row = self.db.read_conn.execute("SELECT workspace_id,agent_id,effective_user_id FROM governance_conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
        member = self.db.read_conn.execute("SELECT role,status FROM memberships WHERE user_id=?", (identity.effective_user_id,)).fetchone()
        if row is None or member is None or member[1] != "active":
            return False
        if member[0] != "owner" and row[2] != identity.effective_user_id:
            return False
        agent = self.db.read_conn.execute("SELECT a.status,w.status,u.status FROM agents a JOIN workspaces w ON w.id=a.workspace_id JOIN users u ON u.id=? WHERE a.id=?", (identity.effective_user_id, row[1])).fetchone()
        if agent is None or tuple(agent) != ("active", "active", "active"):
            return False
        if member[0] == "owner":
            return True
        access = self.db.read_conn.execute("SELECT enabled FROM workspace_members WHERE workspace_id=? AND user_id=?", (row[0], identity.effective_user_id)).fetchone()
        if access is None or not access[0]:
            return False
        return member[0] == "admin" or self.db.read_conn.execute("SELECT 1 FROM grants WHERE resource_type='agent' AND resource_id=? AND grantee_type='user' AND grantee_id=? AND revoked_at IS NULL", (row[1], identity.effective_user_id)).fetchone() is not None

    def visible_scope(self, identity, workspace, agent_id):
        if not self.credential_active(identity):
            return False
        row = self.db.read_conn.execute("SELECT a.status,w.status,u.status,m.status,m.role FROM agents a JOIN workspaces w ON w.id=a.workspace_id JOIN memberships m ON m.org_id=w.org_id JOIN users u ON u.id=m.user_id WHERE a.id=? AND a.workspace_id=? AND m.user_id=?",
            (agent_id, workspace, identity.effective_user_id)).fetchone()
        if row is None or tuple(row[:4]) != ("active", "active", "active", "active"):
            return False
        if row[4] == "owner":
            return True
        access = self.db.read_conn.execute("SELECT enabled FROM workspace_members WHERE workspace_id=? AND user_id=?", (workspace, identity.effective_user_id)).fetchone()
        if access is None or not access[0]:
            return False
        return row[4] == "admin" or self.db.read_conn.execute("SELECT 1 FROM grants WHERE resource_type='agent' AND resource_id=? AND grantee_type='user' AND grantee_id=? AND revoked_at IS NULL", (agent_id, identity.effective_user_id)).fetchone() is not None

    def credential_active(self, identity):
        row = self.db.read_conn.execute("SELECT u.status,m.status,m.role,o.status FROM users u JOIN memberships m ON m.user_id=u.id JOIN organizations o ON o.id=m.org_id WHERE u.id=?", (identity.credential_owner_id,)).fetchone()
        return row is not None and row[0] == row[1] == row[3] == "active" and (not identity.demo or row[2] == "owner")

    def job(self, identity, job_id, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        row = connection.execute("SELECT j.workspace_id,j.agent_id,g.effective_user_id,g.credential_owner_id FROM memory_jobs j JOIN job_governance g ON g.job_id=j.id WHERE j.id=?", (job_id,)).fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "后台作业身份不存在", 404)
        self.agent(identity, row[1], row[0], connection)
        if self.current(identity, connection)["role"] != "owner" and row[2] != identity.effective_user_id:
            raise GovernanceError("OUT_OF_SCOPE", "后台作业属于其他成员", 403)
        origin = RequestIdentity(row[3], row[2], row[3] != row[2])
        self.agent(origin, row[1], row[0], connection)
        return row

    async def issue(self, identity, user_id, change_id):
        if identity.credential_owner_id != "owner":
            raise GovernanceError("OUT_OF_SCOPE", "演示身份签发需要真实所有者凭证", 403)
        self.require(RequestIdentity(identity.credential_owner_id, identity.credential_owner_id), "owner")
        target = RequestIdentity("owner", user_id, user_id != "owner")
        token = secrets.token_urlsafe(32)
        def operation(conn):
            value = self.current(target, conn)
            conn.execute("INSERT INTO demo_identities VALUES(?,?,?, ?,NULL,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, "owner", now(), change_id))
            return {"identity": value, "identity_token": token}, {"org_id": value["org_id"], "workspace_id": None, "agent_id": None, "owner_id": user_id}, {"effective_user_id": user_id}
        result = await self.resources.mutate(change_id=change_id, actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request={"user_id": user_id}, action="governance.identity_changed",
            kind="identity", resource_id=user_id, operation=operation, event_type=RunEventType.GOVERNANCE_IDENTITY_CHANGED,
            authorize=lambda conn: self.current(identity, conn))
        register_secret(result["identity_token"])
        return result

    async def role(self, identity, membership_id, request):
        def operation(conn):
            row = conn.execute("SELECT org_id,user_id,role,status,revision FROM memberships WHERE id=?", (membership_id,)).fetchone()
            if row is None:
                raise GovernanceError("NOT_FOUND", "成员不存在", 404)
            if row[0] != self.current(identity, conn)["org_id"]:
                raise GovernanceError("OUT_OF_SCOPE", "成员属于其他组织", 403)
            if row[4] != request["expected_revision"]:
                raise GovernanceError("REVISION_CONFLICT", "成员修订已经改变")
            status = request.get("status", row[3])
            if row[2] == "owner" and row[3] == "active" and (request["role"] != "owner" or status != "active"):
                if conn.execute("SELECT count(*) FROM memberships m JOIN users u ON u.id=m.user_id WHERE org_id=? AND role='owner' AND m.status='active' AND u.status='active'", (row[0],)).fetchone()[0] <= 1:
                    raise GovernanceError("OUT_OF_SCOPE", "不能撤销最后一名有效所有者", 403)
            conn.execute("UPDATE memberships SET role=?,status=?,revision=revision+1,updated_at=? WHERE id=?", (request["role"], status, now(), membership_id))
            result = {"id": membership_id, "org_id": row[0], "user_id": row[1], "name": conn.execute("SELECT name FROM users WHERE id=?", (row[1],)).fetchone()[0],
                "role": request["role"], "status": status, "revision": row[4] + 1,
                "workspace_ids": [r[0] for r in conn.execute("SELECT workspace_id FROM workspace_members WHERE user_id=? AND enabled=1 ORDER BY workspace_id", (row[1],))]}
            return result, {"org_id": row[0], "workspace_id": None, "agent_id": None, "owner_id": row[1]}, {"user_id": row[1], "role": request["role"], "status": status}
        return await self.resources.mutate(change_id=request["change_id"], actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request=request, action="governance.role_changed", kind="membership",
            resource_id=membership_id, operation=operation, event_type=RunEventType.GOVERNANCE_ROLE_CHANGED,
            authorize=lambda conn: self.require(identity, "owner", conn=conn))

    def authorize_request(self, request, identity):
        path, method, params = request.url.path, request.method, request.path_params
        if path in {"/api/health", "/api/identity", "/api/diagnostics", "/api/identity/demo"}:
            return
        if path.startswith("/api/diagnostics/") or path == "/api/audit/verify" or path.startswith("/api/settings"):
            self.require(identity, "owner")
        if path.startswith("/api/memberships/") or "/members/" in path:
            self.require(identity, "owner")
        elif any(path.startswith(prefix) for prefix in ("/api/workspaces", "/api/agents", "/api/skills", "/api/connectors", "/api/grants", "/api/organization")) and method != "GET":
            self.require(identity, "owner" if path == "/api/organization" else "manage", request.query_params.get("workspace_id"))
        if path.startswith("/api/memory/"):
            if method != "GET" and not path.startswith("/api/memory/jobs/"):
                self.require(identity, "manage", request.query_params.get("workspace_id"))
            workspace, agent = request.query_params.get("workspace_id"), request.query_params.get("agent_id")
            if workspace and agent:
                self.agent(identity, agent, workspace)
            job_id = params.get("job_id")
            if job_id:
                row = self.db.read_conn.execute("SELECT j.workspace_id,j.agent_id,g.effective_user_id FROM memory_jobs j JOIN job_governance g ON g.job_id=j.id WHERE j.id=?", (job_id,)).fetchone()
                if row is None:
                    raise GovernanceError("NOT_FOUND", "后台作业身份不存在", 404)
                self.agent(identity, row[1], row[0])
                if self.current(identity)["role"] != "owner" and row[2] != identity.effective_user_id:
                    raise GovernanceError("OUT_OF_SCOPE", "后台作业属于其他成员", 403)
        conversation_id = params.get("conversation_id")
        task_id = params.get("task_run_id") or request.query_params.get("task_run_id")
        if task_id:
            row = self.db.read_conn.execute("SELECT conversation_id FROM task_runs WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise GovernanceError("NOT_FOUND", "任务不存在", 404)
            self.conversation(identity, row[0])
            if conversation_id and conversation_id != row[0]:
                raise GovernanceError("OUT_OF_SCOPE", "任务不属于请求会话", 403)
        if params.get("call_id"):
            row = self.db.read_conn.execute("SELECT r.conversation_id FROM tool_calls t JOIN task_runs r ON r.id=t.task_run_id WHERE t.call_id=?", (params["call_id"],)).fetchone()
            if row is None:
                raise GovernanceError("NOT_FOUND", "工具调用不存在", 404)
            self.conversation(identity, row[0])
        if params.get("request_id"):
            row = self.db.read_conn.execute("SELECT conversation_id FROM run_events WHERE type='question.requested' AND json_extract(payload,'$.request_id')=?", (params["request_id"],)).fetchone()
            if row is None:
                raise GovernanceError("NOT_FOUND", "提问不存在", 404)
            self.conversation(identity, row[0])
        if conversation_id:
            self.conversation(identity, conversation_id)

    def bind_task(self, conn, event):
        if event.type != RunEventType.RUN_QUEUED:
            return
        row = conn.execute("SELECT workspace_id,agent_id,credential_owner_id,effective_user_id FROM governance_conversations WHERE conversation_id=?", (event.conversation_id,)).fetchone()
        if row is None:
            raise GovernanceError("OUT_OF_SCOPE", "任务缺少已认证会话身份", 403)
        source = event.payload.get("request_identity")
        if source is None and event.payload.get("queue_item_id"):
            queued = conn.execute("SELECT payload FROM run_events WHERE type='queue.item_enqueued' AND conversation_id=? AND json_extract(payload,'$.item_id')=?", (event.conversation_id, event.payload["queue_item_id"])).fetchone()
            source = json.loads(queued[0]).get("request_identity") if queued else None
        if source is None:
            source = {"credential_owner_id": row[2], "effective_user_id": row[3]}
        identity = RequestIdentity(source["credential_owner_id"], source["effective_user_id"], source["effective_user_id"] != source["credential_owner_id"])
        self.conversation(identity, event.conversation_id, conn)
        agent = self.agent(identity, row[1], row[0], conn)
        versions = self.resources.skill_versions.task_snapshot(conn, row[1], agent["spec"]) if hasattr(self.resources, "skill_versions") else {}
        conn.execute("INSERT INTO task_governance(task_run_id,effective_user_id,credential_owner_id,agent_revision,agent_spec,skill_versions,created_at,skill_binding_version) VALUES(?,?,?,?,?,?,?,?)", (event.task_run_id, identity.effective_user_id,
            identity.credential_owner_id, agent["revision"], json.dumps(agent["spec"], ensure_ascii=False), canonical(versions), event.ts,
            int(hasattr(self.resources, "skill_versions"))))

    def bind_job(self, conn, job, identity=None):
        if conn.execute("SELECT 1 FROM job_governance WHERE job_id=?", (job["id"],)).fetchone():
            return
        if identity is None and job["conversation_id"]:
            row = conn.execute("SELECT credential_owner_id,effective_user_id FROM task_governance WHERE task_run_id=?", (job["task_run_id"],)).fetchone()
            if row is None:
                raise GovernanceError("OUT_OF_SCOPE", "后台来源任务缺少请求身份", 403)
            identity = RequestIdentity(row[0], row[1], row[0] != row[1])
        if identity is None:
            identity = RequestIdentity("owner", "owner")
        self.agent(identity, job["agent_id"], job["workspace_id"], conn)
        conn.execute("INSERT INTO job_governance VALUES(?,?,?)", (job["id"], identity.effective_user_id, identity.credential_owner_id))

    def memory_actor(self, memory_identity):
        if memory_identity.actor_type == "user":
            return RequestIdentity("owner", memory_identity.actor_id, memory_identity.actor_id != "owner")
        if memory_identity.job_id:
            row = self.db.read_conn.execute("SELECT credential_owner_id,effective_user_id FROM job_governance WHERE job_id=?", (memory_identity.job_id,)).fetchone()
        elif memory_identity.task_run_id is None and memory_identity.conversation_id:
            row = self.db.read_conn.execute("SELECT credential_owner_id,effective_user_id FROM governance_conversations WHERE conversation_id=?", (memory_identity.conversation_id,)).fetchone()
        else:
            row = self.db.read_conn.execute("SELECT credential_owner_id,effective_user_id FROM task_governance WHERE task_run_id=?", (memory_identity.task_run_id,)).fetchone()
        if row is None:
            raise GovernanceError("OUT_OF_SCOPE", "记忆执行来源缺少当前身份", 403)
        return RequestIdentity(row[0], row[1], row[0] != row[1])

    async def bind_legacy_tasks(self):
        def operation(conn):
            count = 0
            for row in conn.execute("SELECT t.id,g.effective_user_id,g.credential_owner_id,g.agent_id,c.agent_spec_snapshot,t.created_at FROM task_runs t JOIN governance_conversations g ON g.conversation_id=t.conversation_id JOIN conversations c ON c.id=g.conversation_id WHERE NOT EXISTS(SELECT 1 FROM task_governance b WHERE b.task_run_id=t.id)").fetchall():
                agent = self.resources.get("agent", row[3], conn)
                conn.execute("INSERT INTO task_governance(task_run_id,effective_user_id,credential_owner_id,agent_revision,agent_spec,skill_versions,created_at) VALUES(?,?,?,?,?,?,?)", (row[0], row[1], row[2], agent["revision"], row[4], "{}", row[5]))
                count += 1
            return {"id": "legacy-task-identities", "revision": 1, "task_count": count}, {"org_id": "demo-org", "workspace_id": None, "agent_id": None, "owner_id": "owner"}, {"status": "active"}
        return await self.resources.mutate(change_id="m2-legacy-task-identities", actor_id="owner", request={}, action="governance.task_identities_registered",
            kind="identity", resource_id="legacy-task-identities", operation=operation)
