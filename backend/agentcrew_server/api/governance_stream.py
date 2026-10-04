"""治理事件按当前身份过滤历史、实时推送及撤权关闭。"""

import asyncio

from fastapi import Query, Request
from sse_starlette.sse import EventSourceResponse

from agentcrew_core.governance import governance_event_visible
from ..bus import Topic
from ..db.projections import _row_to_event
from .sse import _SSE_HEADERS, PING_INTERVAL_SECONDS, _data_frame, _retry_hint, _shutdown_frame, _resync_frame, _subscribe_or_503


def active(runtime, identity, workspace):
    if not runtime.identities.credential_active(identity):
        return False
    current = runtime.db.read_conn.execute("SELECT m.status,u.status,o.status FROM memberships m JOIN users u ON u.id=m.user_id JOIN organizations o ON o.id=m.org_id WHERE m.user_id=?", (identity.effective_user_id,)).fetchone()
    if current is None or tuple(current) != ("active", "active", "active"):
        return False
    return workspace is None or workspace in runtime.identities.current(identity)["workspace_ids"]


def visible(runtime, event, identity, workspace):
    if not event.type.value.startswith("governance."):
        return False
    if event.conversation_id and not runtime.identities.visible_conversation(identity, event.conversation_id):
        return False
    current = runtime.identities.current(identity)
    employees = {row[0] for row in runtime.db.read_conn.execute("SELECT g.resource_id FROM grants g JOIN agents a ON a.id=g.resource_id WHERE g.resource_type='agent' AND g.grantee_type='user' AND g.grantee_id=? AND g.revoked_at IS NULL AND a.status='active'", (identity.effective_user_id,))}
    return governance_event_visible(event.type.value, event.payload, identity, current, employees, workspace)


async def stream(runtime, sub, identity, workspace, start):
    sub.bind_consumer()
    sent = cursor = start
    try:
        yield _retry_hint()
        head = runtime.db.read_conn.execute("SELECT coalesce(max(global_seq),0) FROM run_events").fetchone()[0]
        while cursor < head:
            if not active(runtime, identity, workspace):
                return
            rows = runtime.db.read_conn.execute("SELECT * FROM run_events WHERE global_seq>? AND global_seq<=? ORDER BY global_seq LIMIT 200", (cursor, head)).fetchall()
            for row in rows:
                event = _row_to_event(row)
                cursor = event.global_seq
                if active(runtime, identity, workspace) and visible(runtime, event, identity, workspace):
                    sent = event.global_seq
                    sub.last_sent_global_seq = sent
                    yield _data_frame(event)
            if not rows:
                cursor = head
        while True:
            if not active(runtime, identity, workspace):
                return
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
            if event.global_seq <= max(start, head, sent):
                continue
            if visible(runtime, event, identity, workspace):
                sent = event.global_seq
                sub.last_sent_global_seq = sent
                yield _data_frame(event)
    finally:
        runtime.bus.unsubscribe(sub)


def install_governance_stream(app, runtime):
    @app.get("/api/governance/stream")
    async def governance_stream(request: Request, workspace_id: str | None = None, from_seq: int = Query(0, alias="from", ge=0)):
        runtime.identities.require(request.state.identity, "use", workspace_id)
        sub = _subscribe_or_503(runtime.bus, Topic("all"))
        return EventSourceResponse(stream(runtime, sub, request.state.identity, workspace_id, from_seq),
            ping=PING_INTERVAL_SECONDS, headers=_SSE_HEADERS, shutdown_grace_period=1)
