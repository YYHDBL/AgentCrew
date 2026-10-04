"""连接器管理、脱敏配置和真实连接校验API。"""

from typing import Literal
import json

from fastapi import Query, Request
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, StrictInt, model_validator

from agentcrew_core.memory.pagination import memory_page
from agentcrew_core.tools import WorkContext, build_protected_paths
from ..governance.connectors import Connectors
from ..governance.resources import GovernanceError


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read_only: bool
    destructive: bool
    risk_level: Literal["low", "medium", "high"]
    needs_approval: bool
    timeout_ms: StrictInt = Field(60000, ge=1, le=300000)


class IdempotentEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=1)
    guarantee: str = Field(min_length=1)


class ConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: AnyHttpUrl | None = None
    allowed_hosts: list[str]
    allowed_ports: list[StrictInt] = Field(max_length=65535)
    allow_loopback: bool = False
    transport: Literal["stdio", "streamable_http"] | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    credential_header: Literal["Authorization", "X-API-Key"] = "Authorization"
    tool_policies: dict[str, ToolPolicy] = Field(default_factory=dict)
    idempotent_endpoints: list[IdempotentEndpoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def check(self):
        if len(set(self.allowed_hosts)) != len(self.allowed_hosts) or len(set(self.allowed_ports)) != len(self.allowed_ports):
            raise ValueError("连接器主机和端口必须唯一")
        if any(not 1 <= port <= 65535 for port in self.allowed_ports):
            raise ValueError("连接器端口超出范围")
        return self


class ConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: StrictInt = Field(ge=0, le=0)
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)
    type: Literal["http", "mcp"]
    config: ConnectorConfig
    credential: str | None = None

    @model_validator(mode="after")
    def check(self):
        if self.type == "http" and self.config.url is None:
            raise ValueError("HTTP连接器需要目标URL")
        if self.type == "mcp" and (self.config.transport is None or self.config.transport == "stdio" and self.config.command is None
                or self.config.transport == "streamable_http" and self.config.url is None):
            raise ValueError("MCP传输配置不完整")
        if self.type == "mcp" and self.config.transport == "stdio" and self.credential is not None:
            raise ValueError("stdio连接器不传递网络凭据")
        return self


class ConnectorPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: StrictInt = Field(ge=1)
    name: str | None = Field(None, min_length=1, max_length=80)
    status: Literal["active", "disabled", "archived"] | None = None
    config: ConnectorConfig | None = None
    credential: str | None = None


def install_connector_routes(app, runtime):
    service = Connectors(runtime.governance, runtime.identities, runtime.grants)
    runtime.connectors = service
    runtime.grants.connectors = service
    runtime.approvals.connectors = service

    @app.get("/api/connectors")
    async def list_connectors(request: Request, workspace_id: str | None = None,
                              limit: int = Query(50, ge=1, le=200), after: str | None = None):
        current = runtime.identities.require(request.state.identity, "manage", workspace_id)
        rows = runtime.db.read_conn.execute("SELECT id FROM connectors WHERE workspace_id IN(SELECT value FROM json_each(?)) AND (? IS NULL OR workspace_id=?) ORDER BY created_at,id",
            (json.dumps(current["workspace_ids"]), workspace_id, workspace_id)).fetchall()
        return memory_page([runtime.governance.get("connector", row[0]) for row in rows],
            {"actor": request.state.identity.effective_user_id, "workspace": workspace_id}, limit, after, key="id")

    @app.get("/api/connectors/{id}")
    async def get_connector(id: str, request: Request):
        resource = runtime.governance.get("connector", id)
        runtime.identities.require(request.state.identity, "manage", resource["workspace_id"])
        return resource

    @app.post("/api/connectors")
    async def create_connector(body: ConnectorCreate, request: Request):
        return await service.create(request.state.identity, body.model_dump(mode="json", exclude_none=True))

    @app.patch("/api/connectors/{id}")
    async def patch_connector(id: str, body: ConnectorPatch, request: Request):
        return await service.update(request.state.identity, id, body.model_dump(mode="json", exclude_unset=True))

    @app.delete("/api/connectors/{id}")
    async def disable_connector(id: str, body: ConnectorPatch, request: Request):
        return await service.update(request.state.identity, id, {"change_id": body.change_id, "expected_revision": body.expected_revision, "status": "disabled"})

    @app.post("/api/connectors/{id}/validate")
    async def validate_connector(id: str, request: Request):
        from pathlib import Path
        resource = runtime.governance.get("connector", id)
        root = Path(runtime.governance.get("workspace", resource["workspace_id"])["data_dir"])
        context = WorkContext(scope=[root], cwd=root, protected=build_protected_paths(runtime.data_dir, Path.home()))
        return await service.validate(request.state.identity, id, context)
