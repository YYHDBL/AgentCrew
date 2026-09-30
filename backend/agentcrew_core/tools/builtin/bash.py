"""bash：Seatbelt 最小 profile + env 白名单 + 超时/取消杀组。纯搬移自 builtin.py。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from ..judgment import bash_readonly
from ..metadata import ToolInvocation, ToolResult, WorkContext
from .externalize import HARD_OUTPUT_LIMIT, INLINE_OUTPUT_LIMIT, _externalize
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
    command = inv.input.get("command", "")
    timeout_ms = int(inv.input.get("timeout_ms", 60_000))
    readonly, verdict_reason = bash_readonly(command, ctx.cwd)
    # 受保护路径全部进 deny 列表（不按 exists() 过滤——尚未创建的
    # config.json 等同样要挡，内核对不存在路径的 deny 实测生效，F07）
    protected_real = [str(Path(p).resolve()) for p in ctx.protected]
    profile = seatbelt_profile(
        [str(Path(p).resolve()) for p in ctx.scope], protected_real,
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

    stdout_buf = bytearray()
    stderr_buf = bytearray()

    async def _pump_stdout() -> bool:
        if await _pump(proc.stdout, stdout_buf, HARD_OUTPUT_LIMIT):
            _kill_group()  # 触顶即杀：写满管道的进程不会自己退出
            return True
        return False

    try:
        await asyncio.wait_for(asyncio.gather(
            _pump_stdout(),
            _pump(proc.stderr, stderr_buf, 64 * 1024),
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
    truncated = len(stdout_buf) >= HARD_OUTPUT_LIMIT
    out = stdout_buf.decode("utf-8", errors="replace")
    err_text = stderr_buf.decode("utf-8", errors="replace").strip()
    pointer = await _externalize(ctx, inv.call_id, out)
    if pointer is None and len(out.encode("utf-8")) > INLINE_OUTPUT_LIMIT \
            and ctx.artifacts_dir is None:
        return ToolResult(ok=False, error="EXTERNALIZATION_UNAVAILABLE",
                          details={"bytes": len(out.encode("utf-8"))})
    ok = proc.returncode == 0
    result = ToolResult(
        ok=ok,
        output=(out if pointer is None else
                f"[输出超限，已外部化] {pointer}"),
        artifact_path=pointer,
        error=None if ok else f"EXIT_{proc.returncode}",
        details={
            "exit_code": proc.returncode, "read_only_verdict": readonly,
            "verdict_reason": verdict_reason, "stderr": err_text[-500:],
        },
    )
    if truncated:
        result.details["truncated"] = True
    return result
