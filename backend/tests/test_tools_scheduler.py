"""调度器规则测试：上限 4 / 按元数据并发 / call_id 校验 / 事件持久化 fail-closed。"""

import asyncio
import time
from pathlib import Path

from agentcrew_core.tools import (
    Tool,
    ToolInvocation,
    ToolMetadata,
    ToolResult,
    WorkContext,
    new_call_id,
)
from agentcrew_core.tools.scheduler import (
    ToolRegistry,
    ToolScheduler,
    redact_event_input,
)


def _meta(name, **kw):
    base = dict(read_only=True, destructive=False, risk_level="low",
                needs_approval=False, concurrent_safe=True,
                side_effect_class="verifiable", timeout_ms=5_000)
    base.update(kw)
    return ToolMetadata(name=name, description="", parameters={}, **base)


def run(coro):
    return asyncio.run(coro)


def _ok():
    return ToolResult(ok=True, output="")


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

    registry.register(Tool(_meta("probe"), probe))
    scheduler = ToolScheduler(registry, max_concurrency=4)

    async def scenario():
        await asyncio.gather(*[
            scheduler.run(ToolInvocation(new_call_id(), "probe", {}), WorkContext())
            for _ in range(8)
        ])

    run(scenario())
    assert peak == 4, f"8 个只读任务并发峰值应为 4，实际 {peak}"


def test_concurrent_safe_nonreadonly_run_parallel():
    """外审回稿：write_file/http_request 这类 concurrent_safe 的非只读工具
    应并行（仅 concurrent_safe=False 的非只读工具串行）。"""
    registry = ToolRegistry()
    active = 0
    peak = 0

    async def job(inv, ctx):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.15)
        active -= 1
        return _ok()

    registry.register(Tool(
        _meta("job", read_only=False, destructive=False, concurrent_safe=True,
              needs_approval=True, side_effect_class="verifiable"),
        job,
    ))
    scheduler = ToolScheduler(registry, max_concurrency=4)

    async def scenario():
        await asyncio.gather(*[
            scheduler.run(ToolInvocation(new_call_id(), "job", {}), WorkContext())
            for _ in range(4)
        ])

    run(scenario())
    assert peak == 4, f"concurrent_safe 非只读工具应满并行，峰值 {peak}"


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
        return _ok()

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


def test_invalid_call_id_rejected(tmp_path):
    """外审致命项回归：call_id 携带路径成分（模型可控的 tool_use id）必须拒绝。"""
    registry = ToolRegistry()

    async def touch(inv, ctx):
        (Path("/tmp") / "c5-evil").write_text("pwned")
        return _ok()

    registry.register(Tool(_meta("touch", read_only=False), touch))
    scheduler = ToolScheduler(registry)
    for bad_id in ("../../evil", "a/b", "a;b", "a b", "", "x" * 65):
        result = run(scheduler.run(
            ToolInvocation(bad_id, "touch", {}), WorkContext()))
        assert not result.ok and result.error == "INVALID_CALL_ID", bad_id
    assert not Path("/tmp/c5-evil").exists(), "非法 call_id 不得执行任何副作用"


def test_prepared_emit_failure_blocks_execution(tmp_path):
    """外审致命项回归：prepared 落账失败 → 副作用不执行（fail-closed）。"""
    target = tmp_path / "side-effect.txt"
    registry = ToolRegistry()

    async def touch(inv, ctx):
        target.write_text("done")
        return _ok()

    registry.register(Tool(_meta("touch", read_only=False), touch))
    scheduler = ToolScheduler(registry)

    async def broken_sink(event_type, payload):
        if event_type == "tool.prepared":
            raise RuntimeError("EventStore 写入失败")

    result = run(scheduler.run(
        ToolInvocation(new_call_id(), "touch", {}),
        WorkContext(emit=broken_sink)))
    assert not result.ok and result.error == "EVENT_PERSIST_FAILED"
    assert not target.exists(), "无账本的副作用不得发生"


def test_completed_emit_failure_marks_record_failed(tmp_path):
    """副作用已发生后记录失败：不撤销，标记 record_failed 交上层（C9 待核验）。"""
    target = tmp_path / "done.txt"
    registry = ToolRegistry()

    async def touch(inv, ctx):
        target.write_text("done")
        return _ok()

    registry.register(Tool(_meta("touch", read_only=False), touch))
    scheduler = ToolScheduler(registry)

    async def broken_sink(event_type, payload):
        if event_type in ("tool.completed", "tool.failed"):
            raise RuntimeError("落账失败")

    result = run(scheduler.run(
        ToolInvocation(new_call_id(), "touch", {}),
        WorkContext(emit=broken_sink)))
    assert result.ok and target.exists()
    assert result.details.get("record_failed") is True


def test_redact_event_input_masks_http_credentials():
    original = {"url": "https://x.io", "headers": {
        "Authorization": "Bearer secret-token", "cookie": "a=b",
        "X-Custom": "keep"}}
    redacted = redact_event_input("http_request", original)
    assert redacted["headers"]["Authorization"] == "***"
    assert redacted["headers"]["cookie"] == "***"
    assert redacted["headers"]["X-Custom"] == "keep"
    assert original["headers"]["Authorization"] == "Bearer secret-token"  # 原件不动
    assert redact_event_input("write_file", original) is original  # 非白名单工具原样
