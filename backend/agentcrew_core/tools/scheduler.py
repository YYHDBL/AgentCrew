"""工具注册表与调度器（harness-session §6.2）。

调度规则（与设计一致）：concurrent_safe（或只读）可并行，其余串行；
并发上限 4。

外审回稿修复：
- call_id 由调度器校验（[A-Za-z0-9_-]{1,64}）——C8 起 call_id 来自模型返回的
  tool_use id，等于外部可控输入，绝不允许携带路径成分；
- tool.prepared/dispatched 的持久化失败**阻断执行**（fail-closed：没有
  prepared 记录的副作用在恢复流程里不可见）；completed/failed 记录失败
  不撤销已发生的副作用，标记 record_failed 交上层（C9 待核验）；
- 事件中的 input 对凭据类字段脱敏（http_request 的 Authorization/Cookie 等），
  input_hash 仍按完整参数计算（审批绑定不可变内容）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from dataclasses import replace

from jsonschema import Draft202012Validator
from ..events import Event

from .judgment import bash_readonly
from .metadata import Tool, ToolInvocation, ToolMetadata, ToolResult, WorkContext
from .builtin.externalize import _externalize

_log = logging.getLogger("agentcrew.tools")

_CALL_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")

# 事件载荷中必须脱敏的凭据类请求头（大小写不敏感）
SENSITIVE_HEADER_KEYS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "x-api-key"}
)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.metadata.name in self._tools:
            raise ValueError(f"工具重复注册：{tool.metadata.name}")
        self._tools[tool.metadata.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict]:
        """给 Provider 的工具定义（Anthropic 格式：name/description/input_schema）。"""
        return [
            {
                "name": t.metadata.name,
                "description": t.metadata.description,
                "input_schema": t.metadata.parameters,
            }
            for t in self._tools.values()
        ]


def new_call_id() -> str:
    return uuid.uuid4().hex


def input_hash(input: dict) -> str:
    """完整参数哈希：审批与对账绑定的不可变内容（v1.7）。"""
    canonical = json.dumps(input, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def redact_event_input(name: str, input: dict) -> dict:
    """事件载荷用的参数副本：http_request 的凭据类请求头值打码。"""
    if name != "http_request":
        return input
    headers = input.get("headers")
    if not isinstance(headers, dict):
        return input
    redacted = dict(input)
    redacted["headers"] = {
        k: ("***" if k.lower() in SENSITIVE_HEADER_KEYS else v)
        for k, v in headers.items()
    }
    return redacted


# 参数 schema 校验失败的专用错误码：循环层把它计入解析重试通道
# （外审回稿：JSON 损坏与 schema 不符共用 ≤2 次重试 → unparseable）
INVALID_PARAMS = "INVALID_PARAMS"

def validate_tool_input(schema: dict, input: dict) -> str | None:
    """执行完整 JSON Schema 校验，错误回填既有解析重试通道。"""
    error = next(Draft202012Validator(schema).iter_errors(input), None)
    return f"{list(error.path)}: {error.message}" if error else None


class ToolScheduler:
    def __init__(self, registry: ToolRegistry, *, max_concurrency: int = 4,
                 gate=None):
        self._registry = registry
        self._sem = asyncio.Semaphore(max_concurrency)
        self._serial = asyncio.Lock()
        self._gate = gate  # C6 审批闸门（server 侧注入；None = 无闸门直通）

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    async def run(self, invocation: ToolInvocation, ctx: WorkContext) -> ToolResult:
        tool = (ctx.registry if ctx.registry is not None else self._registry).get(invocation.name)
        if tool is None:
            return ToolResult(ok=False, error="UNKNOWN_TOOL",
                              details={"known": self._registry.names()})
        if not _CALL_ID_PATTERN.fullmatch(invocation.call_id or ""):
            return ToolResult(ok=False, error="INVALID_CALL_ID",
                              details={"call_id": invocation.call_id[:40]})
        ctx = replace(ctx)
        # 入口即做不可变快照（外审回稿 F04）：审批哈希绑定的是快照，此后
        # 对原 dict 的任何修改都不影响 prepared/闸门/执行三处的一致性
        invocation = ToolInvocation(
            call_id=invocation.call_id, name=invocation.name,
            input=json.loads(json.dumps(invocation.input, ensure_ascii=False)),
        )

        # 参数 schema 校验（外审回稿）：不符 → 账本记 prepared→failed
        # （不派发、不问权限——没有可授权的有效目标），错误码
        # INVALID_PARAMS 由循环层计入解析重试通道
        invalid = validate_tool_input(tool.metadata.parameters,
                                      invocation.input)
        if invalid is not None:
            await self._emit_strict(ctx, "tool.prepared", {
                "call_id": invocation.call_id, "tool_name": tool.metadata.name,
                "side_effect_class": tool.metadata.side_effect_class,
                "input_hash": input_hash(invocation.input),
                "risk_level": tool.metadata.risk_level,
                "input": redact_event_input(tool.metadata.name,
                                            invocation.input),
            })
            await self._emit_strict(ctx, "tool.failed", {
                "call_id": invocation.call_id,
                "error": INVALID_PARAMS,
                "output_summary": "",
            })
            return ToolResult(ok=False, error=INVALID_PARAMS,
                              details={"message": invalid})

        effective_readonly = tool.metadata.read_only
        bash_verdict = ""
        if tool.metadata.name == "bash":
            effective_readonly, bash_verdict = bash_readonly(
                invocation.input.get("command", ""), ctx.cwd,
            )
        meta = tool.metadata
        prepared_payload = {
            "call_id": invocation.call_id, "tool_name": meta.name,
            "side_effect_class": meta.side_effect_class,
            "input_hash": input_hash(invocation.input),
            "risk_level": meta.risk_level,
            "input": redact_event_input(meta.name, invocation.input),
            "read_only_verdict": effective_readonly,
            "verdict_reason": bash_verdict,
        }
        if tool.prepared_extras is not None:
            prepared_payload.update(tool.prepared_extras(invocation.input))
        # prepared 持久化失败 → 阻断执行（没有账本记录的副作用不可恢复）
        if not await self._emit_strict(ctx, "tool.prepared", prepared_payload):
            return ToolResult(ok=False, error="EVENT_PERSIST_FAILED")

        # 权限闸门（§6.2：prepared 落库 → 权限门 → dispatched → 执行）。
        # 挂钩内部完成 ask 的挂起等待；deny 时补记 tool.failed 事件。
        if self._gate is not None:
            decision = await self._gate(invocation, meta, effective_readonly)
            if decision != "allow":
                await self._emit_strict(ctx, "tool.failed", {
                    "call_id": invocation.call_id,
                    "error": "PERMISSION_DENIED",
                    "output_summary": "",
                })
                return ToolResult(ok=False, error="PERMISSION_DENIED",
                                  details={"gate": decision})

        # dispatched 紧邻实际执行写入（外审回稿 S08）：取得并发名额与串行锁
        # 之前不声明 dispatched——否则等待名额期间被取消的调用会被恢复流程
        # 当成"可能已产生副作用"而要求人工核验
        serial_needed = not effective_readonly and not meta.concurrent_safe
        async with self._sem:
            if serial_needed:
                async with self._serial:
                    if not await self._emit_strict(
                            ctx, "tool.dispatched",
                            {"call_id": invocation.call_id}):
                        return ToolResult(ok=False, error="EVENT_PERSIST_FAILED")
                    return await self._execute(tool, invocation, ctx)
            if not await self._emit_strict(
                    ctx, "tool.dispatched", {"call_id": invocation.call_id}):
                return ToolResult(ok=False, error="EVENT_PERSIST_FAILED")
            return await self._execute(tool, invocation, ctx)

    async def _execute(self, tool, invocation, ctx) -> ToolResult:
        meta = tool.metadata
        try:
            result = await asyncio.wait_for(
                tool.execute(invocation, ctx), timeout=meta.timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            result = ToolResult(ok=False, error="TIMEOUT",
                                details={"timeout_ms": meta.timeout_ms})
        await _externalize(ctx, invocation.call_id, result, meta.max_output_bytes)
        payload = {
            "call_id": invocation.call_id,
            # §2.3 持久化契约：事件必须携带完整工具输出（≤32KB 内联；超出由
            # 工具外部化并留 artifact 指针）；details（exit_code/stderr/sha256
            # 等）一并入事件，否则重建上下文时不可恢复（外审回稿 F09）
            "output": result.output or "",
            "output_summary": (result.output or "")[:2000],  # 投影/列表摘要
        }
        if result.details:
            payload["details"] = dict(result.details)
        if result.artifact_path:
            payload["artifact_path"] = result.artifact_path
        if not result.ok:
            payload["error"] = result.error
        # 副作用已发生，记录失败不可撤销——标记后交上层（C9 待核验）
        recorded = await self._emit_strict(ctx, "tool.completed" if result.ok else "tool.failed", payload)
        if not recorded:
            result.details["record_failed"] = True
        elif isinstance(recorded, Event):
            result.event_global_seq = recorded.global_seq
        return result

    async def _emit_strict(self, ctx: WorkContext, event_type: str,
                           payload: dict) -> bool | Event:
        """发出事件；失败返回 False（不吞——调用方决定阻断或标记）。"""
        if ctx.emit is None:
            return True
        try:
            event = await ctx.emit(event_type, payload)
            if event_type == "tool.dispatched" and event is not None:
                ctx.source_global_seq = event.global_seq
            return event if event is not None else True
        except Exception:  # noqa: BLE001
            _log.exception("tool.emit_failed %s", event_type)
            return False
