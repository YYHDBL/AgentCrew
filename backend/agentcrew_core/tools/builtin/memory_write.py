"""受控三库工具；持久化能力由服务装配注入。"""

import json

from ..metadata import ToolInvocation, ToolResult, WorkContext

MEMORY_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": "string", "enum": ["user", "workspace", "soul"]},
        "action": {"type": "string", "enum": ["read", "add", "edit", "archive", "restore", "pin", "unpin", "batch"]},
        "expected_revision": {"type": "integer", "minimum": 0},
        "entry_hash": {"type": "string"}, "text": {"type": "string"},
        "basis": {"type": "string"},
        "operations": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["target", "action"],
}


async def _memory_write(inv: ToolInvocation, ctx: WorkContext) -> ToolResult:
    if ctx.memory_writer is None:
        return ToolResult(ok=False, error="MEMORY_UNAVAILABLE")
    result = await ctx.memory_writer(inv, ctx)
    return ToolResult(ok="error" not in result, output=json.dumps(result, ensure_ascii=False),
                      error=result.get("error"), details=result)
