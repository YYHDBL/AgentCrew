"""后台作业查询、取消和独立人工审批的 HTTP 边界。"""

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .errors import ApiError


class JobDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["allow_once", "reject_once"]
    input_hash: str


def _checked(value):
    if "error" in value and "id" not in value:
        raise ApiError(value["error"], value["message"])
    return value


def install_memory_job_routes(app, runtime):
    review = runtime.memory_jobs.review

    @app.get("/api/memory/jobs/{job_id}")
    async def get_job(job_id: str):
        return {"data": _checked(await asyncio.to_thread(review.view, job_id))}

    @app.post("/api/memory/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        return {"data": _checked(await review.cancel(job_id))}

    @app.post("/api/memory/jobs/{job_id}/approvals/{approval_id}")
    async def decide_job(job_id: str, approval_id: str, body: JobDecision):
        _checked(await review.decide(job_id, approval_id, body.decision, body.input_hash))
        return {"data": _checked(await asyncio.to_thread(review.view, job_id))}
