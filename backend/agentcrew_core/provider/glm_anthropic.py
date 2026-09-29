"""GLM 适配器（Anthropic 兼容端点，ADR-009）。

- 请求：POST {base_url}/v1/messages（x-api-key + anthropic-version），流式；
- 归一：thinking_delta / text_delta / tool_call_started / tool_call（input_json_delta
  碎片缓冲拼装为完整 JSON，绝不外发半成品）/ usage / done / error；
- 错误分类（classify_error 纯函数）：网络 / 限流 / 鉴权 / 内容过滤 / 未知；
- 重试：限流与网络错误退避重试上限 3（首试 + 3 次）；**已向下游产出过事件后
  的流中断不自动重发**——Anthropic 端点无续传原语，重发会重复输出，以
  error(retryable=true) 上交循环层决策（如实声明，见 provider-verification）；
- usage 缺失：记 0 并打警告日志，不失败。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from .sse_parser import SSEDecodeError, iter_sse_json
from .types import (
    ErrorClass,
    ProviderError,
    StreamEvent,
    ToolCall,
    Usage,
    text_delta,
    thinking_delta,
)

_log = logging.getLogger("agentcrew.provider")

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/anthropic"
DEFAULT_MAX_TOKENS = 4096
MAX_RETRIES = 3  # 首试之外的重试上限（M0-cards C4：退避，上限 3）


@dataclass(frozen=True)
class SlotConfig:
    model: str
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    max_tokens: int = DEFAULT_MAX_TOKENS


def backoff_seconds(attempt: int) -> float:
    """attempt 从 0 起：1s → 2s → 4s。"""
    return float(min(2 ** attempt, 8))


def classify_error(status_code: int, body_text: str) -> ProviderError:
    """HTTP 状态 + 错误体 → 错误分类（纯函数，参数化单测）。"""
    text = body_text or ""
    low = text.lower()
    if status_code in (401, 403) or "invalid x-api-key" in low or "authentication" in low \
            or "令牌" in text or "api_key" in low and "invalid" in low:
        return ProviderError(ErrorClass.AUTH, _short_message(text) or "鉴权失败",
                             retryable=False, status_code=status_code)
    if status_code == 429 or "rate limit" in low or "rate_limit" in low:
        return ProviderError(ErrorClass.RATE_LIMIT, _short_message(text) or "限流",
                             retryable=True, status_code=status_code)
    if "content_filter" in low or "内容安全" in text or "敏感内容" in text:
        return ProviderError(ErrorClass.CONTENT_FILTER, _short_message(text) or "内容过滤",
                             retryable=False, status_code=status_code)
    if "余额不足" in text or '"1113"' in text:
        return ProviderError(ErrorClass.UNKNOWN, f"账户资源不足：{_short_message(text)}",
                             retryable=False, status_code=status_code)
    if status_code >= 500 or status_code == 408 or status_code == 529:
        return ProviderError(ErrorClass.NETWORK, _short_message(text) or "上游服务错误",
                             retryable=True, status_code=status_code)
    return ProviderError(ErrorClass.UNKNOWN, _short_message(text) or f"HTTP {status_code}",
                         retryable=False, status_code=status_code)


def _short_message(body_text: str) -> str:
    try:
        payload = json.loads(body_text)
        message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
        if message:
            return str(message)[:200]
    except (json.JSONDecodeError, AttributeError):
        pass
    return body_text[:200].strip()


def _usage(payload: dict) -> Usage:
    return Usage(
        input_tokens=int(payload.get("input_tokens") or 0),
        output_tokens=int(payload.get("output_tokens") or 0),
        cache_read_input_tokens=int(payload.get("cache_read_input_tokens") or 0),
    )


@dataclass
class _ToolBlockState:
    id: str
    name: str
    fragments: list[str] = field(default_factory=list)


class GLMAnthropicProvider:
    def __init__(
        self,
        slots: dict[str, SlotConfig],
        *,
        timeout_s: float = 180.0,
        max_retries: int = MAX_RETRIES,
        client: httpx.AsyncClient | None = None,
    ):
        self._slots = slots
        self._max_retries = max_retries
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def stream(
        self,
        slot,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        thinking: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        cfg = self._slots[slot]
        body: dict[str, Any] = {
            "model": cfg.model,
            "max_tokens": max_tokens or cfg.max_tokens,
            "stream": True,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
        if thinking is not None:
            body["thinking"] = thinking
        headers = {
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        produced = False  # 是否已向下游产出过事件（决定中断时能否安全重发）
        for attempt in range(self._max_retries + 1):
            try:
                async with self._client.stream(
                    "POST", f"{cfg.base_url}/v1/messages",
                    headers=headers, json=body,
                ) as resp:
                    if resp.status_code != 200:
                        raw = (await resp.aread()).decode("utf-8", "replace")
                        error = classify_error(resp.status_code, raw)
                        if error.retryable and attempt < self._max_retries:
                            _log.warning(
                                "provider.retry %s 第 %s 次失败（%s/%s）%.0fs 后重试",
                                slot, attempt + 1, error.error_class.value,
                                error.status_code, backoff_seconds(attempt),
                            )
                            await asyncio.sleep(backoff_seconds(attempt))
                            continue
                        yield StreamEvent(type="error", error=error)
                        return
                    async for event in self._consume(resp.aiter_bytes()):
                        produced = True
                        yield event
                    return
            except (httpx.TimeoutException, httpx.TransportError, SSEDecodeError) as e:
                if produced:
                    # 已产出过事件：重发会重复输出，交上层决策
                    yield StreamEvent(type="error", error=ProviderError(
                        ErrorClass.NETWORK, f"流中断：{e}", retryable=True,
                    ))
                    return
                if attempt < self._max_retries:
                    _log.warning(
                        "provider.retry %s 第 %s 次失败（%s）%.0fs 后重试",
                        slot, attempt + 1, type(e).__name__, backoff_seconds(attempt),
                    )
                    await asyncio.sleep(backoff_seconds(attempt))
                    continue
                yield StreamEvent(type="error", error=ProviderError(
                    ErrorClass.NETWORK, f"请求失败（已重试 {self._max_retries} 次）：{e}",
                    retryable=False,
                ))
                return

    # ── 流式状态机 ────────────────────────────────────────────────
    async def _consume(self, byte_chunks: AsyncIterator[bytes]) -> AsyncIterator[StreamEvent]:
        tools_in_flight: dict[int, _ToolBlockState] = {}
        final_usage: Usage | None = None
        stop_reason: str | None = None
        async for event in iter_sse_json(byte_chunks):
            etype = event.get("type")
            if etype == "content_block_start":
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    tools_in_flight[event["index"]] = _ToolBlockState(
                        id=block.get("id", ""), name=block.get("name", ""),
                    )
                    yield StreamEvent(type="tool_call_started", tool_name=block.get("name"))
            elif etype == "content_block_delta":
                delta = event.get("delta") or {}
                dtype = delta.get("type")
                if dtype == "text_delta":
                    yield text_delta(delta.get("text", ""))
                elif dtype == "thinking_delta":
                    yield thinking_delta(delta.get("thinking", ""))
                elif dtype == "input_json_delta":
                    state = tools_in_flight.get(event.get("index"))
                    if state is not None:
                        state.fragments.append(delta.get("partial_json", ""))
            elif etype == "content_block_stop":
                state = tools_in_flight.pop(event.get("index"), None)
                if state is not None:
                    raw = "".join(state.fragments)
                    try:
                        args = json.loads(raw) if raw.strip() else {}
                    except json.JSONDecodeError:
                        yield StreamEvent(type="error", error=ProviderError(
                            ErrorClass.UNKNOWN,
                            f"tool_use 参数 JSON 无效（{state.name}）：{raw[:120]}",
                        ))
                        continue
                    yield StreamEvent(
                        type="tool_call",
                        tool_call=ToolCall(id=state.id, name=state.name, input=args),
                    )
            elif etype == "message_delta":
                stop_reason = (event.get("delta") or {}).get("stop_reason") or stop_reason
                if event.get("usage"):
                    final_usage = _usage(event["usage"])
                    yield StreamEvent(type="usage", usage=final_usage)
            elif etype == "message_stop":
                if final_usage is None:
                    _log.warning("provider.usage_missing usage 缺失，记 0（不失败）")
                    final_usage = Usage()
                    yield StreamEvent(type="usage", usage=final_usage)
                yield StreamEvent(type="done", usage=final_usage, stop_reason=stop_reason)
            elif etype == "error":
                payload = event.get("error") or {}
                yield StreamEvent(type="error", error=classify_error(
                    int(payload.get("status_code") or 0) or 500,
                    json.dumps(payload, ensure_ascii=False),
                ))
            # message_start / ping：无下游意义，跳过
