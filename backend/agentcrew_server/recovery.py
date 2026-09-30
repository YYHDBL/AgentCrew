"""RecoveryService（M0-C9）：启动对账 / 核验提交 / resume 校验 / 产物探测。

- 启动对账（§7）：扫全部非终态任务（queued/running/waiting_user/
  waiting_verification——C8 外审 F1 附带风险：优雅关闭把名额等待中的
  queued 任务留下，只扫 running/waiting_user 会永久卡死会话）。无
  run.interrupted 事件者补发（状态转 interrupted，不隐式重启）；再结清
  dispatched 无终态调用：ask_user 豁免（§6.2 v1.8，账本停 dispatched），
  verifiable 且 prepared 带 content_sha256 → 文件存在且哈希一致补
  tool.completed，否则与其余 outcome_unknown 一律 tool.pending_verification
  （投影翻任务为 waiting_verification）。顺序必须先 interrupt 后结清——
  反过来 run.interrupted 会把 waiting_verification 盖回 interrupted。
- 核验提交（v1.4 F003）：事件 + 审计链单事务；非待核验态 409。
- resume 校验：待核验存在 409 带清单；重放遇损坏事件行 409 REPLAY_CORRUPT
  （needs_manual_review——上下文完整性无保证，交人工）；通过后交
  RunManager.start_resume 发 run.resumed 进循环。
- artifacts：missing 惰性探测——列表时 stat 真实文件（打开前实检），
  不存在发 artifact.missing_detected。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from agentcrew_core.events import RunEventType
from agentcrew_core.recovery import (
    ReplayResult,
    file_hash_matches,
    replay_messages,
    side_effect_ledger,
)

from .db.audit import append_audit
from .sessions import SessionError
from .api.errors import ErrorCode

if TYPE_CHECKING:
    from .db.database import Database
    from .db.event_store import EventStore
    from .run_manager import RunManager
    from .sessions import SessionService

_log = logging.getLogger("agentcrew.recovery")

# 对账口径：全部非终态（终态 = completed/failed/cancelled 不扫）
NON_TERMINAL_STATUSES = ("queued", "running", "waiting_user",
                         "waiting_verification")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecoveryService:
    def __init__(self, db: "Database", event_store: "EventStore",
                 sessions: "SessionService", data_dir: Path,
                 chain_head_path: Path):
        self._db = db
        self._store = event_store
        self._sessions = sessions
        self._data_dir = data_dir
        self._chain_head_path = chain_head_path
        self._run_manager: "RunManager | None" = None  # cli 装配后注回

    def wire(self, run_manager: "RunManager") -> None:
        self._run_manager = run_manager

    # ── 启动对账（lifespan 内、RunManager.start 之前）─────────────

    async def reconcile(self) -> dict:
        rows = await asyncio.to_thread(self._non_terminal_tasks)
        summary = {"tasks": len(rows), "interrupted": 0,
                   "auto_completed": 0, "pending_verification": 0}
        for task_id, conversation_id in rows:
            has_interrupt = await asyncio.to_thread(
                self._has_interrupt_event, task_id)
            if not has_interrupt:
                await self._store.append(
                    task_run_id=task_id, conversation_id=conversation_id,
                    type=RunEventType.RUN_INTERRUPTED,
                    payload={"reason": "backend_restart_reconciliation"},
                )
                summary["interrupted"] += 1
                _log.warning("reconcile.interrupted task=%s（重启对账收敛，"
                             "不隐式重启，等待显式 resume）", task_id)
            auto_done, pending = await self._settle_dispatched(
                task_id, conversation_id)
            summary["auto_completed"] += auto_done
            summary["pending_verification"] += pending
        if rows:
            _log.info("reconcile.done 非终态任务=%s 补中断=%s verifiable补完=%s"
                      " 转待核验=%s", *summary.values())
        return summary

    def _non_terminal_tasks(self) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self._db.read_conn.execute(
            f"SELECT id, conversation_id FROM task_runs"
            f" WHERE status IN ({','.join('?' * len(NON_TERMINAL_STATUSES))})"
            f" ORDER BY created_at", NON_TERMINAL_STATUSES).fetchall()]

    def _has_interrupt_event(self, task_run_id: str) -> bool:
        return self._db.read_conn.execute(
            "SELECT 1 FROM run_events WHERE task_run_id=?"
            " AND type='run.interrupted' LIMIT 1", (task_run_id,)
        ).fetchone() is not None

    async def _settle_dispatched(self, task_id: str,
                                 conversation_id: str) -> tuple[int, int]:
        """结清 dispatched 无终态调用；返回（自动补完数, 转待核验数）。"""
        rows = await asyncio.to_thread(self._dispatched_calls, task_id)
        auto_done = pending = 0
        for call_id, tool_name, effect_class, input_text in rows:
            if tool_name == "ask_user":
                continue  # §6.2 v1.8 豁免：账本停 dispatched，重放合成占位
            artifact_path: str | None = None
            if effect_class == "verifiable":
                artifact_path = await asyncio.to_thread(
                    self._verify_write_file, task_id, conversation_id, call_id)
            if artifact_path is not None:
                await self._store.append(
                    task_run_id=task_id, conversation_id=conversation_id,
                    type=RunEventType.TOOL_COMPLETED,
                    payload={"call_id": call_id,
                             "output_summary": "重启对账：文件存在且 sha256 与"
                                               " prepared 记录一致（自动核验补完）",
                             "artifact_path": artifact_path},
                )
                auto_done += 1
                _log.info("reconcile.auto_completed task=%s call=%s → completed",
                          task_id, call_id)
            else:
                await self._store.append(
                    task_run_id=task_id, conversation_id=conversation_id,
                    type=RunEventType.TOOL_PENDING_VERIFICATION,
                    payload={"call_id": call_id},
                )
                pending += 1
        return auto_done, pending

    def _dispatched_calls(self, task_id: str) -> list[tuple[str, str, str, str]]:
        return self._db.read_conn.execute(
            "SELECT call_id, tool_name, side_effect_class, input"
            " FROM tool_calls WHERE task_run_id=? AND status='dispatched'"
            " ORDER BY prepared_at", (task_id,)).fetchall()

    def _verify_write_file(self, task_id: str, conversation_id: str,
                           call_id: str) -> str | None:
        """verifiable 自动核验（纯读，不发事件）：文件存在且 sha256 与
        prepared 记录一致 → 返回解析后的绝对路径（调用方补 tool.completed）；
        文件不存在/哈希不一致/prepared 无 content_sha256（文件在也没哈希）
        → None（转待核验）。"""
        prepared = self._db.read_conn.execute(
            "SELECT payload FROM run_events WHERE type='tool.prepared'"
            " AND json_extract(payload, '$.call_id')=?"
            " ORDER BY global_seq DESC LIMIT 1", (call_id,)).fetchone()
        if prepared is None:
            return None
        try:
            p = json.loads(prepared[0])
        except (json.JSONDecodeError, TypeError):
            return None
        expected = p.get("content_sha256")
        raw_path = (p.get("input") or {}).get("path", "")
        if not expected or not raw_path:
            return None
        path = Path(str(raw_path)).expanduser()
        if not path.is_absolute():
            path = Path(self._task_cwd(conversation_id)) / path
        if not file_hash_matches(str(path), str(expected)):
            _log.warning("reconcile.verify_mismatch task=%s call=%s"
                         "（文件不存在或哈希不一致 → 待核验）", task_id, call_id)
            return None
        return str(path)

    def _task_cwd(self, conversation_id: str) -> str:
        conv = self._sessions.conversation_or_404(conversation_id)
        return self._sessions.scope_of(conv)["workspace_dir"]

    # ── 待核验清单 / 提交（v1.4 F003）────────────────────────────

    def pending_verifications(self, conversation_id: str) -> list[dict]:
        self._sessions.conversation_or_404(conversation_id)
        rows = self._db.read_conn.execute(
            "SELECT tc.call_id, tc.tool_name, tc.input, tc.dispatched_at,"
            "       tc.side_effect_class, tc.task_run_id"
            " FROM tool_calls tc JOIN task_runs tr ON tr.id = tc.task_run_id"
            " WHERE tr.conversation_id = ? AND tc.status = 'pending_verification'"
            " ORDER BY tc.dispatched_at", (conversation_id,)).fetchall()
        out: list[dict] = []
        for (call_id, tool, input_text, dispatched_at, effect_class,
             task_id) in rows:
            try:
                input_obj = json.loads(input_text)
            except (json.JSONDecodeError, TypeError):
                input_obj = {}
            out.append({
                "call_id": call_id, "tool": tool, "input": input_obj,
                "dispatched_at": dispatched_at,
                "evidence": self._evidence(task_id, call_id, tool,
                                           effect_class, input_obj),
            })
        return out

    def _evidence(self, task_id: str, call_id: str, tool: str,
                  effect_class: str, input_obj: dict) -> str:
        """已知证据摘要（契约 PendingVerification.evidence）。verifiable 类
        把核验结果写进证据：此刻重跑哈希核验（stat + sha256，真实文件）。"""
        if effect_class == "verifiable":
            prepared = self._db.read_conn.execute(
                "SELECT payload FROM run_events WHERE type='tool.prepared'"
                " AND json_extract(payload, '$.call_id')=?"
                " ORDER BY global_seq DESC LIMIT 1", (call_id,)).fetchone()
            expected = None
            raw_path = input_obj.get("path", "")
            if prepared is not None:
                try:
                    p = json.loads(prepared[0])
                    expected = p.get("content_sha256")
                    raw_path = raw_path or (p.get("input") or {}).get("path", "")
                except (json.JSONDecodeError, TypeError):
                    pass
            if not expected:
                return ("verifiable：prepared 未记 content_sha256，"
                        "无法自动核验，待人工确认")
            path = Path(str(raw_path)).expanduser()
            if not path.is_absolute():
                path = Path(self._task_cwd_for(task_id)) / path
            if not os.path.exists(path):
                return f"verifiable：目标文件不存在（{path}）——写入未发生或已被移动"
            if file_hash_matches(str(path), str(expected)):
                return (f"verifiable：文件存在且哈希一致（{path}）——"
                        "可确认已执行")
            return f"verifiable：文件存在但哈希与 prepared 记录不一致（{path}）"
        return (f"{tool} 申报副作用类 {effect_class}：dispatched 后无终态记录，"
                "执行结果不明，待人工确认")

    def _task_cwd_for(self, task_id: str) -> str:
        row = self._db.read_conn.execute(
            "SELECT conversation_id FROM task_runs WHERE id=?",
            (task_id,)).fetchone()
        return self._task_cwd(row[0]) if row else str(self._data_dir)

    async def submit_verification(self, call_id: str, verdict: str,
                                  note: str | None) -> dict:
        row = self._db.read_conn.execute(
            "SELECT tc.task_run_id, tr.conversation_id, tc.status"
            " FROM tool_calls tc JOIN task_runs tr ON tr.id = tc.task_run_id"
            " WHERE tc.call_id=?", (call_id,)).fetchone()
        if row is None:
            raise SessionError(ErrorCode.NOT_FOUND, f"调用不存在：{call_id}")
        task_id, conversation_id, status = row
        if status != "pending_verification":
            raise SessionError(
                ErrorCode.INVALID_TRANSITION,
                f"调用不处于待核验状态（当前 {status}），不能提交核验")
        if verdict not in ("confirmed_executed", "confirmed_not_executed"):
            raise SessionError(ErrorCode.VALIDATION_ERROR,
                               f"非法核验决定：{verdict}")
        events: list = []

        def tx(conn) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                events.append(self._store.append_in_tx(
                    conn, task_run_id=task_id,
                    conversation_id=conversation_id,
                    type=RunEventType.TOOL_VERIFICATION_SUBMITTED,
                    payload={"call_id": call_id, "verdict": verdict,
                             "note": note},
                ))
                append_audit(
                    conn, ts=_now(), actor_type="user", actor_id="owner",
                    action=f"tool.verification:{verdict}",
                    resource_type="tool_call", resource_id=call_id,
                    detail=json.dumps({"note": note or ""},
                                      ensure_ascii=False),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            for event in events:
                self._store.publish(event)

        await self._store.channel.execute(tx)
        return {"call_id": call_id,
                "status": ("completed" if verdict == "confirmed_executed"
                           else "not_executed"),
                "verdict": verdict}

    # ── resume 校验（重放与派发在 RunManager）────────────────────

    def rebuild_for_resume(self, task_run_id: str) -> tuple[ReplayResult, str]:
        """重放重建消息上下文 + 副作用账本文本（attempt 开始时调用）。"""
        rows = self._db.read_conn.execute(
            "SELECT seq, type, payload FROM run_events WHERE task_run_id=?"
            " ORDER BY seq", (task_run_id,)).fetchall()
        result = replay_messages(
            [tuple(r) for r in rows],
            read_artifact=self._read_artifact,
        )
        calls = self._db.read_conn.execute(
            "SELECT tool_name, input, status FROM tool_calls"
            " WHERE task_run_id=? ORDER BY prepared_at",
            (task_run_id,)).fetchall()
        return result, side_effect_ledger([tuple(c) for c in calls])

    def _read_artifact(self, path_text: str) -> str | None:
        try:
            return Path(path_text).read_text(encoding="utf-8")
        except OSError:
            return None

    async def resume(self, task_run_id: str,
                     resume_reason: str | None) -> None:
        assert self._run_manager is not None, "RunManager 未装配"
        row = await asyncio.to_thread(self._task_row, task_run_id)
        if row is None:
            raise SessionError(ErrorCode.NOT_FOUND,
                               f"任务不存在：{task_run_id}")
        conversation_id, status, attempt_no = row
        if status not in ("interrupted", "waiting_verification"):
            raise SessionError(
                ErrorCode.INVALID_TRANSITION,
                f"任务状态 {status} 不可恢复（仅 interrupted/"
                "waiting_verification）")
        pending = await asyncio.to_thread(
            self._task_pending_calls, task_run_id)
        if pending:
            raise SessionError(
                ErrorCode.PENDING_VERIFICATION,
                "存在待核验调用，处理后才能恢复",
                detail={"pending_verifications": pending})
        # 会话锁内完成校验→发射（与直发/排队/继续同一串行域，杜绝并发
        # resume 双发 run.resumed）；FSM 必须 idle（同会话另一任务在跑则拒）
        async with await self._sessions.lock_for(conversation_id):
            fsm = await asyncio.to_thread(
                self._sessions.sync_fsm, conversation_id)
            if fsm.state != "idle":
                raise SessionError(
                    ErrorCode.INVALID_TRANSITION,
                    f"会话状态 {fsm.state} 不可恢复（有任务执行中）")
            result, _ledger = await asyncio.to_thread(
                self.rebuild_for_resume, task_run_id)
            if result.needs_manual_review:
                _log.error("resume.replay_corrupt task=%s warnings=%s",
                           task_run_id, result.warnings)
                raise SessionError(
                    ErrorCode.REPLAY_CORRUPT,
                    "重放遇损坏事件行，上下文完整性无保证，需人工介入",
                    detail={"warnings": result.warnings})
            await self._run_manager.start_resume(
                task_run_id, conversation_id,
                resume_reason or "user_requested", attempt_no + 1)

    def _task_row(self, task_run_id: str):
        return self._db.read_conn.execute(
            "SELECT conversation_id, status, current_attempt_no"
            " FROM task_runs WHERE id=?", (task_run_id,)).fetchone()

    def _task_pending_calls(self, task_run_id: str) -> list[dict]:
        rows = self._db.read_conn.execute(
            "SELECT call_id, tool_name, dispatched_at FROM tool_calls"
            " WHERE task_run_id=? AND status='pending_verification'"
            " ORDER BY dispatched_at", (task_run_id,)).fetchall()
        return [{"call_id": r[0], "tool": r[1], "dispatched_at": r[2]}
                for r in rows]

    # ── artifacts（F004）：missing 惰性探测───────────────────────

    async def list_artifacts(self, conversation_id: str,
                             task_run_id: str | None) -> list[dict]:
        self._sessions.conversation_or_404(conversation_id)
        rows = await asyncio.to_thread(self._artifact_rows, conversation_id,
                                       task_run_id)
        for row in rows:
            if row["status"] in ("ready", "generating") \
                    and not os.path.exists(row["path"]):
                await self._store.append(
                    task_run_id=row["task_run_id"],
                    conversation_id=conversation_id,
                    type=RunEventType.ARTIFACT_MISSING_DETECTED,
                    payload={"artifact_id": row["id"]},
                )
                row["status"] = "missing"
        return rows

    def _artifact_rows(self, conversation_id: str,
                       task_run_id: str | None) -> list[dict]:
        sql = ("SELECT id, task_run_id, tool_call_id, name, path, ext,"
               " size_bytes, status, created_at FROM artifacts"
               " WHERE task_run_id IN (SELECT id FROM task_runs"
               " WHERE conversation_id = ?)")
        params: tuple = (conversation_id,)
        if task_run_id is not None:
            sql += " AND task_run_id = ?"
            params = (conversation_id, task_run_id)
        sql += " ORDER BY created_at"
        keys = ("id", "task_run_id", "tool_call_id", "name", "path", "ext",
                "size_bytes", "status", "created_at")
        return [dict(zip(keys, r)) for r in self._db.read_conn.execute(
            sql, params).fetchall()]
