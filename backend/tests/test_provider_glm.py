"""GLM 适配器纯逻辑单测：错误分类映射 / 退避与重试决策 / 流式状态机。

状态机测试用构造的事件字典序列直接驱动 `_consume`（测自有纯函数）；
真实网络行为由 scripts/provider/verify.py 的真实 key 脚本验证（ADR-006）。
外审回稿新增：截断终态错误、参数类型校验、usage 合并、thinking 块签名、
坏 UTF-8 包裹、统一重试决策矩阵。
"""

import asyncio
import json

import pytest

from agentcrew_core.provider.glm_anthropic import (
    GLMAnthropicProvider,
    SlotConfig,
    _ProviderStreamError,
    backoff_seconds,
    classify_error,
    should_retry,
)
from agentcrew_core.provider.types import ErrorClass, ProviderError


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


# ── 统一重试决策（外审回稿，纯函数矩阵）──────────────────────────

def test_should_retry_matrix():
    network = ProviderError(ErrorClass.NETWORK, "x", retryable=True)
    auth = ProviderError(ErrorClass.AUTH, "x", retryable=False)
    assert should_retry(network, produced=False, attempt=0) is True
    assert should_retry(network, produced=False, attempt=3) is False  # 额度用尽
    assert should_retry(network, produced=True, attempt=0) is False   # 已产出不自动重发
    assert should_retry(auth, produced=False, attempt=0) is False     # 分类不可重试


# ── 流式状态机（构造事件驱动自有代码）────────────────────────────

def _provider() -> GLMAnthropicProvider:
    return GLMAnthropicProvider({"main": SlotConfig(model="m", api_key="k")})


def _sse(events: list[dict]):
    return [b"".join(
        b"data: " + json.dumps(e, ensure_ascii=False).encode() + b"\n\n" for e in events
    )]


def _consume(chunks: list[bytes]):
    """驱动 _consume，返回 (已产出事件, 终态异常或 None)。"""
    async def gen():
        for c in chunks:
            yield c

    async def run():
        events = []
        provider = _provider()
        try:
            async for e in provider._consume(gen()):
                events.append(e)
            return events, None
        except _ProviderStreamError as err:
            return events, err

    return asyncio.run(run())


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
    events, terminal = _consume(_sse(WEATHER_TOOL_EVENTS))
    assert terminal is None
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
    events, terminal = _consume(_sse([
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "你好"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "！"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"input_tokens": 5, "output_tokens": 3}},
        {"type": "message_stop"},
    ]))
    assert terminal is None
    text = "".join(e.text for e in events if e.type == "text_delta")
    assert text == "你好！"
    assert events[-1].type == "done" and events[-1].stop_reason == "end_turn"


def test_stream_machine_thinking_block_carries_signature():
    events, terminal = _consume(_sse([
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": "sig-head"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "推理"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "文本"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig-tail"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"input_tokens": 5, "output_tokens": 3}},
        {"type": "message_stop"},
    ]))
    assert terminal is None
    block = [e for e in events if e.type == "thinking_block"][0]
    assert block.text == "推理文本"
    assert block.signature == "sig-headsig-tail"  # content_block_start 头 + signature_delta 拼装


def test_stream_machine_usage_merges_start_and_delta():
    """Anthropic 规范位置：输入量在 message_start、输出量在 message_delta。"""
    events, terminal = _consume(_sse([
        {"type": "message_start",
         "message": {"usage": {"input_tokens": 100, "cache_read_input_tokens": 7}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "x"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 50}},
        {"type": "message_stop"},
    ]))
    assert terminal is None
    done = [e for e in events if e.type == "done"][0]
    assert (done.usage.input_tokens, done.usage.output_tokens) == (100, 50)
    assert done.usage.cache_read_input_tokens == 7


def test_stream_machine_usage_missing_defaults_zero():
    events, terminal = _consume(_sse([
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "x"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},  # 无 usage
        {"type": "message_stop"},
    ]))
    assert terminal is None
    done = [e for e in events if e.type == "done"][0]
    assert (done.usage.input_tokens, done.usage.output_tokens) == (0, 0)


def test_truncated_stream_is_terminal_error_not_silence():
    """外审回稿致命项：HTTP 200 但 EOF 且无 message_stop → 终态错误，绝不静默。"""
    events, terminal = _consume(_sse([
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "部分"}},
        {"type": "content_block_stop", "index": 0},
        # 没有 message_delta / message_stop —— 截断
    ]))
    assert terminal is not None
    assert terminal.error.error_class == ErrorClass.NETWORK
    assert terminal.error.retryable is True
    assert not any(e.type == "done" for e in events), "截断流不得发 done"


def test_bad_tool_json_is_terminal_error_without_done():
    events, terminal = _consume(_sse([
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "c1", "name": "t", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": "{broken"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"input_tokens": 1}},
        {"type": "message_stop"},
    ]))
    assert terminal is not None and "JSON 无效" in terminal.error.message
    assert not any(e.type == "tool_call" for e in events)  # 绝不外发半成品
    assert not any(e.type == "done" for e in events), "终态错误后不得再发 done"


def test_non_dict_tool_args_rejected():
    events, terminal = _consume(_sse([
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "c1", "name": "t", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": "[1, 2]"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_stop"},
    ]))
    assert terminal is not None
    assert "必须是 JSON 对象" in terminal.error.message
    assert not any(e.type == "tool_call" for e in events)


def test_inband_error_event_raises_for_retry_decision():
    """流内 error 事件 → 内部异常交给外层统一重试决策（外审回稿）。"""
    events, terminal = _consume(_sse([
        {"type": "error", "error": {"type": "overloaded_error", "message": "上游过载"}},
    ]))
    assert terminal is not None
    assert terminal.error.error_class == ErrorClass.NETWORK
    assert terminal.error.retryable is True
    assert events == []


def test_non_utf8_stream_wrapped_as_error():
    chunks = [b"data: \xff\xfe\xfd bad\n\n"]
    events, terminal = _consume(chunks)
    assert terminal is not None
    assert terminal.error.error_class == ErrorClass.UNKNOWN
    assert "UTF-8" in terminal.error.message
