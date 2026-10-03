"""后台作业查询、取消和独立人工审批的 HTTP 边界。"""

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from fastapi import Query

from .errors import ApiError, ErrorCode
from .memory import memory_scope_exists


class JobDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["allow_once", "reject_once"]
    input_hash: str


class CurateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    client_request_id: str = Field(min_length=1, max_length=128)


def _checked(value):
    if "error" in value and "id" not in value:
        raise ApiError(value["error"], value["message"])
    return value


def install_memory_job_routes(app, runtime):
    review = runtime.memory_jobs.review

    async def authorized_job(job_id, workspace_id, agent_id):
        job = await asyncio.to_thread(runtime.memory_jobs.get, job_id)
        if job is None:
            raise ApiError(ErrorCode.NOT_FOUND, "后台作业不存在")
        if (workspace_id is None) != (agent_id is None):
            raise ApiError(ErrorCode.VALIDATION_ERROR, "工作区与员工范围必须同时提供")
        if workspace_id is not None and (job["workspace_id"], job["agent_id"]) != (workspace_id, agent_id):
            raise ApiError(ErrorCode.OUT_OF_SCOPE, "后台作业不属于当前工作区和员工")

    @app.post("/api/memory/curate/run", status_code=202)
    async def start_curator(body: CurateRequest):
        if not await asyncio.to_thread(memory_scope_exists, runtime, body.workspace_id, body.agent_id):
            raise ApiError(ErrorCode.OUT_OF_SCOPE, "治理范围没有已登记的工作区和员工")
        job_id = await runtime.memory_jobs.curator.enqueue(body.workspace_id, body.agent_id, body.client_request_id)
        if isinstance(job_id, dict):
            _checked(job_id)
        return {"data": _checked(await asyncio.to_thread(review.view, job_id))}

    @app.get("/api/memory/jobs/{job_id}")
    async def get_job(job_id: str, workspace_id: str | None = Query(default=None, min_length=1),
                      agent_id: str | None = Query(default=None, min_length=1)):
        await authorized_job(job_id, workspace_id, agent_id)
        return {"data": _checked(await asyncio.to_thread(review.view, job_id))}

    @app.post("/api/memory/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, workspace_id: str | None = Query(default=None, min_length=1),
                         agent_id: str | None = Query(default=None, min_length=1)):
        await authorized_job(job_id, workspace_id, agent_id)
        return {"data": _checked(await review.cancel(job_id))}

    @app.post("/api/memory/jobs/{job_id}/approvals/{approval_id}")
    async def decide_job(job_id: str, approval_id: str, body: JobDecision,
                         workspace_id: str | None = Query(default=None, min_length=1),
                         agent_id: str | None = Query(default=None, min_length=1)):
        await authorized_job(job_id, workspace_id, agent_id)
        _checked(await review.decide(job_id, approval_id, body.decision, body.input_hash))
        return {"data": _checked(await asyncio.to_thread(review.view, job_id))}
