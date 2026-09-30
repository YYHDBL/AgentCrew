"""成功响应信封中间件：JSON 成功响应统一包裹为 {"data": ...}（backend-service.md §6）。

规则（契约 docs/contracts/openapi.yaml 文件头）：
- 204/304 与 text/event-stream（SSE 流）不套信封；
- 响应体顶层已有 data 或 error 键的（错误信封 / 已包裹）原样通过；
- 纯 ASGI 实现（不用 BaseHTTPMiddleware），对 C3 的流式 SSE 透明。
"""

from __future__ import annotations

import json
import typing as t

_PASS_STATUS = frozenset({204, 304})
EnvelopeedApp = t.Callable[..., t.Awaitable[None]]


class EnvelopeMiddleware:
    def __init__(self, app: EnvelopeedApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        passthrough = False
        status = 200
        headers: list = []
        chunks: list[bytes] = []

        async def send_wrapper(message) -> None:
            nonlocal passthrough, status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers.extend(message.get("headers", []))
                content_type = _header(headers, b"content-type")
                passthrough = (
                    status in _PASS_STATUS
                    or content_type.startswith(b"text/event-stream")
                )
                if passthrough:
                    await send(message)
                return
            if message["type"] != "http.response.body":
                await send(message)  # 其余消息类型（trailers 等）原样透传
                return
            if passthrough:
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return
            body = b"".join(chunks)
            wrapped = _maybe_wrap(status, _header(headers, b"content-type"), body)
            if wrapped is not body:
                _set_header(headers, b"content-length", str(len(wrapped)).encode())
                body = wrapped
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, send_wrapper)


def _header(headers: list, name: bytes) -> bytes:
    for key, value in headers:
        if key.lower() == name:
            return value
    return b""


def _set_header(headers: list, name: bytes, value: bytes) -> None:
    for i, (key, _) in enumerate(headers):
        if key.lower() == name:
            headers[i] = (key, value)
            return
    headers.append((name, value))


def _maybe_wrap(status: int, content_type: bytes, body: bytes) -> bytes:
    """JSON 成功响应 → {"data": ...}；其余原样返回（同一对象，按 is 判定）。"""
    if status >= 400 or not content_type.startswith(b"application/json") or not body:
        return body
    try:
        parsed = json.loads(body)
    except ValueError:
        return body
    if isinstance(parsed, dict) and ("data" in parsed or "error" in parsed):
        return body
    if not isinstance(parsed, (dict, list)):
        return body
    return json.dumps({"data": parsed}, ensure_ascii=False, separators=(",", ":")).encode()
