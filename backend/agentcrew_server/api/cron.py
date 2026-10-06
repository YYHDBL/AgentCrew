"""真实计划管理、授权预览及发生历史 API。"""

import asyncio
from typing import Literal

from fastapi import Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from agentcrew_core.cron.schedule import ScheduleError
from agentcrew_core.cron.models import Authorization, Target, Schedule, CreateBody
from ..cron.store import CronStore
from ..approvals import ApprovalStale, ApprovalNotFound


class PatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: StrictInt = Field(ge=1)
    name: str | None = Field(None, min_length=1, max_length=80)
    schedule: Schedule | None = None
    target: Target | None = None
    pre_authorized: list[Authorization] | None = Field(None, max_length=100)
    enabled: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_null(self):
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("修改字段不能为null")
        return self


class DeleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: StrictInt = Field(ge=1)


class RunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=1)


class ProposalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["allow_once", "reject_once"]
    input_hash: str = Field(min_length=1)
    expected_revision: StrictInt = Field(ge=1)
    selected: list[Authorization] = Field(max_length=100)


def install_cron_routes(app, runtime):
    service = runtime.cron_store = CronStore(runtime)

    @app.exception_handler(ScheduleError)
    async def schedule_error(_: Request, error: ScheduleError):
        return JSONResponse({"error": {"code": "VALIDATION_ERROR", "message": str(error)}}, status_code=422)

    @app.exception_handler(ApprovalStale)
    async def stale_proposal(_: Request, error: ApprovalStale):
        return JSONResponse({"error": {"code": "APPROVAL_STALE", "message": str(error)}}, status_code=409)

    @app.exception_handler(ApprovalNotFound)
    async def missing_approval(_: Request, error: ApprovalNotFound):
        return JSONResponse({"error": {"code": "NOT_FOUND", "message": str(error)}}, status_code=404)

    @app.get("/api/cron/jobs")
    async def list_jobs(request: Request, workspace_id: str | None = None,
                        limit: int = Query(50, ge=1, le=200), after: str | None = None):
        return await asyncio.to_thread(service.list, request.state.identity, workspace_id, limit, after)

    @app.post("/api/cron/jobs", status_code=201)
    async def create_job(body: CreateBody, request: Request):
        return await service.create(request.state.identity, body.model_dump())

    @app.get("/api/cron/jobs/{job_id}")
    async def read_job(job_id: str, request: Request):
        return await asyncio.to_thread(service.get, request.state.identity, job_id)

    @app.patch("/api/cron/jobs/{job_id}")
    async def edit_job(job_id: str, body: PatchBody, request: Request):
        return await service.edit(request.state.identity, job_id, body.model_dump(exclude_unset=True))

    @app.delete("/api/cron/jobs/{job_id}")
    async def delete_job(job_id: str, body: DeleteBody, request: Request):
        return await service.edit(request.state.identity, job_id, body.model_dump(), delete=True)

    @app.post("/api/cron/jobs/{job_id}/run-now", status_code=202)
    async def run_now(job_id: str, body: RunBody, request: Request):
        return await service.run_now(request.state.identity, job_id, body.model_dump())

    @app.get("/api/cron/jobs/{job_id}/runs")
    async def history(job_id: str, request: Request, limit: int = Query(50, ge=1, le=200), after: str | None = None):
        return await asyncio.to_thread(service.history, request.state.identity, job_id, limit, after)

    @app.post("/api/cron/authorization-preview")
    async def authorization(body: CreateBody, request: Request):
        return await asyncio.to_thread(service.preview, request.state.identity, body.model_dump())

    @app.get("/api/cron/proposals/{id}")
    async def read_proposal(id: str, request: Request):
        return await asyncio.to_thread(runtime.cron_proposals.get, request.state.identity, id)

    @app.post("/api/cron/proposals/{id}")
    async def decide_proposal(id: str, body: ProposalDecision, request: Request):
        return await runtime.cron_proposals.decide(request.state.identity, id, body.model_dump())
