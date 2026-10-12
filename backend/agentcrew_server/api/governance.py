"""组织、工作区、员工、Skill资源与当前授权API。"""

from typing import Literal

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from ..governance.management import Management
from ..governance.resources import GovernanceError
from ..memory.skills import MemorySkills
from ..memory.store import MemoryIdentity
from .memory import SkillCreateBody, checked
from .governance_stream import install_governance_stream


class ChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: StrictInt = Field(ge=0)


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position: str = Field(min_length=1, max_length=2000)
    model_slot: Literal["main", "aux"]
    skill_ids: list[str]
    connector_ids: list[str]

    @model_validator(mode="after")
    def unique_references(self):
        if len(set(self.skill_ids)) != len(self.skill_ids) or len(set(self.connector_ids)) != len(self.connector_ids):
            raise ValueError("员工资源引用必须唯一")
        return self


class WorkspaceCreate(ChangeBody):
    expected_revision: StrictInt = Field(ge=0, le=0)
    name: str = Field(min_length=1, max_length=80)
    status: Literal["active", "disabled", "archived"] = "active"


class WorkspacePatch(ChangeBody):
    expected_revision: StrictInt = Field(ge=1)
    name: str | None = Field(None, min_length=1, max_length=80)
    status: Literal["active", "disabled", "archived"] | None = None

    @model_validator(mode="after")
    def no_nulls(self):
        if any(getattr(self, key) is None for key in self.model_fields_set - {"change_id", "expected_revision"}):
            raise ValueError("管理字段不能使用null")
        return self


class AgentCreate(WorkspaceCreate):
    workspace_id: str = Field(min_length=1)
    spec: AgentSpec


class AgentPatch(WorkspacePatch):
    spec: AgentSpec | None = None


class SkillPatch(WorkspacePatch):
    description: str | None = Field(None, min_length=1, max_length=60)


def install_governance_routes(app, runtime):
    management = Management(runtime)
    runtime.management = management
    skills = MemorySkills(runtime.memory)
    install_governance_stream(app, runtime)

    @app.get("/api/organization")
    async def get_organization(request: Request):
        return management.access(request.state.identity, "organization", runtime.identities.current(request.state.identity)["org_id"])

    @app.patch("/api/organization")
    async def patch_organization(body: WorkspacePatch, request: Request):
        return await management.update(request.state.identity, "organization", runtime.identities.current(request.state.identity)["org_id"], body.model_dump(exclude_unset=True))

    @app.get("/api/workspaces")
    async def list_workspaces(request: Request, workspace_id: str | None = None, limit: int = Query(50, ge=1, le=200), after: str | None = None):
        return management.list(request.state.identity, "workspace", workspace_id, limit, after)

    @app.post("/api/workspaces")
    async def create_workspace(body: WorkspaceCreate, request: Request):
        return await management.create(request.state.identity, "workspace", body.model_dump())

    @app.get("/api/workspaces/{id}")
    async def get_workspace(id: str, request: Request):
        return management.access(request.state.identity, "workspace", id)

    @app.patch("/api/workspaces/{id}")
    async def patch_workspace(id: str, body: WorkspacePatch, request: Request):
        return await management.update(request.state.identity, "workspace", id, body.model_dump(exclude_unset=True))

    @app.delete("/api/workspaces/{id}")
    async def disable_workspace(id: str, body: ChangeBody, request: Request):
        return await management.update(request.state.identity, "workspace", id, {**body.model_dump(), "status": "disabled"})

    @app.get("/api/agents")
    async def list_agents(request: Request, workspace_id: str | None = None, limit: int = Query(50, ge=1, le=200), after: str | None = None):
        return management.list(request.state.identity, "agent", workspace_id, limit, after)

    @app.post("/api/agents")
    async def create_agent(body: AgentCreate, request: Request):
        return await management.create(request.state.identity, "agent", body.model_dump())

    @app.get("/api/agents/{id}")
    async def get_agent(id: str, request: Request):
        return management.access(request.state.identity, "agent", id)

    @app.patch("/api/agents/{id}")
    async def patch_agent(id: str, body: AgentPatch, request: Request):
        return await management.update(request.state.identity, "agent", id, body.model_dump(exclude_unset=True))

    @app.delete("/api/agents/{id}")
    async def disable_agent(id: str, body: ChangeBody, request: Request):
        return await management.update(request.state.identity, "agent", id, {**body.model_dump(), "status": "disabled"})

    @app.get("/api/skills")
    async def list_skills(request: Request, workspace_id: str | None = None, limit: int = Query(50, ge=1, le=200), after: str | None = None):
        return management.list(request.state.identity, "skill", workspace_id, limit, after)

    @app.post("/api/skills")
    async def create_skill(body: SkillCreateBody, request: Request):
        runtime.identities.require(request.state.identity, "manage", body.workspace_id)
        runtime.identities.agent(request.state.identity, body.agent_id, body.workspace_id)
        actor = MemoryIdentity(body.workspace_id, body.agent_id, actor_id=request.state.identity.effective_user_id, conversation_id=body.conversation_id)
        result = checked(await skills.change(actor, body.name, action="create", change_id=body.change_id,
            expected_revision=0, basis=body.basis, description=body.description, text=body.text, files=body.files,
            management_request={"operation": "governance_create", "body": body.model_dump()}))
        return result["governance_resource"]

    @app.get("/api/skills/{id}")
    async def get_skill(id: str, request: Request):
        return management.access(request.state.identity, "skill", id)

    @app.patch("/api/skills/{id}")
    async def patch_skill(id: str, body: SkillPatch, request: Request):
        return await management.skill_update(request.state.identity, id, body.model_dump(exclude_unset=True))

    @app.delete("/api/skills/{id}")
    async def archive_skill(id: str, body: ChangeBody, request: Request):
        return await management.skill_update(request.state.identity, id, {**body.model_dump(), "status": "archived"})
