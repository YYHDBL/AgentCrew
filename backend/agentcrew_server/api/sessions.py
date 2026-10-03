"""会话与排队 API：创建/材料/scope/limits/指令/排队控制/state/消息分页。

契约：docs/contracts/openapi.yaml v0.3。202 无响应体（continue）按契约
有意设计；错误码信封经 SessionError → ApiError 映射。
"""

from __future__ import annotations

import asyncio

from fastapi import Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..sessions import SessionError
from .errors import ApiError, ErrorCode, error_response


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1)
    workspace_id: str = "default"
    agent_id: str | None = None
    client_request_id: str | None = None
    import_files: list[str] | None = Field(None, max_length=20)
    folders: list[str] | None = Field(None, max_length=5)


class InstructionRequest(BaseModel):
    text: str = Field(min_length=1)
    client_request_id: str | None = None


class QueueCancelRequest(BaseModel):
    item_ids: list[str] | None = None
    all: bool = False


def install_session_routes(app, runtime) -> None:
    service = runtime.sessions

    @app.get("/api/limits")
    async def limits():
        return service.limits_view()

    @app.post("/api/conversations", status_code=201)
    async def create_conversation(body: ConversationCreateRequest, request: Request):
        identity = request.state.identity if runtime.governance is not None else None
        if identity is not None:
            runtime.identities.agent(identity, body.agent_id or "default", body.workspace_id)
        return await service.create_conversation(
            instruction=body.instruction, agent_id=body.agent_id,
            workspace_id=body.workspace_id, request_identity=identity,
            client_request_id=body.client_request_id,
            import_files=body.import_files, folders=body.folders)

    @app.get("/api/conversations")
    async def list_conversations(request: Request):
        rows = await asyncio.to_thread(service.list_conversations)
        if runtime.governance is None:
            return rows
        return [row for row in rows if runtime.identities.visible_conversation(request.state.identity, row["id"])]

    @app.get("/api/conversations/{conversation_id}/messages")
    async def list_messages(conversation_id: str,
                            limit: int = Query(50, ge=1, le=200),
                            before: str | None = Query(None, min_length=1)):
        return await asyncio.to_thread(
            service.list_messages, conversation_id, limit=limit, before=before)

    @app.get("/api/conversations/{conversation_id}/scope")
    async def get_scope(conversation_id: str):
        conv = await asyncio.to_thread(
            service.conversation_or_404, conversation_id)
        return service.scope_of(conv)

    @app.post("/api/conversations/{conversation_id}/instructions",
              status_code=202)
    async def post_instruction(conversation_id: str,
                               body: InstructionRequest, request: Request):
        return await service.send_instruction(
            conversation_id, body.text, body.client_request_id,
            request_identity=request.state.identity if runtime.governance is not None else None)

    @app.get("/api/conversations/{conversation_id}/state")
    async def get_state(conversation_id: str):
        return await asyncio.to_thread(service.state_snapshot, conversation_id)

    @app.post("/api/conversations/{conversation_id}/queue/continue",
              status_code=202)
    async def queue_continue(conversation_id: str):
        await service.continue_queue(conversation_id)
        return Response(status_code=202)  # 契约：202 无响应体

    @app.post("/api/conversations/{conversation_id}/queue/cancel")
    async def queue_cancel(conversation_id: str, body: QueueCancelRequest):
        if not body.all and not body.item_ids:
            raise ApiError(ErrorCode.VALIDATION_ERROR,
                           "需要 item_ids 或 all=true 之一")
        cancelled = await service.cancel_queue_items(
            conversation_id, body.item_ids, body.all)
        return {"cancelled_ids": cancelled}

    @app.exception_handler(SessionError)
    async def _session_error(_: Request, exc: SessionError) -> Response:
        return error_response(exc.code, exc.message, detail=exc.detail)
