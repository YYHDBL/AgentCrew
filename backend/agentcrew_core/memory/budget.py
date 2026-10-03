"""完整请求的确定性 token 计数与区块预算，IO 由服务层提供。"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from typing import Any, Callable

from tokenizers import Tokenizer

from ..provider.glm_anthropic import SlotConfig
from ..provider.openai_compatible import chat_messages, chat_tools


class ContextBudgetError(ValueError):
    pass


@dataclass(frozen=True)
class ContextBudget:
    model: str
    context_window: int
    counting_method: str
    tokenizer_revision: str
    system_tokens: int
    history_tokens: int
    tool_result_tokens: int
    output_reserved: int
    total_input_tokens: int

    @property
    def history_limit(self) -> int:
        return self.context_window * 60 // 100

    @property
    def compression_required(self) -> bool:
        return self.history_tokens * 100 >= self.history_limit * 80

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if self.system_tokens > self.context_window * 15 // 100:
            raise ContextBudgetError("CONTEXT_BUDGET_EXCEEDED：system 和工具声明超过窗口 15%")
        if self.history_tokens > self.history_limit:
            raise ContextBudgetError("CONTEXT_BUDGET_EXCEEDED：历史超过窗口 60%")
        reserve = self.context_window - self.system_tokens - self.history_tokens
        if reserve < self.context_window * 25 // 100 or self.output_reserved <= 0:
            raise ContextBudgetError("CONTEXT_BUDGET_EXCEEDED：工具结果和输出保留不足")
        if self.total_input_tokens + self.output_reserved > self.context_window or \
                self.tool_result_tokens + self.output_reserved > reserve:
            raise ContextBudgetError("CONTEXT_BUDGET_EXCEEDED：完整输入和最大输出超过实际窗口")


@dataclass(frozen=True)
class RequestCounter:
    tokenizer: Tokenizer
    encode_messages: Callable[..., str]

    def count(self, messages: list[dict], *, thinking_mode: str) -> int:
        prompt = self.encode_messages(messages, thinking_mode=thinking_mode)
        return len(self.tokenizer.encode(prompt, add_special_tokens=False).ids)

    def measure(self, cfg: SlotConfig, messages: list[dict], tools: list[dict] | None,
                *, system: str | None, thinking: dict | None = None,
                max_tokens: int | None = None) -> ContextBudget:
        if cfg.context_window <= 0 or not cfg.context_window_source:
            raise ContextBudgetError("MODEL_WINDOW_UNKNOWN：缺少实际窗口及来源")
        mode = "chat" if thinking is not None and thinking.get("type") == "disabled" else "thinking"
        encoded = chat_messages(messages, system)
        if not encoded or encoded[0]["role"] != "system":
            encoded.insert(0, {"role": "system", "content": ""})
        if tools:
            encoded[0]["tools"] = chat_tools(tools)
        total = self.count(encoded, thinking_mode=mode)
        system_tokens = self.count(encoded[:1], thinking_mode=mode)
        without_results = copy.deepcopy(encoded)
        for message in without_results:
            if message["role"] == "tool":
                message["content"] = ""
        baseline = self.count(without_results, thinking_mode=mode)
        history = baseline - system_tokens
        results = total - baseline
        if history < 0 or results < 0:
            raise ContextBudgetError("TOKENIZER_UNAVAILABLE：消息模板的区块计数不一致")
        return ContextBudget(cfg.model, cfg.context_window,
            "tokenizers/official-deepseek-v4.1", cfg.tokenizer_revision,
            system_tokens, history, results,
            cfg.max_tokens if max_tokens is None else max_tokens, total)
