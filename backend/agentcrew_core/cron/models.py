"""API与员工提案共用的严格计划参数。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .schedule import validate_schedule, MAX_TIMESTAMP_MS


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


class ScheduleTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    schedule: Schedule
    instruction: str = Field(min_length=1, max_length=100000)
    execution_mode: Literal["existing", "new_conversation"]
    pre_authorized: list[Authorization] = Field(max_length=100)
