"""调度器并发规则测试（真实 asyncio 并发观测）：上限 4 / 非只读串行。"""

import asyncio
import time

from agentcrew_core.tools import (
    Tool,
    ToolInvocation,
    ToolMetadata,
    WorkContext,
    new_call_id,
)
from agentcrew_core.tools.scheduler import ToolRegistry, ToolScheduler


def _meta(name, **kw):
    base = dict(read_only=True, destructive=False, risk_level="low",
                needs_approval=False, concurrent_safe=True,
                side_effect_class="verifiable", timeout_ms=5_000)
    base.update(kw)
    return ToolMetadata(name=name, description="", parameters={}, **base)


def run(coro):
    return asyncio.run(coro)


def test_readonly_tools_parallel_up_to_four():
    registry = ToolRegistry()
    active = 0
    peak = 0

    async def probe(inv, ctx):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.15)
        active -= 1
        return _ok()

    def _ok():
        from agentcrew_core.tools import ToolResult
        return ToolResult(ok=True, output="")

    registry.register(Tool(_meta("probe"), probe))
    scheduler = ToolScheduler(registry, max_concurrency=4)

    async def scenario():
        await asyncio.gather(*[
            scheduler.run(ToolInvocation(new_call_id(), "probe", {}), WorkContext())
            for _ in range(8)
        ])

    run(scenario())
    assert peak == 4, f"8 个只读任务并发峰值应为 4，实际 {peak}"


def test_destructive_tools_serialized():
    registry = ToolRegistry()
    active = 0
    overlap_seen = False

    async def mutate(inv, ctx):
        nonlocal active, overlap_seen
        if active > 0:
            overlap_seen = True
        active += 1
        await asyncio.sleep(0.1)
        active -= 1
        from agentcrew_core.tools import ToolResult
        return ToolResult(ok=True, output="")

    registry.register(Tool(
        _meta("mutate", read_only=False, destructive=True, concurrent_safe=False,
              needs_approval=True, side_effect_class="outcome_unknown"),
        mutate,
    ))
    scheduler = ToolScheduler(registry)

    async def scenario():
        await asyncio.gather(*[
            scheduler.run(ToolInvocation(new_call_id(), "mutate", {}), WorkContext())
            for _ in range(4)
        ])

    start = time.monotonic()
    run(scenario())
    elapsed = time.monotonic() - start
    assert not overlap_seen, "destructive 工具不得重叠执行"
    assert elapsed >= 0.4, f"串行 4 个 100ms 任务应 ≥0.4s，实际 {elapsed:.2f}s"


def test_unknown_tool_rejected():
    scheduler = ToolScheduler(ToolRegistry())
    result = run(scheduler.run(
        ToolInvocation(new_call_id(), "nope", {}), WorkContext()))
    assert not result.ok and result.error == "UNKNOWN_TOOL"
