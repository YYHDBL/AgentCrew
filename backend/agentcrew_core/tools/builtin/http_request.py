"""http_request：allowed_hosts 判定 + 外部幂等键 + 流式限额读取。纯搬移。"""

from __future__ import annotations

import httpx

from ..judgment import host_allowed
from ..metadata import ToolInvocation, ToolResult, WorkContext
from .externalize import (HARD_OUTPUT_LIMIT, INLINE_OUTPUT_LIMIT,
                         _externalize)

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
    buf = bytearray()
    truncated = False
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        # 流式读取至硬上限即止（S04）：不先整读再截断
        async with client.stream(
            method, url, headers=headers, content=body,
        ) as resp:
            status_code = resp.status_code
            content_type = resp.headers.get("content-type", "")
            async for chunk in resp.aiter_bytes():
                room = HARD_OUTPUT_LIMIT - len(buf)
                if room <= 0:
                    truncated = True
                    break
                buf.extend(chunk[:room])
    text = bytes(buf).decode("utf-8", errors="replace")
    pointer = await _externalize(ctx, inv.call_id, text)
    if pointer is None and len(text.encode("utf-8")) > INLINE_OUTPUT_LIMIT \
            and ctx.artifacts_dir is None:
        return ToolResult(ok=False, error="EXTERNALIZATION_UNAVAILABLE",
                          details={"status_code": status_code})
    ok = 200 <= status_code < 300
    return ToolResult(
        ok=ok,
        output=(text if pointer is None else
                f"[输出超限，已外部化] {pointer}"),
        artifact_path=pointer,
        error=None if ok else f"HTTP_{status_code}",
        details={"status_code": status_code, "content_type": content_type,
                 **({"truncated": True} if truncated else {})},
    )
