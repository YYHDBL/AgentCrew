"""投影表处理器：事件 → 投影行（harness-session §2.2/§2.3）。

纪律：
- 投影是事件的纯函数——行 id 一律取自事件载荷（或由事件 id 派生），
  保证重放（rebuild）结果与在线写入逐字节一致；
- 全部 handler 在 EventStore 的同一事务内执行（backend-service §2）；
- handler 只做 SQL upsert，不等待外部系统。

C2 固定的 payload 契约（C5-C8 按此发射；新增字段先登记再使用）：
  run.queued        {instruction, cron_job_id?, client_request_id?, queue_item_id?}
                    —— queue_item_id：从队列出队直发时携带（该指令的消息行
                    已在 queue.item_enqueued 写入，此处不重复写 messages）
  run.started       {attempt_no, attempt_id, kind="initial", context_fingerprint?}
                    —— context_fingerprint（C8）：{system_prompt_hash,
                    tools_schema_hash, model_config}，写入 run_attempts 同名列
  run.completed     {final_text?, outcome?}
  run.failed        {reason}
  run.cancelled     {}
  run.interrupted   {reason?}
  run.resumed       {attempt_no, attempt_id, resume_reason,
                    context_fingerprint?}   （C9：与 run.started 同源计算，
                    每次 attempt 都有指纹——v1.7「每次 attempt 记 fingerprint」）
  step.started      {step_id, ordinal, model_slot?}
  step.completed    {step_id, input_tokens?, output_tokens?, latency_ms?}
  llm.request_started {llm_call_id, step_id, model, retry_no?}
  llm.request_done  {llm_call_id, prompt_tokens?, completion_tokens?, latency_ms?,
                    text?, tool_uses?: [{id,name,input}],
                    thinking_blocks?: [{text,signature}], stop_reason?}
                    —— C8 起携带回复全文 + 本次全部 tool_use 块（含 thinking
                    块与签名）：C9 恢复重建对话与结果配对的唯一依据（v1.7）
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
  materials.imported {files: [{original_path, stored_name, size_bytes?, error?}],
                    folders?: [{path, error?}]}   （folders 逐项结果随事件
                    持久化，响应/幂等重放从载荷读回——外审回稿；无投影列，
                    授权目录只进 conversations.folders_json）
  conversation.updated {title?}
  question.requested {request_id, question, options?}   （C8：ask_user 全链）
  question.answered  {request_id, answer}               （answer=null=用户
                    取消回答，按"拒绝回答"告知模型）
  queue.item_enqueued {item_id, text, client_request_id?}   （C7）
  queue.item_cancelled {item_ids: [...]}

C7 起排队域有投影（此前为 no-op）：queue.* 事件维护 conversations.
pending_queue JSON / queue_paused 列，queue.item_enqueued 同步写 messages
用户消息行（排队指令的发送记录——取消时"发送记录保留"的留痕处）。这两列
因此成为事件派生投影：rebuild 前重置，重放重建（见 _reset_queue_columns）。
等待计数器与 FSM 仍在内存 reducer（agentcrew_core.events.reducer）。
task_runs 的 waiting_user 派生随 C8 落地（question.* 事件往返翻转
running↔waiting_user）；waiting_verification 由 tool.pending_verification
事件派生（C9）：非终态任务翻入该态，tool.verification_submitted /
run.resumed / run.* 终态照常覆盖。
"""

from __future__ import annotations

import json
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
        " client_request_id, created_at, updated_at)"
        " VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)",
        (ev.task_run_id, ev.conversation_id, p.get("instruction", ""),
         p.get("cron_job_id"), p.get("client_request_id"), ev.ts, ev.ts),
    )
    if p.get("queue_item_id") is None:
        # 直发指令的消息行；队列出队的指令在 queue.item_enqueued 已写
        conn.execute(
            "INSERT INTO messages (id, conversation_id, task_run_id, role, content, created_at)"
            " VALUES (?, ?, ?, 'user', ?, ?)",
            (f"msg-{ev.id}", ev.conversation_id, ev.task_run_id,
             p.get("instruction", ""), ev.ts),
        )
    else:
        _queue_run_dequeued(conn, ev)


