"""Provider 协议（harness-session §8）：core/循环 依赖此抽象，不依赖具体适配器。

messages/tools 使用内部块格式：content 块 text/thinking/tool_use/tool_result，
工具 {name, description, input_schema}；各适配器负责请求协议转换和输出归一。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Literal, Protocol

from .types import StreamEvent

Slot = Literal["main", "aux"]


class Provider(Protocol):
    def stream(
        self,
        slot: Slot,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        thinking: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式请求：产出归一 StreamEvent；SDK 异常由任务监督层记录失败。"""
        ...
