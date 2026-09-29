"""事件读取查询（只读连接；SSE 历史补播与 JSON 分页共用）。

游标一律**排他**（返回 seq/global_seq > 游标的事件）；分批拉取避免长会话
一次性载入。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Iterator

from agentcrew_core.events import Event, RunEventType


def _row_to_event(row) -> Event:
    return Event(
        global_seq=row[0], id=row[1], task_run_id=row[2], seq=row[3],
        conversation_id=row[4], attempt_no=row[6], agent_run_id=row[5],
        type=RunEventType(row[7]), payload=json.loads(row[8]), ts=row[9],
    )

# 列序：global_seq, id, task_run_id, seq, conversation_id, agent_run_id,
#       attempt_no, type, payload, created_at
_COLS = "global_seq, id, task_run_id, seq, conversation_id, agent_run_id," \
        " attempt_no, type, payload, created_at"


def iter_conversation_events(
    conn: sqlite3.Connection, conversation_id: str, *, after_global_seq: int = 0,
    batch: int = 500,
) -> Iterator[Event]:
    cursor = after_global_seq
    while True:
        rows = conn.execute(
            f"SELECT {_COLS} FROM run_events WHERE conversation_id = ?"
            " AND global_seq > ? ORDER BY global_seq LIMIT ?",
            (conversation_id, cursor, batch),
        ).fetchall()
        if not rows:
            return
        for row in rows:
            yield _row_to_event(row)
        cursor = rows[-1][0]


def iter_task_events(
    conn: sqlite3.Connection, task_run_id: str, *, after_seq: int = 0,
    batch: int = 500,
) -> Iterator[Event]:
    cursor = after_seq
    while True:
        rows = conn.execute(
            f"SELECT {_COLS} FROM run_events WHERE task_run_id = ?"
            " AND seq > ? ORDER BY seq LIMIT ?",
            (task_run_id, cursor, batch),
        ).fetchall()
        if not rows:
            return
        for row in rows:
            yield _row_to_event(row)
        cursor = rows[-1][3]


def conversation_exists(conn: sqlite3.Connection, conversation_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone() is not None


def task_run_exists(conn: sqlite3.Connection, task_run_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM task_runs WHERE id = ?", (task_run_id,)
    ).fetchone() is not None
