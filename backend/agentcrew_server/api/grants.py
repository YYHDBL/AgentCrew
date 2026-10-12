"""Grant授予、回收和按当前身份分页查询。"""

from typing import Literal
import json

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from agentcrew_core.memory.pagination import memory_page
from ..governance.grants import Grants


class GrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    resource_type: Literal["skill", "connector", "agent"]
    resource_id: str = Field(min_length=1)
    grantee_type: Literal["agent", "user"]
    grantee_id: str = Field(min_length=1)


class RevokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: StrictInt = Field(ge=1)


def install_grant_routes(app, runtime):
    service = Grants(runtime.governance, runtime.identities)
    runtime.grants = service
    runtime.event_store.authorization_checker = service.check_dispatch
    runtime.approvals.grants = service
    runtime.run_manager.grants = service
    runtime.memory.grants = service
    runtime.memory_jobs.grants = service

    @app.get("/api/grants")
    async def list_grants(request: Request, workspace_id: str | None = None, revoked: bool = False,
                          grantee_id: str | None = None, limit: int = Query(50, ge=1, le=200), after: str | None = None):
        identity = request.state.identity
        current = runtime.identities.require(identity, "use", workspace_id)
        rows = runtime.db.read_conn.execute("""SELECT g.id FROM grants g JOIN governance_resources r
            ON r.resource_type=g.resource_type AND r.resource_id=g.resource_id
            WHERE r.workspace_id IN(SELECT value FROM json_each(?)) AND (? IS NULL OR r.workspace_id=?)
            AND (? OR g.revoked_at IS NULL) AND (? IS NULL OR g.grantee_id=?) ORDER BY g.created_at,g.id""",
            (json.dumps(current["workspace_ids"]), workspace_id, workspace_id, revoked, grantee_id, grantee_id)).fetchall()
        items = [service.get(row[0]) for row in rows]
        if current["role"] == "member":
            items = [item for item in items if item["grantee_type"] == "user" and item["grantee_id"] == identity.effective_user_id]
        return memory_page(items, {"actor": identity.effective_user_id, "workspace": workspace_id, "revoked": revoked,
            "grantee": grantee_id, "role": current["role"], "order": "created_at,id"}, limit, after, key="id")

    @app.post("/api/grants")
    async def create_grant(body: GrantBody, request: Request):
        return await service.create(request.state.identity, body.model_dump())

    @app.delete("/api/grants/{id}")
    async def revoke_grant(id: str, body: RevokeBody, request: Request):
        return await service.revoke(request.state.identity, id, body.model_dump())
