"""请求身份和角色规则，持久化授权由服务提供当前视图。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestIdentity:
    credential_owner_id: str
    effective_user_id: str
    demo: bool = False


def role_allows(role, operation):
    if role == "owner":
        return True
    if role == "admin":
        return operation in {"use", "manage", "audit_read"}
    if role == "member":
        return operation in {"use", "audit_read"}
    return False


def grant_types_allowed(resource_type, grantee_type):
    return (grantee_type == "agent" and resource_type in {"skill", "connector"}) or \
        (grantee_type == "user" and resource_type == "agent")


def capability_active(resource_workspace, resource_status, grant, workspace):
    return resource_workspace == workspace and resource_status == "active" and grant is not None


def governance_event_visible(kind, payload, identity, current, visible_agents, workspace=None):
    scope = payload.get("scope", {})
    if scope.get("org_id") != current["org_id"]:
        return False
    if workspace is not None and scope.get("workspace_id") != workspace:
        return False
    if current["role"] == "owner":
        return True
    actor = identity.effective_user_id
    if payload.get("resource_type") == "workspace_member" and scope.get("owner_id") == actor:
        return True
    affected_user = payload.get("user_id") or (payload.get("grantee_id") if payload.get("grantee_type") == "user" else None)
    if kind in {"governance.role_changed", "governance.identity_changed", "governance.audit_verified", "governance.backup_created", "governance.backup_restored"}:
        return affected_user == actor or payload.get("actor_id") == actor
    scope_workspace = scope.get("workspace_id")
    if scope_workspace is not None and scope_workspace not in current["workspace_ids"]:
        return False
    if current["role"] == "admin":
        return scope_workspace is not None
    if affected_user == actor or payload.get("actor_id") == actor or scope.get("owner_id") == actor:
        return True
    return scope_workspace is not None and scope.get("agent_id") in visible_agents


def filter_tool_schemas(schemas, http_connector_ids):
    import copy
    result = []
    for original in schemas:
        if original["name"] == "http_request":
            if not http_connector_ids:
                continue
            schema = copy.deepcopy(original)
            schema["input_schema"]["properties"]["connector_id"] = {"type": "string", "enum": http_connector_ids}
            if len(http_connector_ids) > 1:
                schema["input_schema"]["required"] = [*schema["input_schema"].get("required", []), "connector_id"]
            result.append(schema)
        elif not original["name"].startswith("mcp_"):
            result.append(original)
    return result
