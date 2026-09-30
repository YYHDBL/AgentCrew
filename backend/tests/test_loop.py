"""ReAct 循环纯函数单测（M0-C8）：环境块 / 指纹 / 重复守门 / 解析错误分类 /
消息组装。真实 GLM 端到端在 scripts/loop/c8_demo.py（ADR-006）。"""

from __future__ import annotations

from datetime import datetime

from agentcrew_core.loop import (
    LoopGates,
    PARSE_RETRY_LIMIT,
    ROUND_RESEND_LIMIT,
    RepeatGate,
    assistant_message,
    context_fingerprint,
    env_block,
    is_parse_error,
    tool_results_message,
    user_text_message,
)
from agentcrew_core.provider.types import ToolCall
from agentcrew_core.tools.metadata import ToolResult


def _call(name="read_file", **input) -> ToolCall:
    return ToolCall(id="call_x", name=name, input=input or {"path": "a.txt"})


def _result(ok=True, output="", error=None) -> ToolResult:
    return ToolResult(ok=ok, output=output, error=error)


# ── 环境块（v1.7：字段全集，注入尾部由调用方拼接）─────────────────────

def test_env_block_contains_all_facts():
    now = datetime(2026, 9, 30, 21, 33, 0)  # 周三
    block = env_block(now, "/ws/dir", "/mat/dir", ["/授权A", "/授权B"], "default")
    assert "2026-09-30 21:33:00" in block and "星期三" in block
    assert "macOS" in block
    assert "/ws/dir" in block and "/mat/dir" in block
    assert "/授权A" in block and "/授权B" in block
    assert "default" in block


def test_env_block_no_folders_shows_none():
    block = env_block(datetime.now(), "/ws", "/mat", [], "default")
    assert "授权文件夹：无" in block


# ── context_fingerprint ────────────────────────────────────────────

def test_fingerprint_stable_and_sensitive_to_changes():
    tools = [{"name": "read_file"}]
    cfg = {"model": "glm-5.3", "base_url": "u", "max_tokens": 4096,
           "api_key_sha256": "abc"}
    f1 = context_fingerprint("sys", tools, cfg)
    f2 = context_fingerprint("sys", tools, dict(cfg))
    assert f1 == f2  # 内容相同 → 指纹相同
    assert context_fingerprint("sys2", tools, cfg) != f1
    assert context_fingerprint("sys", tools + [{"name": "bash"}], cfg) != f1
    cfg2 = dict(cfg, max_tokens=2048)
    assert context_fingerprint("sys", tools, cfg2)["model_config"] != \
        f1["model_config"]
    assert f1["system_prompt_hash"] and f1["tools_schema_hash"]


def test_fingerprint_contains_no_plaintext_key():
    cfg = {"model": "m", "api_key_sha256": "digest16"}
    f = context_fingerprint("s", [], cfg)
    assert "secret" not in str(f).lower()
    assert f["model_config"]["api_key_sha256"] == "digest16"


# ── 重复调用守门（§5 三招之一）──────────────────────────────────────

def test_repeat_gate_streak_to_correct_then_doom():
    gate = RepeatGate(limit=3)
    call, result = _call(), _result(output="同")
    assert gate.observe("read_file", call, result) == "ok"
    assert gate.observe("read_file", call, result) == "ok"
    assert gate.observe("read_file", call, result) == "correct"  # 第 3 次
    # 纠偏后再达连击上限 → doom
    assert gate.observe("read_file", call, result) == "doom"


def test_repeat_gate_resets_on_different_input_or_result():
    gate = RepeatGate(limit=3)
    r = _result(output="同")
    assert gate.observe("read_file", _call(path="a.txt"), r) == "ok"
    assert gate.observe("read_file", _call(path="a.txt"), r) == "ok"
    # 不同参数 → 连击重置
    assert gate.observe("read_file", _call(path="b.txt"), r) == "ok"
    assert gate.observe("read_file", _call(path="b.txt"), r) == "ok"
    assert gate.observe("read_file", _call(path="b.txt"), r) == "correct"


def test_repeat_gate_same_input_different_result_resets():
    gate = RepeatGate(limit=3)
    call = _call()
    assert gate.observe("bash", call, _result(output="1")) == "ok"
    assert gate.observe("bash", call, _result(output="1")) == "ok"
    assert gate.observe("bash", call, _result(output="2")) == "ok"  # 结果变了


# ── 解析错误分类（§5 三招之三）──────────────────────────────────────

def test_is_parse_error_by_message_markers():
    class E:  # 最小错误形状（ProviderError 同构）
        def __init__(self, message, retryable=False):
            self.message, self.retryable = message, retryable
    assert is_parse_error(E("tool_use 参数 JSON 无效（read_file）：{"))
    assert is_parse_error(E("message_stop 时仍有未关闭的内容块"))
    assert is_parse_error(E("上游流解析失败：bad line"))
    assert not is_parse_error(E("令牌已过期"))
    # retryable 优先走整轮重发通道，不归解析重试
    assert not is_parse_error(E("流被截断：未收到 message_stop", retryable=True))


def test_retry_limits_fixed_by_design():
    assert ROUND_RESEND_LIMIT == 1   # C4 移交：整轮重发 1 次
    assert PARSE_RETRY_LIMIT == 2    # 解析重试 2 次 → unparseable
    assert LoopGates() == LoopGates(40, 600, 3, 2_000_000)


# ── Anthropic 块式消息组装（保守姿态：thinking 带签名回传）──────────

def test_assistant_message_blocks():
    msg = assistant_message(
        [{"text": "思考", "signature": "sig123"}], "正文",
        [_call(name="bash", command="ls")])
    assert msg["role"] == "assistant"
    kinds = [b["type"] for b in msg["content"]]
    assert kinds == ["thinking", "text", "tool_use"]
    assert msg["content"][0] == {"type": "thinking", "thinking": "思考",
                                 "signature": "sig123"}
    assert msg["content"][2]["type"] == "tool_use"
    assert msg["content"][2]["id"] == "call_x"


def test_tool_results_message_pairs_and_marks_error():
    calls = [_call(path="a.txt"), _call(path="b.txt")]
    results = [_result(output="内容A"), _result(ok=False, error="OUT_OF_SCOPE")]
    msg = tool_results_message(calls, results)
    assert msg["role"] == "user"
    first, second = msg["content"]
    assert first["tool_use_id"] == "call_x" and first["content"] == "内容A"
    assert first["is_error"] is False
    assert "OUT_OF_SCOPE" in second["content"] and second["is_error"] is True


def test_user_text_message_shape():
    assert user_text_message("指令") == {
        "role": "user", "content": [{"type": "text", "text": "指令"}]}
