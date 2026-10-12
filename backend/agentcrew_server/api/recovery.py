"""中断恢复 API（M0-C9）：pending-verifications / verification / resume /
artifacts。

契约：docs/contracts/openapi.yaml v0.3——resume 202（data.warnings = 重建
期降级清单，外审回稿 K1：不再只进日志；409 带 ErrEnvelope）；
verification 200 返回 {call_id, status, verdict}；artifacts missing 由
惰性探测更新。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Query, Request
from pydantic import BaseModel

from ..sessions import SessionError
from .errors import ApiError, ErrorCode


class VerificationRequest(BaseModel):
    verdict: str  # confirmed_executed | confirmed_not_executed
    note: str | None = None


def install_recovery_routes(app, runtime) -> None:
    recovery = runtime.recovery
    if recovery is None:
        return

    @app.get("/api/conversations/{conversation_id}/pending-verifications")
    async def list_pending_verifications(conversation_id: str):
        def query() -> list[dict[str, Any]]:
            return recovery.pending_verifications(conversation_id)
        return await asyncio.to_thread(query)

    @app.post("/api/tool-calls/{call_id}/verification")
    async def submit_verification(call_id: str, body: VerificationRequest, request: Request):
        try:
            return await recovery.submit_verification(
                call_id, body.verdict, body.note,
                request_identity=request.state.identity if runtime.governance is not None else None)
        except SessionError as e:
            raise ApiError(e.code, str(e), detail=e.detail) from None

    @app.post("/api/task-runs/{task_run_id}/resume", status_code=202)
    async def resume_task_run(task_run_id: str, request: Request):
        try:
            outcome = await recovery.resume(task_run_id, resume_reason=None,
                request_identity=request.state.identity if runtime.governance is not None else None)
        except SessionError as e:
            raise ApiError(e.code, str(e), detail=e.detail) from None
        # K1：非 409 路径的降级告警（如工件缺失）随 202 返回调用方可见
        return {"warnings": outcome["warnings"]}

    @app.get("/api/conversations/{conversation_id}/artifacts")
    async def list_artifacts(
        conversation_id: str,
        task_run_id: str | None = Query(None),
    ):
        return await recovery.list_artifacts(conversation_id, task_run_id)
