"""Provider 协议（harness-session §8）：core/循环 依赖此抽象，不依赖具体适配器。

messages/tools 为 Provider 原生格式（GLM/Anthropic 块格式：content 块
text/thinking/tool_use，工具 {name, description, input_schema}）；C8 的消息
组装层负责把内部对话记录转成目标协议格式，工具调用**结果**统一归一为内部
ToolCall——"各家差异封在适配层"指输出侧归一。
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
    ) -> AsyncIterator[StreamEvent]:
        """流式请求：产出归一 StreamEvent；错误以 error 事件产出（不抛异常，
        便于循环侧统一消费；请求完全无法建立时同样以 error 事件收尾）。"""
        ...
