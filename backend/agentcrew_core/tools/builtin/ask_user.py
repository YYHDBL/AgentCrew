"""ask_user：交互原语，question.* 事件 + 等待回答通道。纯搬移。"""

from __future__ import annotations

from ..metadata import ToolInvocation, ToolResult, WorkContext

ASK_USER_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "要向用户提出的问题"},
        "options": {"type": "array", "items": {"type": "string"},
                     "description": "可选的候选项"},
    },
    "required": ["question"],
}

async def _ask_user(inv: ToolInvocation, ctx: WorkContext) -> ToolResult:
    question = inv.input.get("question", "")
    options = list(inv.input.get("options") or [])
    if ctx.ask_resolver is None:
        return ToolResult(ok=False, error="NOT_WIRED：ask_user 未接入运行时（C8）")
    if ctx.emit is not None:
        await ctx.emit("question.requested", {
            "request_id": inv.call_id, "question": question, "options": options,
        })
    answer = await ctx.ask_resolver(inv.call_id)
    if ctx.emit is not None:
        await ctx.emit("question.answered", {
            "request_id": inv.call_id, "answer": answer,
        })
    return ToolResult(
        ok=True,
        output=answer if answer is not None else "（用户取消了回答）",
        details={"cancelled": answer is None},
    )
