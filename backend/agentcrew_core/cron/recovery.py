"""自动重试的次数、真实时刻与副作用安全判定。"""


def permission_refused(reason, outputs):
    text = "\n".join([reason or "", *outputs]).casefold()
    return any(code.casefold() in text for code in ("PERMISSION_DENIED", "PRE_AUTH", "OUT_OF_SCOPE", "AUTHORIZATION", "PLAN_DISABLED",
        "PROTECTED_PATH", "UNATTENDED", "permission denied", "operation not permitted"))


def side_effect_safety(calls):
    return {
        "pending": any(call["status"] == "pending_verification" for call in calls),
        "unknown": any(not call["read_only"] and (call["status"] in {"dispatched", "outcome_unknown"} or
            call["status"] == "failed" and call["dispatched_at"] is not None and
            call["side_effect_class"] in {"outcome_unknown", "external_idempotency"}) for call in calls),
        "completed_effect": any(call["status"] == "completed" and not call["read_only"] for call in calls),
    }


def retry_decision(*, enabled, revision_matches, retry_no, pending, denied, unknown,
                   completed_effect, failed, now_ms):
    if pending:
        return {"status": "pending_verification", "retry_at": None, "reason": "存在待核验调用"}
    if unknown:
        return {"status": "interrupted", "retry_at": None, "reason": "副作用无法证明，禁止自动重试"}
    if completed_effect and failed:
        return {"status": "interrupted", "retry_at": None, "reason": "已经完成副作用，需真人核查原事件和账本后接续"}
    if not failed:
        return {"status": "completed", "retry_at": None, "reason": None}
    if not enabled or not revision_matches or denied:
        return {"status": "failed", "retry_at": None, "reason": "计划或当前授权不允许自动重试"}
    if retry_no >= 3:
        return {"status": "failed", "retry_at": None, "reason": "已达到额外三次重试上限"}
    return {"status": "retry_wait", "retry_at": now_ms + 30000, "reason": None}
