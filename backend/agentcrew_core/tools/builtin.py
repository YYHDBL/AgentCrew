"""五个内置工具（harness-session §6.1 五件套 / ADR-008 / v1.7 语义）。

- read_file：分页读（惰性按行取，不整读大文件），scope/受保护路径硬检；
- write_file：独占创建 tmp（mkstemp O_EXCL，不跟随预置符号链接）+ fsync +
  rename 原子写；prepared 附内容 sha256（verifiable 类核验依据）；发
  artifact.* 事件；
- bash：判定结果只影响调度与审批元数据（闸门在 C6）；执行必套 Seatbelt 最小
  profile（写限 scope / 网络全禁 / 受保护路径**读写双向**禁——subpath 一律
  realpath）；env 白名单仅 PATH/HOME/LANG/TZ/TERM；超时或被取消都在 finally
  杀进程组（外审回稿：外层取消不留孤儿）；
- http_request：allowed_hosts 判定，Idempotency-Key=(task_run_id, call_id) 派生；
- ask_user：交互原语，发 question.* 事件并等待回答通道（挂起全链在 C8）；
- 输出 >32KB 落工件文件留指针；artifacts_dir 未配置时明确报错不内联（外审
  回稿）；超 max_output_bytes 硬上限截断并标记（防止超大输出耗尽内存后仍全量
  进入上下文）。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
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

def _check_path(ctx: WorkContext, raw: str) -> str | None:
    path = Path(raw).expanduser()
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


async def _read_file(inv: ToolInvocation, ctx: WorkContext) -> ToolResult:
    raw = inv.input.get("path", "")
    if err := _check_path(ctx, raw):
        return ToolResult(ok=False, error=err)
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        return ToolResult(ok=False, error=f"NOT_FOUND：{path}")
    offset = max(1, int(inv.input.get("offset", 1)))
    limit = max(1, int(inv.input.get("limit", 200)))
    # 惰性按行读：只取本页，不整读大文件（外审回稿）
    from itertools import islice

    total_lines = 0
    picked: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for total_lines, line in enumerate(fh, start=1):
            if offset <= total_lines < offset + limit:
                picked.append(line.rstrip("\n"))
    content = "\n".join(picked)
    pointer = await _externalize(ctx, inv.call_id, content)
    return ToolResult(
        ok=True,
        output=content if pointer is None else
        f"[输出 {len(content.encode('utf-8'))} 字节超限，已外部化] {pointer}",
        artifact_path=pointer,
        details={"lines": f"{offset}-{offset - 1 + len(picked)}",
                 "total_lines": total_lines},
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


async def _write_file(inv: ToolInvocation, ctx: WorkContext) -> ToolResult:
    raw = inv.input.get("path", "")
    content = inv.input.get("content", "")
    if err := _check_path(ctx, raw):
        return ToolResult(ok=False, error=err)
    target = Path(raw).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    sha256 = _write_file_extras(inv.input)["content_sha256"]
    if ctx.emit is not None:  # 生成中 → 写入 → ready（§2.2 artifacts 投影口径）
        await ctx.emit("artifact.created", {
            "artifact_id": inv.call_id, "task_run_id": ctx.task_run_id,
            "tool_call_id": inv.call_id, "path": str(target),
            "name": target.name, "ext": target.suffix,
        })
    # mkstemp 独占创建（O_EXCL）：不跟随预置符号链接、不依赖 call_id 拼名
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:  # type: IO[str]
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    dir_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    size = target.stat().st_size
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


def seatbelt_profile(scope_realpaths: list[str],
                     deny_realpaths: list[str]) -> str:
    """最小 profile（ADR-008）：读默认放开（读取强制边界 M2），写限 scope，
    网络全禁，受保护路径**读写双向**禁（嵌套 subpath 的 deny 压过外层 allow，
    实测验证）。SBPL 规则：特定 subpath 覆盖泛化规则。"""
    rules = ['(version 1)', '(allow default)', '(deny network*)', '(deny file-write*)']
    rules += [f'(allow file-write* (subpath "{p}"))' for p in scope_realpaths]
    # 双向禁：先禁写（含 scope 内受保护路径），再禁读
    rules += [f'(deny file-write* (subpath "{p}"))' for p in deny_realpaths]
    rules += [f'(deny file-read* (subpath "{p}"))' for p in deny_realpaths]
    return "\n".join(rules)


def _sandboxed_argv(profile: str, command: str) -> list[str]:
    return ["/usr/bin/sandbox-exec", "-p", profile, "/bin/sh", "-c", command]


async def _bash(inv: ToolInvocation, ctx: WorkContext) -> ToolResult:
    command = inv.input.get("command", "")
    timeout_ms = int(inv.input.get("timeout_ms", 60_000))
    readonly, verdict_reason = bash_readonly(command)
    protected_real = [str(Path(p).resolve()) for p in ctx.protected
                      if Path(p).exists()]
    profile = seatbelt_profile(
        [str(Path(p).resolve()) for p in ctx.scope], protected_real,
    )
    env = {k: os.environ[k] for k in BASH_ENV_WHITELIST if k in os.environ}
    proc = await asyncio.create_subprocess_exec(
        *_sandboxed_argv(profile, command),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,  # 独立进程组：超时/取消都可整组击杀
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_ms / 1000
        )
    except BaseException:
        # 内部超时（TimeoutError）与外部取消（CancelledError）都收割进程组
        try:
            os.killpg(proc.pid, 9)
        except ProcessLookupError:
            pass
        await proc.wait()
        raise
    out = stdout.decode("utf-8", errors="replace")
    err_text = stderr.decode("utf-8", errors="replace").strip()
    truncated = len(out.encode("utf-8")) > HARD_OUTPUT_LIMIT
    if truncated:
        out = out[:HARD_OUTPUT_LIMIT]
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
    if not allowed:
        return ToolResult(ok=False, error=f"HOST_NOT_ALLOWED：{reason}")
    method = (inv.input.get("method") or "GET").upper()
    headers = dict(inv.input.get("headers") or {})
    headers.setdefault(  # 外部幂等键由 (task_run_id, call_id) 派生（v1.3）
        "Idempotency-Key", f"{ctx.task_run_id}:{inv.call_id}")
    body = inv.input.get("body")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        resp = await client.request(method, url, headers=headers, content=body)
    text = resp.text
    truncated = len(text.encode("utf-8")) > HARD_OUTPUT_LIMIT
    if truncated:
        text = text[:HARD_OUTPUT_LIMIT]
    pointer = await _externalize(ctx, inv.call_id, text)
    if pointer is None and len(text.encode("utf-8")) > INLINE_OUTPUT_LIMIT \
            and ctx.artifacts_dir is None:
        return ToolResult(ok=False, error="EXTERNALIZATION_UNAVAILABLE",
                          details={"status_code": resp.status_code})
    return ToolResult(
        ok=200 <= resp.status_code < 300,
        output=(text[:INLINE_OUTPUT_LIMIT] if pointer is None else
                f"[输出超限，已外部化] {pointer}"),
        artifact_path=pointer,
        error=None if 200 <= resp.status_code < 300 else f"HTTP_{resp.status_code}",
        details={"status_code": resp.status_code,
                 "content_type": resp.headers.get("content-type", ""),
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
