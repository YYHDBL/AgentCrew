"""read_file：分页读（惰性按行、拾取字节达硬上限即停扫）。纯搬移。"""

from __future__ import annotations

from pathlib import Path

from ..metadata import ToolInvocation, ToolResult, WorkContext
from .externalize import (HARD_OUTPUT_LIMIT, INLINE_OUTPUT_LIMIT,
                         _check_path, _externalize, _resolve_input_path)

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
    if err := _check_path(ctx, raw):
        return ToolResult(ok=False, error=err)
    path = _resolve_input_path(ctx, raw).resolve()
    if not path.is_file():
        return ToolResult(ok=False, error=f"NOT_FOUND：{path}")
    offset = max(1, int(inv.input.get("offset", 1)))
    limit = min(READ_FILE_MAX_LINES, max(1, int(inv.input.get("limit", 200))))
    # 惰性按行读：只取本页，不整读大文件；拾取字节达到硬上限即停扫
    # （不再为统计 total_lines 扫完整个文件，S04）
    total_lines = 0
    picked: list[str] = []
    picked_bytes = 0
    scan_truncated = False
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for total_lines, line in enumerate(fh, start=1):
            if offset <= total_lines < offset + limit:
                picked.append(line.rstrip("\n"))
                picked_bytes += len(line.encode("utf-8"))
                if picked_bytes >= HARD_OUTPUT_LIMIT:
                    scan_truncated = True
                    break
    content = "\n".join(picked)
    pointer = await _externalize(ctx, inv.call_id, content)
    if pointer is None and len(content.encode("utf-8")) > INLINE_OUTPUT_LIMIT \
            and ctx.artifacts_dir is None:
        return ToolResult(ok=False, error="EXTERNALIZATION_UNAVAILABLE",
                          details={"bytes": len(content.encode("utf-8"))})
    details: dict = {"lines": f"{offset}-{offset - 1 + len(picked)}",
                     "total_lines": total_lines}
    if scan_truncated:
        details["truncated"] = True  # total_lines 是下界（提前停扫）
    return ToolResult(
        ok=True,
        output=content if pointer is None else
        f"[输出 {len(content.encode('utf-8'))} 字节超限，已外部化] {pointer}",
        artifact_path=pointer,
        details=details,
    )
