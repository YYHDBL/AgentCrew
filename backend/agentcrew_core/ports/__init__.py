"""core 到外部系统的 Port 接口定义（harness-session §9）。

core 只依赖这些协议；agentcrew_server 提供真实实现（SQLite/时钟等）。
随卡片逐个引入，不提前发明。
"""

from __future__ import annotations

from typing import Any, Protocol

from agentcrew_core.events import Event, RunEventType


class EventStore(Protocol):
    """事件存储 Port：追加即事实（ADR-001）。

    实现约束（backend-service §2）：事件与投影同事务提交；提交成功后
    才回调 publisher（进程内总线扇出，C3 注入）。
    """

    async def append(
        self,
        *,
        task_run_id: str,
        conversation_id: str,
        type: RunEventType,
        payload: dict[str, Any],
        attempt_no: int | None = None,
    ) -> Event: ...
