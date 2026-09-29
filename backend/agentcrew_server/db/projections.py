"""投影表处理器：事件 → 投影行（harness-session §2.2/§2.3）。

纪律：
- 投影是事件的纯函数——行 id 一律取自事件载荷（或由事件 id 派生），
  保证重放（rebuild）结果与在线写入逐字节一致；
- 全部 handler 在 EventStore 的同一事务内执行（backend-service §2）；
- handler 只做 SQL upsert，不等待外部系统。

C2 固定的 payload 契约（C5-C8 按此发射；新增字段先登记再使用）：
  run.queued        {instruction, cron_job_id?}
  run.started       {attempt_no, attempt_id, kind="initial"}
  run.completed     {final_text?, outcome?}
  run.failed        {reason}
  run.cancelled     {}
  run.interrupted   {reason?}
  run.resumed       {attempt_no, attempt_id, resume_reason}
  step.started      {step_id, ordinal, model_slot?}
  step.completed    {step_id, input_tokens?, output_tokens?, latency_ms?}
  llm.request_started {llm_call_id, step_id, model, retry_no?}
  llm.request_done  {llm_call_id, prompt_tokens?, completion_tokens?, latency_ms?}
  llm.request_failed {llm_call_id, error, retry_no?}
  tool.prepared     {call_id, tool_call_id?, tool_name, side_effect_class,
                     input_hash, risk_level, input?, step_id?}
  tool.dispatched   {call_id}
  tool.completed    {call_id, output_summary?, artifact_path?}
  tool.failed       {call_id, error}
  tool.skipped_idempotent {call_id, note?}
  tool.pending_verification {call_id}
  tool.verification_submitted {call_id, verdict(confirmed_executed|
                     confirmed_not_executed), note?}
  artifact.created  {artifact_id, task_run_id, tool_call_id?, path, name, ext?}
  artifact.ready    {artifact_id, size_bytes?}
  artifact.failed   {artifact_id, error?}
  artifact.missing_detected {artifact_id}
  materials.imported {files: [{original_path, stored_name, size_bytes?, error?}]}
  conversation.updated {title?}

无投影事件（等待计数器与 FSM 在内存 reducer，C7；上下文事件 M1）：
  permission.* / question.* / queue.* / context.compacted /
  tool.result_externalized —— 注册为显式 no-op。
task_runs 的 waiting_user / waiting_verification 派生随 C7/C8 的 reducer
语义落地（依赖其状态互斥规则），C2 只实现 run.* 直接迁移。
"""

from __future__ import annotations

import sqlite3
from typing import Callable

from agentcrew_core.events import Event, RunEventType

Handler = Callable[[sqlite3.Connection, Event], None]

# 重建时可全量擦除的事件派生表（conversations 是 API 创建的容器，不擦）
PROJECTION_TABLES: tuple[str, ...] = (
    "messages",
    "llm_calls",
    "tool_calls",
    "artifacts",
    "task_materials",
    "steps",
    "run_attempts",
    "task_runs",
)


def _noop(_conn: sqlite3.Connection, _event: Event) -> None:
    return None


# ── 任务生命周期 → task_runs / run_attempts / messages ──────────────

