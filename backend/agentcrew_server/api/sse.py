"""SSE 端点（M0-C3）：会话级流与任务级流 + 任务事件 JSON 分页。

协议（backend-service §3 / contracts/events.md v1.1）：
- 游标 from 排他（返回 seq > 游标）；from=0 重放全部；
- 无遗漏交接：**先注册实时订阅（带缓冲）→ 再从库读历史至当前已提交 head →
  转入实时**；缓冲中 global_seq ≤ head 的帧丢弃——两段之间无窗口；
- 控制帧：event:resync（data 含最后连续游标，慢消费者溢出）、event:shutdown
  （优雅关闭）；默认帧 = 事件信封 JSON；
- retry: 建议行在流首发送；心跳注释行由 sse-starlette ping 提供；
- 订阅不存在的任务/会话 → 404；连接超上限 → 503 SSE_LIMIT；
- 流不套信封（EnvelopeMiddleware 放行 text/event-stream）。
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import Query
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from ..bus import SHUTDOWN, ConnectionLimitError, EventBus, Subscription, Topic
from ..db.queries import (
    conversation_exists,
    iter_conversation_events,
    iter_task_events,
    task_run_exists,
)
from .errors import ApiError, ErrorCode

_log = logging.getLogger("agentcrew.api.sse")

RETRY_HINT_MS = 3000
PING_INTERVAL_SECONDS = 15


def _retry_hint() -> ServerSentEvent:
    return ServerSentEvent(data=None, retry=RETRY_HINT_MS)


def _data_frame(event) -> ServerSentEvent:
    return ServerSentEvent(
        data=json.dumps(event.as_frame(), ensure_ascii=False, separators=(",", ":"))
    )


def _resync_frame(last_global_seq: int, extra: dict | None = None) -> ServerSentEvent:
    data = {"last_continuous_global_seq": last_global_seq, "reason": "slow_consumer"}
    if extra:
        data.update(extra)
    return ServerSentEvent(
        data=json.dumps(data, ensure_ascii=False), event="resync"
    )


def _shutdown_frame(head: int, extra: dict | None = None) -> ServerSentEvent:
    data = {"last_continuous_global_seq": head, "reason": "server_shutdown"}
    if extra:
        data.update(extra)
    return ServerSentEvent(
        data=json.dumps(data, ensure_ascii=False), event="shutdown"
    )


def _subscribe_or_503(bus: EventBus, topic: Topic) -> Subscription:
    try:
        return bus.subscribe(topic)
    except ConnectionLimitError as e:
        raise ApiError(ErrorCode.SSE_LIMIT, str(e)) from None


async def _conversation_stream(
    runtime, sub: Subscription, conversation_id: str, from_seq: int
):
    sub.bind_consumer()
    read_conn = runtime.db.read_conn
    try:
        yield _retry_hint()
        head = from_seq
        # 历史段（订阅已注册，缓冲覆盖读取期间的新提交）
        history = await asyncio.to_thread(
            list, iter_conversation_events(
                read_conn, conversation_id, after_global_seq=from_seq
            )
        )
        for event in history:
            head = event.global_seq
            sub.last_sent_global_seq = head
            yield _data_frame(event)
        # 实时段：缓冲中 ≤ head 的帧丢弃（无遗漏交接的去重侧）
        while True:
            if sub.overflowed:
                _log.info(
                    "sse.resync 会话流溢出 conversation=%s 游标=%s",
                    conversation_id, head,
                )
                yield _resync_frame(head)
                return
            item = sub.take_nowait()
            if item is None:
                await sub.wait_for_data()
                continue
            if item is SHUTDOWN:
                yield _shutdown_frame(head)
                return
            if item.global_seq <= head:
                continue
            head = item.global_seq
            sub.last_sent_global_seq = head
            yield _data_frame(item)
    finally:
        runtime.bus.unsubscribe(sub)


async def _task_stream(runtime, sub: Subscription, task_run_id: str, from_seq: int):
    sub.bind_consumer()
    read_conn = runtime.db.read_conn
    try:
        yield _retry_hint()
        head_global = from_seq
        head_seq = from_seq
        history = await asyncio.to_thread(
            list, iter_task_events(read_conn, task_run_id, after_seq=from_seq)
        )
        for event in history:
            head_global = event.global_seq
            head_seq = event.seq
            sub.last_sent_global_seq = head_global
            yield _data_frame(event)
        while True:
            if sub.overflowed:
                _log.info(
                    "sse.resync 任务流溢出 task=%s 游标 seq=%s", task_run_id, head_seq
                )
                yield _resync_frame(
                    head_global,
                    extra={"task_run_id": task_run_id, "seq": head_seq},
                )
                return
            item = sub.take_nowait()
            if item is None:
                await sub.wait_for_data()
                continue
            if item is SHUTDOWN:
                yield _shutdown_frame(
                    head_global, extra={"task_run_id": task_run_id, "seq": head_seq}
                )
                return
            if item.global_seq <= head_global:
                continue
            head_global = item.global_seq
            head_seq = item.seq
            sub.last_sent_global_seq = head_global
            yield _data_frame(item)
    finally:
        runtime.bus.unsubscribe(sub)


# 说明：uvicorn 忽略应用层的 Connection 头（hop-by-hop 由服务端自管）——
# SSE 流结束后 socket 进 keep-alive 池、按 timeout_keep_alive（默认 5s）关闭；
# 对客户端而言"服务端终止订阅"的可见语义 = 响应流在 resync/shutdown 帧后结束
# （chunked 终止块），EventSource/curl 均在流结束时刻退出，无需等 socket FIN。
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def install_sse_routes(app, runtime) -> None:
    bus: EventBus = runtime.bus

    @app.get("/api/conversations/{conversation_id}/stream")
    async def conversation_stream(
        conversation_id: str,
        from_seq: int = Query(0, alias="from"),
    ):
        if not await asyncio.to_thread(
            conversation_exists, runtime.db.read_conn, conversation_id
        ):
            raise ApiError(ErrorCode.NOT_FOUND, f"会话不存在：{conversation_id}")
        sub = _subscribe_or_503(bus, Topic("conversation", conversation_id))
        return EventSourceResponse(
            _conversation_stream(runtime, sub, conversation_id, from_seq),
            ping=PING_INTERVAL_SECONDS,
            headers=_SSE_HEADERS,
        )

    @app.get("/api/task-runs/{task_run_id}/events")
    async def task_run_events(
        task_run_id: str,
        from_seq: int = Query(0, alias="from"),
        after_seq: int | None = Query(None),
        limit: int | None = Query(None, le=500),
    ):
        if not await asyncio.to_thread(
            task_run_exists, runtime.db.read_conn, task_run_id
        ):
            raise ApiError(ErrorCode.NOT_FOUND, f"任务不存在：{task_run_id}")

        if after_seq is not None or limit is not None:
            # JSON 分页模式（契约：{data:{items, next_after_seq}}；after_seq 排他）
            effective_after = after_seq if after_seq is not None else 0
            effective_limit = limit if limit is not None else 500
            events = await asyncio.to_thread(
                _task_events_page,
                runtime.db.read_conn, task_run_id, effective_after, effective_limit,
            )
            return {
                "items": [event.as_frame() for event in events],
                "next_after_seq": events[-1].seq if events else effective_after,
            }

        sub = _subscribe_or_503(bus, Topic("task", task_run_id))
        return EventSourceResponse(
            _task_stream(runtime, sub, task_run_id, from_seq),
            ping=PING_INTERVAL_SECONDS,
            headers=_SSE_HEADERS,
        )


def _task_events_page(conn, task_run_id: str, after_seq: int, limit: int):
    events = []
    for event in iter_task_events(conn, task_run_id, after_seq=after_seq):
        events.append(event)
        if len(events) >= limit:
            break
    return events
