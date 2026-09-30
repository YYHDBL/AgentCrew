"""Provider 层数据类型与协议（harness-session §8）。

归一事件：text_delta / thinking_delta / tool_call_started / tool_call /
usage / done / error。相对设计枚举（text_delta/tool_call/usage/done/error）
的补充（ADR-009 实测依据 + C4 外审回稿）：
- thinking_delta：GLM 默认输出思考块，如实暴露、循环侧可忽略；
- tool_call_started：v1.6"拼装完成前对前端只报进行中不带参数"的载体；
- thinking_block：思考块完成事件（全文 + signature）——C8 重建历史时按
  保守姿态回传 thinking 块所需的签名只能从这里拿到（signature_delta 碎片
  由适配层拼装，外审回稿修复）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class ErrorClass(StrEnum):
    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    CONTENT_FILTER = "content_filter"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True)
class ToolCall:
    """内部工具调用格式（各家 tool_use 差异封在适配层）；input 必须是对象。"""

    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderError:
    error_class: ErrorClass
    message: str
    retryable: bool = False
    status_code: int | None = None


EventType = Literal[
    "text_delta", "thinking_delta", "thinking_block", "tool_call_started",
    "tool_call", "usage", "done", "error",
]


@dataclass(frozen=True)
class StreamEvent:
    type: EventType
    text: str | None = None            # text_delta/thinking_delta 增量；thinking_block 全文
    signature: str | None = None       # thinking_block：思考块签名（回传历史所需）
    tool_name: str | None = None       # tool_call_started：进行中提示（不带参数）
    tool_call: ToolCall | None = None  # tool_call：拼装完成的完整调用
    usage: Usage | None = None         # usage / done
    stop_reason: str | None = None     # done：end_turn / tool_use / max_tokens…
    error: ProviderError | None = None


def text_delta(chunk: str) -> StreamEvent:
    return StreamEvent(type="text_delta", text=chunk)


def thinking_delta(chunk: str) -> StreamEvent:
    return StreamEvent(type="thinking_delta", text=chunk)
