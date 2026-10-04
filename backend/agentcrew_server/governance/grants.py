"""当前Grant、任务有效能力及串行派发检查。"""

import json
import uuid

from agentcrew_core.events import RunEventType as T
from agentcrew_core.memory import sha256
from agentcrew_core.governance import capability_active, filter_tool_schemas, grant_types_allowed
from agentcrew_core.connectors import connector_target
from ..approvals import ApprovalStale
from ..memory.store import MemoryIdentity
from .resources import GovernanceError, canonical, now


class Grants:
    def __init__(self, resources, identities):
        self.resources, self.identities, self.db = resources, identities, resources.db

    def get(self, grant_id, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        cursor = connection.execute("SELECT g.*,r.workspace_id FROM grants g JOIN governance_resources r ON r.resource_type=g.resource_type AND r.resource_id=g.resource_id WHERE g.id=?", (grant_id,))
        row = cursor.fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "授权不存在", 404)
        value = dict(zip((column[0] for column in cursor.description), row))
        value.pop("grantee_agent_id")
        value.pop("grantee_user_id")
        return value

    async def create(self, identity, request):
        resource = self.resources.get(request["resource_type"], request["resource_id"])
        workspace = resource["workspace_id"]
        grant_id = uuid.uuid5(uuid.NAMESPACE_URL, "agentcrew:grant:" + request["change_id"]).hex
        def operation(conn):
            current = self.resources.get(request["resource_type"], request["resource_id"], conn)
            if current["status"] != "active":
                raise GovernanceError("OUT_OF_SCOPE", "资源已禁用或归档", 403)
            grantee, kind = request["grantee_id"], request["grantee_type"]
            if not grant_types_allowed(request["resource_type"], kind):
                raise GovernanceError("VALIDATION_ERROR", "授权资源和接受者类型不匹配", 422)
            if kind == "agent":
                recipient = self.resources.get("agent", grantee, conn)
                if recipient["workspace_id"] != workspace or recipient["status"] != "active":
                    raise GovernanceError("OUT_OF_SCOPE", "接受员工不属于相同有效工作区", 403)
            else:
                member = conn.execute("SELECT m.role FROM memberships m JOIN users u ON u.id=m.user_id JOIN workspaces w ON w.org_id=m.org_id WHERE m.user_id=? AND w.id=? AND m.status='active' AND u.status='active'", (grantee, workspace)).fetchone()
                access = conn.execute("SELECT enabled FROM workspace_members WHERE workspace_id=? AND user_id=?", (workspace, grantee)).fetchone()
                if member is None or member[0] != "owner" and (access is None or not access[0]):
                    raise GovernanceError("OUT_OF_SCOPE", "接受成员没有该工作区访问资格", 403)
            existing = conn.execute("SELECT id FROM grants WHERE resource_type=? AND resource_id=? AND grantee_type=? AND grantee_id=? AND revoked_at IS NULL",
                (request["resource_type"], request["resource_id"], kind, grantee)).fetchone()
            if existing:
                raise GovernanceError("REVISION_CONFLICT", "相同有效授权已经存在")
            conn.execute("INSERT INTO grants(id,resource_type,resource_id,grantee_type,grantee_id,grantee_agent_id,grantee_user_id,granted_by_user_id,created_at,change_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (grant_id, request["resource_type"], request["resource_id"], kind, grantee, grantee if kind == "agent" else None,
                 grantee if kind == "user" else None, identity.effective_user_id, now(), request["change_id"]))
            if kind == "agent":
                spec = recipient["spec"]
                key = "skill_ids" if request["resource_type"] == "skill" else "connector_ids"
                if request["resource_id"] not in spec[key]:
                    spec[key].append(request["resource_id"])
                    conn.execute("UPDATE agents SET spec=?,revision=revision+1,updated_at=? WHERE id=?", (canonical(spec), now(), grantee))
                if request["resource_type"] == "skill":
                    conn.execute("INSERT INTO skill_references VALUES('agent',?,?,1,?) ON CONFLICT(resource_type,resource_id,skill_id) DO UPDATE SET active=1",
                        (grantee, request["resource_id"], now()))
            value = self.get(grant_id, conn)
            return value, self.resources.scope(request["resource_type"], request["resource_id"], conn), self._event(value)
        return await self.resources.mutate(change_id=request["change_id"], actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request=request, action="governance.grant_created", kind="grant", resource_id=grant_id,
            operation=operation, event_type=T.GOVERNANCE_GRANT_CHANGED,
            authorize=lambda conn: self.identities.require(identity, "manage", workspace, conn))

    async def revoke(self, identity, grant_id, request):
        workspace = self.get(grant_id)["workspace_id"]
        def operation(conn):
            prior = self.get(grant_id, conn)
            if prior["revision"] != request["expected_revision"]:
                raise GovernanceError("REVISION_CONFLICT", "授权修订已经变化")
            if prior["revoked_at"] is None:
                conn.execute("UPDATE grants SET revoked_at=?,revision=revision+1 WHERE id=?", (now(), grant_id))
                if prior["resource_type"] == "skill":
                    conn.execute("UPDATE skill_references SET active=0 WHERE resource_type='agent' AND resource_id=? AND skill_id=?", (prior["grantee_id"], prior["resource_id"]))
            value = self.get(grant_id, conn)
            return value, self.resources.scope(prior["resource_type"], prior["resource_id"], conn), {**self._event(value), "revocation_changed": prior["revoked_at"] is None}
        return await self.resources.mutate(change_id=request["change_id"], actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request=request, action="governance.grant_revoked", kind="grant", resource_id=grant_id,
            operation=operation, event_type=T.GOVERNANCE_GRANT_CHANGED,
            authorize=lambda conn: self.identities.require(identity, "manage", workspace, conn))

    @staticmethod
    def _event(value):
        return {"grantee_type": value["grantee_type"], "grantee_id": value["grantee_id"], "revoked_at": value["revoked_at"],
            "capability_type": value["resource_type"], "capability_id": value["resource_id"]}

    def view(self, task_id, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        caller = self.identities.task(task_id, connection)
        row = connection.execute("SELECT c.workspace_id,c.agent_id,c.id,g.agent_spec,g.skill_versions FROM task_runs t JOIN conversations c ON c.id=t.conversation_id JOIN task_governance g ON g.task_run_id=t.id WHERE t.id=?", (task_id,)).fetchone()
        spec = json.loads(row[3])
        capabilities, connectors = [], []
        for kind, key in (("skill", "skill_ids"), ("connector", "connector_ids")):
            for resource_id in spec.get(key, []):
                resource = self.resources.get(kind, resource_id, connection)
                grant = connection.execute("SELECT id,revision FROM grants WHERE resource_type=? AND resource_id=? AND grantee_type='agent' AND grantee_id=? AND revoked_at IS NULL", (kind, resource_id, row[1])).fetchone()
                if not capability_active(resource["workspace_id"], resource["status"], grant, row[0]):
                    continue
                capabilities.append({"type": kind, "id": resource_id, "revision": resource["revision"], "grant_id": grant[0], "grant_revision": grant[1]})
                if kind == "connector":
                    connectors.append(resource)
        actor = self.identities.current(caller, connection)
        body = {"actor_id": caller.effective_user_id, "credential_owner_id": caller.credential_owner_id,
            "role": actor["role"], "workspace_id": row[0], "agent_id": row[1], "conversation_id": row[2], "capabilities": capabilities}
        return {**body, "connectors": connectors, "skill_versions": json.loads(row[4]), "sha256": sha256(canonical(body).encode())}

    def schemas(self, task_id, schemas, conn=None):
        view = self.view(task_id, conn)
        http_ids = [connector["id"] for connector in view["connectors"] if connector["type"] == "http"]
        filtered = filter_tool_schemas(schemas, http_ids)
        if hasattr(self, "connectors"):
            for connector, row, definition in self.connectors.authorized_mcp(task_id):
                filtered.append({"name": row["stable_name"], "description": self.connectors.description(definition), "input_schema": definition["inputSchema"]})
        return filtered, view

    def check_tool(self, task_id, name, inputs, conn=None):
        view = self.view(task_id, conn)
        connection = conn if conn is not None else self.db.read_conn
        if connection.execute("SELECT 1 FROM tool_calls c JOIN task_runs t ON t.id=c.task_run_id WHERE t.conversation_id=? AND c.status='pending_verification'", (view["conversation_id"],)).fetchone():
            raise GovernanceError("PENDING_VERIFICATION", "必须先核验已有调用的真实效果")
        if name == "http_request":
            from agentcrew_core.connectors import connector_headers
            choices = [item for item in view["connectors"] if item["type"] == "http"]
            connector_id = inputs.get("connector_id")
            if connector_id is None and len(choices) == 1:
                connector_id = choices[0]["id"]
            selected = next((item for item in choices if item["id"] == connector_id), None)
            if selected is None:
                raise GovernanceError("OUT_OF_SCOPE", "当前任务没有所选HTTP连接器授权", 403)
            connector_headers(inputs.get("headers") or {}, selected["config"].get("credential_header", "Authorization"))
            connector_target(inputs.get("url", ""), selected["config"])
            view["selected_connector"] = selected
        elif name in {"skill_view", "skill_patch"}:
            skill = connection.execute("SELECT id FROM skills WHERE workspace_id=? AND name=?", (view["workspace_id"], inputs.get("name"))).fetchone()
            if skill is not None and not any(item["type"] == "skill" and item["id"] == skill[0] for item in view["capabilities"]):
                raise GovernanceError("OUT_OF_SCOPE", "当前任务没有技能授权", 403)
        elif name.startswith("mcp_"):
            row = connection.execute("SELECT connector_id,connector_revision FROM connector_tools WHERE stable_name=?", (name,)).fetchone()
            selected = next((item for item in view["connectors"] if row is not None and item["id"] == row[0] and item["revision"] == row[1]), None)
            if selected is None:
                raise GovernanceError("OUT_OF_SCOPE", "当前任务未授权该MCP工具或目录已经过期", 403)
        return view

    def check_dispatch(self, conn, kind, task_id, payload):
        if task_id is None:
            return
        if kind == T.TOOL_DISPATCHED:
            self.check_binding(task_id, payload["call_id"], conn)
            status = conn.execute("SELECT status FROM task_runs WHERE id=?", (task_id,)).fetchone()
            if status is None or status[0] not in {"running", "waiting_user"}:
                raise GovernanceError("OUT_OF_SCOPE", "任务已经停止，禁止派发", 403)
            row = conn.execute("SELECT tool_name,input FROM tool_calls WHERE task_run_id=? AND call_id=?", (task_id, payload["call_id"])).fetchone()
            if row is None:
                raise GovernanceError("NOT_FOUND", "派发缺少已准备调用", 404)
            view = self.check_tool(task_id, row[0], json.loads(row[1]), conn)
            if row[0] == "ask_user":
                prepared = conn.execute("SELECT attempt_no FROM run_events WHERE task_run_id=? AND type='tool.prepared' AND json_extract(payload,'$.call_id')=? ORDER BY global_seq DESC LIMIT 1", (task_id, payload["call_id"])).fetchone()
                attempt = conn.execute("SELECT current_attempt_no FROM task_runs WHERE id=?", (task_id,)).fetchone()[0]
                if prepared is None or prepared[0] != attempt:
                    raise ApprovalStale("提问派发绑定的活动尝试已经过期")
            if hasattr(self, "approvals") and row[0] != "ask_user":
                self.approvals.check_current(conn, task_id, payload["call_id"])
            payload["authorization_sha256"] = view["sha256"]
        elif kind in {T.LLM_REQUEST_STARTED, T.RUN_RESUMED}:
            view = self.view(task_id, conn)
            if conn.execute("SELECT 1 FROM tool_calls c JOIN task_runs t ON t.id=c.task_run_id WHERE t.conversation_id=? AND c.status='pending_verification'", (view["conversation_id"],)).fetchone():
                raise GovernanceError("PENDING_VERIFICATION", "必须先核验该会话已有调用的真实效果")
            payload["authorization_sha256"] = view["sha256"]
            if kind == T.LLM_REQUEST_STARTED:
                from agentcrew_core.tools import build_default_registry
                names = [schema["name"] for schema in self.schemas(task_id, build_default_registry().schemas(), conn)[0]]
                payload["tool_names"] = names

    def check_binding(self, task_id, call_id, conn):
        row = conn.execute("SELECT payload FROM run_events WHERE task_run_id=? AND type='tool.prepared' AND json_extract(payload,'$.call_id')=? ORDER BY global_seq DESC LIMIT 1", (task_id, call_id)).fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "调用缺少准备记录", 404)
        original = json.loads(row[0])
        connector_id = original.get("connector_id")
        if connector_id is not None:
            current = self.resources.get("connector", connector_id, conn)
            if current["revision"] != original["connector_revision"] or current["status"] != "active":
                raise GovernanceError("REVISION_CONFLICT", "准备调用的连接器修订已经失效")
            if hasattr(self, "connectors"):
                self.connectors.startup_resources(current, conn)

    def affects(self, event, task_id):
        row = self.db.read_conn.execute("SELECT c.workspace_id,c.agent_id,g.effective_user_id,g.agent_spec FROM task_runs t JOIN conversations c ON c.id=t.conversation_id JOIN task_governance g ON g.task_run_id=t.id WHERE t.id=?", (task_id,)).fetchone()
        if row is None:
            return False
        p = event.payload
        if event.type == T.GOVERNANCE_RULE_CHANGED:
            return p.get("agent_id") == row[1] and p.get("source_task_run_id") != task_id and \
                (p.get("effect") == "deny" and p.get("revoked_at") is None or p.get("effect") == "allow" and bool(p.get("revoked_at")) and p.get("revocation_changed", True))
        if event.type == T.GOVERNANCE_GRANT_CHANGED and p.get("revoked_at"):
            if not p.get("revocation_changed", True):
                return False
            if p["grantee_type"] == "user":
                return p["grantee_id"] == row[2] and p["capability_id"] == row[1]
            spec = json.loads(row[3])
            return p["grantee_id"] == row[1] and p["capability_id"] in spec.get("skill_ids" if p["capability_type"] == "skill" else "connector_ids", [])
        if event.type == T.GOVERNANCE_ROLE_CHANGED:
            owner = self.db.read_conn.execute("SELECT credential_owner_id FROM task_governance WHERE task_run_id=?", (task_id,)).fetchone()[0]
            return p.get("user_id") in {row[2], owner}
        if event.type == T.GOVERNANCE_RESOURCE_CHANGED and (p.get("status") != "active" or p.get("configuration_changed")):
            kind, resource_id = p.get("resource_type"), p.get("resource_id")
            if kind == "agent":
                return resource_id == row[1]
            if kind == "workspace":
                return resource_id == row[0]
            if kind == "organization":
                return True
            if kind == "workspace_member":
                return p.get("scope", {}).get("workspace_id") == row[0] and p.get("scope", {}).get("owner_id") == row[2]
            return resource_id in json.loads(row[3]).get("skill_ids" if kind == "skill" else "connector_ids", [])
        return False

    def check_skill_change(self, conn, identity, skill_id):
        if identity.actor_type == "user":
            self.identities.require(self.identities.memory_actor(identity), "manage", identity.workspace_id, conn)
            return
        row = conn.execute("SELECT workspace_id,status FROM skills WHERE id=?", (skill_id,)).fetchone()
        if row is None:
            return
        if row[0] != identity.workspace_id or row[1] != "active" or conn.execute("SELECT 1 FROM grants WHERE resource_type='skill' AND resource_id=? AND grantee_type='agent' AND grantee_id=? AND revoked_at IS NULL",
                (skill_id, identity.agent_id)).fetchone() is None:
            raise GovernanceError("OUT_OF_SCOPE", "准备写入时技能当前授权已经失效", 403)

    def affects_job(self, event, job_id):
        row = self.db.read_conn.execute("SELECT j.task_run_id,j.workspace_id,j.agent_id,g.effective_user_id,g.credential_owner_id FROM memory_jobs j JOIN job_governance g ON g.job_id=j.id WHERE j.id=?", (job_id,)).fetchone()
        if row is None:
            return False
        if row[0]:
            return self.affects(event, row[0])
        payload = event.payload
        if event.type == T.GOVERNANCE_GRANT_CHANGED:
            if not payload.get("revoked_at") or not payload.get("revocation_changed", True):
                return False
            return (payload.get("grantee_type") == "agent" and payload.get("grantee_id") == row[2]) or \
                (payload.get("grantee_type") == "user" and payload.get("grantee_id") == row[3] and payload.get("capability_id") == row[2])
        if event.type == T.GOVERNANCE_ROLE_CHANGED:
            return payload.get("user_id") in {row[3], row[4]}
        if event.type == T.GOVERNANCE_RESOURCE_CHANGED and (payload.get("status") != "active" or payload.get("configuration_changed")):
            kind, resource_id = payload.get("resource_type"), payload.get("resource_id")
            if kind == "organization":
                return True
            if kind == "workspace":
                return resource_id == row[1]
            if kind == "agent":
                return resource_id == row[2]
            if kind == "workspace_member":
                return payload.get("scope", {}).get("workspace_id") == row[1] and payload.get("scope", {}).get("owner_id") == row[3]
            return kind == "skill" and payload.get("scope", {}).get("workspace_id") == row[1]
        return False
