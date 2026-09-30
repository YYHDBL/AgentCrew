"""write_file：O_NOFOLLOW 钉住父目录 + 原子写 + artifact 事件。纯搬移。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import IO

from ..metadata import ToolInvocation, ToolResult, WorkContext
from .externalize import _check_path, _resolve_input_path

WRITE_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "目标文件路径"},
        "content": {"type": "string", "description": "完整写入内容"},
    },
    "required": ["path", "content"],
}

def _write_file_extras(input: dict) -> dict:
    """prepared 载荷附内容 sha256（v1.7：verifiable 类核验依据，记于执行前）。"""
    content = input.get("content", "")
    return {"content_sha256":
            hashlib.sha256(content.encode("utf-8")).hexdigest()}

def _open_dir_nofollow(target: Path) -> int:
    """逐组件 O_NOFOLLOW 打开 target 的父目录并返回 fd（外审回稿 F08）。

    scope 检查已 realpath（此刻路径上没有符号链接）；此后任何组件被换成
    符号链接都属于检查与写入之间的篡改——O_NOFOLLOW 直接 ELOOP 拒绝，
    目录 fd 钉住 inode，等待事件落库期间父目录被替换也无法改写落点。
    """
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    for part in target.parent.parts[1:]:
        nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                      dir_fd=fd)
        os.close(fd)
        fd = nxt
    return fd

async def _write_file(inv: ToolInvocation, ctx: WorkContext) -> ToolResult:
    raw = inv.input.get("path", "")
    content = inv.input.get("content", "")
    if err := _check_path(ctx, raw):
        return ToolResult(ok=False, error=err)
    target = _resolve_input_path(ctx, raw).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    sha256 = _write_file_extras(inv.input)["content_sha256"]
    if ctx.emit is not None:  # 生成中 → 写入 → ready（§2.2 artifacts 投影口径）
        await ctx.emit("artifact.created", {
            "artifact_id": inv.call_id, "task_run_id": ctx.task_run_id,
            "tool_call_id": inv.call_id, "path": str(target),
            "name": target.name, "ext": target.suffix,
        })
    # 父目录 fd 钉住 inode：目录 fd 内的创建与替换不受父目录被替换成符号
    # 链接的影响（等待 artifact.created 落库的窗口正是竞态窗口，F08）。
    # 组件遍历遇符号链接（ELOOP）= 检查后被篡改，如实拒绝
    try:
        dir_fd = _open_dir_nofollow(target)
    except OSError as e:
        return ToolResult(ok=False, error=f"PATH_CHANGED：路径在检查后被替换（{e}）")
    tmp_name = f".{target.name}.{inv.call_id}.tmp"
    try:
        fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644,
                     dir_fd=dir_fd)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:  # type: IO[str]
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target.name,
                   src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
        size = os.stat(target.name, dir_fd=dir_fd).st_size
    except BaseException:
        try:
            os.unlink(tmp_name, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(dir_fd)
    if ctx.emit is not None:
        await ctx.emit("artifact.ready", {
            "artifact_id": inv.call_id, "size_bytes": size,
        })
    return ToolResult(
        ok=True, output=f"已写入 {target}（{size} 字节，sha256={sha256[:16]}…）",
        artifact_path=str(target),
        details={"sha256": sha256, "size_bytes": size},
    )
