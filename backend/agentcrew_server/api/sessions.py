"""会话与排队 API（M0-C7）：创建/材料/scope/limits/指令/排队控制/state。

契约：docs/contracts/openapi.yaml v0.3。202 无响应体（continue）按契约
有意设计；错误码信封经 SessionError → ApiError 映射。
"""

from __future__ import annotations

import asyncio

from fastapi import Request, Response
from pydantic import BaseModel, Field

from ..sessions import SessionError
from .errors import ApiError, ErrorCode, error_response


class ConversationCreateRequest(BaseModel):
    instruction: str = Field(min_length=1)
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
    async def create_conversation(body: ConversationCreateRequest):
        return await service.create_conversation(
            instruction=body.instruction, agent_id=body.agent_id,
            client_request_id=body.client_request_id,
            import_files=body.import_files, folders=body.folders)

    @app.get("/api/conversations")
    async def list_conversations():
        return await asyncio.to_thread(service.list_conversations)

    @app.get("/api/conversations/{conversation_id}/scope")
    async def get_scope(conversation_id: str):
        conv = await asyncio.to_thread(
            service.conversation_or_404, conversation_id)
        return service.scope_of(conv)

    @app.post("/api/conversations/{conversation_id}/instructions",
              status_code=202)
    async def post_instruction(conversation_id: str,
                               body: InstructionRequest):
        return await service.send_instruction(
            conversation_id, body.text, body.client_request_id)

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
