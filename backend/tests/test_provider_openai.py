import asyncio
import json
from pathlib import Path

import httpx
import pytest

from agentcrew_core.loop import assistant_message, tool_results_message, user_text_message
from agentcrew_core.provider.openai_compatible import chat_messages
from agentcrew_core.provider.types import ToolCall
from agentcrew_core.tools.metadata import ToolResult
from agentcrew_server.providers import bind_slot, build_provider


def test_recorded_opencode_null_deltas_preserve_actual_tool_identity():
    from openai._streaming import SSEDecoder
    from openai.lib.streaming.chat import ChatCompletionStreamState
    from openai.types.chat import ChatCompletionChunk
    from agentcrew_core.provider import openai_compatible

    # 真实 2026-10-03 响应，仅验证已收到的分片，保持中断状态。
    raw = Path(__file__).with_name("fixtures").joinpath("opencode-null-tool-delta.sse").read_bytes()
    state = ChatCompletionStreamState()
    chunks = [ChatCompletionChunk.model_validate(json.loads(event.data))
              for event in SSEDecoder().iter_bytes(iter([raw]))]
    originals = [chunk.model_dump() for chunk in chunks]
    for chunk in chunks:
        state.handle_chunk(openai_compatible.normalize_chat_chunk(chunk))
    call = state.current_completion_snapshot.choices[0].message.tool_calls[0]
    assert call.id == "chatcmpl-tool-b4781891f114923a"
    assert call.type == "function"
    assert call.function.name == "memory_write"
    assert call.function.arguments == ""
    assert state.current_completion_snapshot.choices[0].finish_reason is None
    assert [chunk.model_dump() for chunk in chunks] == originals


def test_chat_messages_preserve_tool_results_and_thinking():
    call = ToolCall("call-1", "write_file", {"path": "中文.txt", "content": "实际内容"})
    messages = [
        user_text_message("创建文件"),
        assistant_message([{"text": "需要写入文件", "signature": ""}], "正在写入", [call]),
        tool_results_message([call], [ToolResult(ok=False, error="PERMISSION_DENIED")]),
    ]
    messages[-1]["content"].append({"type": "text", "text": "请处理拒绝结果"})
    translated = chat_messages(messages, system="使用实际工具")
    assert translated[0] == {"role": "system", "content": "使用实际工具"}
    assert translated[1] == {"role": "user", "content": "创建文件"}
    assert translated[2]["reasoning_content"] == "需要写入文件"
    assert translated[2]["content"] == "正在写入"
    tool = translated[2]["tool_calls"][0]
    assert tool["id"] == call.id and tool["function"]["name"] == call.name
    assert json.loads(tool["function"]["arguments"]) == call.input
    assert translated[3] == {"role": "tool", "tool_call_id": call.id,
                             "content": "错误：PERMISSION_DENIED"}
    assert translated[4] == {"role": "user", "content": "请处理拒绝结果"}
    assert messages[-1]["role"] == "user"
    with pytest.raises(ValueError, match="不支持"):
        chat_messages([{"role": "user", "content": [{"type": "unknown"}]}])


def test_provider_binding_uses_configured_protocol_and_shared_client():
    entry = {"provider": "openai-compatible", "model": "deepseek-v4.1-flash",
             "base_url": "https://opencode.ai/zen/go/v1/chat/completions"}
    slot = bind_slot(entry)
    assert slot.base_url == "https://opencode.ai/zen/go/v1"
    assert slot.provider == "openai-compatible"
    assert bind_slot({}).provider == "glm"
    with pytest.raises(ValueError, match="provider"):
        bind_slot({"provider": "unsupported"})
    with pytest.raises(ValueError, match="base_url"):
        bind_slot({"provider": "openai-compatible"})
    async def check():
        async with httpx.AsyncClient() as client:
            provider = build_provider({"main": entry}, client=client, session_id="conversation-1")
            assert provider.slots["main"] == slot
            assert provider.slots["aux"].provider == "glm"
            assert provider.client is client
            assert provider.session_id == "conversation-1"
    asyncio.run(check())
