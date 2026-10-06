"""根据已保存事件计算一个 TaskRun 在指定历史水位的状态。"""

from . import RunEventType as T


def project_run_state(events):
    state = {"status": "queued", "current_attempt_no": 0, "finished_at": None,
             "updated_at": None, "error": None}
    pending = set()
    last_terminal = None
    for event in events:
        kind, payload = event.type, event.payload
        changed = False
        if kind == T.RUN_QUEUED:
            state["status"] = "queued"
            changed = True
        elif kind in {T.RUN_STARTED, T.RUN_RESUMED}:
            state["status"] = "running"
            state["current_attempt_no"] = payload["attempt_no"]
            changed = True
        elif kind in {T.RUN_COMPLETED, T.RUN_FAILED, T.RUN_CANCELLED}:
            status = {T.RUN_COMPLETED: "completed", T.RUN_FAILED: "failed", T.RUN_CANCELLED: "cancelled"}[kind]
            state["status"] = "waiting_verification" if pending and kind != T.RUN_CANCELLED else status
            state["finished_at"] = None if state["status"] == "waiting_verification" else event.ts
            last_terminal = kind
            if kind == T.RUN_FAILED:
                state["error"] = payload.get("reason")
            changed = True
        elif kind == T.RUN_INTERRUPTED:
            state["status"] = "interrupted"
            changed = True
        elif kind == T.QUESTION_REQUESTED and state["status"] == "running":
            state["status"] = "waiting_user"
            changed = True
        elif kind == T.QUESTION_ANSWERED and state["status"] == "waiting_user":
            state["status"] = "running"
            changed = True
        elif kind == T.TOOL_PENDING_VERIFICATION:
            pending.add(payload["call_id"])
            if state["status"] in {"queued", "running", "waiting_user", "interrupted"}:
                state["status"] = "waiting_verification"
                changed = True
        elif kind in {T.TOOL_COMPLETED, T.TOOL_FAILED, T.TOOL_SKIPPED_IDEMPOTENT, T.TOOL_VERIFICATION_SUBMITTED}:
            pending.discard(payload["call_id"])
            if kind == T.TOOL_VERIFICATION_SUBMITTED and not pending and state["status"] == "waiting_verification":
                state["status"] = "completed" if last_terminal == T.RUN_COMPLETED else "interrupted"
                state["finished_at"] = event.ts if state["status"] == "completed" else None
                changed = True
        if changed:
            state["updated_at"] = event.ts
    return state


def project_attempts(events):
    attempts = {}
    current = 0
    for event in events:
        if event.type in {T.RUN_STARTED, T.RUN_RESUMED}:
            payload = event.payload
            current = payload["attempt_no"]
            attempts[current] = {"id": payload.get("attempt_id") or "att-" + event.id, "task_run_id": event.task_run_id, "attempt_no": current,
                "kind": "initial" if event.type == T.RUN_STARTED else "resume", "status": "running", "outcome": None,
                "resume_reason": payload.get("resume_reason"), "context_fingerprint": payload.get("context_fingerprint"),
                "started_at": event.ts, "ended_at": None}
        elif event.type in {T.RUN_COMPLETED, T.RUN_FAILED, T.RUN_CANCELLED, T.RUN_INTERRUPTED}:
            number = event.attempt_no if event.attempt_no is not None else current
            if number in attempts:
                status = {T.RUN_COMPLETED: "completed", T.RUN_FAILED: "failed", T.RUN_CANCELLED: "cancelled", T.RUN_INTERRUPTED: "interrupted"}[event.type]
                attempts[number].update(status=status, ended_at=event.ts)
                if event.type != T.RUN_INTERRUPTED:
                    attempts[number]["outcome"] = "failed:" + event.payload.get("reason", "unknown") if event.type == T.RUN_FAILED else "cancelled" if event.type == T.RUN_CANCELLED else event.payload.get("outcome", status)
    return list(attempts.values())
