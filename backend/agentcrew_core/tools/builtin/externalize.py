"""路径解析（任务 cwd）/受保护与 scope 硬检/大输出外部化——五件套共享件。纯搬移自 builtin.py。"""

from __future__ import annotations

from pathlib import Path

from ..judgment import path_in_scope, path_is_protected
from ..metadata import WorkContext

INLINE_OUTPUT_LIMIT = 32 * 1024

HARD_OUTPUT_LIMIT = 100_000  # 超限截断（内存与上下文双保护）

def _resolve_input_path(ctx: WorkContext, raw: str) -> Path:
    """工具入参路径统一解析（S09）：相对路径以任务工作目录为基。"""
    path = Path(raw).expanduser()
    if not path.is_absolute() and ctx.cwd is not None:
        path = Path(ctx.cwd) / path
    return path

def _check_path(ctx: WorkContext, raw: str) -> str | None:
    path = _resolve_input_path(ctx, raw)
    if path_is_protected(path, ctx.protected):
        return f"PROTECTED_PATH：{path.resolve()}"
    if not path_in_scope(path, ctx.scope):
        return f"OUT_OF_SCOPE：{path.resolve()} 不在任务合法范围内"
    return None

async def _externalize(ctx: WorkContext, call_id: str, content: str) -> str | None:
    """>32KB 输出落工件文件并计 artifacts 投影；artifacts_dir 未配置则明确报错。

    返回工件路径；不可外部化（未配置）返回 None 且由调用方报错——绝不把
    超限输出内联塞进上下文（外审回稿）。
    """
    if len(content.encode("utf-8")) <= INLINE_OUTPUT_LIMIT:
        return None
    if ctx.artifacts_dir is None:
        return None  # 调用方据 content 长度判断并报 EXTERNALIZATION_UNAVAILABLE
    ctx.artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = ctx.artifacts_dir / f"{call_id}.out"
    artifact_path.write_text(content, encoding="utf-8")
    if ctx.emit is not None:
        await ctx.emit("artifact.created", {
            "artifact_id": call_id, "task_run_id": ctx.task_run_id,
            "tool_call_id": call_id, "path": str(artifact_path),
            "name": artifact_path.name, "ext": ".out",
        })
        await ctx.emit("artifact.ready", {
            "artifact_id": call_id, "size_bytes": artifact_path.stat().st_size,
        })
    return str(artifact_path)
