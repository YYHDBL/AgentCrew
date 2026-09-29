"""事件总线纯逻辑测试（线程安全入队/上限/溢出/匹配/shutdown/强断计时）。"""

import asyncio
import threading
import time

import pytest

from agentcrew_core.events import Event, RunEventType as T
from agentcrew_server.bus import ConnectionLimitError, EventBus, Topic


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


def test_overflow_timer_starts_once_and_not_reset():
    """外审回稿修复：溢出计时由首次溢出起表，后续入队失败不重置。"""
    bus = EventBus(max_queue=2)
    sub = bus.subscribe(Topic("all"))
    sub.offer(_event(gseq=1))
    sub.offer(_event(gseq=2))
    sub.offer(_event(gseq=3))  # 首次溢出
    first_at = sub.overflowed_at
    assert first_at is not None
    time.sleep(0.02)
    sub.offer(_event(gseq=4))  # 持续写入继续失败
    sub.offer(_event(gseq=5))
    assert sub.overflowed_at == first_at, "溢出计时不得被后续失败重置"


def test_sweep_force_cancels_stale_overflowed_sub():
    """宽限超时 → sweep 强断并移出订阅表；shutdown 请求中的订阅豁免。"""
    bus = EventBus(max_queue=1, hard_kill_grace=5.0)
    sub = bus.subscribe(Topic("all"))

    async def scenario():
        sub.bind_consumer()
        stalled = asyncio.ensure_future(asyncio.Event().wait())  # 模拟卡死的消费协程
        sub._task = stalled
        await asyncio.sleep(0.01)
        sub.offer(_event(gseq=1))
        sub.offer(_event(gseq=2))  # 溢出
        assert sub.overflowed
        # 未超宽限：不清理
        assert bus.sweep(now=sub.overflowed_at + 1.0) == 0
        assert bus.connection_count() == 1
        # 超宽限：清理 + 强断
        removed = bus.sweep(now=sub.overflowed_at + 6.0)
        assert removed == 1
        assert bus.connection_count() == 0
        await asyncio.sleep(0.05)
        assert sub._task.cancelled() or sub._task.done()

    asyncio.run(scenario())


def test_sweep_skips_shutdown_requested():
    bus = EventBus(max_queue=1, hard_kill_grace=5.0)
    sub = bus.subscribe(Topic("all"))
    sub.offer(_event(gseq=1))
    sub.offer(_event(gseq=2))  # 溢出
    bus.shutdown_all()  # 优雅关闭中的订阅：不强断（等它发完 shutdown 帧自行收尾）
    assert bus.sweep(now=sub.overflowed_at + 60.0) == 0


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


def test_shutdown_all_sets_flag_even_when_queue_full():
    """外审回稿修复：队满时 shutdown 也不丢——标志位不依赖队列。"""
    bus = EventBus(max_queue=2)
    sub = bus.subscribe(Topic("all"))
    sub.offer(_event(gseq=1))
    sub.offer(_event(gseq=2))
    bus.shutdown_all()
    assert sub.shutdown_requested is True
    assert sub.queue.qsize() == 2  # SHUTDOWN 未入队（走标志），存量保留


def test_wakeup_cross_thread():
    """写通道线程 offer → 消费者事件循环被唤醒（call_soon_threadsafe）。"""
    bus = EventBus()

    async def consumer():
        sub = bus.subscribe(Topic("all"))
        sub.bind_consumer()
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
