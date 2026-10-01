"""FSM reducer 参数化单测（M0-C7 核心验收：迁移表全行 + 真值表矩阵）。

自有纯函数参数化单测（ADR-006 允许）；事件构造走真实 Event 信封。
"""

from __future__ import annotations

import pytest

from agentcrew_core.events import Event, RunEventType as T
from agentcrew_core.events.reducer import (
    INITIAL_STATE,
    ConversationState,
    InvalidTransition,
    QueueItem,
    apply_fatal,
    can_cancel,
    can_continue_queue,
    can_queue,
    can_send,
    legal_actions,
    reduce,
    replay,
)

SEQ = iter(range(1, 10_000))


def ev(type_: T, task_run_id: str | None = "run-1", **payload) -> Event:
    return Event(
        global_seq=next(SEQ), id=f"e{next(SEQ)}", task_run_id=task_run_id,
        seq=1, conversation_id="conv-1", type=type_, payload=payload,
    )


def queued_fsm(items: int = 1, **kw) -> ConversationState:
    """idle + paused=False + N 个排队项的常用前置。"""
    queue = tuple(
        QueueItem(id=f"item-{i}", text=f"指令{i}", enqueued_at="t")
        for i in range(items)
    )
    return ConversationState(queue=queue, **kw)


def running_fsm(**kw) -> ConversationState:
    return ConversationState(state="running", current_task_run_id="run-1", **kw)


# ── 迁移表全行（§4 v1.4）────────────────────────────────────────────

@pytest.mark.parametrize("before,after_kind,event_factory,check", [
    # run.queued：idle → starting（记 current_task_run_id）
    ("idle", "starting", lambda s: ev(T.RUN_QUEUED),
     lambda s: s.current_task_run_id == "run-1"),
    # run.queued 从队列出队：对应项标 dequeued
    ("idle_with_queue", "starting",
     lambda s: ev(T.RUN_QUEUED, queue_item_id="item-0"),
     lambda s: s.queued_items == () and s.queue[0].state == "dequeued"),
    # run.started：starting → running
    ("starting", "running", lambda s: ev(T.RUN_STARTED), None),
    # run.resumed：idle → running（恢复 = 新 attempt 进循环）
    ("idle", "running", lambda s: ev(T.RUN_RESUMED),
     lambda s: s.current_task_run_id == "run-1"),
    # permission.requested / resolved：计数器增减（仅 running）
    ("running", "running", lambda s: ev(T.PERMISSION_REQUESTED),
     lambda s: s.waiting_approvals == 1),
    ("running_wa1", "running", lambda s: ev(T.PERMISSION_RESOLVED),
     lambda s: s.waiting_approvals == 0),
    # question.requested / answered：同上
    ("running", "running", lambda s: ev(T.QUESTION_REQUESTED),
     lambda s: s.waiting_questions == 1),
    ("running_wq1", "running", lambda s: ev(T.QUESTION_ANSWERED),
     lambda s: s.waiting_questions == 0),
    # run.completed / failed：starting|running → idle（计数清零）
    ("running", "idle", lambda s: ev(T.RUN_COMPLETED),
     lambda s: s.current_task_run_id is None),
    ("running", "idle", lambda s: ev(T.RUN_FAILED), None),
    ("starting", "idle", lambda s: ev(T.RUN_FAILED), None),
    # run.cancelled：队列非空 → idle + queue_paused；队列空 → 不暂停
    ("running_with_queue", "idle", lambda s: ev(T.RUN_CANCELLED),
     lambda s: s.queue_paused is True),
    ("running", "idle", lambda s: ev(T.RUN_CANCELLED),
     lambda s: s.queue_paused is False),
    # run.interrupted：starting|running|error → idle
    ("running", "idle", lambda s: ev(T.RUN_INTERRUPTED), None),
    ("error", "idle", lambda s: ev(T.RUN_INTERRUPTED), None),
    # queue.*：任意状态置/清暂停、入队、按 ids 取消
    ("idle", "idle", lambda s: ev(T.QUEUE_PAUSED),
     lambda s: s.queue_paused is True),
    ("idle", "idle", lambda s: ev(T.QUEUE_RESUMED),
     lambda s: s.queue_paused is False),
    ("idle", "idle", lambda s: ev(T.QUEUE_ITEM_ENQUEUED, item_id="q9", text="新"),
     lambda s: s.queued_items[0].id == "q9"),
    ("idle_with_queue", "idle",
     lambda s: ev(T.QUEUE_ITEM_CANCELLED, item_ids=["item-0"]),
     lambda s: s.queued_items == () and s.queue[0].state == "cancelled"),
    # 对账后到达的解决事件：计数=0 时 no-op（C6 重启后决定仍可提交）
    ("idle", "idle", lambda s: ev(T.PERMISSION_RESOLVED),
     lambda s: s.waiting_approvals == 0),
    ("idle", "idle", lambda s: ev(T.QUESTION_ANSWERED),
     lambda s: s.waiting_questions == 0),
    # 历史任务的决定不清零当前任务的计数（外审回稿：跨任务计数污染——
    # running_wa1 的当前任务是 run-1，事件锚在别的任务上）
    ("running_wa1", "running",
     lambda s: ev(T.PERMISSION_RESOLVED, task_run_id="run-9"),
     lambda s: s.waiting_approvals == 1),
    ("running_wq1", "running",
     lambda s: ev(T.QUESTION_ANSWERED, task_run_id="run-9"),
     lambda s: s.waiting_questions == 1),
    # FSM 域外事件 no-op
    ("running", "running", lambda s: ev(T.STEP_STARTED), None),
    ("idle", "idle",
     lambda s: ev(T.MATERIALS_IMPORTED, files=[]), None),
])
def test_transition_table(before, after_kind, event_factory, check):
    state = {
        "idle": INITIAL_STATE,
        "idle_with_queue": queued_fsm(1),
        "starting": ConversationState(state="starting",
                                      current_task_run_id="run-1"),
        "running": running_fsm(),
        "running_wa1": running_fsm(waiting_approvals=1),
        "running_wq1": running_fsm(waiting_questions=1),
        "running_with_queue": running_fsm(queue=queued_fsm(1).queue),
        "error": ConversationState(state="error"),
    }[before]
    nxt = reduce(state, event_factory(state))
    assert nxt.state == after_kind, f"{before} --ev--> {nxt.state}"
    if check is not None:
        assert check(nxt)


