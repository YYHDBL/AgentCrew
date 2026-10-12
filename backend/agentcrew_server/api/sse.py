"""SSE 端点（M0-C3）：会话级流与任务级流 + 任务事件 JSON 分页。

协议（backend-service §3 / contracts/events.md v1.1）：
- 游标 from 排他（返回 seq > 游标）；from=0 重放全部；
- 无遗漏交接：**先注册实时订阅（带缓冲）→ 取订阅时刻的已提交 head →
  只读历史到 head 为止（分批流式，不追新事件）→ 转实时**；缓冲中
  global_seq ≤ head 的帧丢弃——两段之间无窗口（head 上界与分批发送为
  外审回稿修复）；
- 控制帧优先级：shutdown（优雅关闭，标志位不依赖队列，队满也能送达）>
  resync（慢消费者溢出，data 含最后连续游标）；默认帧 = 事件信封 JSON；
- retry: 建议行在流首发送；心跳注释行由 sse-starlette ping 提供；
- 订阅不存在的任务/会话 → 404；连接超上限 → 503 SSE_LIMIT；
- 流不套信封（EnvelopeMiddleware 放行 text/event-stream）。
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import Query, Request
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from ..bus import ConnectionLimitError, EventBus, Subscription, Topic
from ..db.queries import (
    conversation_batch,
    conversation_exists,
    iter_task_events,
    max_global_seq_for_conversation,
    max_seq_for_task,
    task_batch,
    task_run_exists,
)
from .errors import ApiError, ErrorCode
from ..runs import Runs, event_frame

_log = logging.getLogger("agentcrew.api.sse")

RETRY_HINT_MS = 3000
PING_INTERVAL_SECONDS = 15
# 说明：uvicorn 忽略应用层的 Connection 头（hop-by-hop 由服务端自管）——
# SSE 流结束后 socket 进 keep-alive 池、按 timeout_keep_alive（默认 5s）关闭；
# 对客户端而言"服务端终止订阅"的可见语义 = 响应流在 resync/shutdown 帧后
# 以 chunked 终止块结束（EventSource/curl 在该时刻退出，无需等 socket FIN）。
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _retry_hint() -> ServerSentEvent:
    return ServerSentEvent(data=None, retry=RETRY_HINT_MS)


def _data_frame(event) -> ServerSentEvent:
    return ServerSentEvent(
        data=json.dumps(event_frame(event), ensure_ascii=False, separators=(",", ":"))
    )


def _resync_frame(last_global_seq: int, extra: dict | None = None) -> ServerSentEvent:
    data = {"last_continuous_global_seq": last_global_seq, "reason": "slow_consumer"}
    if extra:
        data.update(extra)
    return ServerSentEvent(data=json.dumps(data, ensure_ascii=False), event="resync")


def _shutdown_frame(head: int, extra: dict | None = None) -> ServerSentEvent:
    data = {"last_continuous_global_seq": head, "reason": "server_shutdown"}
    if extra:
        data.update(extra)
    return ServerSentEvent(data=json.dumps(data, ensure_ascii=False), event="shutdown")


def _subscribe_or_503(bus: EventBus, topic: Topic) -> Subscription:
    try:
        return bus.subscribe(topic)
    except ConnectionLimitError as e:
        raise ApiError(ErrorCode.SSE_LIMIT, str(e)) from None


async def _conversation_stream(
    runtime, sub: Subscription, conversation_id: str, from_seq: int, identity=None
):
    sub.bind_consumer()
    db = runtime.db
    try:
        yield _retry_hint()
        # head 固定：订阅时刻该会话的已提交上界（新事件走订阅缓冲）。
        # read_conn 在 to_thread worker 内解析——每线程一条只读连接
        head = await asyncio.to_thread(
            _head_global, db, conversation_id
        )
        if from_seq >= head:
            head = from_seq  # 无可补播（或游标超前）：直接进实时
        sent = from_seq
        cursor = from_seq
        while cursor < head:
            batch = await asyncio.to_thread(
                _conversation_batch, db, conversation_id,
                after_global_seq=cursor, up_to_global_seq=head,
            )
            if not batch:
                break
            for event in batch:
                if identity is not None and not runtime.identities.visible_conversation(identity, conversation_id):
                    return
                cursor = event.global_seq
                sent = event.global_seq
                sub.last_sent_global_seq = sent
                yield _data_frame(event)
        while True:
            if identity is not None and not runtime.identities.visible_conversation(identity, conversation_id):
                return
            if sub.shutdown_requested:  # 优雅关闭优先于溢出（队满也送达）
                yield _shutdown_frame(sent)
                return
            if sub.overflowed:
                _log.info(
                    "sse.resync 会话流溢出 conversation=%s 游标=%s",
                    conversation_id, sent,
                )
                yield _resync_frame(sent)
                return
            item = sub.take_nowait()
            if item is None:
                if identity is None:
                    await sub.wait_for_data()
                else:
                    await asyncio.sleep(0.25)
                continue
            if item.global_seq <= sent:
                continue  # 交接去重：缓冲中 ≤ 补播 head 的帧丢弃
            sent = item.global_seq
            sub.last_sent_global_seq = sent
            yield _data_frame(item)
    finally:
        runtime.bus.unsubscribe(sub)


async def _task_stream(runtime, sub: Subscription, task_run_id: str, from_seq: int, identity=None, conversation_id=None):
    sub.bind_consumer()
    db = runtime.db
    try:
        yield _retry_hint()
        head_seq = await asyncio.to_thread(_head_seq, db, task_run_id)
        if from_seq >= head_seq:
            head_seq = from_seq
        sent_seq = from_seq
        cursor = from_seq
        while cursor < head_seq:
            batch = await asyncio.to_thread(
                _task_batch, db, task_run_id,
                after_seq=cursor, up_to_seq=head_seq,
            )
            if not batch:
                break
            for event in batch:
                if identity is not None and not runtime.identities.visible_conversation(identity, conversation_id):
                    return
                cursor = event.seq
                sent_seq = event.seq
                sub.last_sent_global_seq = event.global_seq
                yield _data_frame(event)
        sent_global = sub.last_sent_global_seq
        while True:
            if identity is not None and not runtime.identities.visible_conversation(identity, conversation_id):
                return
            if sub.shutdown_requested:
                yield _shutdown_frame(
                    sent_global, extra={"task_run_id": task_run_id, "seq": sent_seq}
                )
                return
            if sub.overflowed:
                _log.info(
                    "sse.resync 任务流溢出 task=%s 游标 seq=%s", task_run_id, sent_seq
                )
                yield _resync_frame(
                    sent_global,
                    extra={"task_run_id": task_run_id, "seq": sent_seq},
                )
                return
            item = sub.take_nowait()
            if item is None:
                if identity is None:
                    await sub.wait_for_data()
                else:
                    await asyncio.sleep(0.25)
                continue
            # 双重去重（外审回稿 S13）：无补播段（from ≥ head）时 sent_global
            # 仍是 0，仅按 global_seq 过滤会把 ≤ 游标的旧帧再发一遍——任务
            # 话题上帧与 seq 一一对应，必须同时按任务内 seq 排他
            if item.global_seq <= sent_global or item.seq <= sent_seq:
                continue
            sent_global = item.global_seq
            sent_seq = item.seq
            sub.last_sent_global_seq = sent_global
            yield _data_frame(item)
    finally:
        runtime.bus.unsubscribe(sub)


def install_sse_routes(app, runtime) -> None:
    bus: EventBus = runtime.bus

    @app.get("/api/conversations/{conversation_id}/stream")
    async def conversation_stream(
        conversation_id: str, request: Request,
        from_seq: int = Query(0, alias="from"),
    ):
        if not await asyncio.to_thread(_conversation_exists, runtime.db, conversation_id):
            raise ApiError(ErrorCode.NOT_FOUND, f"会话不存在：{conversation_id}")
        sub = _subscribe_or_503(bus, Topic("conversation", conversation_id))
        return EventSourceResponse(
            _conversation_stream(runtime, sub, conversation_id, from_seq,
                request.state.identity if runtime.governance is not None else None),
            ping=PING_INTERVAL_SECONDS,
            headers=_SSE_HEADERS,
            shutdown_grace_period=1,
        )

    @app.get("/api/task-runs/{task_run_id}/events")
    async def task_run_events(
        task_run_id: str, request: Request,
        from_seq: int = Query(0, alias="from"),
        after_seq: int | None = Query(None, ge=0),
        limit: int | None = Query(None, ge=1, le=500),
        through_global_seq: int | None = Query(None, ge=0),
        attempt_no: int | None = Query(None, ge=1),
        after_global_seq: int | None = Query(None, ge=0),
    ):
        if not await asyncio.to_thread(_task_run_exists, runtime.db, task_run_id):
            raise ApiError(ErrorCode.NOT_FOUND, f"任务不存在：{task_run_id}")

        if after_seq is not None or limit is not None:
            # JSON 分页模式（契约：{data:{items, next_after_seq}}；after_seq 排他）
            effective_after = after_seq if after_seq is not None else 0
            effective_limit = limit if limit is not None else 500
            if runtime.identities is not None:
                return await asyncio.to_thread(Runs(runtime).events, request.state.identity,
                    task_run_id, effective_after, effective_limit, through_global_seq, attempt_no, after_global_seq)
            events = await asyncio.to_thread(
                _task_events_page, runtime.db, task_run_id,
                effective_after, effective_limit,
            )
            return {
                "items": [event_frame(event) for event in events],
                "next_after_seq": events[-1].seq if events else effective_after,
            }

        sub = _subscribe_or_503(bus, Topic("task", task_run_id))
        return EventSourceResponse(
            _task_stream(runtime, sub, task_run_id, from_seq,
                request.state.identity if runtime.governance is not None else None,
                runtime.db.read_conn.execute("SELECT conversation_id FROM task_runs WHERE id=?", (task_run_id,)).fetchone()[0]),
            ping=PING_INTERVAL_SECONDS,
            headers=_SSE_HEADERS,
            shutdown_grace_period=1,
        )


def _task_events_page(db, task_run_id: str, after_seq: int, limit: int):
    events = []
    for event in iter_task_events(db.read_conn, task_run_id, after_seq=after_seq):
        events.append(event)
        if len(events) >= limit:
            break
    return events


def _head_global(db, conversation_id: str) -> int:
    return max_global_seq_for_conversation(db.read_conn, conversation_id)


def _head_seq(db, task_run_id: str) -> int:
    return max_seq_for_task(db.read_conn, task_run_id)


def _conversation_batch(db, conversation_id: str, *, after_global_seq: int,
                        up_to_global_seq: int):
    return conversation_batch(
        db.read_conn, conversation_id,
        after_global_seq=after_global_seq, up_to_global_seq=up_to_global_seq,
    )


def _task_batch(db, task_run_id: str, *, after_seq: int, up_to_seq: int):
    return task_batch(
        db.read_conn, task_run_id, after_seq=after_seq, up_to_seq=up_to_seq,
    )


def _conversation_exists(db, conversation_id: str) -> bool:
    return conversation_exists(db.read_conn, conversation_id)


def _task_run_exists(db, task_run_id: str) -> bool:
    return task_run_exists(db.read_conn, task_run_id)
