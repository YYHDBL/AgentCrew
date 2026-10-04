"""bash：Seatbelt、环境白名单、进程组收尾及完整输出缓冲。"""

from __future__ import annotations

import asyncio
import os
from contextlib import ExitStack
from pathlib import Path

from ..judgment import bash_readonly, filesystem_boundary, path_in_scope
from ..metadata import ToolInvocation, ToolResult, WorkContext
from .seatbelt import _pump, _sandboxed_argv, seatbelt_profile

BASH_ENV_WHITELIST = ("PATH", "HOME", "LANG", "TZ", "TERM")

BASH_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "要执行的 shell 命令"},
        "timeout_ms": {"type": "integer", "description": "超时毫秒（默认 60000）"},
    },
    "required": ["command"],
}

async def _bash(inv: ToolInvocation, ctx: WorkContext) -> ToolResult:
    if ctx.output_store is None:
        raise RuntimeError("EXTERNALIZATION_UNAVAILABLE：缺少工具工件服务")
    with ExitStack() as captures:
        stdout_buf = captures.enter_context(ctx.output_store.capture(ctx.artifacts_dir))
        stderr_buf = captures.enter_context(ctx.output_store.capture(ctx.artifacts_dir))
        result = await _run_bash(inv, ctx, stdout_buf, stderr_buf)
        captures.pop_all()
        return result


async def _run_bash(inv, ctx, stdout_buf, stderr_buf):
    boundary = filesystem_boundary(ctx)
    if ctx.cwd is None or not path_in_scope(Path(ctx.cwd), boundary.read_roots, normalized=True):
        raise ValueError("bash需要合法范围内的明确任务工作目录")
    command = inv.input.get("command", "")
    timeout_ms = int(inv.input.get("timeout_ms", 60_000))
    readonly, verdict_reason = bash_readonly(command, ctx.cwd)
    # 受保护路径全部进 deny 列表（不按 exists() 过滤——尚未创建的
    # config.json 等同样要挡，内核对不存在路径的 deny 实测生效，F07）
    protected_real = [str(path) for path in boundary.protected]
    profile = seatbelt_profile(
        [str(path) for path in boundary.write_roots], protected_real,
        read_realpaths=[str(path) for path in boundary.read_roots],
        readonly_realpaths=[str(path) for path in boundary.readonly_roots],
    )
    env = {k: os.environ[k] for k in BASH_ENV_WHITELIST if k in os.environ}
    proc = await asyncio.create_subprocess_exec(
        *_sandboxed_argv(profile, command),
        env=env,
        cwd=str(ctx.cwd) if ctx.cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,  # 独立进程组：超时/取消/收尾都可整组击杀
    )

    def _kill_group() -> None:
        try:
            os.killpg(proc.pid, 9)
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(asyncio.gather(
            _pump(proc.stdout, stdout_buf),
            _pump(proc.stderr, stderr_buf),
            proc.wait(),
        ), timeout=timeout_ms / 1000)
    except BaseException:
        # 内部超时（TimeoutError）与外部取消（CancelledError）都收割进程组
        _kill_group()
        await proc.wait()
        raise
    # 正常退出也整组击杀（S05）：`sh -c "srv &"` 的 shell 会先退出，后台
    # 子进程留在进程组里继续跑——C8 契约是"所有子进程收割后才写任务终态"
    _kill_group()
    await proc.wait()
    ok = proc.returncode == 0
    result = ToolResult(
        ok=ok,
        output_source=stdout_buf,
        stderr_source=stderr_buf,
        error=None if ok else f"EXIT_{proc.returncode}",
        details={
            "exit_code": proc.returncode, "read_only_verdict": readonly,
            "verdict_reason": verdict_reason,
        },
    )
    return result
