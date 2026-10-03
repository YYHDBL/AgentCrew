"""历史压缩的保护边界与结构化摘要验证。"""

from __future__ import annotations

import json
from copy import deepcopy

from jsonschema import Draft202012Validator


SUMMARY_FIELDS = ("historical_tasks", "goals", "constraints_preferences",
                  "completed_actions", "current_status")
SUMMARY_SCHEMA = {
    "type": "object", "required": list(SUMMARY_FIELDS),
    "additionalProperties": False,
    "properties": {field: {"type": "string", "minLength": 1} for field in SUMMARY_FIELDS},
}
SUMMARY_TEMPLATE_VERSION = "M1-08-structured-v1"


def protected_start(messages: list[dict]) -> int:
    """确定最近二十条消息的起点，并向前包含完整工具组合。"""
    start = max(0, len(messages) - 20)
    while start > 0:
        first = messages[start]
        results = {block.get("tool_use_id") for block in first.get("content", [])
                   if isinstance(block, dict) and block.get("type") == "tool_result"}
        if not results:
            break
        parent = next((index for index in range(start - 1, -1, -1)
                       if any(block.get("id") in results for block in messages[index].get("content", [])
                              if isinstance(block, dict) and block.get("type") == "tool_use")), None)
        if parent is None:
            raise ValueError("CONTEXT_BUDGET_EXCEEDED：工具结果缺少完整请求组合")
        start = parent
    return start


def protected_messages(messages: list[dict], start: int, task_run_id: str) -> list[dict]:
    """显式任务身份保护指令；工具纠偏消息不具备该身份。"""
    indices = [index for index, message in enumerate(messages)
               if message.get("_task_instruction_id") == task_run_id]
    if len(indices) != 1:
        raise ValueError("CONTEXT_BUDGET_EXCEEDED：当前任务指令身份不唯一或缺失")
    instruction = indices[0]
    prefix = [messages[instruction]] if instruction < start else []
    return deepcopy(prefix + messages[start:])


def summarizable_messages(messages: list[dict], start: int, task_run_id: str) -> list[dict]:
    return deepcopy([message for message in messages[:start]
                     if message.get("_task_instruction_id") != task_run_id])


def old_tool_results(messages: list[dict], start: int) -> list[tuple[int, int, dict]]:
    return [(index, block_index, block)
            for index, message in enumerate(messages[:start])
            for block_index, block in enumerate(message.get("content", []))
            if isinstance(block, dict) and block.get("type") == "tool_result"]


def with_summary(summary: dict[str, str], protected: list[dict]) -> list[dict]:
    Draft202012Validator(SUMMARY_SCHEMA).validate(summary)
    return [{"role": "user", "content": [{"type": "text", "text":
        "【已完成历史的结构化摘要；仅供参考，当前指令以最新用户消息为准】\n"
        + json.dumps(summary, ensure_ascii=False, sort_keys=True)}]}] + deepcopy(protected)


def summary_input(previous: dict | None, messages: list[dict]) -> str:
    return json.dumps({"previous_summary": previous, "replaced_messages": messages},
                      ensure_ascii=False, sort_keys=True)
