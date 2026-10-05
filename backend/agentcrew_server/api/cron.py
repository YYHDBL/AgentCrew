"""真实计划管理、授权预览及发生历史 API。"""

import asyncio
from typing import Annotated, Literal

from fastapi import Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from agentcrew_core.cron.schedule import validate_schedule, MAX_TIMESTAMP_MS, ScheduleError
from ..cron.store import CronStore


class ScheduleBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tz: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def check_schedule(self):
        validate_schedule(self.model_dump())
        return self


class AtSchedule(ScheduleBase):
    kind: Literal["at"]
    at_ms: StrictInt = Field(ge=0, le=MAX_TIMESTAMP_MS)


class EverySchedule(ScheduleBase):
    kind: Literal["every"]
    every_ms: StrictInt = Field(ge=1000, le=MAX_TIMESTAMP_MS)


class CronSchedule(ScheduleBase):
    kind: Literal["cron"]
    expr: str = Field(min_length=1, max_length=200)


Schedule = Annotated[AtSchedule | EverySchedule | CronSchedule, Field(discriminator="kind")]


class Authorization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(min_length=1, max_length=128)
    pattern: str = Field(min_length=1, max_length=32768)


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=100000)
    execution_mode: Literal["existing", "new_conversation"]
    conversation_id: str | None

    @model_validator(mode="after")
    def check_conversation(self):
        if self.execution_mode == "existing" and not self.conversation_id:
            raise ValueError("existing 必须指定会话标识")
        if self.execution_mode == "new_conversation" and self.conversation_id is not None:
            raise ValueError("new_conversation 不接受会话标识")
        return self


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    workspace_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)
    schedule: Schedule
    target: Target
    pre_authorized: list[Authorization] = Field(max_length=100)
    enabled: bool = True


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


def install_cron_routes(app, runtime):
    service = runtime.cron_store = CronStore(runtime)

    @app.exception_handler(ScheduleError)
    async def schedule_error(_: Request, error: ScheduleError):
        return JSONResponse({"error": {"code": "VALIDATION_ERROR", "message": str(error)}}, status_code=422)

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