def _attempt_started(conn: sqlite3.Connection, ev: Event, kind: str, reason: str | None) -> None:
    p = ev.payload
    conn.execute(
        "INSERT INTO run_attempts (id, task_run_id, attempt_no, kind, status,"
        " resume_reason, context_fingerprint, started_at)"
        " VALUES (?, ?, ?, ?, 'running', ?, ?, ?)",
        (p.get("attempt_id") or f"att-{ev.id}", ev.task_run_id, p["attempt_no"],
         kind, reason,
         json.dumps(p["context_fingerprint"], ensure_ascii=False, sort_keys=True)
         if p.get("context_fingerprint") is not None else None,
         ev.ts),
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
                 artifact_path: str | None = None, finish: bool = False,
                 dispatch: bool = False) -> None:
    """工具状态迁移。只填**实际发生**的阶段时间（外审回稿 S11）：
    prepared 后被拒（PERMISSION_DENIED）没有 dispatched_at；转
    pending_verification 也不是完成，不得填 completed_at。"""
    sets = ["status=?", "error=COALESCE(?, error)",
            "output_summary=COALESCE(?, output_summary)",
            "artifact_path=COALESCE(?, artifact_path)"]
    params: list = [status, error, output_summary, artifact_path]
    if dispatch:
        sets.append("dispatched_at=COALESCE(dispatched_at, ?)")
        params.append(ev.ts)
    if finish:
        sets.append("completed_at=?")
        params.append(ev.ts)
    params.append(ev.payload["call_id"])
    conn.execute(
        f"UPDATE tool_calls SET {', '.join(sets)} WHERE call_id=?", params)


def _tool_dispatched(conn: sqlite3.Connection, ev: Event) -> None:
    _tool_status(conn, ev, "dispatched", dispatch=True)


