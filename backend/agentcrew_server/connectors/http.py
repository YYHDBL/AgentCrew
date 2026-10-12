"""HTTPX真实传输：DNS固定、目标复查及服务端凭据绑定。"""

import asyncio
import socket
from urllib.parse import urljoin
from contextlib import ExitStack

import httpx

from agentcrew_core.connectors import ConnectorBoundaryError, connector_address_allowed, connector_effect, connector_headers, connector_target
from agentcrew_core.tools.metadata import ToolResult
from ..secrets import known_secrets, redact


class ConnectorTransport(httpx.AsyncBaseTransport):
    def __init__(self, connector, credential=None, authorize=None):
        self.connector, self.credential, self.authorize = connector, credential, authorize
        self.backends = {}

    async def handle_async_request(self, request):
        config = self.connector["config"]
        scheme, host, port = connector_target(str(request.url), config)
        addresses = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses or any(not connector_address_allowed(entry[4][0], config["allow_loopback"]) for entry in addresses):
            raise ConnectorBoundaryError("连接器DNS解析到未授权地址")
        address = sorted({entry[4][0] for entry in addresses}, key=lambda value: (":" in value, value))[0]
        if self.authorize is not None:
            self.authorize()
        original = httpx.URL(request.url)
        headers = httpx.Headers(request.headers)
        headers["Host"] = original.netloc.decode("ascii")
        credential_header = config.get("credential_header", "Authorization")
        for key in ("Authorization", "Cookie", "Proxy-Authorization", credential_header):
            headers.pop(key, None)
        origin = connector_target(config["url"], config)
        if self.credential and (scheme, host, port) == origin:
            headers[credential_header] = self.credential
        extensions = {**request.extensions, "sni_hostname": host}
        pinned = httpx.Request(request.method, request.url.copy_with(host=address), headers=headers, stream=request.stream, extensions=extensions)
        key = scheme, host, port
        if key not in self.backends:
            self.backends[key] = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        return await self.backends[key].handle_async_request(pinned)

    async def aclose(self):
        for backend in self.backends.values():
            await backend.aclose()


def connector_client(connector, credential=None, authorize=None):
    return httpx.AsyncClient(transport=ConnectorTransport(connector, credential, authorize), timeout=30,
        trust_env=False, follow_redirects=False)


async def request_http(invocation, context, connector, credential, authorize):
    with ExitStack() as captures:
        capture = captures.enter_context(context.output_store.capture(context.artifacts_dir))
        result = await _request_http(invocation, context, connector, credential, authorize, capture)
        captures.pop_all()
        return result


async def _request_http(invocation, context, connector, credential, authorize, capture):
    config = connector["config"]
    url = invocation.input["url"]
    method = invocation.input.get("method", "GET").upper()
    headers = connector_headers(invocation.input.get("headers") or {}, config.get("credential_header", "Authorization"))
    body = invocation.input.get("body")
    if connector_effect(method, url, config) == "external_idempotency":
        headers["Idempotency-Key"] = f"{context.task_run_id}:{invocation.call_id}"
    async with connector_client(connector, credential, authorize) as client:
        for redirect in range(6):
            async with client.stream(method, url, headers=headers, content=body) as response:
                if response.is_redirect:
                    if connector_effect(method, url, config) == "external_idempotency":
                        raise ConnectorBoundaryError("承诺外部幂等的写入端点禁止重定向")
                    if redirect == 5:
                        raise ConnectorBoundaryError("连接器重定向次数超限")
                    target = urljoin(url, response.headers["location"])
                    connector_target(target, config)
                    if connector_target(target, config) != connector_target(url, config) and body is not None:
                        raise ConnectorBoundaryError("跨来源重定向不能转发请求正文")
                    if response.status_code in {301, 302, 303} and method not in {"GET", "HEAD"}:
                        raise ConnectorBoundaryError("写操作重定向不能改变方法")
                    url = target
                    continue
                content = bytearray()
                async for part in response.aiter_bytes():
                    content.extend(part)
                    if len(content) > 8 * 1024 * 1024:
                        raise ConnectorBoundaryError("HTTP连接器响应超出8MiB限制", invalid=True)
                safe = bytes(content)
                for secret in known_secrets():
                    safe = safe.replace(secret.encode(), b"***")
                await asyncio.to_thread(capture.write, safe)
                return ToolResult(ok=response.is_success, error=None if response.is_success else f"HTTP_{response.status_code}",
                    output_source=capture, details={"status_code": response.status_code, "connector_id": connector["id"],
                        "content_type": redact(response.headers.get("content-type", "")), "target": redact(url)})
    raise RuntimeError("HTTP连接器缺少最终响应")
