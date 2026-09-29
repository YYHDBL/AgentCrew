"""服务层 Provider 装配：config.models 双槽 → GLM 适配器（M0-C4，ADR-009）。"""

from __future__ import annotations

from typing import Any

from agentcrew_core.provider import GLMAnthropicProvider, SlotConfig
from agentcrew_core.provider.glm_anthropic import DEFAULT_BASE_URL


def build_provider(models_cfg: dict[str, Any]) -> GLMAnthropicProvider:
    slots: dict[str, SlotConfig] = {}
    for slot in ("main", "aux"):
        entry = models_cfg.get(slot) or {}
        slots[slot] = SlotConfig(
            model=entry.get("model", ""),
            api_key=entry.get("api_key", ""),
            base_url=entry.get("base_url") or DEFAULT_BASE_URL,
            max_tokens=int(entry.get("max_tokens", 4096)),
        )
    return GLMAnthropicProvider(slots)
