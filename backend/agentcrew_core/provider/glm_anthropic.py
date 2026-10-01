"""GLM 适配器（Anthropic 兼容端点，ADR-009）。

- 请求：POST {base_url}/v1/messages（x-api-key + anthropic-version），流式；
- 归一：thinking_delta / thinking_block（含 signature，C8 回传历史所需）/
  text_delta / tool_call_started / tool_call（input_json_delta 碎片缓冲拼装为
  完整 JSON 且必须是对象，绝不外发半成品）/ usage（message_start 与
  message_delta 合并）/ done / error；
- 错误分类（classify_error 纯函数）：网络 / 限流 / 鉴权 / 内容过滤 / 未知；
- 重试（外审回稿统一）：**任何**终态失败（非 200 响应、流内 error 事件、流截断、
  传输异常）在尚未向下游产出事件时按分类退避重试（上限 3，1s/2s/4s）；已产出
  事件后不自动重发（重发会重复输出），以 error(retryable=…) 上交循环层决策；
- 流完整性：必须收到 message_stop 才算成功，截断（EOF 无 message_stop）与
  非 UTF-8 / 坏 JSON 行都归一为 ProviderError，绝不静默结束、绝不在终态
  错误后再发 done（外审回稿修复）；
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


class _ProviderStreamError(Exception):
    """流级终态失败（内部）：由 stream() 统一转 error 事件或触发重试。"""

    def __init__(self, error: ProviderError):
        super().__init__(error.message)
        self.error = error


@dataclass(frozen=True)
class SlotConfig:
    model: str
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    max_tokens: int = DEFAULT_MAX_TOKENS
    provider: str = "glm"


def backoff_seconds(attempt: int) -> float:
    """attempt 从 0 起：1s → 2s → 4s。"""
    return float(min(2 ** attempt, 8))


def should_retry(error: ProviderError, produced: bool, attempt: int,
                 max_retries: int = MAX_RETRIES) -> bool:
    """统一重试决策（纯函数）：可重试分类 × 尚未产出 × 还有重试额度。"""
    return error.retryable and not produced and attempt < max_retries


def classify_error(status_code: int, body_text: str) -> ProviderError:
    """HTTP 状态 + 错误体 → 错误分类（纯函数，参数化单测）。"""
    text = body_text or ""
    low = text.lower()
    if status_code in (401, 403) or "invalid x-api-key" in low or "authentication" in low \
            or "令牌" in text or ("api_key" in low and "invalid" in low):
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


def _merge_usage(start: Usage | None, delta: Usage | None) -> Usage:
    """合并两端 usage：Anthropic 流式常把输入量放 message_start、输出量放
    message_delta；delta 的非零字段优先，缺失/为零的回退 start。"""
    if start is None:
        return delta or Usage()
    if delta is None:
        return start
    return Usage(
        input_tokens=delta.input_tokens or start.input_tokens,
        output_tokens=delta.output_tokens or start.output_tokens,
        cache_read_input_tokens=(
            delta.cache_read_input_tokens or start.cache_read_input_tokens
        ),
    )


@dataclass
class _ToolBlockState:
    id: str
    name: str
    fragments: list[str] = field(default_factory=list)


@dataclass
class _ThinkingBlockState:
    text_parts: list[str] = field(default_factory=list)
    signature_parts: list[str] = field(default_factory=list)


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
        system: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        cfg = self._slots[slot]
        body: dict[str, Any] = {
            "model": cfg.model,
            "max_tokens": max_tokens or cfg.max_tokens,
            "stream": True,
            "messages": messages,
        }
        if system is not None:
            body["system"] = system  # C8：环境块注入 system 尾部（v1.7）
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
            error: ProviderError | None = None
            try:
                async with self._client.stream(
                    "POST", f"{cfg.base_url}/v1/messages",
                    headers=headers, json=body,
                ) as resp:
                    if resp.status_code != 200:
                        raw = (await resp.aread()).decode("utf-8", "replace")
                        error = classify_error(resp.status_code, raw)
                    else:
                        async for event in self._consume(resp.aiter_bytes()):
                            produced = True
                            yield event
                        return
            except _ProviderStreamError as stream_error:
                error = stream_error.error
            except (httpx.TimeoutException, httpx.TransportError) as e:
                error = ProviderError(
                    ErrorClass.NETWORK, f"请求失败：{e}", retryable=True,
                )
            if error is None:
                return  # 理论不可达（成功路径已 return），防御性收尾

            # 统一重试决策（外审回稿）：未产出 + 可重试分类 → 退避重试
            if should_retry(error, produced, attempt, self._max_retries):
                _log.warning(
                    "provider.retry %s 第 %s 次失败（%s/%s）%.0fs 后重试",
                    slot, attempt + 1, error.error_class.value,
                    error.status_code, backoff_seconds(attempt),
                )
                await asyncio.sleep(backoff_seconds(attempt))
                continue
            yield StreamEvent(type="error", error=error)
            return

    # ── 流式状态机 ────────────────────────────────────────────────
    async def _consume(self, byte_chunks: AsyncIterator[bytes]) -> AsyncIterator[StreamEvent]:
        tools_in_flight: dict[int, _ToolBlockState] = {}
        thinking_in_flight: dict[int, _ThinkingBlockState] = {}
        start_usage: Usage | None = None
        delta_usage: Usage | None = None
        stop_reason: str | None = None
        try:
            async for event in iter_sse_json(byte_chunks):
                etype = event.get("type")
                if etype == "content_block_start":
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        tools_in_flight[event["index"]] = _ToolBlockState(
                            id=block.get("id", ""), name=block.get("name", ""),
                        )
                        yield StreamEvent(type="tool_call_started", tool_name=block.get("name"))
                    elif block.get("type") == "thinking":
                        thinking_in_flight[event["index"]] = _ThinkingBlockState(
                            signature_parts=[block.get("signature") or ""],
                        )
                elif etype == "content_block_delta":
                    delta = event.get("delta") or {}
                    dtype = delta.get("type")
                    if dtype == "text_delta":
                        yield text_delta(delta.get("text", ""))
                    elif dtype == "thinking_delta":
                        chunk = delta.get("thinking", "")
                        state = thinking_in_flight.get(event.get("index"))
                        if state is not None:
                            state.text_parts.append(chunk)
                        yield thinking_delta(chunk)
                    elif dtype == "signature_delta":
                        state = thinking_in_flight.get(event.get("index"))
                        if state is not None:
                            state.signature_parts.append(delta.get("signature", ""))
                    elif dtype == "input_json_delta":
                        state = tools_in_flight.get(event.get("index"))
                        if state is not None:
                            state.fragments.append(delta.get("partial_json", ""))
                elif etype == "content_block_stop":
                    t_state = thinking_in_flight.pop(event.get("index"), None)
                    if t_state is not None:
                        yield StreamEvent(
                            type="thinking_block",
                            text="".join(t_state.text_parts),
                            signature="".join(t_state.signature_parts),
                        )
                    state = tools_in_flight.pop(event.get("index"), None)
                    if state is not None:
                        raw = "".join(state.fragments)
                        try:
                            args = json.loads(raw) if raw.strip() else {}
                        except json.JSONDecodeError as e:
                            raise _ProviderStreamError(ProviderError(
                                ErrorClass.UNKNOWN,
                                f"tool_use 参数 JSON 无效（{state.name}）：{raw[:120]}",
                            )) from e
                        if not isinstance(args, dict):
                            raise _ProviderStreamError(ProviderError(
                                ErrorClass.UNKNOWN,
                                f"tool_use 参数必须是 JSON 对象（{state.name}）："
                                f"{raw[:120]}",
                            ))
                        yield StreamEvent(
                            type="tool_call",
                            tool_call=ToolCall(id=state.id, name=state.name, input=args),
                        )
                elif etype == "message_start":
                    message = event.get("message") or {}
                    if message.get("usage"):
                        start_usage = _usage(message["usage"])
                elif etype == "message_delta":
                    stop_reason = (event.get("delta") or {}).get("stop_reason") or stop_reason
                    if event.get("usage"):
                        delta_usage = _usage(event["usage"])
                elif etype == "message_stop":
                    if tools_in_flight or thinking_in_flight:
                        # 内容块未关闭就终流：工具调用会被静默丢弃（外审回稿
                        # S15）——归一为错误事件，绝不带 usage/done 假装成功
                        raise _ProviderStreamError(ProviderError(
                            ErrorClass.UNKNOWN,
                            "message_stop 时仍有未关闭的内容块"
                            f"（tool_use×{len(tools_in_flight)}"
                            f"/thinking×{len(thinking_in_flight)}）",
                            retryable=False,
                        ))
                    final_usage = _merge_usage(start_usage, delta_usage)
                    if delta_usage is None and start_usage is None:
                        _log.warning("provider.usage_missing usage 缺失，记 0（不失败）")
                    yield StreamEvent(type="usage", usage=final_usage)
                    yield StreamEvent(type="done", usage=final_usage, stop_reason=stop_reason)
                    return
                elif etype == "error":
                    payload = event.get("error") or {}
                    raise _ProviderStreamError(classify_error(
                        int(payload.get("status_code") or 0) or 500,
                        json.dumps(payload, ensure_ascii=False),
                    ))
                # ping：无下游意义，跳过
        except SSEDecodeError as e:
            raise _ProviderStreamError(ProviderError(
                ErrorClass.UNKNOWN, f"上游流解析失败：{e}", retryable=False,
            )) from e
        except UnicodeDecodeError as e:
            raise _ProviderStreamError(ProviderError(
                ErrorClass.UNKNOWN, f"上游流包含非 UTF-8 字节：{e}", retryable=False,
            )) from e
        # HTTP 200 但流被截断（EOF 且未收到 message_stop）——绝不静默结束
        raise _ProviderStreamError(ProviderError(
            ErrorClass.NETWORK, "流被截断：未收到 message_stop", retryable=True,
        ))
