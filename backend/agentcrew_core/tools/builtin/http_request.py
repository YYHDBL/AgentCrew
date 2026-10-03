"""http_request：主机范围、外部幂等键和完整响应流保存。"""

from __future__ import annotations

import asyncio
import sys

import httpx

from ..judgment import host_allowed
from ..metadata import ToolInvocation, ToolResult, WorkContext

HTTP_SCHEMA = {
    "type": "object",
    "properties": {
        "method": {"type": "string", "description": "HTTP 方法（默认 GET）"},
        "url": {"type": "string", "description": "目标 URL"},
        "headers": {"type": "object", "description": "附加请求头"},
        "body": {"type": "string", "description": "请求体"},
    },
    "required": ["url"],
}

HTTP_TIMEOUT_S = 30.0

async def _http_request(inv: ToolInvocation, ctx: WorkContext) -> ToolResult:
    url = inv.input.get("url", "")
    allowed, reason = host_allowed(url, ctx.allowed_hosts)
    if not allowed and inv.call_id not in ctx.approved_calls:
        # allowed_hosts 是预授权清单（M0 默认空 = 全部需审批）；本次调用
        # 已经三级闸门（人工/规则）授权的，执行器不再硬拒（外审回稿 S02）
        return ToolResult(ok=False, error=f"HOST_NOT_ALLOWED：{reason}")
    method = (inv.input.get("method") or "GET").upper()
    headers = dict(inv.input.get("headers") or {})
    headers.setdefault(  # 外部幂等键由 (task_run_id, call_id) 派生（v1.3）
        "Idempotency-Key", f"{ctx.task_run_id}:{inv.call_id}")
    body = inv.input.get("body")
    if ctx.output_store is None:
        raise RuntimeError("EXTERNALIZATION_UNAVAILABLE：缺少工具工件服务")
    buf = ctx.output_store.capture(ctx.artifacts_dir)
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            async with client.stream(method, url, headers=headers, content=body) as resp:
                status_code = resp.status_code
                content_type = resp.headers.get("content-type", "")
                async for chunk in resp.aiter_bytes():
                    await asyncio.to_thread(buf.write, chunk)
    finally:
        if sys.exception() is not None:
            buf.close()
    ok = 200 <= status_code < 300
    return ToolResult(
        ok=ok,
        output_source=buf,
        error=None if ok else f"HTTP_{status_code}",
        details={"status_code": status_code, "content_type": content_type},
    )