def test_run_queued_from_queue_keeps_head_order():
    """出队取队首：多排队项时只标第一项，其余保持 queued。"""
    state = queued_fsm(2)
    nxt = reduce(state, ev(T.RUN_QUEUED, queue_item_id="item-0"))
    assert [i.id for i in nxt.queued_items] == ["item-1"]
    assert nxt.queue[0].state == "dequeued"


def test_fatal_enters_error_and_interrupted_converges():
    """致命错误 → error（apply_fatal）；重启对账 run.interrupted 收敛回 idle。"""
    err = apply_fatal(running_fsm())
    assert err.state == "error"
    assert reduce(err, ev(T.RUN_INTERRUPTED)).state == "idle"


def test_stale_resolution_does_not_pollute_next_task():
    """外审回稿：A 等审批→中断→B 等审批，A 的旧决定不清零 B 的计数。

    approvals.submit 对重启前留下的审批卡以**原任务**为锚点落库（C6 已
    验收语义），该决定经重放到达时 B 正在等审批——修复前会清零 B 的计数
    使 can_queue 错误变 true。"""
    events = [
        ev(T.RUN_QUEUED, task_run_id="run-A"),
        ev(T.RUN_STARTED, task_run_id="run-A"),
        ev(T.PERMISSION_REQUESTED, task_run_id="run-A", tool_call_id="c-a"),
        ev(T.RUN_INTERRUPTED, task_run_id="run-A"),
        ev(T.RUN_QUEUED, task_run_id="run-B"),
        ev(T.RUN_STARTED, task_run_id="run-B"),
        ev(T.PERMISSION_REQUESTED, task_run_id="run-B", tool_call_id="c-b"),
        ev(T.PERMISSION_RESOLVED, task_run_id="run-A",
           tool_call_id="c-a", decision="allow_once"),
    ]
    s = replay(events)
    assert s.current_task_run_id == "run-B"
    assert s.waiting_approvals == 1, "历史任务的决定不得清零 B 的未决计数"
    assert can_queue(s) is False
    # B 自己的决定正常清零
    s2 = reduce(s, ev(T.PERMISSION_RESOLVED, task_run_id="run-B",
                      tool_call_id="c-b", decision="allow_once"))
    assert s2.waiting_approvals == 0 and can_queue(s2) is True


