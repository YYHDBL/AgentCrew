"""所有者记忆管理 API，复用实际存储、Skill、检索与后台作业。"""

import asyncio
import json
from typing import Annotated, Literal

from fastapi import Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from agentcrew_core.memory.pagination import memory_page

from ..memory.skills import MemorySkills
from ..memory.store import MemoryIdentity
from .errors import ApiError, ErrorCode
from .memory_stream import scoped_memory_response

StoreType = Literal["user", "workspace", "soul", "skill"]
StoreId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
EntryHash = Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")]
PageLimit = Annotated[int, Query(ge=1, le=200)]


class MemoryChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=0)
    basis: str = Field(min_length=1)
    conversation_id: str | None = None
    text: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, min_length=1, max_length=60)
    files: dict[str, str | None] | None = None
    review_decision: Literal["approve", "reject"] | None = None


class MemoryWriteBody(MemoryChangeBody):
    text: str = Field(min_length=1)


class MemoryReviewBody(MemoryChangeBody):
    review_decision: Literal["approve", "reject"]


class SkillCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128)
    basis: str = Field(min_length=1)
    text: str = Field(min_length=1)
    conversation_id: str | None = None
    workspace_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    agent_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: StrictInt = Field(ge=0, le=0)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=60)
    files: dict[str, str] | None = None


def memory_scope_exists(runtime, workspace, agent):
    return (workspace, agent) == ("default", "default") or runtime.db.read_conn.execute(
        "SELECT 1 FROM conversations WHERE workspace_id=? AND agent_id=? UNION "
        "SELECT 1 FROM memory_skills WHERE workspace_id=? AND agent_id=? LIMIT 1",
        (workspace, agent, workspace, agent)).fetchone() is not None


def checked(value):
    if "error" in value:
        raise ApiError(value["error"], value["message"], detail=value["details"])
    return value


