"""身份、角色及工作区访问API。"""

import asyncio
from typing import Literal

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from agentcrew_core.events import RunEventType
from agentcrew_core.memory.pagination import memory_page
from ..governance.identity import Identities
from ..governance.resources import GovernanceError


class DemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1)
    change_id: str = Field(min_length=1, max_length=128)


class RoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["owner", "admin", "member"]
    status: Literal["active", "disabled"] | None = None
    change_id: str = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=1)


class WorkspaceMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    change_id: str = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=0)


def install_identity_routes(app, runtime):
    service = Identities(runtime.governance)
    runtime.identities = service
    runtime.event_store.task_preparer = service.bind_task
    runtime.event_store.authorization_checker = service.check_dispatch
    runtime.memory.identities = service
    if runtime.memory_jobs is not None:
        runtime.memory_jobs.identities = service
        runtime.memory_jobs.review.sessions.identities = service
    if runtime.approvals is not None:
        runtime.approvals.identities = service
    if runtime.recovery is not None:
        runtime.recovery.identities = service
    if runtime.run_manager is not None:
        runtime.run_manager.identities = service
    if runtime.sessions is not None:
        runtime.sessions.identities = service

    @app.get("/api/identity")
    async def read_identity(request: Request):
        return service.current(request.state.identity)

    @app.post("/api/identity/demo")
    async def issue_demo(body: DemoRequest, request: Request):
        return await service.issue(request.state.identity, body.user_id, body.change_id)

    @app.get("/api/memberships")
    async def list_memberships(request: Request, limit: int = Query(50, ge=1, le=200), after: str | None = None):
        identity = request.state.identity
        value = service.current(identity)
        rows = runtime.db.read_conn.execute("SELECT m.*,u.name FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.org_id=? AND (?='owner' OR m.user_id=?) ORDER BY m.id",
            (value["org_id"], value["role"], identity.effective_user_id)).fetchall()
        items = [{**dict(row), "workspace_ids": [r[0] for r in runtime.db.read_conn.execute("SELECT workspace_id FROM workspace_members WHERE user_id=? AND enabled=1 ORDER BY workspace_id", (row["user_id"],))],
            "workspace_access": [{"workspace_id": item[0], "enabled": bool(item[1]), "revision": item[2]} for item in runtime.db.read_conn.execute("SELECT workspace_id,enabled,revision FROM workspace_members WHERE user_id=? ORDER BY workspace_id", (row["user_id"],))]} for row in rows]
        return memory_page(items, {"actor": identity.effective_user_id, "org": value["org_id"], "order": "membership-id"}, limit, after, key="id")

    @app.patch("/api/memberships/{id}/role")
    async def edit_role(id: str, body: RoleRequest, request: Request):
        return await service.role(request.state.identity, id, body.model_dump(exclude_none=True))

    @app.put("/api/workspaces/{id}/members/{user_id}")
    async def edit_workspace_member(id: str, user_id: str, body: WorkspaceMemberRequest, request: Request):
        identity = request.state.identity
        def operation(conn):
            workspace = runtime.governance.get("workspace", id, conn)
            if conn.execute("SELECT 1 FROM memberships WHERE user_id=? AND org_id=? AND status='active'", (user_id, workspace["org_id"])).fetchone() is None:
                raise GovernanceError("OUT_OF_SCOPE", "成员不属于工作区组织", 403)
            row = conn.execute("SELECT revision FROM workspace_members WHERE workspace_id=? AND user_id=?", (id, user_id)).fetchone()
            revision = row[0] if row else 0
            if revision != body.expected_revision:
                raise GovernanceError("REVISION_CONFLICT", "工作区成员修订已经改变")
            conn.execute("INSERT INTO workspace_members VALUES(?,?,?,?) ON CONFLICT(workspace_id,user_id) DO UPDATE SET enabled=excluded.enabled,revision=excluded.revision", (id, user_id, int(body.enabled), revision + 1))
            return {"workspace_id": id, "user_id": user_id, "enabled": body.enabled, "revision": revision + 1}, {"org_id": workspace["org_id"], "workspace_id": id, "agent_id": None, "owner_id": user_id}, {"status": "active" if body.enabled else "disabled"}
        return await runtime.governance.mutate(change_id=body.change_id, actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request=body.model_dump(), action="governance.workspace_member_changed",
            kind="workspace_member", resource_id=id + ":" + user_id, operation=operation,
            event_type=RunEventType.GOVERNANCE_RESOURCE_CHANGED, authorize=lambda conn: service.require(identity, "owner", id, conn=conn))
