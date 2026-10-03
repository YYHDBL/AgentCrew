"""范围事件的历史与实时交接，复用既有 SSE、背压和关闭协议。"""

import asyncio
import json

from sse_starlette.sse import EventSourceResponse

from agentcrew_core.memory.event_scope import MEMORY_EVENT_TYPES, memory_event_visible

from ..bus import Topic
from ..db.projections import _row_to_event
from .sse import (_SSE_HEADERS, PING_INTERVAL_SECONDS, _data_frame, _resync_frame,
                  _retry_hint, _shutdown_frame, _subscribe_or_503)


def memory_batch(db, workspace, agent, cursor, head):
    rows = db.read_conn.execute("SELECT e.*,c.workspace_id AS context_workspace,c.agent_id AS context_agent "
        "FROM run_events e LEFT JOIN conversations c ON c.id=e.conversation_id WHERE e.global_seq>? AND e.global_seq<=? "
        "AND e.type IN (SELECT value FROM json_each(?)) ORDER BY e.global_seq LIMIT 200",
        (cursor, head, json.dumps(sorted(MEMORY_EVENT_TYPES)))).fetchall()
    selected = []
    for row in rows:
        event = _row_to_event(row)
        context_scope = {"owner_id": "owner", "workspace_id": row["context_workspace"], "agent_id": row["context_agent"]}
        if memory_event_visible(event.type, event.payload, workspace, agent, context_scope=context_scope):
            selected.append(event)
    return selected, rows[-1]["global_seq"] if rows else head


async def memory_stream(runtime, sub, workspace, agent, start):
    sub.bind_consumer()
    sent = cursor = start
    try:
        yield _retry_hint()
        head = await asyncio.to_thread(lambda: runtime.db.read_conn.execute("SELECT COALESCE(MAX(global_seq),0) FROM run_events").fetchone()[0])
        while cursor < head:
            batch, cursor = await asyncio.to_thread(memory_batch, runtime.db, workspace, agent, cursor, head)
            for event in batch:
                sent = event.global_seq
                sub.last_sent_global_seq = sent
                yield _data_frame(event)
        while True:
            if sub.shutdown_requested:
                yield _shutdown_frame(sent)
                return
            if sub.overflowed:
                yield _resync_frame(sent)
                return
            event = sub.take_nowait()
            if event is None:
                await sub.wait_for_data()
                continue
            if event.global_seq <= max(start, head, sent):
                continue
            sent = event.global_seq
            sub.last_sent_global_seq = sent
            yield _data_frame(event)
    finally:
        runtime.bus.unsubscribe(sub)


def scoped_memory_response(runtime, workspace, agent, start):
    sub = _subscribe_or_503(runtime.bus, Topic("memory", workspace_id=workspace, agent_id=agent))
    return EventSourceResponse(memory_stream(runtime, sub, workspace, agent, start),
        ping=PING_INTERVAL_SECONDS, headers=_SSE_HEADERS, shutdown_grace_period=1)
