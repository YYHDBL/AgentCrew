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
