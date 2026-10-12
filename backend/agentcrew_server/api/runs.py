"""运行记录与提问回答 API（M0-C8）：cancel / attempts / task-runs / questions。

契约：docs/contracts/openapi.yaml v0.3（/api/task-runs/{id}/cancel 与
/api/questions/{requestId}/answer 均为 202/200 无业务信封外字段；attempts
列表暴露 context_fingerprint——配置版本绑定的验收面）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from typing import Literal

from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel

from ..questions import QuestionNotFound, QuestionStale
from ..sessions import SessionError
from .errors import ApiError, ErrorCode
from ..runs import Runs


class QuestionAnswerRequest(BaseModel):
    answer: str | None = None  # null = 用户取消，按"拒绝回答"告知模型


def run_filters(workspace_id: str | None = None, agent_id: str | None = None,
                conversation_id: str | None = None,
                status: Literal["queued", "running", "waiting_user", "waiting_verification", "interrupted", "completed", "failed", "cancelled"] | None = None,
                source: Literal["user", "cron", "manual"] | None = None,
                since: datetime | None = None, until: datetime | None = None):
    values = {"workspace_id": workspace_id, "agent_id": agent_id,
              "conversation_id": conversation_id, "status": status, "source": source}
    for name, value in (("since", since), ("until", until)):
        if value is not None:
            if value.tzinfo is None:
                raise ApiError(ErrorCode.VALIDATION_ERROR, "时间筛选必须包含时区")
            values[name] = value.astimezone(timezone.utc).isoformat()
    if since is not None and until is not None and since > until:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "开始时间不能晚于结束时间")
    return {key: value for key, value in values.items() if value is not None}


def install_run_routes(app, runtime) -> None:
    sessions = runtime.sessions
    questions = runtime.questions
    run_manager = runtime.run_manager
    db = runtime.db
    center = Runs(runtime) if runtime.identities is not None else None

    @app.get("/api/task-runs")
    async def list_runs(request: Request, filters: dict = Depends(run_filters),
                        limit: int = Query(50, ge=1, le=200), after: str | None = None):
        return await asyncio.to_thread(center.list, request.state.identity, filters, limit, after)

    @app.get("/api/runs/metrics")
    async def metrics(request: Request, filters: dict = Depends(run_filters)):
        return await asyncio.to_thread(center.metrics, request.state.identity, filters)

    @app.get("/api/task-runs/{task_run_id}")
    async def read_run(task_run_id: str, request: Request):
        return {"data": await asyncio.to_thread(center.read, request.state.identity, task_run_id)}

    @app.get("/api/task-runs/{task_run_id}/events/{seq}")
    async def locate_event(task_run_id: str, seq: int, request: Request,
                           attempt_no: int | None = Query(None, ge=1)):
        return await asyncio.to_thread(center.locate, request.state.identity, task_run_id, seq, attempt_no)

    @app.get("/api/task-runs/{task_run_id}/calls/{run_call_id}")
    async def read_call(task_run_id: str, run_call_id: str, request: Request):
        return await asyncio.to_thread(center.call, request.state.identity, task_run_id, run_call_id)

    @app.get("/api/conversations/{conversation_id}/task-runs")
    async def list_task_runs(conversation_id: str):
        def query() -> list[dict[str, Any]]:
            sessions.conversation_or_404(conversation_id)
            rows = db.read_conn.execute(
                "SELECT id, instruction, status, current_attempt_no,"
                " cron_job_id, created_at, finished_at FROM task_runs"
                " WHERE conversation_id = ? ORDER BY created_at",
                (conversation_id,)).fetchall()
            return [dict(zip(
                ("id", "instruction", "status", "current_attempt_no",
                 "cron_job_id", "created_at", "finished_at"), r))
                for r in rows]
        return await asyncio.to_thread(query)

    @app.get("/api/task-runs/{task_run_id}/attempts")
    async def list_attempts(task_run_id: str):
        def query() -> list[dict[str, Any]]:
            exists = db.read_conn.execute(
                "SELECT 1 FROM task_runs WHERE id = ?",
                (task_run_id,)).fetchone()
            if exists is None:
                raise SessionError(ErrorCode.NOT_FOUND,
                                   f"任务不存在：{task_run_id}")
            rows = db.read_conn.execute(
                "SELECT attempt_no, kind, outcome, context_fingerprint,"
                " resume_reason, started_at, ended_at FROM run_attempts"
                " WHERE task_run_id = ? ORDER BY attempt_no",
                (task_run_id,)).fetchall()
            out = []
            for r in rows:
                fingerprint = json.loads(r[3]) if r[3] else None
                out.append({
                    "attempt_no": r[0], "kind": r[1], "outcome": r[2],
                    "context_fingerprint": fingerprint,
                    "resume_reason": r[4], "started_at": r[5],
                    "ended_at": r[6]})
            return out
        return await asyncio.to_thread(query)

    @app.post("/api/task-runs/{task_run_id}/cancel", status_code=202)
    async def cancel_task_run(task_run_id: str):
        try:
            await run_manager.request_cancel(task_run_id)
        except LookupError as e:
            raise ApiError(ErrorCode.NOT_FOUND, str(e)) from None
        return Response(status_code=202)  # 契约：202 无响应体

    @app.get("/api/conversations/{conversation_id}/questions")
    async def list_questions(conversation_id: str):
        def query() -> list[dict]:
            sessions.conversation_or_404(conversation_id)
            return questions.pending_questions(conversation_id)
        return await asyncio.to_thread(query)

    @app.post("/api/questions/{request_id}/answer")
    async def answer_question(request_id: str, body: QuestionAnswerRequest):
        try:
            return await questions.submit(request_id, body.answer)
        except QuestionNotFound as e:
            raise ApiError(ErrorCode.NOT_FOUND, str(e)) from None
        except QuestionStale as e:
            raise ApiError(ErrorCode.QUESTION_STALE, str(e)) from None
