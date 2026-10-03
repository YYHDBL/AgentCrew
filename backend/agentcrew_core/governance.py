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
