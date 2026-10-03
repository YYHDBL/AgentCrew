"""read_file：完整读取所选分页，输出保存由公共调度器处理。"""

from __future__ import annotations

import sys

from ..metadata import ToolInvocation, ToolResult, WorkContext
from .externalize import _check_path, _resolve_input_path

READ_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "要读取的文件路径"},
        "offset": {"type": "integer", "description": "起始行号（1 起，默认 1）"},
        "limit": {"type": "integer", "description": "读取行数（默认 200）"},
    },
    "required": ["path"],
}

READ_FILE_MAX_LINES = 10_000  # 单页行数上限（分页参数钳制，外审回稿 S04）

async def _read_file(inv: ToolInvocation, ctx: WorkContext) -> ToolResult:
    raw = inv.input.get("path", "")
    if err := _check_path(ctx, raw, read_only=True):
        return ToolResult(ok=False, error=err)
    path = _resolve_input_path(ctx, raw).resolve()
    if not path.is_file():
        return ToolResult(ok=False, error=f"NOT_FOUND：{path}")
    if ctx.output_store is None:
        raise RuntimeError("EXTERNALIZATION_UNAVAILABLE：缺少工具工件服务")
    offset = max(1, int(inv.input.get("offset", 1)))
    limit = min(READ_FILE_MAX_LINES, max(1, int(inv.input.get("limit", 200))))
    output = ctx.output_store.capture(ctx.artifacts_dir)
    try:
        lines, total_lines = await ctx.output_store.read_page(path, offset, limit, output,
            expected=ctx.readable_artifacts.get(str(path)))
    finally:
        if sys.exception() is not None:
            output.close()
    return ToolResult(ok=True, output_source=output,
        details={"lines": f"{offset}-{offset - 1 + lines}", "total_lines": total_lines})
