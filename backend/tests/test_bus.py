"""事件总线纯逻辑测试（线程安全入队/上限/溢出/匹配/shutdown）。"""

import asyncio
import queue

import pytest

from agentcrew_core.events import Event, RunEventType as T
from agentcrew_server.bus import SHUTDOWN, ConnectionLimitError, EventBus, Topic


def _event(gseq=1, conv="c1", task="r1"):
    return Event(
        global_seq=gseq, id=f"e{gseq}", task_run_id=task, seq=gseq,
        conversation_id=conv, type=T.QUESTION_REQUESTED, payload={}, ts="t",
    )


def test_topic_matching():
    conv_topic = Topic("conversation", "c1")
    task_topic = Topic("task", "r2")
    all_topic = Topic("all")
    ev = _event(conv="c1", task="r1")
    assert conv_topic.matches(ev)
    assert not task_topic.matches(ev)
    assert all_topic.matches(ev)


def test_connection_limit_enforced():
    bus = EventBus(max_connections=2)
    bus.subscribe(Topic("all"))
    bus.subscribe(Topic("all"))
    with pytest.raises(ConnectionLimitError):
        bus.subscribe(Topic("all"))
    bus.unsubscribe(bus._subs[0])
    bus.subscribe(Topic("all"))  # 释放后可再订


def test_offer_overflow_marks_not_silent_drop():
    bus = EventBus(max_queue=3)
    sub = bus.subscribe(Topic("all"))
    for i in range(3):
        sub.offer(_event(gseq=i + 1))
    assert not sub.overflowed
    sub.offer(_event(gseq=4))  # 满：标记溢出，不入队
    assert sub.overflowed and sub.overflowed_at is not None
    assert sub.queue.qsize() == 3  # 存量 3 条保留（消费后发 resync 再终止）


def test_publish_routes_by_topic():
    bus = EventBus()
    sub_c1 = bus.subscribe(Topic("conversation", "c1"))
    sub_r2 = bus.subscribe(Topic("task", "r2"))
    bus.publish(_event(conv="c1", task="r1"))
    assert sub_c1.queue.qsize() == 1
    assert sub_r2.queue.qsize() == 0
    bus.publish(_event(conv="c9", task="r2"))
    assert sub_c1.queue.qsize() == 1
    assert sub_r2.queue.qsize() == 1


def test_shutdown_all_reaches_every_subscriber():
    bus = EventBus()
    sub1 = bus.subscribe(Topic("conversation", "c1"))
    sub2 = bus.subscribe(Topic("task", "r1"))
    bus.shutdown_all()
    assert sub1.queue.get_nowait() is SHUTDOWN
    assert sub2.queue.get_nowait() is SHUTDOWN


def test_wakeup_cross_thread():
    """写通道线程 offer → 消费者事件循环被唤醒（call_soon_threadsafe）。"""
    bus = EventBus()

    async def consumer():
        sub = bus.subscribe(Topic("all"))
        sub.bind_consumer()
        # 在独立线程里 offer（模拟写通道线程）
        import threading
        t = threading.Timer(0.05, lambda: sub.offer(_event(gseq=1)))
        t.start()
        await asyncio.wait_for(sub.wait_for_data(), timeout=2)
        item = sub.take_nowait()
        t.join()
        return item

    item = asyncio.run(consumer())
    assert item is not None and item.global_seq == 1


def test_take_nowait_empty_returns_none():
    bus = EventBus()
    sub = bus.subscribe(Topic("all"))
    assert sub.take_nowait() is None
