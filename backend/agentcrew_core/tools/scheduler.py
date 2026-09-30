"""工具注册表与调度器（harness-session §6.2）。

调度规则：destructive（或动态判定的非只读 bash）串行；read_only /
concurrent_safe 并行；并发上限 4。执行前后经 ctx.emit 发 tool.* 事件
（载荷契约与 C2 projections 对齐；C8 接 EventStore 落库）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid

from .judgment import bash_readonly
from .metadata import Tool, ToolInvocation, ToolResult, WorkContext

_log = logging.getLogger("agentcrew.tools")


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


class ToolScheduler:
    def __init__(self, registry: ToolRegistry, *, max_concurrency: int = 4):
        self._registry = registry
        self._sem = asyncio.Semaphore(max_concurrency)
        self._serial = asyncio.Lock()

    async def run(self, invocation: ToolInvocation, ctx: WorkContext) -> ToolResult:
        tool = self._registry.get(invocation.name)
        if tool is None:
            return ToolResult(ok=False, error="UNKNOWN_TOOL",
                              details={"known": self._registry.names()})
        effective_readonly = tool.metadata.read_only
        bash_verdict = ""
        if tool.metadata.name == "bash":
            effective_readonly, bash_verdict = bash_readonly(
                invocation.input.get("command", "")
            )
        meta = tool.metadata
        await self._emit(ctx, "tool.prepared", {
            "call_id": invocation.call_id, "tool_name": meta.name,
            "side_effect_class": meta.side_effect_class,
            "input_hash": input_hash(invocation.input),
            "risk_level": meta.risk_level, "input": invocation.input,
            "read_only_verdict": effective_readonly,
            "verdict_reason": bash_verdict,
        })
        async with self._sem:
            if not effective_readonly and not meta.read_only:
                async with self._serial:  # 非只读一律串行
                    return await self._dispatch(tool, invocation, ctx)
            return await self._dispatch(tool, invocation, ctx)

    async def _dispatch(self, tool, invocation, ctx) -> ToolResult:
        meta = tool.metadata
        await self._emit(ctx, "tool.dispatched", {"call_id": invocation.call_id})
        try:
            result = await asyncio.wait_for(
                tool.execute(invocation, ctx), timeout=meta.timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            result = ToolResult(ok=False, error="TIMEOUT",
                                details={"timeout_ms": meta.timeout_ms})
        except Exception as e:  # noqa: BLE001 —— 工具异常收敛为结果，不崩任务
            _log.exception("tool.failed %s", meta.name)
            result = ToolResult(ok=False, error=f"TOOL_CRASH:{type(e).__name__}",
                                details={"message": str(e)[:200]})
        payload = {
            "call_id": invocation.call_id,
            "output_summary": (result.output or "")[:2000],
        }
        if result.artifact_path:
            payload["artifact_path"] = result.artifact_path
        if not result.ok:
            payload["error"] = result.error
        await self._emit(ctx, "tool.completed" if result.ok else "tool.failed", payload)
        return result

    async def _emit(self, ctx: WorkContext, event_type: str, payload: dict) -> None:
        if ctx.emit is not None:
            try:
                await ctx.emit(event_type, payload)
            except Exception:  # noqa: BLE001 —— 事件出口故障不阻断工具执行
                _log.exception("tool.emit_failed %s", event_type)
