"""真实 SQLite 历史与记忆检索，复用身份、防护和串行统计。"""

import asyncio
import json
import re
from contextlib import AsyncExitStack

from agentcrew_core.memory import context_entries, failure, sha256, suspected_injection

from ..secrets import redact
from .store import MemoryIdentity, MemoryStore, canonical

_CURSOR = re.compile(r"([0-9a-f]{64}):(message|memory|summary):([A-Za-z0-9_-]{1,128})\Z")


class MemorySearch:
    def __init__(self, store: MemoryStore):
        self.store = store
        self.db = store.db

    async def search(self, identity: MemoryIdentity, query: str, *, archived: bool = False,
                     limit: int = 20, after: str | None = None, use_id: str | None = None) -> dict:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            return failure("VALIDATION_ERROR", "查询必须包含 1 至 200 个字符")
        if type(limit) is not int or not 1 <= limit <= 200 or type(archived) is not bool:
            return failure("VALIDATION_ERROR", "分页上限必须为 1 至 200，归档参数必须为布尔值")
        query = query.strip()
        binding = sha256(canonical({"workspace_id": identity.workspace_id, "agent_id": identity.agent_id,
            "actor_type": identity.actor_type, "actor_id": identity.actor_id, "query": query, "archived": archived}).encode())
        cursor = _CURSOR.fullmatch(after) if isinstance(after, str) else None
        if after is not None and (cursor is None or cursor[1] != binding):
            return failure("VALIDATION_ERROR", "检索游标无效或与查询身份不一致")
        targets = [("user", "owner"), ("workspace", identity.workspace_id), ("soul", identity.agent_id)]
        async with AsyncExitStack() as locks:
            for target in sorted(targets):
                lock = self.store._locks.setdefault(target, asyncio.Lock())
                if lock.locked():
                    return failure("STORE_RECOVERING", "检索范围中的记忆库正在写入")
                await locks.enter_async_context(lock)
            result = await asyncio.to_thread(self._query, identity, query, archived, limit, cursor, binding, targets)
        if "error" not in result:
            await self.store.record_hits([r["id"] for r in result["items"] if r["kind"] == "memory"], use_id=use_id)
        return result

    def _query(self, identity, query, archived, limit, cursor, binding, targets):
        conn = self.db.read_conn
        conn.create_function("memory_context_safe", 1, lambda text: int(not suspected_injection(text)), deterministic=True)
        conn.execute("BEGIN")
        try:
            denied = self.store._authorize(identity, "soul", identity.agent_id)
            if denied:
                return denied
            allowed = []
            for kind, store_id in targets:
                loaded = self.store._load(kind, store_id)
                if "error" in loaded:
                    return loaded
                allowed.extend(e["entry_id"] for e in context_entries(loaded["metadata"]["entries"], include_archived=archived)
                    if not any("[BLOCKED: 疑似注入]" in str(e.get(field, "")) for field in ("text", "basis", "review_basis")))
            phrase = '"' + query.replace('"', '""') + '"'
            message_match = "m.rowid IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)" if len(query) >= 3 else "instr(m.content, ?) > 0"
            memory_match = "e.rowid IN (SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?)" if len(query) >= 3 else "instr(e.text, ?) > 0"
            summary_match = "s.rowid IN (SELECT rowid FROM summaries_fts WHERE summaries_fts MATCH ?)" if len(query) >= 3 else "instr(s.text, ?) > 0"
            term = phrase if len(query) >= 3 else query
            sql = f"""SELECT m.id,'message' AS kind,m.content AS text,m.created_at,
                json_object('conversation_id',m.conversation_id,'task_run_id',m.task_run_id,
                    'message_id',m.id,'role',m.role,'workspace_id',c.workspace_id,
                    'agent_id',c.agent_id,'conversation_status',c.status) AS source,
                NULL AS store_type,NULL AS store_id,NULL AS state
                FROM messages m JOIN conversations c ON c.id=m.conversation_id
                WHERE c.workspace_id=? AND c.agent_id=? AND {message_match}
                    AND memory_context_safe(m.content)
                UNION ALL
                SELECT e.entry_id,'memory',e.text,e.created_at,
                json_object('entry_id',e.entry_id,'entry_hash',e.entry_hash,'revision',s.revision,
                    'basis',e.basis,'origin',json(e.source)) AS source,
                e.store_type,e.store_id,e.state
                FROM memory_entries e JOIN memory_stores s USING(store_type,store_id)
                WHERE e.entry_id IN (SELECT value FROM json_each(?)) AND e.needs_review=0
                    AND {memory_match}
                UNION ALL
                SELECT s.id,'summary',s.text,s.created_at,
                json_object('conversation_id',s.conversation_id,'task_run_id',s.task_run_id,
                    'summary_id',s.id,'job_id',s.job_id,'workspace_id',c.workspace_id,
                    'agent_id',c.agent_id,'conversation_status',c.status),NULL,NULL,NULL
                FROM session_summaries s JOIN conversations c ON c.id=s.conversation_id
                JOIN task_runs t ON t.id=s.task_run_id
                WHERE c.workspace_id=? AND c.agent_id=? AND t.status='completed'
                    AND {summary_match} AND memory_context_safe(s.text)"""
            params = [identity.workspace_id, identity.agent_id, term, canonical(allowed), term,
                      identity.workspace_id, identity.agent_id, term]
            where = ""
            if cursor:
                position = conn.execute(f"SELECT created_at,id,kind FROM ({sql}) WHERE kind=? AND id=?",
                    (*params, cursor[2], cursor[3])).fetchone()
                if position is None:
                    return failure("VALIDATION_ERROR", "检索游标对应的结果已不存在或不属于当前查询")
                where = "WHERE (created_at,id,kind) < (?,?,?)"
                params.extend(position)
            rows = conn.execute(f"SELECT * FROM ({sql}) {where} ORDER BY created_at DESC,id DESC,kind DESC LIMIT ?", (*params, limit + 1)).fetchall()
            items = [{**dict(r), "text": redact(r["text"]), "source": json.loads(redact(r["source"]))} for r in rows[:limit]]
            last = items[-1] if items else None
            return {"items": items, "next_after": f"{binding}:{last['kind']}:{last['id']}" if len(rows) > limit else None,
                    "method": "trigram" if len(query) >= 3 else "substring"}
        finally:
            conn.execute("ROLLBACK")

    async def rebuild(self):
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
                conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
                conn.execute("INSERT INTO messages_fts(messages_fts,rank) VALUES('integrity-check',1)")
                conn.execute("INSERT INTO memory_fts(memory_fts,rank) VALUES('integrity-check',1)")
                conn.execute("INSERT INTO summaries_fts(summaries_fts) VALUES('rebuild')")
                conn.execute("INSERT INTO summaries_fts(summaries_fts,rank) VALUES('integrity-check',1)")
        await self.store.events.channel.execute(tx)

    async def run_tool(self, invocation, context):
        row = self.db.read_conn.execute("SELECT c.workspace_id,c.agent_id,t.conversation_id FROM task_runs t JOIN conversations c ON c.id=t.conversation_id WHERE t.id=?", (context.task_run_id,)).fetchone()
        if row is None:
            return failure("OUT_OF_SCOPE", "检索缺少真实任务身份")
        if set(invocation.input) - {"query", "archived", "limit", "after"}:
            return failure("VALIDATION_ERROR", "检索不能指定任务范围以外的参数")
        identity = MemoryIdentity(row[0], row[1], "agent", row[1], row[2], context.task_run_id)
        return await self.search(identity, invocation.input.get("query"), archived=invocation.input.get("archived", False),
            limit=invocation.input.get("limit", 20), after=invocation.input.get("after"), use_id=invocation.call_id)
