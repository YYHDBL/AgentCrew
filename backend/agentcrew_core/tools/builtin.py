"""五个内置工具（harness-session §6.1 五件套 / ADR-008 / v1.7 语义）。

- read_file：分页读（惰性按行取，拾取字节达硬上限即停扫），scope/受保护
  路径硬检；
- write_file：逐组件 O_NOFOLLOW 打开父目录钉住 inode → fd 内独占创建 tmp
  + fsync + rename 原子替换（检查与写入之间父目录被换符号链接也无法改写
  落点）；prepared 附内容 sha256（verifiable 类核验依据）；发 artifact.*
  事件；
- bash：判定结果只影响调度与审批元数据（闸门在 C6）；执行必套 Seatbelt 最小
  profile（写限 scope / 网络全禁 / 受保护路径**读写双向**禁——subpath 一律
  realpath，含尚不存在的路径；SBPL 字面量经转义）；env 白名单仅
  PATH/HOME/LANG/TZ/TERM；超时/取消/收尾都杀进程组（含正常退出后的后台
  子进程）；stdout/stderr 流式限额读取；
- http_request：allowed_hosts 判定 + 三级闸门授权（approved_calls）放行，
  Idempotency-Key=(task_run_id, call_id) 派生；流式限额读取；
- ask_user：交互原语，发 question.* 事件并等待回答通道（挂起全链在 C8）；
- 输出 >32KB 落工件文件留指针；artifacts_dir 未配置时明确报错不内联（外审
  回稿）；超 max_output_bytes 硬上限截断并标记（读取过程中执行限额，不先
  整读后截断）。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import IO

import httpx

from .judgment import bash_readonly, host_allowed, path_in_scope, path_is_protected
from .metadata import Tool, ToolInvocation, ToolResult, WorkContext
from .scheduler import ToolRegistry

INLINE_OUTPUT_LIMIT = 32 * 1024
HARD_OUTPUT_LIMIT = 100_000  # 超限截断（内存与上下文双保护）
BASH_ENV_WHITELIST = ("PATH", "HOME", "LANG", "TZ", "TERM")
HTTP_TIMEOUT_S = 30.0


# ── 公共：路径硬检与输出外部化 ───────────────────────────────────

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


# ── read_file ─────────────────────────────────────────────────────

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


# ── write_file ────────────────────────────────────────────────────

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


# ── bash（Seatbelt + env 白名单 + 超时/取消杀组）──────────────────

BASH_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "要执行的 shell 命令"},
        "timeout_ms": {"type": "integer", "description": "超时毫秒（默认 60000）"},
    },
    "required": ["command"],
}


def _sbpl_quote(path: str) -> str:
    """SBPL 字符串字面量转义（外审回稿 S06，macOS 实测）：`\"` 生效（未转义
    的双引号会让 profile 解析失败，含规则语法的路径名还可能改变生成内容）；
    反斜杠先转。"""
    return path.replace("\\", "\\\\").replace('"', '\\"')


def seatbelt_profile(scope_realpaths: list[str],
                     deny_realpaths: list[str]) -> str:
    """最小 profile（ADR-008）：读默认放开（读取强制边界 M2），写限 scope，
    网络全禁，受保护路径**读写双向**禁（嵌套 subpath 的 deny 压过外层 allow，
    实测验证；deny 对尚不存在的路径同样生效——创建即被拒，实测验证）。SBPL
    规则：特定 subpath 覆盖泛化规则。"""
    rules = ['(version 1)', '(allow default)', '(deny network*)', '(deny file-write*)']
    rules += [f'(allow file-write* (subpath "{_sbpl_quote(p)}"))'
              for p in scope_realpaths]
    # 双向禁：先禁写（含 scope 内受保护路径），再禁读
    rules += [f'(deny file-write* (subpath "{_sbpl_quote(p)}"))'
              for p in deny_realpaths]
    rules += [f'(deny file-read* (subpath "{_sbpl_quote(p)}"))'
              for p in deny_realpaths]
    return "\n".join(rules)


def _sandboxed_argv(profile: str, command: str) -> list[str]:
    return ["/usr/bin/sandbox-exec", "-p", profile, "/bin/sh", "-c", command]


async def _pump(stream, buf: bytearray, cap: int) -> bool:
    """流式读取子进程输出到 cap 字节为止（S04）：不再先整读后截断——
    `yes | head -c 4G` 类命令不会把内存吃满。返回是否触顶。"""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return False
        room = cap - len(buf)
        if room <= 0:
            return True
        buf.extend(chunk[:room])


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


# ── http_request ──────────────────────────────────────────────────

HTTP_SCHEMA = {
    "type": "object",
    "properties": {
        "method": {"type": "string", "description": "HTTP 方法（默认 GET）"},
        "url": {"type": "string", "description": "目标 URL"},
        "headers": {"type": "object", "description": "附加请求头"},
        "body": {"type": "string", "description": "请求体"},
    },
    "required": ["url"],
}


async def _http_request(inv: ToolInvocation, ctx: WorkContext) -> ToolResult:
    url = inv.input.get("url", "")
    allowed, reason = host_allowed(url, ctx.allowed_hosts)
    if not allowed and inv.call_id not in ctx.approved_calls:
        # allowed_hosts 是预授权清单（M0 默认空 = 全部需审批）；本次调用
        # 已经三级闸门（人工/规则）授权的，执行器不再硬拒（外审回稿 S02）
        return ToolResult(ok=False, error=f"HOST_NOT_ALLOWED：{reason}")
    method = (inv.input.get("method") or "GET").upper()
    headers = dict(inv.input.get("headers") or {})
    headers.setdefault(  # 外部幂等键由 (task_run_id, call_id) 派生（v1.3）
        "Idempotency-Key", f"{ctx.task_run_id}:{inv.call_id}")
    body = inv.input.get("body")
    buf = bytearray()
    truncated = False
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        # 流式读取至硬上限即止（S04）：不先整读再截断
        async with client.stream(
            method, url, headers=headers, content=body,
        ) as resp:
            status_code = resp.status_code
            content_type = resp.headers.get("content-type", "")
            async for chunk in resp.aiter_bytes():
                room = HARD_OUTPUT_LIMIT - len(buf)
                if room <= 0:
                    truncated = True
                    break
                buf.extend(chunk[:room])
    text = bytes(buf).decode("utf-8", errors="replace")
    pointer = await _externalize(ctx, inv.call_id, text)
    if pointer is None and len(text.encode("utf-8")) > INLINE_OUTPUT_LIMIT \
            and ctx.artifacts_dir is None:
        return ToolResult(ok=False, error="EXTERNALIZATION_UNAVAILABLE",
                          details={"status_code": status_code})
    ok = 200 <= status_code < 300
    return ToolResult(
        ok=ok,
        output=(text if pointer is None else
                f"[输出超限，已外部化] {pointer}"),
        artifact_path=pointer,
        error=None if ok else f"HTTP_{status_code}",
        details={"status_code": status_code, "content_type": content_type,
                 **({"truncated": True} if truncated else {})},
    )


# ── ask_user（交互原语；挂起-回答全链在 C8）──────────────────────

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


# ── 装配 ──────────────────────────────────────────────────────────

def _meta(name, description, schema, **kwargs):
    from .metadata import ToolMetadata

    return ToolMetadata(
        name=name, description=description, parameters=schema, **kwargs
    )


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool(
        _meta("read_file", "读取文件内容（分页，每页默认 200 行）", READ_FILE_SCHEMA,
              read_only=True, destructive=False, risk_level="low",
              needs_approval=False, concurrent_safe=True,
              side_effect_class="verifiable"),
        _read_file,
    ))
    registry.register(Tool(
        _meta("write_file", "把完整内容原子写入指定文件（覆盖式）", WRITE_FILE_SCHEMA,
              read_only=False, destructive=False, risk_level="medium",
              needs_approval=True, concurrent_safe=True,
              side_effect_class="verifiable"),
        _write_file, prepared_extras=_write_file_extras,
    ))
    registry.register(Tool(
        _meta("bash", "在受控沙盒中执行 shell 命令（写限任务范围、禁止联网、"
                      "受保护路径与凭据不可读写；只读白名单命令免审批）", BASH_SCHEMA,
              read_only=False, destructive=True, risk_level="medium",
              needs_approval=True, concurrent_safe=False,
              side_effect_class="outcome_unknown"),
        _bash,
    ))
    registry.register(Tool(
        _meta("http_request", "发起 HTTP 请求（受 allowed_hosts 白名单约束）",
              HTTP_SCHEMA,
              read_only=False, destructive=False, risk_level="medium",
              needs_approval=True, concurrent_safe=True,
              side_effect_class="outcome_unknown"),
        _http_request,
    ))
    registry.register(Tool(
        _meta("ask_user", "向用户提问并等待回答（用于澄清任务，答案会回到对话）",
              ASK_USER_SCHEMA,
              read_only=True, destructive=False, risk_level="low",
              needs_approval=False, concurrent_safe=True,
              side_effect_class="verifiable"),
        _ask_user,
    ))
    return registry
