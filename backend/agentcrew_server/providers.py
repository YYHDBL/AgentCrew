"""服务层 Provider 装配：按模型槽绑定协议，共享 HTTP 连接池。"""

from __future__ import annotations

from typing import Any, AsyncIterator

import httpx

from agentcrew_core.provider import GLMAnthropicProvider, SlotConfig
from agentcrew_core.provider.glm_anthropic import DEFAULT_BASE_URL
from agentcrew_core.provider.openai_compatible import OpenAICompatibleProvider
from agentcrew_core.provider.types import StreamEvent


def bind_slot(entry: dict[str, Any]) -> SlotConfig:
    provider = entry.get("provider", "glm")
    if provider not in ("glm", "openai-compatible"):
        raise ValueError(f"不支持的 provider：{provider}")
    base_url = entry.get("base_url") or (DEFAULT_BASE_URL if provider == "glm" else "")
    if not base_url:
        raise ValueError("openai-compatible 需要配置 base_url")
    if provider == "openai-compatible":
        base_url = base_url.rstrip("/").removesuffix("/chat/completions")
    return SlotConfig(model=entry.get("model", ""), api_key=entry.get("api_key", ""),
                      base_url=base_url, max_tokens=int(entry.get("max_tokens", 4096)),
                      provider=provider)


class ConfiguredProvider:
    def __init__(self, slots: dict[str, SlotConfig], *, client: httpx.AsyncClient | None = None,
                 session_id: str | None = None):
        self.slots = slots
        self.client = client if client is not None else httpx.AsyncClient(timeout=180)
        self.session_id = session_id

    async def aclose(self) -> None:
        await self.client.aclose()

    def stream(self, slot: str, *args, **kwargs) -> AsyncIterator[StreamEvent]:
        if self.slots[slot].provider == "glm":
            adapter = GLMAnthropicProvider(self.slots, client=self.client)
        else:
            adapter = OpenAICompatibleProvider(self.slots, client=self.client,
                                               session_id=self.session_id)
        return adapter.stream(slot, *args, **kwargs)


def build_provider(models_cfg: dict[str, Any], *,
                   client: httpx.AsyncClient | None = None,
                   session_id: str | None = None) -> ConfiguredProvider:
    return ConfiguredProvider({slot: bind_slot(models_cfg.get(slot) or {})
                               for slot in ("main", "aux")}, client=client, session_id=session_id)
