"""管理事件可见范围；共享 USER、工作区及私有员工内容各自核查。"""

MEMORY_EVENT_TYPES = frozenset({"memory.updated", "memory.archived", "memory.snapshot_created", "skill.patched",
    "memory.job_status", "memory.summary_created", "memory.approval_requested", "memory.approval_resolved",
    "memory.curated", "context.compacted", "context.budget_checked"})


def memory_event_visible(kind, payload, workspace, agent, *, context_scope=None):
    if kind not in MEMORY_EVENT_TYPES:
        return False
    scope = payload.get("scope")
    if scope is None and kind in {"context.compacted", "context.budget_checked"}:
        scope = context_scope
    if not isinstance(scope, dict) or scope.get("owner_id") != "owner":
        return False
    if kind in {"memory.updated", "memory.archived", "skill.patched"}:
        store_type = payload.get("store_type")
        if store_type == "user":
            return payload.get("store_id") == "owner"
        if store_type == "workspace":
            return scope.get("workspace_id") == workspace
    return scope.get("workspace_id") == workspace and scope.get("agent_id") == agent