def install_memory_routes(app, runtime):
    store, skills = runtime.memory, MemorySkills(runtime.memory)

    async def identity(request: Request, workspace_id: str = Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
                       agent_id: str = Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")):
        if runtime.governance is not None:
            runtime.identities.agent(request.state.identity, agent_id, workspace_id)
        elif not await asyncio.to_thread(memory_scope_exists, runtime, workspace_id, agent_id):
            raise ApiError(ErrorCode.OUT_OF_SCOPE, "工作区和员工没有已登记的执行范围")
        return MemoryIdentity(workspace_id, agent_id, actor_id=request.state.identity.effective_user_id if runtime.governance is not None else "owner")

    def source(scope, body):
        return MemoryIdentity(scope.workspace_id, scope.agent_id, actor_id=scope.actor_id, conversation_id=body.conversation_id)

    @app.get("/api/memory/stream")
    async def stream_events(scope=Depends(identity), from_seq: int = Query(0, alias="from", ge=0)):
        return scoped_memory_response(runtime, scope.workspace_id, scope.agent_id, from_seq,
            runtime.identities.memory_actor(scope) if runtime.governance is not None else None)

    async def skill_row(scope, skill_id):
        row = await asyncio.to_thread(lambda: runtime.db.read_conn.execute("SELECT * FROM memory_skills WHERE id=?", (skill_id,)).fetchone())
        if row is None:
            raise ApiError(ErrorCode.NOT_FOUND, "Skill 身份不存在")
        if (row["workspace_id"], row["agent_id"]) != (scope.workspace_id, scope.agent_id):
            raise ApiError(ErrorCode.OUT_OF_SCOPE, "Skill 不属于当前工作区和员工")
        return dict(row)

    async def read(scope, kind, store_id):
        row = await skill_row(scope, store_id) if kind == "skill" else None
        value = checked(await store.read(scope, kind, store_id))
        if row:
            value.update(name=row["name"], description=row["description"], files=sorted(value["metadata"].get("files", {})))
        value.update(workspace_id=None if kind == "user" else scope.workspace_id,
            agent_id=scope.agent_id if kind in {"soul", "skill"} else None)
        return value

    @app.get("/api/memory/stores/{store_type}/{store_id}")
    async def read_store(store_type: StoreType, store_id: StoreId, scope=Depends(identity),
                         state: Literal["all", "active", "stale", "archived", "pinned"] = "all",
                         limit: PageLimit = 50, after: str | None = Query(default=None, min_length=1)):
        value = await read(scope, store_type, store_id)
        entries = sorted((entry for entry in value["entries"] if state == "all" or entry["state"] == state),
            key=lambda entry: (entry["created_at"], entry["entry_id"]))
        page = memory_page(entries, {"scope": scope.source(), "workspace": scope.workspace_id, "agent": scope.agent_id,
            "kind": store_type, "id": store_id, "state": state, "revision": value["revision"], "order": "created_at,entry_id"}, limit, after, key="entry_id")
        return {"data": {**value, "entries": page["items"], "next_after": page["next_after"]}}

    @app.post("/api/memory/stores/{store_type}/{store_id}")
    async def create_entry(store_type: StoreType, store_id: StoreId, body: MemoryWriteBody, scope=Depends(identity)):
        if store_type == "skill":
            raise ApiError(ErrorCode.INVALID_TRANSITION, "Skill 创建使用技能创建端点")
        return {"data": checked(await store.change(source(scope, body), store_type, store_id,
            change_id=body.change_id, expected_revision=body.expected_revision, basis=body.basis,
            operations=[{"action": "add", "text": body.text}], request_context={"operation": "create", "body": body.model_dump()}))}

    @app.patch("/api/memory/stores/{store_type}/{store_id}/entries/{entry_hash}")
    async def edit_entry(store_type: StoreType, store_id: StoreId, entry_hash: EntryHash, body: MemoryWriteBody, scope=Depends(identity)):
        actor = source(scope, body)
        if store_type == "skill":
            row = await skill_row(scope, store_id)
            if body.name is not None and body.name != row["name"]:
                raise ApiError(ErrorCode.VALIDATION_ERROR, "Skill 名称必须对应当前目标身份")
            result = await skills.change(actor, row["name"], action="edit", change_id=body.change_id,
                expected_revision=body.expected_revision, basis=body.basis, text=body.text,
                description=body.description, files=body.files, entry_hash=entry_hash,
                management_request=body.model_dump())
        else:
            result = await store.change(actor, store_type, store_id, change_id=body.change_id,
                expected_revision=body.expected_revision, basis=body.basis,
                operations=[{"action": "edit", "entry_hash": entry_hash, "text": body.text}],
                request_context={"operation": "edit", "entry_hash": entry_hash, "body": body.model_dump()})
        return {"data": checked(result)}

    async def state_change(scope, kind, store_id, entry_hash, action, body):
        if kind == "skill":
            await skill_row(scope, store_id)
        return {"data": checked(await store.change(source(scope, body), kind, store_id,
            change_id=body.change_id, expected_revision=body.expected_revision, basis=body.basis,
            operations=[{"action": action, "entry_hash": entry_hash}], request_context={"action": action, "entry_hash": entry_hash, "body": body.model_dump()}))}

    @app.delete("/api/memory/stores/{store_type}/{store_id}/entries/{entry_hash}")
    async def archive_entry(store_type: StoreType, store_id: StoreId, entry_hash: EntryHash, body: MemoryChangeBody, scope=Depends(identity)):
        return await state_change(scope, store_type, store_id, entry_hash, "archive", body)

    @app.post("/api/memory/stores/{store_type}/{store_id}/entries/{entry_hash}/review")
    async def review_entry(store_type: StoreType, store_id: StoreId, entry_hash: EntryHash, body: MemoryReviewBody, scope=Depends(identity)):
        if store_type == "skill":
            await skill_row(scope, store_id)
        return {"data": checked(await store.change(source(scope, body), store_type, store_id,
            change_id=body.change_id, expected_revision=body.expected_revision, basis=body.basis,
            operations=[{"action": "review", "entry_hash": entry_hash, "decision": body.review_decision}],
            request_context={"operation": "review", "entry_hash": entry_hash, "body": body.model_dump()}))}

    @app.post("/api/memory/stores/{store_type}/{store_id}/entries/{entry_hash}/{action}")
    async def change_state(store_type: StoreType, store_id: StoreId, entry_hash: EntryHash,
                           action: Literal["pin", "unpin", "archive", "restore"], body: MemoryChangeBody, scope=Depends(identity)):
        return await state_change(scope, store_type, store_id, entry_hash, action, body)

    @app.get("/api/memory/ledger")
    async def list_ledger(store_type: StoreType, store_id: str, scope=Depends(identity), limit: PageLimit = 50,
                          after: str | None = Query(default=None, min_length=1)):
        await read(scope, store_type, store_id)
        rows = await asyncio.to_thread(lambda: [dict(row) for row in runtime.db.read_conn.execute(
            "SELECT * FROM memory_ledger WHERE store_type=? AND store_id=? ORDER BY id DESC", (store_type, store_id))])
        for row in rows:
            for field in ("before_metadata", "after_metadata", "before_files", "after_files", "source"):
                row[field] = json.loads(row[field])
        return {"data": memory_page(rows, {"kind": store_type, "id": store_id, "workspace": scope.workspace_id,
            "agent": scope.agent_id, "actor": scope.actor_id, "order": "id DESC"}, limit, after, key="id")}

    @app.post("/api/memory/ledger/{ledger_id}/rollback")
    async def restore_ledger(ledger_id: Annotated[int, Path(ge=1, le=9223372036854775807)], body: MemoryChangeBody, scope=Depends(identity)):
        row = await asyncio.to_thread(lambda: runtime.db.read_conn.execute("SELECT store_type,store_id FROM memory_ledger WHERE id=?", (ledger_id,)).fetchone())
        if row is None:
            raise ApiError(ErrorCode.NOT_FOUND, "账本记录不存在")
        await read(scope, row[0], row[1])
        return {"data": checked(await store.change(source(scope, body), row[0], row[1], change_id=body.change_id,
            expected_revision=body.expected_revision, basis=body.basis, restored_ledger_id=ledger_id,
            request_context={"operation": "ledger_restore", "ledger_id": ledger_id, "body": body.model_dump()}))}

    @app.get("/api/memory/search")
    async def search_memory(query: str = Query(min_length=1, max_length=200), scope=Depends(identity),
                            archived: bool = False, limit: PageLimit = 50, after: str | None = Query(default=None, min_length=1)):
        return {"data": checked(await runtime.memory_search.search(scope, query, archived=archived, limit=limit, after=after))}

    @app.get("/api/memory/skills")
    async def list_skills(scope=Depends(identity), archived: bool = False, limit: PageLimit = 50,
                          after: str | None = Query(default=None, min_length=1)):
        value = checked(await skills.index(scope, archived=archived))
        return {"data": memory_page(value["items"], {"workspace": scope.workspace_id, "agent": scope.agent_id,
            "archived": archived, "actor": scope.actor_id, "order": "name,id"}, limit, after, key="id")}

    @app.post("/api/memory/skills")
    async def create_skill(body: SkillCreateBody, request: Request):
        scope = await identity(request, body.workspace_id, body.agent_id)
        return {"data": checked(await skills.change(source(scope, body), body.name, action="create",
            change_id=body.change_id, expected_revision=body.expected_revision, basis=body.basis,
            description=body.description, text=body.text, files=body.files))}

    @app.get("/api/memory/skills/{skill_id}/file")
    async def read_skill_file(skill_id: StoreId, file: str = Query(min_length=1), scope=Depends(identity)):
        row = await skill_row(scope, skill_id)
        return {"data": checked(await skills.view(scope, row["name"], file=file))}

    @app.get("/api/memory/jobs")
    async def list_jobs(scope=Depends(identity), conversation_id: str | None = None, limit: PageLimit = 50,
                        after: str | None = Query(default=None, min_length=1)):
        if conversation_id:
            conv = await asyncio.to_thread(lambda: runtime.db.read_conn.execute("SELECT workspace_id,agent_id FROM conversations WHERE id=?", (conversation_id,)).fetchone())
            if conv is None:
                raise ApiError(ErrorCode.NOT_FOUND, "作业来源会话不存在")
            if tuple(conv) != (scope.workspace_id, scope.agent_id):
                raise ApiError(ErrorCode.OUT_OF_SCOPE, "作业来源会话不属于当前范围")
        def records():
            rows = runtime.db.read_conn.execute("SELECT id FROM memory_jobs WHERE workspace_id=? AND agent_id=? "
                "AND (? IS NULL OR conversation_id=?) ORDER BY created_at DESC,id DESC",
                (scope.workspace_id, scope.agent_id, conversation_id, conversation_id)).fetchall()
            if runtime.governance is not None:
                caller = runtime.identities.memory_actor(scope)
                owner = runtime.identities.current(caller)["role"] == "owner"
                rows = [row for row in rows if owner or runtime.db.read_conn.execute("SELECT 1 FROM job_governance WHERE job_id=? AND effective_user_id=?", (row[0], caller.effective_user_id)).fetchone()]
            return [runtime.memory_jobs.review.view(row[0]) for row in rows]
        return {"data": memory_page(await asyncio.to_thread(records), {"workspace": scope.workspace_id, "agent": scope.agent_id,
            "conversation": conversation_id, "actor": scope.actor_id, "order": "created_at,id DESC"}, limit, after, key="id")}
