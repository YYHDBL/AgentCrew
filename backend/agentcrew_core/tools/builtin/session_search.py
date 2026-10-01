"""只读历史和记忆检索，当前任务身份由服务注入。"""

import json

from ..metadata import ToolInvocation, ToolResult, WorkContext

SESSION_SEARCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 200},
        "archived": {"type": "boolean", "default": False},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
        "after": {"type": "string"},
    },
    "required": ["query"],
}


async def _session_search(inv: ToolInvocation, ctx: WorkContext) -> ToolResult:
    if ctx.memory_search is None:
        return ToolResult(ok=False, error="MEMORY_UNAVAILABLE")
    result = await ctx.memory_search(inv, ctx)
    return ToolResult(ok="error" not in result, output=json.dumps(result, ensure_ascii=False),
                      error=result.get("error"), details=result)
