"""审批 API（M0-C6）：POST /api/tool-approvals/:callId + GET pending（契约 v0.3）。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from ..approvals import ApprovalNotFound, ApprovalStale
from ..db.queries import task_run_exists
from .errors import ApiError, ErrorCode
import asyncio


class ApprovalDecisionRequest(BaseModel):
    decision: str  # allow_once | allow_always | reject_once | reject_always
    input_hash: str | None = None  # 可选：与审批卡绑定校验，不一致 409


def install_approval_routes(app, runtime) -> None:
    service = runtime.approvals

    @app.post("/api/tool-approvals/{call_id}")
    async def submit_decision(call_id: str, body: ApprovalDecisionRequest):
        try:
            result = await service.submit(
                call_id, body.decision, body.input_hash)
        except ApprovalNotFound:
            raise ApiError(ErrorCode.NOT_FOUND, f"审批不存在：{call_id}") from None
        except ApprovalStale as e:
            raise ApiError(ErrorCode.APPROVAL_STALE, str(e)) from None
        return result

    @app.get("/api/task-runs/{task_run_id}/approvals")
    async def list_approvals(
        task_run_id: str,
        status: str = Query("pending", pattern="^(pending|resolved)$"),
    ):
        # read_conn 必须在 worker 线程内解析（每线程一条只读连接）——在事件
        # 循环线程取出再交给 worker 会共享同一条连接（C3 已修过的类，外审 S03）
        if not await asyncio.to_thread(
                lambda: task_run_exists(runtime.db.read_conn, task_run_id)):
            raise ApiError(ErrorCode.NOT_FOUND, f"任务不存在：{task_run_id}")
        return await service.list_approvals(task_run_id, status)
