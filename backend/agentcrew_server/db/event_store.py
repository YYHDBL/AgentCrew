"""EventStore：run_events 追加 + 投影同步，单事务（ADR-001 / backend-service §2）。

- 任务内 seq = 该任务已有最大 seq + 1（写通道串行内计算，无竞态）；
  UNIQUE(task_run_id, seq) 兜底——冲突抛 IntegrityError，绝不覆盖；
- 事件 + 投影同一事务：投影失败则事件也不入库（回滚整体）；
- publisher 在事务提交成功后、仍在写通道线程内同步调用（发布顺序 = 提交
  顺序，v1.9；C3 注入进程内总线，内存入队非阻塞）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Callable

from agentcrew_core.events import Event, RunEventType

from .projections import apply_projection
from .write_channel import WriteChannel

Publisher = Callable[[Event], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventStore:
    def __init__(self, channel: WriteChannel, publisher: Publisher | None = None):
        self._channel = channel
        self._publisher = publisher

    def set_publisher(self, publisher: Publisher | None) -> None:
        """C3 注入事件总线扇出（提交后回调）。"""
        self._publisher = publisher

    async def append(
        self,
        *,
        task_run_id: str,
        conversation_id: str,
        type: RunEventType,
        payload: dict,
        attempt_no: int | None = None,
        agent_run_id: str | None = None,
    ) -> Event:
        return await self._channel.execute(
            lambda conn: self._append_tx(
                conn,
                task_run_id=task_run_id,
                conversation_id=conversation_id,
                type=type,
                payload=payload,
                attempt_no=attempt_no,
                agent_run_id=agent_run_id,
            )
        )

    def _append_tx(
        self,
        conn: sqlite3.Connection,
        *,
        task_run_id: str,
        conversation_id: str,
        type: RunEventType,
        payload: dict,
        attempt_no: int | None,
        agent_run_id: str | None,
    ) -> Event:
        event_id = uuid.uuid4().hex
        ts = _utc_now()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # FK 延迟到 COMMIT 检查：RUN_QUEUED 的 task_runs 父行由本事务内的
            # 投影创建（事件先插、投影后建），提交时父行已存在；坏引用同样在
            # COMMIT 处如实失败（走下面的回滚路径）
            conn.execute("PRAGMA defer_foreign_keys=ON")
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE task_run_id = ?",
                (task_run_id,),
            ).fetchone()
            seq = int(row[0])
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
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass  # 事务已不存在（如 BEGIN 即失败）——保持原异常
            raise
        if self._publisher is not None:
            self._publisher(event)  # 已提交；写通道线程内同步入队（§2 发布顺序）
        return event