@pytest.mark.parametrize("event_factory,before", [
    (lambda: ev(T.RUN_QUEUED), "starting"),
    (lambda: ev(T.RUN_QUEUED), "running"),
    (lambda: ev(T.RUN_STARTED), "idle"),
    (lambda: ev(T.RUN_RESUMED), "running"),
    (lambda: ev(T.PERMISSION_REQUESTED), "idle"),
    (lambda: ev(T.PERMISSION_REQUESTED), "starting"),
    (lambda: ev(T.QUESTION_REQUESTED), "idle"),
    (lambda: ev(T.RUN_COMPLETED), "idle"),
    # run.cancelled from idle 自 C9 起合法（显式放弃 interrupted/
    # waiting_verification 任务）——新语义见 test_c9_recovery
    (lambda: ev(T.RUN_INTERRUPTED), "idle"),
])
def test_invalid_transitions_raise(event_factory, before):
    state = {"idle": INITIAL_STATE,
             "starting": ConversationState(state="starting",
                                           current_task_run_id="run-1"),
             "running": running_fsm()}[before]
    with pytest.raises(InvalidTransition) as e:
        reduce(state, event_factory())
    assert isinstance(e.value.legal_actions, list)  # 拒绝并返回合法动作列表


# ── 真值表矩阵（can_send/can_queue/can_cancel/can_continue_queue）────

@pytest.mark.parametrize(
    "state,send,queue,cancel,continue_q",
    [
        # idle 无队列：可直发
        (ConversationState(), True, False, False, False),
        # idle + 暂停 + 有队列：继续可用（F006）
        (queued_fsm(1, queue_paused=True), True, False, False, True),
        # idle + 未暂停 + 有队列：不自动跑也不可"继续"（等运行时自动出队，C8）
        (queued_fsm(1), True, False, False, False),
        # idle + 暂停 + 队列全被取消：无可继续项
        (ConversationState(
            queue_paused=True,
            queue=(QueueItem("i", "t", "t", state="cancelled"),)),
         True, False, False, False),
        # starting：指令直发后的瞬时相位——第二条必入队（卡内失败状态）
        (ConversationState(state="starting", current_task_run_id="r"),
         False, True, True, False),
        # running：入队可、直发不可、可取消
        (running_fsm(), False, True, True, False),
        # running + 审批挂起：can_send/can_queue 皆否（先处理审批）
        (running_fsm(waiting_approvals=1), False, False, True, False),
        # running + 提问挂起：提问不堵入队（真值表只约束审批）
        (running_fsm(waiting_questions=1), False, True, True, False),
        # queue_paused 不影响 running 时入队（保持暂停态）
        (running_fsm(queue_paused=True), False, True, True, False),
        # error：全否
        (ConversationState(state="error"), False, False, False, False),
    ],
)
def test_truth_table_matrix(state, send, queue, cancel, continue_q):
    assert can_send(state) is send
    assert can_queue(state) is queue
    assert can_cancel(state) is cancel
    assert can_continue_queue(state) is continue_q


def test_legal_actions_consistent_with_truth_table():
    s = running_fsm(waiting_approvals=1)
    assert legal_actions(s) == ["cancel"]
    assert legal_actions(queued_fsm(1, queue_paused=True)) == \
        ["send", "continue_queue"]


@pytest.mark.parametrize("pv,ua,expected", [
    (0, 0, True),
    (1, 0, False),
    (0, 1, False),
    (2, 1, False),
])
def test_can_continue_queue_db_blockers(pv, ua, expected):
    """外审回稿真值表扩行：待核验/未决审批是 POST queue/continue 的阻断
    条件，能力字段必须同样为 False（快照不得宣称可继续而接口必 409）。"""
    s = queued_fsm(1, queue_paused=True)
    assert can_continue_queue(
        s, pending_verifications=pv, unresolved_approvals=ua) is expected
    # FSM 条件不因参数放宽：未暂停/无队列仍不可继续
    assert can_continue_queue(queued_fsm(1), pending_verifications=pv,
                              unresolved_approvals=ua) is False


def test_queue_item_positions():
    state = queued_fsm(3)
    assert state.queued_position("item-0") == 1
    assert state.queued_position("item-2") == 3
    cancelled = reduce(state, ev(T.QUEUE_ITEM_CANCELLED, item_ids=["item-0"]))
    assert cancelled.queued_position("item-1") == 1  # 取消后位置前移
    assert cancelled.queued_position("item-0") is None
