"""会话 FSM 纯函数 reducer（harness-session §4，D4 四态 + 等待计数器 + v1.4 排队）。

唯一入口 `reduce(state, event) -> state`；能力真值表 can_send / can_queue /
can_cancel / can_continue_queue 前端直接调用。全部无副作用、可参数化单测
（迁移表全行覆盖是 C7 核心验收）。

迁移表（§4 v1.4，行集与实现一一对应）：
  run.queued            idle → starting（记 current_task_run_id；载荷带
                        queue_item_id 时把对应排队项标 dequeued）
  run.started           starting → running
  run.resumed           idle → running（恢复 = 新 attempt 直接进循环，§7）
  permission.requested  running: waiting_approvals += 1
  permission.resolved   running 且计数>0 且事件属于当前任务（task_run_id 一致）:
                        -= 1；否则 no-op——对账后（run.interrupted 已收敛）
                        到达的**历史任务**决定不改变状态（C6 已验收"重启后
                        决定仍可提交并落库"：事件事实照落库，但不得清零后继
                        任务的未决计数——外审回稿：跨任务计数污染）
  question.requested / question.answered  同上，计数器增减（同任务校验）
  run.completed / run.failed   starting|running → idle（计数清零）
  run.cancelled         starting|running → idle；队列有 queued 项 →
                        queue_paused = 1（§4：用户停止后不自动接续）。
                        C9 起 idle 也合法：显式放弃 interrupted/
                        waiting_verification 任务（无 live runner，对账后
                        FSM 已回 idle）——同样套用队列暂停规则
  run.interrupted       starting|running|error → idle（重启对账收敛）
  queue.paused / queue.resumed   任意状态置 / 清 queue_paused（幂等）
  queue.item_enqueued   任意状态追加排队项
  queue.item_cancelled  按 item_ids 把 queued 项标 cancelled
  fatal（运行时崩溃，非事件——apply_fatal）  → error
其余事件类型（step/llm/tool/artifact/materials/...）与 FSM 无关：no-op。

can_queue 在 §4 真值表原文为 state == running；本实现把 starting 也计入
（running 的启动瞬时相位）：卡内失败状态"并发同会话两条指令（锁序化，第二
条必入队）"要求指令直发后、run.started 落库前的第二条指令入队而非被拒。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Literal

from . import Event, RunEventType

StateName = Literal["idle", "starting", "running", "error"]


class InvalidTransition(RuntimeError):
    """非法迁移——拒绝并携带当前合法动作列表（M0-cards C7 失败状态）。"""

    def __init__(self, event: str, state: "ConversationState"):
        self.event = event
        self.state = state
        self.legal_actions = legal_actions(state)
        super().__init__(
            f"非法迁移：事件 {event} 在 state={state.state} 下不可用"
            f"（waiting_approvals={state.waiting_approvals},"
            f" waiting_questions={state.waiting_questions}），"
            f"当前合法动作：{self.legal_actions or '无'}"
        )


@dataclass(frozen=True)
class QueueItem:
    """排队指令（conversations.pending_queue 的内存镜像，事件重放可重建）。"""

    id: str
    text: str
    enqueued_at: str
    state: Literal["queued", "cancelled", "dequeued"] = "queued"

    def as_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "enqueued_at": self.enqueued_at}


@dataclass(frozen=True)
class ConversationState:
    state: StateName = "idle"
    waiting_approvals: int = 0
    waiting_questions: int = 0
    current_task_run_id: str | None = None
    queue_paused: bool = False
    queue: tuple[QueueItem, ...] = field(default=())

    @property
    def queued_items(self) -> tuple[QueueItem, ...]:
        return tuple(i for i in self.queue if i.state == "queued")

    def queued_position(self, item_id: str) -> int | None:
        """队列明细位置（1 起）；已不在队列返回 None。"""
        for pos, item in enumerate(self.queued_items, start=1):
            if item.id == item_id:
                return pos
        return None


INITIAL_STATE = ConversationState()


def _mark_item(state: ConversationState, item_id: str,
               new_state: str) -> ConversationState:
    queue = tuple(
        replace(i, state=new_state) if i.id == item_id and i.state == "queued"
        else i
        for i in state.queue
    )
    return replace(state, queue=queue)


def _finish(state: ConversationState) -> ConversationState:
    """任务终态：回 idle、计数清零、卸下当前任务。"""
    return replace(state, state="idle", waiting_approvals=0,
                   waiting_questions=0, current_task_run_id=None)


def reduce(state: ConversationState, event: Event) -> ConversationState:
    """迁移规则唯一入口。FSM 域外事件 no-op；非法迁移抛 InvalidTransition。"""
    t = event.type
    p = event.payload

    if t == RunEventType.RUN_QUEUED:
        if state.state != "idle":
            raise InvalidTransition("run.queued", state)
        nxt = replace(state, state="starting",
                      current_task_run_id=event.task_run_id)
        item_id = p.get("queue_item_id")
        if item_id is not None:
            nxt = _mark_item(nxt, str(item_id), "dequeued")
        return nxt

    if t == RunEventType.RUN_STARTED:
        if state.state != "starting":
            raise InvalidTransition("run.started", state)
        return replace(state, state="running")

    if t == RunEventType.RUN_RESUMED:
        if state.state != "idle":
            raise InvalidTransition("run.resumed", state)
        return replace(state, state="running",
                       current_task_run_id=event.task_run_id)

    if t == RunEventType.PERMISSION_REQUESTED:
        if state.state != "running":
            raise InvalidTransition("permission.requested", state)
        return replace(state, waiting_approvals=state.waiting_approvals + 1)

    if t == RunEventType.PERMISSION_RESOLVED:
        if state.state == "running" and state.waiting_approvals > 0 \
                and event.task_run_id == state.current_task_run_id:
            return replace(state, waiting_approvals=state.waiting_approvals - 1)
        return state  # 对账后到达的决定（见模块 docstring）——历史任务的
        # 决定只留事件事实，不清零后继任务的未决计数（外审回稿）

    if t == RunEventType.QUESTION_REQUESTED:
        if state.state != "running":
            raise InvalidTransition("question.requested", state)
        return replace(state, waiting_questions=state.waiting_questions + 1)

    if t == RunEventType.QUESTION_ANSWERED:
        if state.state == "running" and state.waiting_questions > 0 \
                and event.task_run_id == state.current_task_run_id:
            return replace(state, waiting_questions=state.waiting_questions - 1)
        return state

    if t in (RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED):
        if state.state not in ("starting", "running"):
            raise InvalidTransition(str(t), state)
        return _finish(state)

    if t == RunEventType.RUN_CANCELLED:
        if state.state not in ("starting", "running", "idle"):
            raise InvalidTransition("run.cancelled", state)
        nxt = _finish(state)
        if nxt.queued_items:
            nxt = replace(nxt, queue_paused=True)  # §4：队列非空 → 暂停
        return nxt

    if t == RunEventType.RUN_INTERRUPTED:
        if state.state not in ("starting", "running", "error"):
            raise InvalidTransition("run.interrupted", state)
        return _finish(state)

    if t == RunEventType.QUEUE_PAUSED:
        return replace(state, queue_paused=True)

    if t == RunEventType.QUEUE_RESUMED:
        return replace(state, queue_paused=False)

    if t == RunEventType.QUEUE_ITEM_ENQUEUED:
        item = QueueItem(id=str(p["item_id"]), text=str(p.get("text", "")),
                         enqueued_at=event.ts)
        return replace(state, queue=state.queue + (item,))

    if t == RunEventType.QUEUE_ITEM_CANCELLED:
        nxt = state
        for item_id in p.get("item_ids", []):
            nxt = _mark_item(nxt, str(item_id), "cancelled")
        return nxt

    # FSM 域外事件（step/llm/tool/artifact/materials/conversation.* 等）
    return state


def apply_fatal(state: ConversationState) -> ConversationState:
    """致命错误（运行时崩溃）→ error（§4 迁移表行；重启对账后由
    run.interrupted 收敛回 idle）。"""
    return replace(state, state="error")


# ── 能力真值表（§4；前端与 API 判定共用）──────────────────────────

def can_send(s: ConversationState) -> bool:
    return s.state == "idle"


def can_queue(s: ConversationState) -> bool:
    return s.state in ("starting", "running") and s.waiting_approvals == 0


def can_cancel(s: ConversationState) -> bool:
    return s.state in ("starting", "running")


def can_continue_queue(s: ConversationState, *,
                       pending_verifications: int = 0,
                       unresolved_approvals: int = 0) -> bool:
    """继续队列能力（与 POST queue/continue 同一判定源，外审回稿）。

    FSM 三条件之外还有两个库侧阻断事实（不在事件域，由调用方查实传入）：
    待核验调用与未决审批——接口对这两者分别 409，能力字段必须同样为 False，
    否则快照宣称可继续而请求必被拒。缺省 0 = 纯 FSM 视角（前端真值表）。"""
    return (s.state == "idle" and s.queue_paused
            and bool(s.queued_items)
            and pending_verifications == 0 and unresolved_approvals == 0)


_TRUTH_TABLE = (
    ("send", can_send),
    ("queue", can_queue),
    ("cancel", can_cancel),
    ("continue_queue", can_continue_queue),
)


def legal_actions(s: ConversationState) -> list[str]:
    """当前合法动作列表（InvalidTransition.detail 与前端状态机共用）。"""
    return [name for name, fn in _TRUTH_TABLE if fn(s)]


def replay(events: Iterable[Event],
           state: ConversationState = INITIAL_STATE) -> ConversationState:
    """按序折叠事件重建状态（GET state / 重启恢复共用）。"""
    for event in events:
        state = reduce(state, event)
    return state
