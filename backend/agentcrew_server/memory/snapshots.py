"""会话三库快照与首次 soul 生成，复用写通道、Provider 和原子替换。"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import AsyncExitStack
from datetime import datetime, timezone

from agentcrew_core.events import RunEventType
from agentcrew_core.loop import LoopDeps, LoopGates, run_task, user_text_message
from agentcrew_core.memory import context_entries, failure, parse_entries, sha256, snapshot_prompt

from .store import MemoryIdentity, MemoryStore, canonical


class MemorySnapshotError(RuntimeError):
    def __init__(self, result: dict):
        self.result = result
        super().__init__(canonical(result))


def require(result: dict) -> dict:
    if "error" in result:
        raise MemorySnapshotError(result)
    return result


class MemorySnapshots:
    def __init__(self, store: MemoryStore):
        self.store = store
        self.db = store.db
        self.events = store.events
        self._conversations: dict[str, asyncio.Lock] = {}
        self._agents: dict[str, asyncio.Lock] = {}

    def _identity(self, conversation_id, task_run_id):
        row = self.db.read_conn.execute("SELECT workspace_id,agent_id,agent_spec_snapshot FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if row is None:
            raise MemorySnapshotError(failure("SNAPSHOT_IDENTITY_MISMATCH", "会话身份不存在"))
        identity = MemoryIdentity(row[0], row[1], "agent", row[1], conversation_id, task_run_id)
        require(self.store._authorize(identity, "soul", row[1]) or {})
        return identity, json.loads(row[2])

    def path(self, conversation_id):
        return self.store._intent_path(f"conversations/{conversation_id}/memory-snapshot.json")

    def _row(self, conversation_id):
        row = self.db.read_conn.execute("SELECT * FROM memory_snapshots WHERE conversation_id=?", (conversation_id,)).fetchone()
        return dict(row) if row else None

    def _validated(self, row, identity):
        if (row["owner_id"], row["workspace_id"], row["agent_id"]) != ("owner", identity.workspace_id, identity.agent_id):
            raise MemorySnapshotError(failure("SNAPSHOT_IDENTITY_MISMATCH", "冻结快照与会话范围不一致"))
        if sha256(row["body"].encode()) != row["sha256"]:
            raise MemorySnapshotError(failure("SNAPSHOT_CHECKSUM_MISMATCH", "冻结快照数据库校验失败"))
        content = self.store._file_text(self.path(identity.conversation_id))
        if content is None:
            raise MemorySnapshotError(failure("SNAPSHOT_MISSING", "冻结快照文件丢失"))
        if sha256(content.encode()) != row["sha256"]:
            raise MemorySnapshotError(failure("SNAPSHOT_CHECKSUM_MISMATCH", "冻结快照文件校验失败"))
        event = self.db.read_conn.execute("SELECT type,payload,conversation_id FROM run_events WHERE global_seq=?", (row["global_seq"],)).fetchone()
        if event is None or event[0] != "memory.snapshot_created" or event[2] != identity.conversation_id or json.loads(event[1])["snapshot_sha256"] != row["sha256"]:
            raise MemorySnapshotError(failure("SNAPSHOT_CHECKSUM_MISMATCH", "冻结快照与提交事件不一致"))
        body = json.loads(content)
        if (body["conversation_id"], body["snapshot_id"], body["scope"]) != (identity.conversation_id, row["snapshot_id"], {"owner_id": "owner", "workspace_id": identity.workspace_id, "agent_id": identity.agent_id}):
            raise MemorySnapshotError(failure("SNAPSHOT_IDENTITY_MISMATCH", "冻结快照正文身份不一致"))
        return {**body, "sha256": row["sha256"], "global_seq": row["global_seq"]}

    async def ensure(self, conversation_id: str, task_run_id: str, *, provider=None,
                     aux_model: str | None = None, default_role: str = "") -> dict:
        async with self._conversations.setdefault(conversation_id, asyncio.Lock()):
            identity, specification = await asyncio.to_thread(self._identity, conversation_id, task_run_id)
            row = await asyncio.to_thread(self._row, conversation_id)
            if row:
                if row["status"] == "prepared":
                    await self._finish(row)
                    row = await asyncio.to_thread(self._row, conversation_id)
                return await asyncio.to_thread(self._validated, row, identity)
            await self._ensure_soul(identity, specification, provider, aux_model, default_role)
            stores = []
            async with AsyncExitStack() as locks:
                targets = [("user", "owner"), ("workspace", identity.workspace_id), ("soul", identity.agent_id)]
                for target in sorted(targets):
                    await locks.enter_async_context(self.store._locks.setdefault(target, asyncio.Lock()))
                for kind, store_id in targets:
                    value = require(await asyncio.to_thread(self.store._load, kind, store_id))
                    stores.append({**value, "used_characters": len(value["text"]), "quota": self.store.quota(kind),
                                   "injected_entries": context_entries(value["metadata"]["entries"])})
                body = {"snapshot_id": uuid.uuid4().hex, "conversation_id": conversation_id,
                        "scope": {"owner_id": "owner", "workspace_id": identity.workspace_id, "agent_id": identity.agent_id},
                        "stores": stores, "skill_index": [], "skill_index_sha256": sha256(b"[]"),
                        "system_block": snapshot_prompt(stores), "created_at": datetime.now(timezone.utc).isoformat()}
                content = canonical(body)
                row = {"conversation_id": conversation_id, "snapshot_id": body["snapshot_id"], **body["scope"],
                       "body": content, "sha256": sha256(content.encode()), "status": "prepared", "created_at": body["created_at"]}
                task = asyncio.create_task(self._prepare_and_finish(row))
                try:
                    await asyncio.shield(task)
                finally:
                    await task
            return await asyncio.to_thread(self._validated, await asyncio.to_thread(self._row, conversation_id), identity)

    async def _prepare_and_finish(self, row):
        await self.events.channel.execute(lambda conn: self._prepare_snapshot_tx(conn, row))
        await self._finish(row)

    @staticmethod
    def _prepare_snapshot_tx(conn, row):
        with conn:
            conn.execute("INSERT INTO memory_snapshots(conversation_id,snapshot_id,owner_id,workspace_id,agent_id,body,sha256,status,created_at) VALUES(?,?,?,?,?,?,?,'prepared',?)",
                tuple(row[k] for k in ("conversation_id", "snapshot_id", "owner_id", "workspace_id", "agent_id", "body", "sha256", "created_at")))

    async def _finish(self, row):
        identity, _specification = await asyncio.to_thread(self._identity, row["conversation_id"], None)
        if sha256(row["body"].encode()) != row["sha256"]:
            raise MemorySnapshotError(failure("SNAPSHOT_CHECKSUM_MISMATCH", "未完成快照数据库校验失败"))
        if (row["owner_id"], row["workspace_id"], row["agent_id"]) != ("owner", identity.workspace_id, identity.agent_id):
            raise MemorySnapshotError(failure("SNAPSHOT_IDENTITY_MISMATCH", "未完成快照范围不一致"))
        path = self.path(row["conversation_id"])
        current = await asyncio.to_thread(self.store._file_text, path)
        if current is not None and sha256(current.encode()) != row["sha256"]:
            raise MemorySnapshotError(failure("SNAPSHOT_CHECKSUM_MISMATCH", "未完成快照文件出现外部修改"))
        await asyncio.to_thread(self.store._atomic_replace, path, row["body"].encode(), None, row["sha256"], row["snapshot_id"])
        body = json.loads(row["body"])
        def extra(conn, event):
            conn.execute("UPDATE memory_snapshots SET status='committed',global_seq=? WHERE conversation_id=? AND status='prepared'", (event.global_seq, row["conversation_id"]))
            conn.executemany("INSERT INTO memory_injections VALUES(?,?,?,?,?,?)",
                [(row["snapshot_id"], e["entry_id"], s["store_type"], s["store_id"], int(e["text"] == "[BLOCKED: 疑似注入]"), row["created_at"])
                 for s in body["stores"] for e in s["injected_entries"]])
        await self.events.append(task_run_id=None, conversation_id=row["conversation_id"], type=RunEventType.MEMORY_SNAPSHOT_CREATED,
            payload={"snapshot_id": row["snapshot_id"], "conversation_id": row["conversation_id"], "snapshot_sha256": row["sha256"], "scope": body["scope"],
                     "stores": [{k: s[k] for k in ("store_type", "store_id", "revision", "text_sha256", "metadata_sha256")} for s in body["stores"]]}, extra_writes=extra)

    async def recover(self):
        def interrupt(conn):
            with conn:
                conn.execute("UPDATE memory_soul_generations SET status='interrupted',error='进程终止期间生成未完成',finished_at=? WHERE status='running'", (datetime.now(timezone.utc).isoformat(),))
        await self.events.channel.execute(interrupt)
        rows = self.db.read_conn.execute("SELECT * FROM memory_snapshots WHERE status='prepared' ORDER BY created_at,snapshot_id").fetchall()
        for row in rows:
            await self._finish(dict(row))

    async def _ensure_soul(self, identity, specification, provider, aux_model, default_role):
        async with self._agents.setdefault(identity.agent_id, asyncio.Lock()):
            value = require(await self.store.read(identity, "soul", identity.agent_id))
            if value["revision"]:
                return
            if provider is None or not aux_model:
                raise MemorySnapshotError(failure("SOUL_GENERATION_UNAVAILABLE", "首次员工执行缺少有效 aux 模型"))
            quota = self.store.quota("soul")
            lower, upper = (quota * 45 + 99) // 100, quota * 55 // 100
            role = canonical(specification) if specification else default_role
            if not role:
                raise MemorySnapshotError(failure("SOUL_ROLE_MISSING", "员工岗位来源为空"))
            prompt = (f"请根据以下真实员工岗位生成初始 soul，自我认知、职责、工作风格和核验纪律使用第一人称中文表达。\n"
                      f"正文必须为 {lower} 至 {upper} 字符（Python len），目标 {(lower + upper) // 2} 字符；"
                      f"返回一个完整 Markdown 条目，采用八个充分展开的段落，每段约 {(lower + upper) // 16} 字符，禁止使用分隔符 §。"
                      "八段分别描述身份职责、任务理解、执行准备、工具执行、审批纪律、结果核验、沟通协作、经验维护。"
                      "每段写四至五个完整句子，具体说明自己的工作步骤和判断依据；输出前检查八段是否充分展开，"
                      "正文不足最低字符数时补充岗位工作方法，超过最高字符数时精简重复表达。"
                      "只输出正文，只描写岗位职责、工作方法、沟通和核验纪律。"
                      "具体偏好、历史经历、业务事实、人员资料和授权状态必须省略。\n真实岗位：\n" + role)
            previous = self.db.read_conn.execute("SELECT * FROM memory_soul_generations WHERE agent_id=? AND prompt=? AND status='completed' ORDER BY created_at DESC LIMIT 1", (identity.agent_id, prompt)).fetchone()
            if previous:
                generation_id, text = previous["id"], previous["result"]
                identity = MemoryIdentity(identity.workspace_id, identity.agent_id, "agent", identity.agent_id, previous["conversation_id"], previous["task_run_id"])
            else:
                generation_id = uuid.uuid4().hex
                await self.events.channel.execute(lambda conn: self._begin_generation(conn, generation_id, identity, aux_model, prompt))
                recorded = []
                async def sink(event_type, payload):
                    recorded.append({"type": event_type, "payload": payload})
                    await self.events.channel.execute(lambda conn: self._update_generation(conn, generation_id, events=canonical(recorded)))
                async def forbidden(call):
                    raise MemorySnapshotError(failure("SOUL_TOOL_FORBIDDEN", "初始 soul 生成不能执行工具"))
                messages = [user_text_message(prompt)]
                try:
                    result = await run_task(messages, LoopDeps(request=lambda: provider.stream("aux", messages, [], thinking={"type": "disabled"}, system="根据给出的真实岗位生成员工自我认知，遵守正文格式与字符配额。"),
                        execute=forbidden, emit=sink, on_progress=lambda: None, gates=LoopGates(max_steps=1), model=aux_model, model_slot="aux"))
                    text = result.final_text.strip()
                    if result.status != "completed" or not lower <= len(text) <= upper or len(parse_entries(text)) != 1:
                        error = failure("SOUL_GENERATION_FAILED", "真实 aux 未生成符合配额的初始 soul", reason=result.reason, characters=len(text), minimum=lower, maximum=upper)
                        await self.events.channel.execute(lambda conn: self._update_generation(conn, generation_id, status="failed", result=text, error=canonical(error)))
                        raise MemorySnapshotError(error)
                    await self.events.channel.execute(lambda conn: self._update_generation(conn, generation_id, status="completed", result=text))
                finally:
                    cancelling = asyncio.current_task().cancelling()
                    await self.events.channel.execute(lambda conn: self._end_unfinished_generation(conn, generation_id, cancelling))
            require(await self.store.change(identity, "soul", identity.agent_id, change_id=generation_id, expected_revision=0,
                basis=f"真实员工岗位 {role}；aux 生成记录 {generation_id}", operations=[{"action": "add", "text": text}]))

    @staticmethod
    def _begin_generation(conn, generation_id, identity, model, prompt):
        with conn:
            conn.execute("INSERT INTO memory_soul_generations(id,agent_id,conversation_id,task_run_id,model,prompt,status,created_at) VALUES(?,?,?,?,?,?,'running',?)",
                (generation_id, identity.agent_id, identity.conversation_id, identity.task_run_id, model, prompt, datetime.now(timezone.utc).isoformat()))

    @staticmethod
    def _update_generation(conn, generation_id, *, events=None, status=None, result=None, error=None):
        with conn:
            if events is not None:
                conn.execute("UPDATE memory_soul_generations SET events=? WHERE id=?", (events, generation_id))
            if status is not None:
                conn.execute("UPDATE memory_soul_generations SET status=?,result=?,error=?,finished_at=? WHERE id=?", (status, result, error, datetime.now(timezone.utc).isoformat(), generation_id))

    @staticmethod
    def _end_unfinished_generation(conn, generation_id, cancelling):
        with conn:
            conn.execute("UPDATE memory_soul_generations SET status=?,error='初始生成执行终止',finished_at=? WHERE id=? AND status='running'",
                ("cancelled" if cancelling else "failed", datetime.now(timezone.utc).isoformat(), generation_id))
