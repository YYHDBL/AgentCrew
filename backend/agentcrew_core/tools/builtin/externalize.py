"""工具路径检查及统一工件化策略；文件持久化由服务层执行。"""

from __future__ import annotations

from pathlib import Path

from ..judgment import filesystem_boundary, path_in_scope, path_is_protected
from ..metadata import ToolResult, WorkContext

INLINE_OUTPUT_LIMIT = 32 * 1024

def _resolve_input_path(ctx: WorkContext, raw: str) -> Path:
    """工具入参路径统一解析（S09）：相对路径以任务工作目录为基。"""
    path = Path(raw).expanduser()
    if not path.is_absolute() and ctx.cwd is not None:
        path = Path(ctx.cwd) / path
    return path

def _check_path(ctx: WorkContext, raw: str, *, read_only: bool = False) -> str | None:
    path = _resolve_input_path(ctx, raw)
    boundary = filesystem_boundary(ctx)
    if path_is_protected(path, boundary.protected, normalized=True):
        return f"PROTECTED_PATH：{path.resolve()}"
    if not read_only and path_in_scope(path, boundary.readonly_roots, normalized=True):
        return f"OUT_OF_SCOPE：{path.resolve()} 属于只读授权目录"
    if read_only and str(path.resolve()) in ctx.readable_artifacts:
        return None
    if not path_in_scope(path, boundary.read_roots if read_only else boundary.write_roots, normalized=True):
        return f"OUT_OF_SCOPE：{path.resolve()} 不在任务合法范围内"
    return None

async def _externalize(ctx: WorkContext, call_id: str, result: ToolResult,
                       max_output_bytes: int) -> None:
    """所有工具共享工件策略，服务层负责完整字节的持久化。"""
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes 必须大于零")
    max_output_bytes = min(max_output_bytes, INLINE_OUTPUT_LIMIT)
    if result.output_source is None and result.stderr_source is None \
            and len(result.output.encode("utf-8")) <= max_output_bytes:
        return
    if ctx.output_store is None:
        raise RuntimeError("EXTERNALIZATION_UNAVAILABLE：缺少工具工件服务")
    await ctx.output_store.finish(ctx, call_id, result, max_output_bytes)
