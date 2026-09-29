"""进程内事件总线（M0-C3）：订阅/扇出/背压/优雅关闭。

设计依据 backend-service §2/§3：
- publish 由 EventStore 在**写通道线程**内、事务提交之后同步调用——发布顺序
  = 提交顺序，各订阅队列按 global_seq 严格递增；入队是纯内存操作，非阻塞；
- 每连接独立队列上限 1000：溢出置 overflowed 标记（不静默丢帧），消费者发完
  存量后发 `event:resync`（含最后连续游标）并终止；完全不读的死连接在宽限
  期后被强制取消（防泄漏）；
- 并发 SSE 连接 ≤ 32（ConnectionLimitError → API 层 503 SSE_LIMIT）；
- shutdown_all() 向全部订阅投递 sentinel → 消费者发 `event:shutdown` 后收尾
  （§7 第 3 步，预算 ≤1s——入队本身瞬时，帧的冲刷由响应关闭流程完成）。
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Iterable

from agentcrew_core.events import Event

_log = logging.getLogger("agentcrew.bus")

SHUTDOWN = object()  # sentinel：优雅关闭控制项

DEFAULT_MAX_QUEUE = 1000
DEFAULT_MAX_CONNECTIONS = 32
HARD_KILL_GRACE_SECONDS = 5.0  # 溢出后仍无法送达的连接，强制取消的宽限


class ConnectionLimitError(RuntimeError):
    """并发 SSE 连接超上限（API 层转 503 SSE_LIMIT）。"""


@dataclass(frozen=True)
class Topic:
    """订阅话题：会话通道 / 任务通道 / 全局通配（M0 仅内部用）。"""

    kind: str  # "conversation" | "task" | "all"

    def matches(self, event: Event) -> bool:
        if self.kind == "all":
            return True
        if self.kind == "conversation":
            return event.conversation_id == self.key
        return event.task_run_id == self.key

    key: str = ""


class Subscription:
    """一个 SSE 连接的订阅状态。入队线程（写通道）与消费协程（事件循环）共用。"""

    def __init__(self, topic: Topic, maxsize: int):
        self.topic = topic
        self.queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self.overflowed = False
        self.overflowed_at: float | None = None
        self.last_sent_global_seq = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wakeup: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    # ── 写通道线程侧 ──────────────────────────────────────────────
    def offer(self, item: object) -> None:
        """非阻塞入队；满 = 溢出标记（消费者随后发 resync 并终止）。"""
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            self.overflowed = True
            self.overflowed_at = time.monotonic()
        self._nudge()

    def _nudge(self) -> None:
        loop = self._loop
        wakeup = self._wakeup
        if loop is not None and wakeup is not None:
            try:
                loop.call_soon_threadsafe(wakeup.set)
            except RuntimeError:
                pass  # 消费侧事件循环已关闭：随 unsubscribe 清理

    def force_cancel(self) -> None:
        """死连接强断（宽限期后）：取消消费协程所在任务，连接随之终止。"""
        loop, task = self._loop, self._task
        if loop is not None and task is not None and not task.done():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    # ── 消费协程侧（事件循环线程）────────────────────────────────
    def bind_consumer(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wakeup = asyncio.Event()
        self._task = asyncio.current_task()

    def take_nowait(self) -> object | None:
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None

    async def wait_for_data(self) -> None:
        if self.queue.empty() and self._wakeup is not None:
            await self._wakeup.wait()
            self._wakeup.clear()


class EventBus:
    def __init__(
        self,
        *,
        max_queue: int = DEFAULT_MAX_QUEUE,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        hard_kill_grace: float = HARD_KILL_GRACE_SECONDS,
    ):
        self.max_queue = max_queue
        self.max_connections = max_connections
        self.hard_kill_grace = hard_kill_grace
        self._subs: list[Subscription] = []
        self._lock = threading.Lock()
        self._closed = False

    # ── 订阅管理（事件循环线程）──────────────────────────────────
    def subscribe(self, topic: Topic) -> Subscription:
        with self._lock:
            if self._closed:
                raise RuntimeError("事件总线已关闭")
            if len(self._subs) >= self.max_connections:
                raise ConnectionLimitError(
                    f"并发 SSE 连接超上限（{self.max_connections}）"
                )
            sub = Subscription(topic, maxsize=self.max_queue)
            self._subs.append(sub)
            return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            try:
                self._subs.remove(sub)
            except ValueError:
                pass

    # ── 发布（写通道线程内、COMMIT 之后调用）─────────────────────
    def publish(self, event: Event) -> None:
        now = time.monotonic()
        with self._lock:
            subs = tuple(self._subs)
        for sub in subs:
            if not sub.topic.matches(event):
                continue
            sub.offer(event)
            if sub.overflowed and sub.overflowed_at is not None:
                if now - sub.overflowed_at > self.hard_kill_grace:
                    _log.warning(
                        "bus.overflow 订阅溢出 %.1fs 仍无法送达，强制断开（topic=%s）",
                        now - sub.overflowed_at, sub.topic,
                    )
                    sub.force_cancel()
                    self.unsubscribe(sub)

    def publish_many(self, events: Iterable[Event]) -> None:
        for event in events:
            self.publish(event)

    def shutdown_all(self) -> None:
        """§7 第 3 步：向所有订阅发 shutdown 控制帧（入队瞬时，非阻塞）。"""
        with self._lock:
            subs = tuple(self._subs)
        for sub in subs:
            sub.offer(SHUTDOWN)

    def connection_count(self) -> int:
        with self._lock:
            return len(self._subs)
