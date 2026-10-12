"""员工规则查询、人工创建及回收API。"""

from typing import Literal

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict, Field

from agentcrew_core.memory.pagination import memory_page
from ..governance.rules import Rules
from .grants import RevokeBody


class RuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    tool_name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    pattern: str = Field(min_length=1, max_length=32768)
    effect: Literal["allow", "deny"]


def install_rule_routes(app, runtime):
    service = Rules(runtime.governance, runtime.identities)
    runtime.approvals.rules = service
    runtime.grants.approvals = runtime.approvals
    runtime.memory_jobs.rules = service

    @app.get("/api/agents/{id}/permission-rules")
    async def list_rules(id: str, request: Request, revoked: bool = False,
                         limit: int = Query(50, ge=1, le=200), after: str | None = None):
        runtime.identities.agent(request.state.identity, id)
        rows = runtime.db.read_conn.execute("SELECT id FROM agent_permission_rules WHERE agent_id=? AND (? OR revoked_at IS NULL) ORDER BY created_at,id", (id, revoked)).fetchall()
        return memory_page([service.get(row[0], id) for row in rows],
            {"actor": request.state.identity.effective_user_id, "agent": id, "revoked": revoked}, limit, after, key="id")

    @app.post("/api/agents/{id}/permission-rules")
    async def create_rule(id: str, body: RuleBody, request: Request):
        return await service.create(request.state.identity, id, body.model_dump())

    @app.delete("/api/agents/{id}/permission-rules/{rule_id}")
    async def revoke_rule(id: str, rule_id: str, body: RevokeBody, request: Request):
        return await service.revoke(request.state.identity, id, rule_id, body.model_dump())
