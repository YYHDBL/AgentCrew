"""Skill 正文及受限支撑文件读取。"""

import json

from ..metadata import ToolInvocation, ToolResult, WorkContext

SKILL_VIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 80}, "file": {"type": "string"}},
    "required": ["name"],
}


async def _skill_view(inv: ToolInvocation, ctx: WorkContext) -> ToolResult:
    if ctx.memory_skills is None:
        return ToolResult(ok=False, error="MEMORY_UNAVAILABLE")
    result = await ctx.memory_skills(inv, ctx)
    return ToolResult(ok="error" not in result, output=json.dumps(result, ensure_ascii=False), error=result.get("error"), details=result)
