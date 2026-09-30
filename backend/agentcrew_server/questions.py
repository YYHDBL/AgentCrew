"""提问回答链（M0-C8，S07：ask_user 独立生命周期）。

- resolver（WorkContext.ask_resolver）：ask_user 工具的等待通道——注册
  future 后无限期等待（不占工具并发名额、无 60s 超时，S07）；任务取消时
  CancelledError 传播，注册表就地清理，库中留下 question.requested 无
  answered 的"未回答状态"；
- submit：live future 存在 → 跨线程唤醒（answered 事件由 _ask_user 在
  resolver 返回后经 ctx.emit 发射，单一来源）；无 live（重启后补答）→
  本服务直接落 question.answered 事件留事实（FSM 对历史任务 no-op，
  与审批决定同语义）；
- answer=null = 用户取消回答，按"拒绝回答"告知模型（_ask_user 输出侧
  已实现，事件载荷如实带 null）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agentcrew_core.events import RunEventType

if TYPE_CHECKING:
    from .db.database import Database
    from .db.event_store import EventStore

_log = logging.getLogger("agentcrew.questions")


class QuestionNotFound(LookupError):
    pass


class QuestionStale(RuntimeError):
    pass


@dataclass
class _QPending:
    future: asyncio.Future
    loop: asyncio.AbstractEventLoop
    task_run_id: str
    conversation_id: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QuestionService:
    def __init__(self, db: "Database", event_store: "EventStore"):
        self._db = db
        self._store = event_store
        self._pending: dict[str, _QPending] = {}

    def make_resolver(self, task_run_id: str, conversation_id: str):
        async def resolver(request_id: str) -> str | None:
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            self._pending[request_id] = _QPending(
                future=future, loop=loop,
                task_run_id=task_run_id, conversation_id=conversation_id)
            try:
                return await future  # S07：等用户回答不设超时
            finally:
                self._pending.pop(request_id, None)  # 取消时干净释放

        return resolver

    async def submit(self, request_id: str, answer: str | None) -> dict:
        row = self._db.read_conn.execute(
            "SELECT task_run_id, conversation_id, created_at FROM run_events"
            " WHERE type = 'question.requested'"
            " AND json_extract(payload, '$.request_id') = ?"
            " ORDER BY global_seq DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        if row is None:
            raise QuestionNotFound(f"提问不存在：{request_id}")
        task_run_id, conversation_id, _ = row
        answered = self._db.read_conn.execute(
            "SELECT created_at FROM run_events"
            " WHERE type = 'question.answered'"
            " AND json_extract(payload, '$.request_id') = ?"
            " AND conversation_id = ?"
            " ORDER BY global_seq DESC LIMIT 1",
            (request_id, conversation_id),
        ).fetchone()
        pending = self._pending.get(request_id)
        if answered is not None and pending is None:
            raise QuestionStale("该提问已回答过（不可重复提交）")
        answered_at = _now()
        if pending is not None and not pending.future.done():
            # live：唤醒等待方；question.answered 事件由 _ask_user 发射
            pending.loop.call_soon_threadsafe(
                pending.future.set_result, answer)
        elif answered is None:
            # 无 live（重启后孤儿提问）：本服务直接落回答事实留痕
            await self._store.append(
                task_run_id=task_run_id, conversation_id=conversation_id,
                type=RunEventType.QUESTION_ANSWERED,
                payload={"request_id": request_id, "answer": answer},
            )
        return {"request_id": request_id, "answered_at": answered_at}

    def pending_questions(self, conversation_id: str) -> list[dict]:
        """未回答提问（仅活跃任务；终端任务的孤儿提问不再是可回答卡）。"""
        rows = self._db.read_conn.execute(
            "SELECT re.task_run_id, re.payload, re.created_at FROM run_events re"
            " WHERE re.conversation_id = ? AND re.type = 'question.requested'"
            "   AND re.task_run_id IN (SELECT id FROM task_runs"
            "       WHERE conversation_id = ?"
            "       AND status NOT IN ('completed', 'failed', 'cancelled'))"
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM run_events r2"
            "     WHERE r2.conversation_id = re.conversation_id"
            "       AND r2.type = 'question.answered'"
            "       AND json_extract(r2.payload, '$.request_id')"
            "           = json_extract(re.payload, '$.request_id'))"
            " ORDER BY re.global_seq",
            (conversation_id, conversation_id),
        ).fetchall()
        out = []
        for task_run_id, payload_text, asked_at in rows:
            p = json.loads(payload_text)
            out.append({
                "request_id": p.get("request_id"),
                "question": p.get("question"),
                "options": p.get("options") or [],
                "asked_at": asked_at,
                "task_run_id": task_run_id,
            })
        return out
