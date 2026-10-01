"""事件类型枚举与事件信封（契约：docs/contracts/events.md；定义：harness-session §3）。

纪律：新增机制先加事件类型再写实现；append-only，不改历史事件。
payload 字段约定随实现卡片在 agentcrew_server/db/projections.py 顶部集中登记。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RunEventType(StrEnum):
    # 任务生命周期
    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_INTERRUPTED = "run.interrupted"
    RUN_RESUMED = "run.resumed"

    # 回合（Step）
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"

    # 模型交互
    LLM_REQUEST_STARTED = "llm.request_started"
    LLM_REQUEST_DONE = "llm.request_done"
    LLM_REQUEST_FAILED = "llm.request_failed"

    # 工具（三态账本）
    TOOL_PREPARED = "tool.prepared"
    TOOL_DISPATCHED = "tool.dispatched"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_SKIPPED_IDEMPOTENT = "tool.skipped_idempotent"
    TOOL_PENDING_VERIFICATION = "tool.pending_verification"

    # 权限与人机交互
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_RESOLVED = "permission.resolved"
    QUESTION_REQUESTED = "question.requested"
    QUESTION_ANSWERED = "question.answered"

    # 排队控制（v1.4，F006；item_enqueued 为 C7 落地补位——入队是事实，
    # 队列内容必须可由事件重放重建，纪律"新增机制先加事件类型"）
    QUEUE_PAUSED = "queue.paused"
    QUEUE_RESUMED = "queue.resumed"
    QUEUE_ITEM_ENQUEUED = "queue.item_enqueued"
    QUEUE_ITEM_CANCELLED = "queue.item_cancelled"

    # 核验提交
    TOOL_VERIFICATION_SUBMITTED = "tool.verification_submitted"

    # 产物
    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_READY = "artifact.ready"
    ARTIFACT_FAILED = "artifact.failed"
    ARTIFACT_MISSING_DETECTED = "artifact.missing_detected"

    # 任务材料与元数据
    MATERIALS_IMPORTED = "materials.imported"
    CONVERSATION_UPDATED = "conversation.updated"

    # 上下文管理（M1 起启用，占位）
    CONTEXT_COMPACTED = "context.compacted"
    TOOL_RESULT_EXTERNALIZED = "tool.result_externalized"

    MEMORY_UPDATED = "memory.updated"
    MEMORY_ARCHIVED = "memory.archived"


@dataclass(frozen=True)
class Event:
    """已落库事件的内存信封（SSE 帧与投影共用形状，contracts/events.md v1.1）。

    task_run_id 可空：会话域事件（queue.*，F006）没有任务锚点——此时 seq
    无意义恒 0，帧里也不携带（契约 `task_run_id?`/`seq?` 为可选键）。"""

    global_seq: int
    id: str
    task_run_id: str | None
    seq: int
    conversation_id: str | None
    type: RunEventType
    payload: dict[str, Any] = field(default_factory=dict)
    attempt_no: int | None = None
    agent_run_id: str | None = None
    ts: str = ""

    def as_frame(self) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "global_seq": self.global_seq,
            "type": self.type.value,
            "payload": self.payload,
            "ts": self.ts,
        }
        if self.task_run_id is not None:
            frame["task_run_id"] = self.task_run_id
            frame["seq"] = self.seq
        if self.attempt_no is not None:
            frame["attempt_no"] = self.attempt_no
        return frame
