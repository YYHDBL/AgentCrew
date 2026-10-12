"""连接器URL、地址、请求头及幂等分类的纯领域规则。"""

import ipaddress
from urllib.parse import urlsplit

from .tools.judgment import host_allowed


class ConnectorBoundaryError(ValueError):
    def __init__(self, message, *, invalid=False):
        self.code = "VALIDATION_ERROR" if invalid else "OUT_OF_SCOPE"
        self.status = 422 if invalid else 403
        super().__init__(message)


def connector_target(url, config):
    target = urlsplit(url)
    if target.scheme not in {"http", "https"} or not target.hostname or target.username is not None or target.password is not None or target.fragment:
        raise ConnectorBoundaryError("连接器URL必须使用HTTP(S)，禁止userinfo和fragment", invalid=True)
    host = target.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    port = target.port or (443 if target.scheme == "https" else 80)
    patterns = [("*." if pattern.startswith("*.") else "") + pattern.removeprefix("*.").rstrip(".").encode("idna").decode("ascii").lower()
        for pattern in config["allowed_hosts"]]
    allowed, _reason = host_allowed(f"{target.scheme}://{host if ':' not in host else '[' + host + ']'}:{port}", patterns)
    if not allowed or port not in config["allowed_ports"]:
        raise ConnectorBoundaryError("连接器主机或端口未授权")
    return target.scheme, host, port


def connector_address_allowed(address, allow_loopback):
    value = ipaddress.ip_address(address)
    return value.is_global or (allow_loopback and value.is_loopback)


def connector_headers(headers, credential_header):
    forbidden = {"authorization", "proxy-authorization", "cookie", "host", "idempotency-key", credential_header.lower(),
        "content-length", "transfer-encoding", "connection", "trailer", "upgrade"}
    if any(name.lower() in forbidden for name in headers):
        raise ConnectorBoundaryError("模型不能指定连接器认证、Host或幂等信息")
    return {str(name): str(value) for name, value in headers.items()}


def connector_effect(method, url, config):
    target = urlsplit(url)
    if any(endpoint["method"] == method.upper() and endpoint["path"] == target.path and endpoint["guarantee"]
            for endpoint in config.get("idempotent_endpoints", [])):
        return "external_idempotency"
    return "outcome_unknown"


def validate_schema_references(schema):
    pending = [schema]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key in ("$ref", "$dynamicRef"):
                if key in item and not item[key].startswith("#"):
                    raise ConnectorBoundaryError("MCP参数schema不能引用外部网络资源", invalid=True)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
