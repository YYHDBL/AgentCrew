"""EventStore：run_events 追加 + 投影同步，单事务（ADR-001 / backend-service §2）。

- 任务内 seq = 该任务已有最大 seq + 1（写通道串行内计算，无竞态）；
  UNIQUE(task_run_id, seq) 兜底——冲突抛 IntegrityError，绝不覆盖；
- 事件 + 投影同一事务：投影失败则事件也不入库（回滚整体）；
- publisher 在事务提交成功后、仍在写通道线程内同步调用（发布顺序 = 提交
  顺序，v1.9；C3 注入进程内总线，内存入队非阻塞）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from agentcrew_core.events import Event, RunEventType

from .projections import apply_projection
from .write_channel import WriteChannel
from .audit import SNAPSHOT_EVERY, snapshot_chain_head

Publisher = Callable[[Event], None]

_log = logging.getLogger("agentcrew.db.event_store")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventStore:
    def __init__(self, channel: WriteChannel, publisher: Publisher | None = None):
        self._channel = channel
        self._publisher = publisher
        self.task_preparer = None
        self.task_observer = None
        self.authorization_checker = None
        self.audit_anchor_path = None

    @property
    def channel(self) -> WriteChannel:
        """写通道句柄（审批服务等需要复合单事务写入的调用方使用）。"""
        return self._channel

    def set_publisher(self, publisher: Publisher | None) -> None:
        """C3 注入事件总线扇出（提交后回调）。"""
        self._publisher = publisher

    async def append(
        self,
        *,
        task_run_id: str | None,
        conversation_id: str | None,
        type: RunEventType,
        payload: dict,
        attempt_no: int | None = None,
        agent_run_id: str | None = None,
        extra_writes: Callable[[sqlite3.Connection, Event], None] | None = None,
    ) -> Event:
        """事件 + 投影（+ 可选 extra_writes 同事务写入，如审计/规则——外审
        回稿 F03：审批决定相关写入必须与事件原子提交）单事务落库并发布。
        task_run_id=None 为会话域事件（queue.*，v2 起 run_events 放开可空），
        此时 seq 无意义恒 0。"""
        return await self._channel.execute(
            lambda conn: self._append_tx(
                conn,
                task_run_id=task_run_id,
                conversation_id=conversation_id,
                type=type,
                payload=payload,
                attempt_no=attempt_no,
                agent_run_id=agent_run_id,
                extra_writes=extra_writes,
            )
        )

    @staticmethod
    def _next_seq(conn: sqlite3.Connection, task_run_id: str | None) -> int:
        if task_run_id is None:
            return 0  # 会话域事件：seq 属任务内游标，无任务即无 seq
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE task_run_id = ?",
            (task_run_id,),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _context_scope(conn, kind, conversation_id, payload):
        if kind not in {RunEventType.CONTEXT_COMPACTED, RunEventType.CONTEXT_BUDGET_CHECKED} or conversation_id is None:
            return payload
        row = conn.execute("SELECT workspace_id,agent_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if row is None:
            raise ValueError("上下文事件缺少真实会话范围")
        return {**payload, "scope": {"owner_id": "owner", "workspace_id": row[0], "agent_id": row[1]}}

    def _append_tx(
        self,
        conn: sqlite3.Connection,
        *,
        task_run_id: str | None,
        conversation_id: str | None,
        type: RunEventType,
        payload: dict,
        attempt_no: int | None,
        agent_run_id: str | None,
        extra_writes: Callable[[sqlite3.Connection, Event], None] | None = None,
    ) -> Event:
        event_id = uuid.uuid4().hex
        ts = _utc_now()
        previous_audit_seq = conn.execute("SELECT coalesce(max(seq),0) FROM audit_log").fetchone()[0]
        try:
            conn.execute("BEGIN IMMEDIATE")
            # FK 延迟到 COMMIT 检查：RUN_QUEUED 的 task_runs 父行由本事务内的
            # 投影创建（事件先插、投影后建），提交时父行已存在；坏引用同样在
            # COMMIT 处如实失败（走下面的回滚路径）
            conn.execute("PRAGMA defer_foreign_keys=ON")
            seq = self._next_seq(conn, task_run_id)
            if self.authorization_checker is not None:
                self.authorization_checker(conn, type, task_run_id, payload)
            payload = self._context_scope(conn, type, conversation_id, payload)
            cursor = conn.execute(
                "INSERT INTO run_events (id, task_run_id, seq, conversation_id,"
                " agent_run_id, attempt_no, type, payload, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id, task_run_id, seq, conversation_id,
                    agent_run_id, attempt_no, type.value,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True), ts,
                ),
            )
            event = Event(
                global_seq=int(cursor.lastrowid), id=event_id,
                task_run_id=task_run_id, seq=seq,
                conversation_id=conversation_id, type=type, payload=payload,
                attempt_no=attempt_no, agent_run_id=agent_run_id, ts=ts,
            )
            apply_projection(conn, event)
            if self.task_preparer is not None:
                self.task_preparer(conn, event)
            if extra_writes is not None:
                extra_writes(conn, event)  # 抛异常 → 整体回滚（事件不落库）
            if self.task_observer is not None:
                event = replace(event, followups=tuple(self.task_observer(conn, event)))
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass  # 事务已不存在（如 BEGIN 即失败）——保持原异常
            raise
        audit_seq = conn.execute("SELECT coalesce(max(seq),0) FROM audit_log").fetchone()[0]
        if self.audit_anchor_path is not None and audit_seq // SNAPSHOT_EVERY > previous_audit_seq // SNAPSHOT_EVERY:
            snapshot_chain_head(conn, self.audit_anchor_path)
        self.publish(event)
        return event

    def publish(self, event: Event) -> None:
        """提交后发布（供同事务复合写入方在事务成功后调用，语义与 append 一致）。"""
        if self._publisher is None:
            return
        # 已提交；回调异常必须就地吞掉：若让它带着 locked/busy 字样冒泡，写通道会
        # 重试整个闭包导致已提交事件被重复追加（外审回稿修复）。
        try:
            self._publisher(event)
        except Exception:
            _log.exception(
                "eventstore.publish 发布回调失败（事件已提交 global_seq=%s，"
                "不重试、不重复追加）",
                event.global_seq,
            )
        for followup in event.followups:
            self.publish(followup)

    def append_in_tx(
        self,
        conn: sqlite3.Connection,
        *,
        task_run_id: str | None,
        conversation_id: str | None,
        type: RunEventType,
        payload: dict,
        attempt_no: int | None = None,
    ) -> Event:
        """在**调用方已开启**的写通道事务内追加事件+投影（不 BEGIN/COMMIT、
        不发布）——供需要把事件与更多写入合并为单事务的复合操作使用
        （外审回稿 F02/F03：审批决定的查重+事件+审计+规则单事务）。
        提交与发布由调用方负责：COMMIT 后必须调 publish(event)。"""
        event_id = uuid.uuid4().hex
        ts = _utc_now()
        # FK 延迟到 COMMIT 检查：RUN_QUEUED 的 task_runs 父行可能由本事务内
        # 的投影稍后创建（调用方复合事务里事件先插、投影后建）
        conn.execute("PRAGMA defer_foreign_keys=ON")
        seq = self._next_seq(conn, task_run_id)
        if self.authorization_checker is not None:
            self.authorization_checker(conn, type, task_run_id, payload)
        payload = self._context_scope(conn, type, conversation_id, payload)
        cursor = conn.execute(
            "INSERT INTO run_events (id, task_run_id, seq, conversation_id,"
            " agent_run_id, attempt_no, type, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, task_run_id, seq, conversation_id,
                None, attempt_no, type.value,
                json.dumps(payload, ensure_ascii=False, sort_keys=True), ts,
            ),
        )
        event = Event(
            global_seq=int(cursor.lastrowid), id=event_id,
            task_run_id=task_run_id, seq=seq,
            conversation_id=conversation_id, type=type, payload=payload,
            attempt_no=attempt_no, agent_run_id=None, ts=ts,
        )
        apply_projection(conn, event)
        if self.task_preparer is not None:
            self.task_preparer(conn, event)
        if self.task_observer is not None:
            event = replace(event, followups=tuple(self.task_observer(conn, event)))
        return event
