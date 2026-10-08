"""计划域提交水位、当前权限过滤与实时事件交接。"""

import asyncio
import json

from fastapi import Query, Request
from sse_starlette.sse import EventSourceResponse

from ..bus import Topic
from ..db.projections import _row_to_event
from .governance_stream import active
from .sse import _SSE_HEADERS, PING_INTERVAL_SECONDS, _data_frame, _retry_hint, _shutdown_frame, _resync_frame, _subscribe_or_503


def visible(runtime, event, actor, workspace):
    if not event.type.value.startswith("cron."):
        return False
    current = runtime.identities.current(actor)
    scope = event.payload["scope"]
    if scope["org_id"] != current["org_id"] or scope["workspace_id"] not in current["workspace_ids"]:
        return False
    if workspace is not None and scope["workspace_id"] != workspace:
        return False
    if event.conversation_id and not runtime.identities.visible_conversation(actor, event.conversation_id):
        return False
    if current["role"] == "member" and (scope["owner_id"] != actor.effective_user_id or not runtime.identities.visible_scope(actor, scope["workspace_id"], scope["agent_id"])):
        return False
    if current["role"] != "owner" and event.payload.get("job_id"):
        row = runtime.db.read_conn.execute("SELECT target FROM cron_jobs WHERE id=?", (event.payload["job_id"],)).fetchone()
        if row:
            target = json.loads(row[0])
            if target["execution_mode"] == "existing" and not runtime.identities.visible_conversation(actor, target["conversation_id"]):
                return False
    return True


async def events(runtime, sub, actor, workspace, start):
    sub.bind_consumer()
    sent = cursor = start
    try:
        yield _retry_hint()
        head = runtime.db.read_conn.execute("SELECT coalesce(max(global_seq),0) FROM run_events").fetchone()[0]
        while cursor < head:
            if not active(runtime, actor, workspace):
                return
            rows = runtime.db.read_conn.execute("SELECT * FROM run_events WHERE global_seq>? AND global_seq<=? ORDER BY global_seq LIMIT 200", (cursor, head)).fetchall()
            for row in rows:
                event = _row_to_event(row)
                cursor = event.global_seq
                if active(runtime, actor, workspace) and visible(runtime, event, actor, workspace):
                    sent = sub.last_sent_global_seq = event.global_seq
                    yield _data_frame(event)
            if not rows:
                cursor = head
        while active(runtime, actor, workspace):
            if sub.shutdown_requested:
                yield _shutdown_frame(sent)
                return
            if sub.overflowed:
                yield _resync_frame(sent)
                return
            event = sub.take_nowait()
            if event is None:
                await asyncio.sleep(0.25)
                continue
            if event.global_seq > max(start, head, sent) and visible(runtime, event, actor, workspace):
                sent = sub.last_sent_global_seq = event.global_seq
                yield _data_frame(event)
    finally:
        runtime.bus.unsubscribe(sub)


def install_cron_stream(app, runtime):
    @app.get("/api/cron/stream")
    async def cron_stream(request: Request, workspace_id: str | None = None, from_seq: int = Query(0, alias="from", ge=0)):
        runtime.identities.require(request.state.identity, "use", workspace_id)
        sub = _subscribe_or_503(runtime.bus, Topic("all"))
        return EventSourceResponse(events(runtime, sub, request.state.identity, workspace_id, from_seq),
            ping=PING_INTERVAL_SECONDS, headers=_SSE_HEADERS, shutdown_grace_period=1)