def _run_queued(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute(
        "INSERT INTO task_runs (id, conversation_id, instruction, status, cron_job_id,"
        " created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?, ?)",
        (ev.task_run_id, ev.conversation_id, p.get("instruction", ""),
         p.get("cron_job_id"), ev.ts, ev.ts),
    )
    conn.execute(
        "INSERT INTO messages (id, conversation_id, task_run_id, role, content, created_at)"
        " VALUES (?, ?, ?, 'user', ?, ?)",
        (f"msg-{ev.id}", ev.conversation_id, ev.task_run_id,
         p.get("instruction", ""), ev.ts),
    )


def _attempt_started(conn: sqlite3.Connection, ev: Event, kind: str, reason: str | None) -> None:
    p = ev.payload
    conn.execute(
        "INSERT INTO run_attempts (id, task_run_id, attempt_no, kind, status,"
        " resume_reason, started_at) VALUES (?, ?, ?, ?, 'running', ?, ?)",
        (p.get("attempt_id") or f"att-{ev.id}", ev.task_run_id, p["attempt_no"],
         kind, reason, ev.ts),
    )
    conn.execute(
        "UPDATE task_runs SET status='running', current_attempt_no=?, updated_at=?"
        " WHERE id=?",
        (p["attempt_no"], ev.ts, ev.task_run_id),
    )


def _run_started(conn: sqlite3.Connection, ev: Event) -> None:
    _attempt_started(conn, ev, "initial", None)


def _run_resumed(conn: sqlite3.Connection, ev: Event) -> None:
    _attempt_started(conn, ev, "resume", ev.payload.get("resume_reason"))


def _run_finished(conn: sqlite3.Connection, ev: Event, status: str, outcome: str,
                  final_text: str | None = None) -> None:
    conn.execute(
        "UPDATE task_runs SET status=?, finished_at=?, updated_at=? WHERE id=?",
        (status, ev.ts, ev.ts, ev.task_run_id),
    )
    conn.execute(
        "UPDATE run_attempts SET status=?, outcome=?, ended_at=? WHERE task_run_id=?"
        " AND attempt_no=?",
        (status, outcome, ev.ts, ev.task_run_id, ev.attempt_no or 0),
    )
    if final_text is not None:
        conn.execute(
            "INSERT INTO messages (id, conversation_id, task_run_id, role, content,"
            " created_at) VALUES (?, ?, ?, 'assistant', ?, ?)",
            (f"msg-{ev.id}", ev.conversation_id, ev.task_run_id, final_text, ev.ts),
        )


def _run_completed(conn: sqlite3.Connection, ev: Event) -> None:
    _run_finished(conn, ev, "completed", ev.payload.get("outcome", "completed"),
                  ev.payload.get("final_text"))


def _run_failed(conn: sqlite3.Connection, ev: Event) -> None:
    _run_finished(conn, ev, "failed", f"failed:{ev.payload.get('reason', 'unknown')}")


def _run_cancelled(conn: sqlite3.Connection, ev: Event) -> None:
    _run_finished(conn, ev, "cancelled", "cancelled")


def _run_interrupted(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute(
        "UPDATE task_runs SET status='interrupted', updated_at=? WHERE id=?",
        (ev.ts, ev.task_run_id),
    )
    conn.execute(
        "UPDATE run_attempts SET status='interrupted', ended_at=? WHERE task_run_id=?"
        " AND status='running'",
        (ev.ts, ev.task_run_id),
    )


# ── 回合与模型 → steps / llm_calls ─────────────────────────────────

def _step_started(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute(
        "INSERT INTO steps (id, task_run_id, ordinal, model_slot, status)"
        " VALUES (?, ?, ?, ?, 'running')",
        (p["step_id"], ev.task_run_id, p["ordinal"], p.get("model_slot")),
    )


def _step_completed(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute(
        "UPDATE steps SET status='completed', input_tokens=?, output_tokens=?,"
        " latency_ms=? WHERE id=?",
        (p.get("input_tokens"), p.get("output_tokens"), p.get("latency_ms"),
         p["step_id"]),
    )


def _llm_started(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute(
        "INSERT INTO llm_calls (id, step_id, model, retry_no) VALUES (?, ?, ?, ?)",
        (p["llm_call_id"], p["step_id"], p["model"], p.get("retry_no", 0)),
    )


def _llm_done(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute(
        "UPDATE llm_calls SET prompt_tokens=?, completion_tokens=?, latency_ms=?"
        " WHERE id=?",
        (p.get("prompt_tokens"), p.get("completion_tokens"), p.get("latency_ms"),
         p["llm_call_id"]),
    )


def _llm_failed(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute(
        "UPDATE llm_calls SET error=? WHERE id=?",
        (ev.payload.get("error"), ev.payload["llm_call_id"]),
    )


# ── 工具 → tool_calls ───────────────────────────────────────────────

def _tool_prepared(conn: sqlite3.Connection, ev: Event) -> None:
    import json

    p = ev.payload
    conn.execute(
        "INSERT INTO tool_calls (id, call_id, task_run_id, step_id, tool_name,"
        " side_effect_class, input_hash, status, risk_level, input, prepared_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?)",
        (p.get("tool_call_id") or p["call_id"], p["call_id"], ev.task_run_id,
         p.get("step_id"), p["tool_name"], p["side_effect_class"], p["input_hash"],
         p.get("risk_level", "medium"),
         json.dumps(p.get("input", {}), ensure_ascii=False, sort_keys=True), ev.ts),
    )


def _tool_status(conn: sqlite3.Connection, ev: Event, status: str, *,
                 error: str | None = None, output_summary: str | None = None,
                 artifact_path: str | None = None, finish: bool = True) -> None:
    conn.execute(
        "UPDATE tool_calls SET status=?, error=COALESCE(?, error),"
        " output_summary=COALESCE(?, output_summary),"
        " artifact_path=COALESCE(?, artifact_path),"
        f"{'completed_at=?,' if finish else ''} dispatched_at=COALESCE(dispatched_at, ?)"
        " WHERE call_id=?",
        (
            status, error, output_summary, artifact_path,
            *([ev.ts] if finish else []), ev.ts, ev.payload["call_id"],
        ),
    )


def _tool_dispatched(conn: sqlite3.Connection, ev: Event) -> None:
    _tool_status(conn, ev, "dispatched", finish=False)


def _tool_completed(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    _tool_status(conn, ev, "completed",
                 output_summary=p.get("output_summary"),
                 artifact_path=p.get("artifact_path"))


def _tool_failed(conn: sqlite3.Connection, ev: Event) -> None:
    _tool_status(conn, ev, "failed", error=ev.payload.get("error"))


def _tool_skipped(conn: sqlite3.Connection, ev: Event) -> None:
    _tool_status(conn, ev, "completed",
                 output_summary=ev.payload.get("note", "skipped: external idempotency hit"))


def _tool_pending(conn: sqlite3.Connection, ev: Event) -> None:
    _tool_status(conn, ev, "pending_verification")


def _tool_verification(conn: sqlite3.Connection, ev: Event) -> None:
    verdict = ev.payload.get("verdict")
    status = "completed" if verdict == "confirmed_executed" else "not_executed"
    _tool_status(conn, ev, status,
                 output_summary=f"verified_by_user:{verdict}:{ev.payload.get('note', '')}")


# ── 产物 → artifacts ────────────────────────────────────────────────

def _artifact_created(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    conn.execute(
        "INSERT INTO artifacts (id, conversation_id, task_run_id, tool_call_id, path,"
        " name, ext, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'generating', ?, ?)",
        (p["artifact_id"], ev.conversation_id,
         p.get("task_run_id") or ev.task_run_id, p.get("tool_call_id"),
         p["path"], p["name"], p.get("ext"), ev.ts, ev.ts),
    )


def _artifact_update(conn: sqlite3.Connection, ev: Event, status: str,
                     size: int | None = None) -> None:
    conn.execute(
        "UPDATE artifacts SET status=?, size_bytes=COALESCE(?, size_bytes),"
        " updated_at=? WHERE id=?",
        (status, size, ev.ts, ev.payload["artifact_id"]),
    )


# ── 材料 / 会话元数据 ───────────────────────────────────────────────

def _materials_imported(conn: sqlite3.Connection, ev: Event) -> None:
    for i, f in enumerate(ev.payload.get("files", [])):
        conn.execute(
            "INSERT INTO task_materials (id, conversation_id, original_path,"
            " stored_name, size_bytes, error, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"mat-{ev.id}-{i}", ev.conversation_id, f.get("original_path", ""),
             f.get("stored_name", ""), f.get("size_bytes"), f.get("error"), ev.ts),
        )


def _conversation_updated(conn: sqlite3.Connection, ev: Event) -> None:
    title = ev.payload.get("title")
    if title is not None:
        conn.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
            (title, ev.ts, ev.conversation_id),
        )


HANDLERS: dict[RunEventType, Handler] = {
    RunEventType.RUN_QUEUED: _run_queued,
    RunEventType.RUN_STARTED: _run_started,
    RunEventType.RUN_RESUMED: _run_resumed,
    RunEventType.RUN_COMPLETED: _run_completed,
    RunEventType.RUN_FAILED: _run_failed,
    RunEventType.RUN_CANCELLED: _run_cancelled,
    RunEventType.RUN_INTERRUPTED: _run_interrupted,
    RunEventType.STEP_STARTED: _step_started,
    RunEventType.STEP_COMPLETED: _step_completed,
    RunEventType.LLM_REQUEST_STARTED: _llm_started,
    RunEventType.LLM_REQUEST_DONE: _llm_done,
    RunEventType.LLM_REQUEST_FAILED: _llm_failed,
    RunEventType.TOOL_PREPARED: _tool_prepared,
    RunEventType.TOOL_DISPATCHED: _tool_dispatched,
    RunEventType.TOOL_COMPLETED: _tool_completed,
    RunEventType.TOOL_FAILED: _tool_failed,
    RunEventType.TOOL_SKIPPED_IDEMPOTENT: _tool_skipped,
    RunEventType.TOOL_PENDING_VERIFICATION: _tool_pending,
    RunEventType.TOOL_VERIFICATION_SUBMITTED: _tool_verification,
    RunEventType.ARTIFACT_CREATED: _artifact_created,
    RunEventType.ARTIFACT_READY: lambda c, e: _artifact_update(c, e, "ready", e.payload.get("size_bytes")),
    RunEventType.ARTIFACT_FAILED: lambda c, e: _artifact_update(c, e, "failed"),
    RunEventType.ARTIFACT_MISSING_DETECTED: lambda c, e: _artifact_update(c, e, "missing"),
    RunEventType.MATERIALS_IMPORTED: _materials_imported,
    RunEventType.CONVERSATION_UPDATED: _conversation_updated,
    # 无投影（FSM/计数器在内存 reducer，C7）
    RunEventType.PERMISSION_REQUESTED: _noop,
    RunEventType.PERMISSION_RESOLVED: _noop,
    RunEventType.QUESTION_REQUESTED: _noop,
    RunEventType.QUESTION_ANSWERED: _noop,
    RunEventType.QUEUE_PAUSED: _noop,
    RunEventType.QUEUE_RESUMED: _noop,
    RunEventType.QUEUE_ITEM_CANCELLED: _noop,
    RunEventType.CONTEXT_COMPACTED: _noop,
    RunEventType.TOOL_RESULT_EXTERNALIZED: _noop,
}


def apply_projection(conn: sqlite3.Connection, ev: Event) -> None:
    """单事件投影（在追加事务内调用）。未注册类型 = 枚举外事件，显式拒绝。"""
    handler = HANDLERS.get(ev.type)
    if handler is None:
        raise ValueError(f"事件类型未注册投影处理器：{ev.type}")
    handler(conn, ev)


def _row_to_event(row: sqlite3.Row) -> Event:
    import json

    return Event(
        global_seq=row["global_seq"], id=row["id"], task_run_id=row["task_run_id"],
        seq=row["seq"], conversation_id=row["conversation_id"],
        type=RunEventType(row["type"]), payload=json.loads(row["payload"]),
        attempt_no=row["attempt_no"], agent_run_id=row["agent_run_id"],
        ts=row["created_at"],
    )


def rebuild_projections(conn: sqlite3.Connection) -> int:
    """重放器：擦除全部投影表，按 global_seq 重放 run_events 重建。

    返回重放事件数。事务内 defer_foreign_keys——重放结束时父行全部复在。
    """
    conn.execute("BEGIN IMMEDIATE")
    previous_factory = conn.row_factory
    try:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        for table in PROJECTION_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.row_factory = sqlite3.Row  # _row_to_event 按列名取值
        count = 0
        for row in conn.execute(
            "SELECT * FROM run_events ORDER BY global_seq"
        ).fetchall():
            apply_projection(conn, _row_to_event(row))
            count += 1
        conn.execute("COMMIT")
        return count
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.row_factory = previous_factory
