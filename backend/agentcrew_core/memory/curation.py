"""基于真实使用时间、固定状态和有效引用的确定性遗忘。"""

from copy import deepcopy
from datetime import datetime, timedelta

from . import render_entries


def curation_plan(entries, *, now: datetime, quota: int | None, referenced=False):
    if now.tzinfo is None:
        raise ValueError("治理时间必须包含时区")
    actions, exempt = [], []
    prospective = deepcopy(entries)
    for entry in prospective:
        if entry["state"] == "archived":
            continue
        if entry["state"] == "pinned" or referenced:
            exempt.append({"entry_id": entry["entry_id"], "reason": "pinned" if entry["state"] == "pinned" else "active_skill_reference"})
            continue
        used = datetime.fromisoformat(entry["last_hit_at"] or entry["created_at"])
        if used.tzinfo is None or used > now:
            raise ValueError("记忆使用时间缺少时区或位于未来")
        age = now - used
        action = "archive" if age >= timedelta(days=30) else "stale" if age >= timedelta(days=14) and entry["state"] == "active" else None
        if action:
            actions.append({"action": action, "entry_hash": entry["entry_hash"], "entry_id": entry["entry_id"],
                "reason": "unused_30_days" if action == "archive" else "unused_14_days"})
            entry["state"] = "archived" if action == "archive" else "stale"
    if quota is not None and len(render_entries(prospective)) > quota:
        candidates = sorted((entry for entry in prospective if entry["state"] not in {"archived", "pinned"} and not referenced),
            key=lambda entry: (entry["state"] != "stale", entry["hits"], entry["last_hit_at"] or entry["created_at"], entry["entry_id"]))
        for entry in candidates:
            if len(render_entries(prospective)) <= quota:
                break
            actions = [action for action in actions if action["entry_id"] != entry["entry_id"]]
            actions.append({"action": "archive", "entry_hash": entry["entry_hash"], "entry_id": entry["entry_id"], "reason": "quota_priority"})
            entry["state"] = "archived"
        if len(render_entries(prospective)) > quota:
            raise ValueError("QUOTA_EXCEEDED：固定条目或有效引用超过配额，禁止自动归档")
    return actions, exempt
