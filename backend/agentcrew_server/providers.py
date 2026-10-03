"""服务层 Provider 装配：按模型槽绑定协议，共享 HTTP 连接池。"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, AsyncIterator

import httpx

from agentcrew_core.provider import GLMAnthropicProvider, SlotConfig
from agentcrew_core.provider.glm_anthropic import DEFAULT_BASE_URL
from agentcrew_core.provider.openai_compatible import OpenAICompatibleProvider
from agentcrew_core.provider.types import StreamEvent

from .memory.tokenizer import DEEPSEEK_V41, TOKENIZER_FIELDS, load_counter
from agentcrew_core.memory.budget import ContextBudgetError

_log = logging.getLogger("agentcrew.context")


def bind_slot(entry: dict[str, Any]) -> SlotConfig:
    provider = entry.get("provider", "glm")
    if provider not in ("glm", "openai-compatible"):
        raise ValueError(f"不支持的 provider：{provider}")
    base_url = entry.get("base_url") or (DEFAULT_BASE_URL if provider == "glm" else "")
    if not base_url:
        raise ValueError("openai-compatible 需要配置 base_url")
    if provider == "openai-compatible":
        base_url = base_url.rstrip("/").removesuffix("/chat/completions")
    verified = (DEEPSEEK_V41 if provider == "openai-compatible"
                and entry.get("model") == "deepseek-v4.1-flash"
                and base_url == "https://opencode.ai/zen/go/v1"
                and not any(field in entry for field in TOKENIZER_FIELDS) else {})
    return SlotConfig(model=entry.get("model", ""), api_key=entry.get("api_key", ""),
                      base_url=base_url, max_tokens=int(entry.get("max_tokens", 4096)),
                      provider=provider,
                      **{**verified, **{field: entry[field] for field in TOKENIZER_FIELDS if field in entry}})


class ConfiguredProvider:
    def __init__(self, slots: dict[str, SlotConfig], *, client: httpx.AsyncClient | None = None,
                 session_id: str | None = None, budget_sink=None):
        self.slots = slots
        self.client = client if client is not None else httpx.AsyncClient(timeout=180)
        self.session_id = session_id
        self.budget_sink = budget_sink

    async def aclose(self) -> None:
        await self.client.aclose()

    async def stream(self, slot: str, messages, tools=None, **kwargs) -> AsyncIterator[StreamEvent]:
        cfg = self.slots[slot]
        counter = await asyncio.to_thread(load_counter, cfg)
        max_tokens = kwargs.get("max_tokens")
        max_tokens = cfg.max_tokens if max_tokens is None else max_tokens
        if type(max_tokens) is not int or not 1 <= max_tokens <= 384_000:
            raise ContextBudgetError("CONTEXT_BUDGET_EXCEEDED：max_tokens 超过实际模型输出上限")
        budget = await asyncio.to_thread(counter.measure, cfg, messages, tools,
            system=kwargs.get("system"), thinking=kwargs.get("thinking"),
            max_tokens=kwargs.get("max_tokens"))
        _log.info("context.budget_checked slot=%s session=%s counts=%s source=%s sha256=%s",
            slot, self.session_id, budget.payload(), cfg.context_window_source, cfg.tokenizer_sha256)
        if self.budget_sink is not None:
            await self.budget_sink("context.budget_checked", budget.payload())
        try:
            budget.validate()
        finally:
            if rejection := sys.exception():
                _log.warning("context.budget_rejected slot=%s session=%s reason=%s counts=%s",
                             slot, self.session_id, rejection, budget.payload())
        if self.slots[slot].provider == "glm":
            adapter = GLMAnthropicProvider(self.slots, client=self.client)
        else:
            adapter = OpenAICompatibleProvider(self.slots, client=self.client,
                                               session_id=self.session_id)
        stream = adapter.stream(slot, messages, tools, **kwargs)
        try:
            async for event in stream:
                yield event
        finally:
            await stream.aclose()


def build_provider(models_cfg: dict[str, Any], *,
                   client: httpx.AsyncClient | None = None,
                   session_id: str | None = None) -> ConfiguredProvider:
    return ConfiguredProvider({slot: bind_slot(models_cfg.get(slot) or {})
                               for slot in ("main", "aux")}, client=client, session_id=session_id)
