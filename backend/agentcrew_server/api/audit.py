"""审计查询、导出、验证及服务管理的诊断备份。"""

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from ..governance.audit import AuditService
from ..governance.backups import DiagnosticBackups


class BackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128)
    kind: Literal["database", "directory"]


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128)
    backup_id: str = Field(min_length=1)


def install_audit_routes(app, runtime):
    service = AuditService(runtime)
    runtime.audit = service
    backups = DiagnosticBackups(runtime)

    @app.get("/api/audit")
    @app.get("/api/audit/export")
    async def query_audit(request: Request, workspace_id: str | None = None, actor: str | None = None,
            action: str | None = None, resource: str | None = None, limit: int = Query(50, ge=1, le=200), after: str | None = None):
        return service.query(request.state.identity, workspace_id=workspace_id, actor=actor, action=action,
            resource=resource, limit=limit, after=after)

    @app.post("/api/audit/verify")
    async def verify_audit(request: Request):
        return await service.verify(request.state.identity)

    @app.get("/api/diagnostics/backups")
    async def list_backups(request: Request):
        return backups.list(request.state.identity)

    @app.post("/api/diagnostics/backups")
    async def create_backup(body: BackupRequest, request: Request):
        return await backups.create(request.state.identity, body.change_id, body.kind)

    @app.post("/api/diagnostics/restore")
    async def restore_backup(body: RestoreRequest, request: Request):
        return await backups.restore(request.state.identity, body.backup_id, body.change_id)
