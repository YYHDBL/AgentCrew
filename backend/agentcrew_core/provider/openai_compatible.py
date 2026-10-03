"""Chat Completions 适配：SDK 负责 SSE 和工具参数增量拼装。"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx
from openai import AsyncOpenAI, omit
from openai.lib.streaming.chat import ChatCompletionStreamState
from openai.types.chat import ChatCompletionChunk

from .base import Slot
from .glm_anthropic import SlotConfig
from .types import StreamEvent, ToolCall, Usage


def chat_messages(messages: list[dict[str, Any]],
                  system: str | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if system:
        result.append({"role": "system", "content": system})
    for message in messages:
        role, content = message["role"], message["content"]
        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue
        text, reasoning, calls = [], [], []
        for block in content:
            kind = block["type"]
            if kind == "text":
                text.append(block["text"])
            elif kind == "thinking" and role == "assistant":
                reasoning.append(block["thinking"])
            elif kind == "tool_use" and role == "assistant":
                calls.append({"id": block["id"], "type": "function", "function": {
                    "name": block["name"],
                    "arguments": json.dumps(block["input"], ensure_ascii=False),
                }})
            elif kind == "tool_result" and role == "user":
                result.append({"role": "tool", "tool_call_id": block["tool_use_id"],
                               "content": block["content"]})
            else:
                raise ValueError(f"不支持的 Chat Completions 消息块：{role}/{kind}")
        if text or calls or reasoning:
            translated: dict[str, Any] = {"role": role, "content": "\n".join(text) or None}
            if calls:
                translated["tool_calls"] = calls
            if reasoning:
                translated["reasoning_content"] = "\n".join(reasoning)
            result.append(translated)
    return result


def chat_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {
        "name": tool["name"], "description": tool.get("description", ""),
        "parameters": tool["input_schema"],
    }} for tool in tools]


def normalize_chat_chunk(chunk: ChatCompletionChunk) -> ChatCompletionChunk:
    # 增量中的 null 表示没有提供更新，空字符串仍保留；原始响应保持完整。
    return ChatCompletionChunk.model_validate(chunk.model_dump(exclude_none=True))


class OpenAICompatibleProvider:
    def __init__(self, slots: dict[str, SlotConfig], *, client: httpx.AsyncClient,
                 session_id: str | None = None):
        self._slots = slots
        self._client = client
        self._session_id = session_id

    async def stream(
        self, slot: Slot, messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None, *,
        thinking: dict[str, Any] | None = None,
        max_tokens: int | None = None, system: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        cfg = self._slots[slot]
        headers = {"User-Agent": "agentcrew/0.1.0"}
        if self._session_id:
            headers["x-opencode-session"] = self._session_id
        client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url,
                             http_client=self._client, max_retries=3, timeout=180,
                             default_headers=headers)
        request: dict[str, Any] = {
            "model": cfg.model, "messages": chat_messages(messages, system),
            "max_tokens": max_tokens if max_tokens is not None else cfg.max_tokens,
            "stream_options": {"include_usage": True},
        }
        if tools:
            request["tools"] = chat_tools(tools)
        if thinking is not None:
            request["extra_body"] = {"thinking": thinking}
        started: set[int] = set()
        reasoning: list[str] = []
        raw_stream = await client.chat.completions.create(**request, stream=True)
        state = ChatCompletionStreamState(response_format=omit, input_tools=omit)
        async with raw_stream:
            async for chunk in raw_stream:
                for event in state.handle_chunk(normalize_chat_chunk(chunk)):
                    if event.type == "content.delta":
                        yield StreamEvent(type="text_delta", text=event.delta)
                    elif event.type == "refusal.delta":
                        yield StreamEvent(type="text_delta", text=event.delta)
                    elif event.type == "tool_calls.function.arguments.delta":
                        if event.index not in started:
                            started.add(event.index)
                            yield StreamEvent(type="tool_call_started", tool_name=event.name)
                    elif event.type == "chunk":
                        for choice in event.chunk.choices:
                            delta = getattr(choice.delta, "reasoning_content", None)
                            if delta:
                                reasoning.append(delta)
                                yield StreamEvent(type="thinking_delta", text=delta)
            completion = state.get_final_completion()
        choice = completion.choices[0]
        if choice.finish_reason not in ("stop", "tool_calls"):
            raise ValueError(f"Chat Completions 未正常结束：{choice.finish_reason}")
        if reasoning:
            yield StreamEvent(type="thinking_block", text="".join(reasoning), signature="")
        for call in choice.message.tool_calls or []:
            arguments = json.loads(call.function.arguments)
            if not isinstance(arguments, dict) or not call.id or not call.function.name:
                raise ValueError("Chat Completions 工具调用缺少标识或对象参数")
            yield StreamEvent(type="tool_call", tool_call=ToolCall(
                call.id, call.function.name, arguments))
        usage = completion.usage
        if usage:
            yield StreamEvent(type="usage", usage=Usage(
                input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
                cache_read_input_tokens=(usage.prompt_tokens_details.cached_tokens or 0)
                if usage.prompt_tokens_details else 0))
        yield StreamEvent(type="done", stop_reason=(
            "tool_use" if choice.finish_reason == "tool_calls" else "end_turn"))
