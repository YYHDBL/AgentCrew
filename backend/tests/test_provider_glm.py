"""GLM 适配器纯逻辑单测：错误分类映射 / 退避计算 / 流式状态机拼装。

状态机测试用构造的事件字典序列直接驱动 `_consume`（测自有纯函数）；
真实网络行为由 scripts/provider/verify.py 的真实 key 脚本验证（ADR-006）。
"""

import asyncio
import json

import pytest

from agentcrew_core.provider.glm_anthropic import (
    GLMAnthropicProvider,
    SlotConfig,
    backoff_seconds,
    classify_error,
)
from agentcrew_core.provider.types import ErrorClass


# ── 错误分类（纯函数参数化）───────────────────────────────────────

@pytest.mark.parametrize("status,body,expected,retryable", [
    (401, '{"error":{"message":"令牌已过期或验证不正确","type":"401"}}', ErrorClass.AUTH, False),
    (403, '{"error":{"message":"forbidden"}}', ErrorClass.AUTH, False),
    (429, '{"error":{"message":"rate limit exceeded"}}', ErrorClass.RATE_LIMIT, True),
    (429, "", ErrorClass.RATE_LIMIT, True),
    (500, '{"error":{"message":"internal"}}', ErrorClass.NETWORK, True),
    (529, '{"error":{"message":"overloaded"}}', ErrorClass.NETWORK, True),
    (504, "", ErrorClass.NETWORK, True),
    (200, "", ErrorClass.UNKNOWN, False),  # 无状态码兜底（流内 error 用 500 包裹，此处验证默认分支）
    (400, '{"error":{"message":"max_tokens must be greater"}}', ErrorClass.UNKNOWN, False),
    (400, '{"error":{"code":"1113","message":"余额不足或无可用资源包"}}', ErrorClass.UNKNOWN, False),
])
def test_classify_error_mapping(status, body, expected, retryable):
    error = classify_error(status, body)
    assert error.error_class == expected
    assert error.retryable is retryable


def test_classify_content_filter():
    error = classify_error(400, '{"error":{"message":"output content_filter hit"}}')
    assert error.error_class == ErrorClass.CONTENT_FILTER
    assert error.retryable is False


def test_classify_extracts_readable_message():
    error = classify_error(401, '{"error":{"message":"令牌已过期或验证不正确","type":"401"}}')
    assert error.message == "令牌已过期或验证不正确"


def test_backoff_sequence():
    assert [backoff_seconds(i) for i in range(4)] == [1.0, 2.0, 4.0, 8.0]


# ── 流式状态机（构造事件驱动自有代码）────────────────────────────

def _provider() -> GLMAnthropicProvider:
    return GLMAnthropicProvider({"main": SlotConfig(model="m", api_key="k")})


def _sse(events: list[dict]):
    payload = b"".join(
        b"data: " + json.dumps(e, ensure_ascii=False).encode() + b"\n\n" for e in events
    )
    return [payload]


def _consume(chunks: list[bytes]):
    async def gen():
        for c in chunks:
            yield c
    provider = _provider()
    return asyncio.run(_collect(provider._consume(gen())))


async def _collect(agen):
    return [e async for e in agen]


WEATHER_TOOL_EVENTS = [
    {"type": "message_start", "message": {"usage": {"input_tokens": 0, "output_tokens": 0}}},
    {"type": "ping"},
    {"type": "content_block_start", "index": 0,
     "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "查"}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "天气"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1,
     "content_block": {"type": "tool_use", "id": "call_001", "name": "get_weather", "input": {}}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\""}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "ci"}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "ty\":\"北京\"}"}},
    {"type": "content_block_stop", "index": 1},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
     "usage": {"input_tokens": 158, "output_tokens": 39, "cache_read_input_tokens": 0}},
    {"type": "message_stop"},
]


def test_stream_machine_assembles_tool_call():
    events = _consume(_sse(WEATHER_TOOL_EVENTS))
    by_type = {}
    for e in events:
        by_type.setdefault(e.type, []).append(e)

    assert by_type["thinking_delta"][0].text == "查"
    started = by_type["tool_call_started"][0]
    assert started.tool_name == "get_weather" and started.tool_call is None  # 进行中不带参数
    call = by_type["tool_call"][0].tool_call
    assert call.id == "call_001" and call.name == "get_weather"
    assert call.input == {"city": "北京"}  # 碎片拼装为完整 JSON（含切在词中间的碎片）
    usage = by_type["usage"][0].usage
    assert (usage.input_tokens, usage.output_tokens) == (158, 39)
    done = by_type["done"][0]
    assert done.stop_reason == "tool_use" and done.usage == usage
    assert "error" not in by_type


def test_stream_machine_text_only():
    events = _consume(_sse([
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "你好"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "！"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"input_tokens": 5, "output_tokens": 3}},
        {"type": "message_stop"},
    ]))
    text = "".join(e.text for e in events if e.type == "text_delta")
    assert text == "你好！"
    assert events[-1].type == "done" and events[-1].stop_reason == "end_turn"


def test_stream_machine_bad_tool_json_yields_error_not_half_call():
    events = _consume(_sse([
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "c1", "name": "t", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": "{broken"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_stop"},
    ]))
    types = [e.type for e in events]
    assert "tool_call" not in types  # 绝不外发半成品
    assert "error" in types and "JSON 无效" in events[types.index("error")].error.message


def test_stream_machine_usage_missing_defaults_zero():
    events = _consume(_sse([
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "x"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},  # 无 usage
        {"type": "message_stop"},
    ]))
    usage_events = [e for e in events if e.type == "usage"]
    done = [e for e in events if e.type == "done"][0]
    assert done.usage == usage_events[0].usage
    assert (done.usage.input_tokens, done.usage.output_tokens) == (0, 0)


def test_stream_machine_inband_error_event():
    events = _consume(_sse([
        {"type": "error", "error": {"type": "overloaded_error", "message": "上游过载"}},
    ]))
    error = events[0].error
    assert events[0].type == "error"
    assert error.error_class == ErrorClass.NETWORK and error.retryable is True
