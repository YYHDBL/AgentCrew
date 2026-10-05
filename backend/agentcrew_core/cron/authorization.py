"""规范化预授权范围的交集与缩小判定。"""

from pathlib import Path

from ..tools.gate import rule_matches
from ..tools.gate import evaluate_gate, GateResult


def path_candidates(tool, allowed, roots):
    candidates = []
    parent = Path(allowed)
    for scope in map(Path, roots):
        if parent.is_relative_to(scope):
            candidates.append({"tool": tool, "pattern": str(parent)})
        elif scope.is_relative_to(parent):
            candidates.append({"tool": tool, "pattern": str(scope)})
    return candidates


def authorization_contains(candidate, choice):
    tool, pattern = choice["tool"], choice["pattern"]
    if candidate["tool"] != tool:
        return False
    if tool in {"write_file", "read_file"}:
        return Path(pattern).is_relative_to(Path(candidate["pattern"]))
    if tool == "http_request":
        if pattern.startswith("*."):
            return pattern == candidate["pattern"] or (candidate["pattern"].startswith("*.") and pattern[2:].endswith(candidate["pattern"][1:]))
        return rule_matches(tool, {"url": "https://" + pattern}, candidate["pattern"])
    return pattern == candidate["pattern"]


def conflict_reason(*, active_occurrence, conversation_active=True, queue_paused=False,
                    queued_items=False, active_task=False, pending_verification=False):
    if active_occurrence:
        return "该计划存在未结束发生"
    if not conversation_active:
        return "目标会话已归档"
    if queue_paused:
        return "目标会话队列已暂停"
    if pending_verification:
        return "目标会话存在待核验调用"
    if active_task:
        return "目标会话存在未结束任务"
    if queued_items:
        return "目标会话已有排队指令"
    return None


def unattended_gate(metadata, inputs, readonly, rules, pre_authorized, agent_id, cwd):
    if metadata.name in {"ask_user", "schedule_task"}:
        return GateResult("deny", "UNATTENDED_INTERACTION：无人值守不能等待人工决定")
    current = evaluate_gate(metadata, inputs, readonly, rules, agent_id, cwd)
    if current.action == "deny":
        return current
    if not metadata.needs_approval or metadata.name == "bash" and readonly:
        return current
    employee = any(rule.agent_id == agent_id and rule.tool_name == metadata.name and rule.effect == "allow"
                   and rule_matches(metadata.name, inputs, rule.pattern, cwd) for rule in rules)
    plan = any(choice["tool"] == metadata.name and rule_matches(metadata.name, inputs, choice["pattern"], cwd)
               for choice in pre_authorized)
    if employee and plan:
        return GateResult("allow", "pre_authorized_pass：员工allow与计划预授权均匹配")
    return GateResult("deny", "PRE_AUTH_EXCEEDED：员工allow与计划预授权必须同时匹配")
