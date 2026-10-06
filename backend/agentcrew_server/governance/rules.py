"""员工规则的人类管理、规范化与事务写入。"""

import json
import uuid
from pathlib import Path

from agentcrew_core.events import RunEventType as T
from agentcrew_core.tools.judgment import build_protected_paths, host_allowed, path_is_protected
from agentcrew_core.tools import build_default_registry, evaluate_gate, PermissionRule
from agentcrew_core.tools.gate import GateResult
from agentcrew_core.tools.builtin.externalize import _check_path
from .resources import GovernanceError, now


class Rules:
    def __init__(self, resources, identities):
        self.resources, self.identities, self.db = resources, identities, resources.db

    def get(self, rule_id, agent_id, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        row = connection.execute("SELECT r.*,o.revision,o.change_id FROM agent_permission_rules r JOIN governance_rule_owners o ON o.rule_id=r.id WHERE r.id=? AND r.agent_id=?", (rule_id, agent_id)).fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "员工规则不存在", 404)
        return dict(row)

    def gate(self, agent_id, invocation, context, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        if invocation.name in {"read_file", "write_file"}:
            boundary = _check_path(context, invocation.input["path"], read_only=invocation.name == "read_file")
            if boundary:
                return GateResult("deny", boundary)
        metadata = build_default_registry().get(invocation.name).metadata
        rows = connection.execute("SELECT agent_id,tool_name,pattern,effect FROM agent_permission_rules WHERE agent_id=? AND revoked_at IS NULL", (agent_id,)).fetchall()
        return evaluate_gate(metadata, invocation.input, metadata.read_only, [PermissionRule(*row) for row in rows], agent_id, context.cwd)

    def normalize(self, agent_id, tool_name, pattern, effect, conn):
        agent = self.resources.get("agent", agent_id, conn)
        if agent["status"] != "active":
            raise GovernanceError("OUT_OF_SCOPE", "员工已禁用或归档", 403)
        if tool_name in {"write_file", "read_file"}:
            path = Path(pattern).expanduser()
            if not path.is_absolute():
                raise GovernanceError("VALIDATION_ERROR", "目录规则需要绝对路径", 422)
            path = path.resolve()
            if effect == "allow" and path_is_protected(path, build_protected_paths(self.resources.data_dir)):
                raise GovernanceError("OUT_OF_SCOPE", "保护目录不能授予允许规则", 403)
            return str(path)
        if tool_name == "bash":
            return pattern
        if tool_name == "schedule_task" and pattern == tool_name:
            return pattern
        if tool_name in {"trace_read", "session_search"} and effect == "deny":
            return pattern
        if tool_name.startswith("mcp_"):
            selected = conn.execute("SELECT c.id FROM connector_tools t JOIN connectors c ON c.id=t.connector_id AND c.revision=t.connector_revision JOIN grants g ON g.resource_type='connector' AND g.resource_id=c.id WHERE t.stable_name=? AND c.workspace_id=? AND c.status='active' AND g.grantee_type='agent' AND g.grantee_id=? AND g.revoked_at IS NULL", (tool_name, agent["workspace_id"], agent_id)).fetchone()
            if selected is None or pattern != tool_name:
                raise GovernanceError("OUT_OF_SCOPE", "MCP规则必须绑定当前已授权工具的稳定名称", 403)
            return pattern
        if tool_name == "http_request":
            if any(value in pattern for value in ("/", ":", "@", "?", "#")) or "*" in pattern.removeprefix("*."):
                raise GovernanceError("VALIDATION_ERROR", "HTTP规则需要主机域名", 422)
            pattern = ("*." if pattern.startswith("*.") else "") + pattern.removeprefix("*.").rstrip(".").encode("idna").decode("ascii").lower()
            connectors = conn.execute("SELECT c.config FROM connectors c JOIN grants g ON g.resource_type='connector' AND g.resource_id=c.id WHERE g.grantee_type='agent' AND g.grantee_id=? AND g.revoked_at IS NULL AND c.status='active' AND c.type='http'", (agent_id,)).fetchall()
            patterns = [host for row in connectors for host in json.loads(row[0])["allowed_hosts"]]
            target = "probe." + pattern[2:] if pattern.startswith("*.") else pattern
            allowed = [host for host in patterns if not pattern.startswith("*.") or host.startswith("*.")]
            if effect == "allow" and not host_allowed("https://" + target, allowed)[0]:
                raise GovernanceError("OUT_OF_SCOPE", "HTTP允许规则超出当前连接器授权范围", 403)
            return pattern
        raise GovernanceError("VALIDATION_ERROR", "该工具不支持持久权限规则", 422)

    def insert(self, conn, rule_id, agent_id, request, actor_id):
        pattern = self.normalize(agent_id, request["tool_name"], request["pattern"], request["effect"], conn)
        conn.execute("INSERT INTO agent_permission_rules(id,agent_id,tool_name,pattern,effect,created_by_user_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (rule_id, agent_id, request["tool_name"], pattern, request["effect"], actor_id, now()))
        conn.execute("INSERT INTO governance_rule_owners(rule_id,agent_id,created_by_user_id,change_id) VALUES(?,?,?,?)",
            (rule_id, agent_id, actor_id, request["change_id"]))
        return self.get(rule_id, agent_id, conn)

    @staticmethod
    def event(value):
        return {key: value[key] for key in ("agent_id", "tool_name", "effect", "revoked_at")}

    async def create(self, identity, agent_id, request):
        agent = self.resources.get("agent", agent_id)
        rule_id = uuid.uuid5(uuid.NAMESPACE_URL, "agentcrew:rule:" + request["change_id"]).hex
        def operation(conn):
            value = self.insert(conn, rule_id, agent_id, request, identity.effective_user_id)
            return value, self.resources.scope("agent", agent_id, conn), self.event(value)
        return await self.resources.mutate(change_id=request["change_id"], actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request=request, action="governance.rule_created",
            kind="permission_rule", resource_id=rule_id, operation=operation, event_type=T.GOVERNANCE_RULE_CHANGED,
            authorize=lambda conn: self.identities.require(identity, "manage", agent["workspace_id"], conn))

    async def revoke(self, identity, agent_id, rule_id, request):
        agent = self.resources.get("agent", agent_id)
        def operation(conn):
            prior = self.get(rule_id, agent_id, conn)
            if prior["revision"] != request["expected_revision"]:
                raise GovernanceError("REVISION_CONFLICT", "规则修订已经变化")
            if prior["revoked_at"] is None:
                conn.execute("UPDATE agent_permission_rules SET revoked_at=? WHERE id=?", (now(), rule_id))
                conn.execute("UPDATE governance_rule_owners SET revision=revision+1 WHERE rule_id=?", (rule_id,))
            value = self.get(rule_id, agent_id, conn)
            return value, self.resources.scope("agent", agent_id, conn), {**self.event(value), "revocation_changed": prior["revoked_at"] is None}
        return await self.resources.mutate(change_id=request["change_id"], actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request=request, action="governance.rule_revoked",
            kind="permission_rule", resource_id=rule_id, operation=operation, event_type=T.GOVERNANCE_RULE_CHANGED,
            authorize=lambda conn: self.identities.require(identity, "manage", agent["workspace_id"], conn))