def _tool_completed(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    _tool_status(conn, ev, "completed",
                 output_summary=p.get("output_summary"),
                 artifact_path=p.get("artifact_path"), finish=True)


def _tool_failed(conn: sqlite3.Connection, ev: Event) -> None:
    _tool_status(conn, ev, "failed", error=ev.payload.get("error"),
                 artifact_path=ev.payload.get("artifact_path"), finish=True)


def _tool_skipped(conn: sqlite3.Connection, ev: Event) -> None:
    _tool_status(conn, ev, "completed",
                 output_summary=ev.payload.get("note", "skipped: external idempotency hit"),
                 finish=True)


def _tool_pending(conn: sqlite3.Connection, ev: Event) -> None:
    _tool_status(conn, ev, "pending_verification")  # 非终态：不填 completed_at
    # C9：待核验派生态（事件派生，rebuild 稳定）——非终态任务转
    # waiting_verification（resume 前置校验与清单查询的口径）。终态任务
    # 不翻（C8 _finish 先结清再落终态，此处只是不覆盖其后到来的终态）
    conn.execute(
        "UPDATE task_runs SET status='waiting_verification', updated_at=?"
        " WHERE id=? AND status IN ('queued', 'running', 'waiting_user',"
        " 'interrupted')",
        (ev.ts, ev.task_run_id),
    )


def _tool_verification(conn: sqlite3.Connection, ev: Event) -> None:
    verdict = ev.payload.get("verdict")
    status = "completed" if verdict == "confirmed_executed" else "not_executed"
    _tool_status(conn, ev, status, finish=True,
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

def _question_requested(conn: sqlite3.Connection, ev: Event) -> None:
    """C8：ask_user 挂起 → task_runs 派生态 waiting_user（C9 对账扫描
    running/waiting_user；回答后翻回）。非 running 态（对账后补答等）不动。"""
    if ev.task_run_id:
        conn.execute(
            "UPDATE task_runs SET status='waiting_user', updated_at=?"
            " WHERE id=? AND status='running'",
            (ev.ts, ev.task_run_id),
        )


def _question_answered(conn: sqlite3.Connection, ev: Event) -> None:
    if ev.task_run_id:
        conn.execute(
            "UPDATE task_runs SET status='running', updated_at=?"
            " WHERE id=? AND status='waiting_user'",
            (ev.ts, ev.task_run_id),
        )


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


# ── 排队控制 → conversations.pending_queue / queue_paused（C7）──────

def _load_queue(conn: sqlite3.Connection, conversation_id: str) -> list[dict]:
    row = conn.execute(
        "SELECT pending_queue FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"排队事件指向不存在的会话：{conversation_id}")
    items = json.loads(row[0] or "[]")
    return items if isinstance(items, list) else []


def _save_queue(conn: sqlite3.Connection, conversation_id: str,
                items: list[dict], ts: str) -> None:
    conn.execute(
        "UPDATE conversations SET pending_queue=?, updated_at=? WHERE id=?",
        (json.dumps(items, ensure_ascii=False), ts, conversation_id),
    )


def _queue_enqueued(conn: sqlite3.Connection, ev: Event) -> None:
    p = ev.payload
    items = _load_queue(conn, ev.conversation_id)
    items.append({
        "id": p["item_id"], "text": p.get("text", ""), "enqueued_at": ev.ts,
        "state": "queued", "client_request_id": p.get("client_request_id"),
    })
    _save_queue(conn, ev.conversation_id, items, ev.ts)
    # 发送记录入 messages（取消排队项时记录仍在，F006 留痕口径）
    conn.execute(
        "INSERT INTO messages (id, conversation_id, task_run_id, role, content,"
        " created_at) VALUES (?, ?, NULL, 'user', ?, ?)",
        (f"msg-{ev.id}", ev.conversation_id, p.get("text", ""), ev.ts),
    )


def _queue_item_cancelled(conn: sqlite3.Connection, ev: Event) -> None:
    ids = set(ev.payload.get("item_ids", []))
    items = _load_queue(conn, ev.conversation_id)
    for item in items:
        if item.get("id") in ids and item.get("state") == "queued":
            item["state"] = "cancelled"
    _save_queue(conn, ev.conversation_id, items, ev.ts)


def _queue_run_dequeued(conn: sqlite3.Connection, ev: Event) -> None:
    """run.queued 从队列出队：排队项标 dequeued（消息行已在入队时写）。"""
    item_id = ev.payload.get("queue_item_id")
    if item_id is None:
        return
    items = _load_queue(conn, ev.conversation_id)
    for item in items:
        if item.get("id") == item_id and item.get("state") == "queued":
            item["state"] = "dequeued"
    _save_queue(conn, ev.conversation_id, items, ev.ts)


def _queue_paused(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute(
        "UPDATE conversations SET queue_paused=1, updated_at=? WHERE id=?",
        (ev.ts, ev.conversation_id),
    )


def _queue_resumed(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute(
        "UPDATE conversations SET queue_paused=0, updated_at=? WHERE id=?",
        (ev.ts, ev.conversation_id),
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
    # 排队域投影（C7）：pending_queue / queue_paused / messages 发送记录
    RunEventType.QUEUE_ITEM_ENQUEUED: _queue_enqueued,
    RunEventType.QUEUE_ITEM_CANCELLED: _queue_item_cancelled,
    RunEventType.QUEUE_PAUSED: _queue_paused,
    RunEventType.QUEUE_RESUMED: _queue_resumed,
    # 无投影（等待计数器与 FSM 在内存 reducer，C7 起；上下文事件 M1）
    RunEventType.PERMISSION_REQUESTED: _noop,
    RunEventType.PERMISSION_RESOLVED: _noop,
    RunEventType.CONTEXT_COMPACTED: _noop,
    RunEventType.CONTEXT_BUDGET_CHECKED: _noop,
    RunEventType.TOOL_RESULT_EXTERNALIZED: _noop,
    RunEventType.MEMORY_UPDATED: _noop,
    RunEventType.MEMORY_ARCHIVED: _noop,
    RunEventType.MEMORY_SNAPSHOT_CREATED: _noop,
    RunEventType.SKILL_PATCHED: _noop,
    RunEventType.MEMORY_JOB_STATUS: _noop,
    RunEventType.MEMORY_SUMMARY_CREATED: _noop,
    RunEventType.MEMORY_APPROVAL_REQUESTED: _noop,
    RunEventType.MEMORY_APPROVAL_RESOLVED: _noop,
    RunEventType.MEMORY_CURATED: _noop,
    # C8：question.* 驱动 task_runs 派生态 waiting_user（计数器仍在内存 FSM）
    RunEventType.QUESTION_REQUESTED: _question_requested,
    RunEventType.QUESTION_ANSWERED: _question_answered,
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
    pending_queue / queue_paused 是 queue.* 事件的派生投影（C7）：会话行
    本身由 API 创建不擦除，但这两列重放前重置，保证重放幂等。
    """
    conn.execute("BEGIN IMMEDIATE")
    previous_factory = conn.row_factory
    try:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        for table in PROJECTION_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute(
            "UPDATE conversations SET pending_queue='[]', queue_paused=0"
        )
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
