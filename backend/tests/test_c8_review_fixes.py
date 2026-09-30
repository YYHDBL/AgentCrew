"""C8 外审回稿修复的回归测试（2 致命 + 5 建议 + 欠账#1）。

反证纪律：本文件断言的是修复后语义；在修复前代码上运行应失败
（缺失方法/行为不符）。GLM 不进本文件（ADR-006）——真实 GLM 全链回归
在 scripts/loop/c8_demo.py（含新增场景 H：bash 取消终态前结清）。
run_task 的解析重试计数用构造 StreamEvent 驱动自有编排（C4 _consume
同款手法，非假 Provider——被测对象是循环的守门决策，不是模型行为）。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from agentcrew_core.events import RunEventType, RunEventType as T
from agentcrew_core.loop import LoopDeps, LoopGates, run_task
from agentcrew_core.provider.types import StreamEvent, ToolCall, Usage
from agentcrew_core.tools import (
    ToolInvocation,
    ToolScheduler,
    build_default_registry,
    new_call_id,
)
from agentcrew_core.tools.metadata import ToolResult
from agentcrew_core.tools.scheduler import validate_tool_input
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.bus import EventBus, Topic
from agentcrew_server.config import load_config
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.questions import QuestionService
from agentcrew_server.run_manager import RunManager
from agentcrew_server.sessions import SessionService
from agentcrew_server.settings import SettingsService


class Assembly:
    def __init__(self, tmp_path: Path):
        self.data_dir = tmp_path
        self.db = Database(tmp_path / "t.db")
        run_migrations(self.db.write_conn, tmp_path / "backups")
        self.channel = WriteChannel(self.db.write_conn)
        self.bus = EventBus()
        self.store = EventStore(self.channel, publisher=self.bus.publish)
        self.settings = SettingsService(
            load_config(tmp_path, {}), tmp_path, self.channel,
            tmp_path / "chain-head.txt")
        self.sessions = SessionService(self.db, self.store, tmp_path,
                                       self.settings)
        self.approvals = ApprovalService(self.db, self.store,
                                         tmp_path / "chain-head.txt")
        self.approvals.scheduler = ToolScheduler(
            build_default_registry(), gate=self.approvals.gate)
        self.questions = QuestionService(self.db, self.store)
        self.run_manager = RunManager(
            db=self.db, event_store=self.store, bus=self.bus,
            settings=self.settings, sessions=self.sessions,
            approvals=self.approvals, scheduler=self.approvals.scheduler,
            questions=self.questions)

    def read(self, sql: str, params=()):
        return self.db.read_conn.execute(sql, params).fetchone()


@pytest.fixture()
def asm(tmp_path: Path) -> Assembly:
    return Assembly(tmp_path)


async def _running_task(a: Assembly) -> tuple[str, str]:
    created = await a.sessions.create_conversation(
        instruction="任务一", client_request_id="c1")
    conv_id = created["conversation"]["id"]
    task_id = created["task_run_id"]
    await a.store.append(
        task_run_id=task_id, conversation_id=conv_id,
        type=T.RUN_STARTED,
        payload={"attempt_no": 1, "attempt_id": "att-1", "kind": "initial"})
    return conv_id, task_id


# ── 致命①：名额等待期取消必须落终态（成对事件）───────────────────────

def test_cancel_while_waiting_for_slot_writes_terminal(asm: Assembly):
    """占满全局名额后取消等待名额的任务 → run.cancelled + 队列非空时
    queue.paused 成对落库、任务 cancelled、FSM 不留 starting。"""
    async def scenario():
        from agentcrew_server.run_manager import _Run
        # 新会话首任务：queued、FSM starting（等名额语义）
        created = await asm.sessions.create_conversation(
            instruction="等名额的任务", client_request_id="w1")
        conv_id = created["conversation"]["id"]
        task_id = created["task_run_id"]
        await asm.sessions.send_instruction(conv_id, "排队指令")  # 队列非空
        slots = int(asm.settings.config.values["gates"]["global_concurrency"])
        for _ in range(slots):  # 占满全部全局名额
            await asm.run_manager._sem.acquire()
        fut = asyncio.ensure_future(
            asm.run_manager._runner(task_id, conv_id))
        asm.run_manager._runs[task_id] = _Run(
            task_run_id=task_id, conversation_id=conv_id, task=fut)
        await asyncio.sleep(0.3)  # runner 已进入名额等待
        result = await asm.run_manager.request_cancel(task_id)
        assert result["status"] in ("cancelling", "cancelled")
        for _ in range(100):  # 已取消的 task 不可直接 await，轮询终态
            if fut.done():
                break
            await asyncio.sleep(0.05)
        assert fut.cancelled()
        asm.run_manager._runs.pop(task_id, None)
        status = asm.read("SELECT status FROM task_runs WHERE id=?",
                          (task_id,))[0]
        paused = asm.read("SELECT queue_paused FROM conversations"
                          " WHERE id=?", (conv_id,))[0]
        cancelled_n = asm.read(
            "SELECT count(*) FROM run_events WHERE type='run.cancelled'"
            " AND task_run_id=?", (task_id,))[0]
        assert status == "cancelled" and cancelled_n == 1, \
            "等待期取消也必须落 run.cancelled（不留 queued/starting）"
        assert paused == 1, "队列非空时 queue.paused 必须成对落库"
    asyncio.run(scenario())


# ── 致命②：终态前结清 dispatched 无结果调用 ─────────────────────────

def test_terminal_settles_dispatched_calls(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await asm.sessions.send_instruction(conv_id, "排队指令")  # 队列非空
        # 真实路径构造 dispatched 无终态：prepared + dispatched 事件
        for etype, payload in (
                (T.TOOL_PREPARED, {
                    "call_id": "call_x", "tool_name": "bash",
                    "side_effect_class": "outcome_unknown",
                    "input_hash": "h", "risk_level": "medium",
                    "input": {}}),
                (T.TOOL_DISPATCHED, {"call_id": "call_x"})):
            await asm.store.append(
                task_run_id=task_id, conversation_id=conv_id,
                type=etype, payload=payload)
        # 终态（经 RunManager 的统一收尾路径：先结清再发终态）
        await asm.run_manager._finish(
            task_id, conv_id, "run.completed",
            {"final_text": "x", "outcome": "completed"})
        row = asm.read(
            "SELECT status, (SELECT count(*) FROM run_events"
            "  WHERE type='tool.pending_verification'"
            "  AND json_extract(payload,'$.call_id')='call_x') FROM tool_calls"
            " WHERE call_id='call_x'")
        assert row[0] == "pending_verification", \
            "终态前 dispatched 无结果必须转待核验落库"
        assert row[1] == 1
        order = asm.read(
            "SELECT (SELECT MAX(global_seq) FROM run_events"
            "   WHERE type='tool.pending_verification')"
            " < (SELECT MAX(global_seq) FROM run_events"
            "     WHERE type='run.completed')")[0]
        assert order == 1, "结清事件必须先于终态事件（终态时序契约③）"
        # 待核验阻断继续队列：把队列置暂停后 continue 必须 409 PENDING_VERIFICATION
        await asm.store.append(
            task_run_id=None, conversation_id=conv_id,
            type=T.QUEUE_PAUSED, payload={})
        pending = asm.sessions._pending_verifications(conv_id)
        assert [p["call_id"] for p in pending] == ["call_x"]
        try:
            await asm.sessions.continue_queue(conv_id)
            raised = ""
        except Exception as e:  # noqa: BLE001
            raised = str(e)
        assert "待核验" in raised, "存在结清的待核验调用时继续队列必须 409"
    asyncio.run(scenario())


# ── 欠账#1：ask_user 等待期间释放全局名额 ───────────────────────────

def test_ask_user_releases_global_slot(asm: Assembly):
    async def scenario():
        from agentcrew_server.run_manager import _Run
        conv_id, task_id = await _running_task(asm)
        run = _Run(task_run_id=task_id, conversation_id=conv_id,
                   task=asyncio.current_task())
        asm.run_manager._runs[task_id] = run
        ctx = asm.sessions.build_work_context(conv_id, task_id)
        ctx.ask_resolver = asm.questions.make_resolver(task_id, conv_id)
        events: list[tuple] = []

        async def sink(event_type: str, payload: dict) -> None:
            events.append((event_type, payload))
            await asm.store.append(  # 真实落库（submit 按事件找提问）
                task_run_id=task_id, conversation_id=conv_id,
                type=RunEventType(event_type), payload=payload)

        ctx.emit = sink
        # 占满全部 8 个全局名额：ask_user 等待必须仍能进入
        for _ in range(8):
            await asm.run_manager._sem.acquire()
        call = ToolCall(id="call_ask", name="ask_user",
                        input={"question": "占用名额测试"})
        asking = asyncio.ensure_future(
            asm.run_manager._ask_user_direct(run, sink, ctx, call))
        for _ in range(100):
            if any(e[0] == "question.requested" for e in events):
                break
            await asyncio.sleep(0.05)
        assert any(e[0] == "question.requested" for e in events)
        # 满载下仍能取得名额 = 等待中的 ask_user 已释放全局名额
        await asyncio.wait_for(asm.run_manager._sem.acquire(), timeout=3)
        asm.run_manager._sem.release()  # 还回证明用的名额，供回答后重取
        # 回答（真实 request_id 即 call_id）→ 工具完成并重新占住名额
        await asm.questions.submit("call_ask", "回答内容")
        result = await asyncio.wait_for(asking, timeout=5)
        assert result.ok and "回答内容" in result.output
        await asyncio.sleep(0.1)
        assert asm.run_manager._sem.locked(), "回答后应重新取得名额"
        asm.run_manager._runs.pop(task_id, None)
    asyncio.run(scenario())


# ── 建议④：gates 绑定时点（名额到手后）──────────────────────────────

def test_gates_bound_after_slot_acquired(asm: Assembly):
    async def scenario():
        # 反证点在 _runner 结构（_bind_gates 位于 sem.acquire 之后）；
        # 可观测行为：PATCH 后下一次绑定即时反映新值
        assert asm.run_manager._bind_gates().max_steps == 40
        await asm.settings.patch({"gates": {"max_steps": 7}})
        assert asm.run_manager._bind_gates().max_steps == 7
    asyncio.run(scenario())


# ── 建议⑤：取消成对事件单事务 + 会话锁（与 continue_queue 串行）──────

def test_cancel_pair_single_transaction(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await asm.sessions.send_instruction(conv_id, "排队指令")
        await asm.sessions.emit_cancel_pair(conv_id, task_id)
        seqs = asm.read(
            "SELECT (SELECT MAX(global_seq) FROM run_events"
            "   WHERE type='run.cancelled' AND task_run_id=:t),"
            "       (SELECT MAX(global_seq) FROM run_events"
            "   WHERE type='queue.paused' AND conversation_id=:c)",
            {"t": task_id, "c": conv_id})
        assert seqs[0] and seqs[1] == seqs[0] + 1, \
            "run.cancelled 与 queue.paused 必须同事务相邻提交（无观察窗口）"
        # 成对提交后继续队列：看到完整暂停态 → 正常继续
        await asm.sessions.continue_queue(conv_id)
        state = asm.sessions.state_snapshot(conv_id)
        assert not state["queue_paused"] and state["state"] != "idle", \
            "继续后不应再被晚到的 paused 二次暂停"
    asyncio.run(scenario())


def test_concurrent_cancel_and_continue_no_interleave(asm: Assembly):
    """写通道占位法：取消对与继续在写通道上串行化，终态二选一无交错。"""
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await asm.sessions.send_instruction(conv_id, "排队指令")
        gate = asyncio.Event()

        def blocker(conn):
            while not gate.is_set():
                time.sleep(0.005)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("COMMIT")

        occupy = asyncio.ensure_future(asm.channel.execute(blocker))
        await asyncio.sleep(0.05)  # 占位生效
        cancel = asyncio.ensure_future(
            asm.sessions.emit_cancel_pair(conv_id, task_id))
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.wait_for(asyncio.gather(occupy, cancel), timeout=5)
        # 取消完整落库（成对原子）后继续：要么成功要么被拒，绝无交错
        try:
            await asm.sessions.continue_queue(conv_id)
        except Exception:  # noqa: BLE001 —— 状态不符被拒属合法结局
            pass
        state = asm.sessions.state_snapshot(conv_id)
        buggy = (state["state"] in ("starting", "running")
                 and state["queue_paused"])
        assert not buggy, "新任务执行中又被 paused = 取消/继续交错（外审竞态）"
    asyncio.run(scenario())


# ── 建议⑥：internal 订阅绕开溢出/清扫 ──────────────────────────────

def _synthetic(seq: int, etype=T.TOOL_PREPARED):
    return type("E", (), {"global_seq": seq, "conversation_id": "c",
                          "task_run_id": "t", "type": etype})()


def test_internal_subscription_no_loss_no_overflow():
    bus = EventBus()
    sub = bus.subscribe(Topic("all"), internal=True)
    for i in range(1, 1101):
        bus.publish(_synthetic(i))
    assert sub.queue.qsize() == 1100, "internal 订阅零丢失（不受 1000 上限）"
    assert not sub.overflowed, "internal 订阅永不置溢出标记"
    bus.sweep()
    bus.sweep()
    assert sub.queue.qsize() == 1100, "internal 订阅不被清扫"


def test_dispatch_task_survives_event_flood(asm: Assembly):
    async def scenario():
        await asm.run_manager.start()
        for i in range(1100):  # 非 FSM 域事件洪泛（不触发 spawn）
            asm.bus.publish(_synthetic(i))
        for _ in range(100):
            if asm.run_manager._dispatch_sub.queue.qsize() == 0:
                break
            await asyncio.sleep(0.05)
        assert not asm.run_manager._dispatch_task.done(), \
            "派发任务必须在洪泛后存活（internal 不被 sweep 强断）"
        assert asm.run_manager._dispatch_sub.queue.qsize() == 0, \
            "internal 订阅零丢失（全部被派发消费）"
        await asm.run_manager.shutdown()
    asyncio.run(scenario())


# ── 建议⑦：工具参数 schema 校验 → 解析重试通道（计数共享）───────────

def test_validate_tool_input_matrix():
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "limit": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "force": {"type": "boolean"},
        },
        "required": ["path"],
    }
    assert validate_tool_input(schema, {"path": "a.txt"}) is None
    assert validate_tool_input(schema, {"path": "a.txt", "limit": 3}) is None
    assert "path" in validate_tool_input(schema, {})
    assert "path" in validate_tool_input(schema, {"path": 1})
    assert "limit" in validate_tool_input(
        schema, {"path": "a", "limit": "x"})
    assert "tags" in validate_tool_input(schema, {"path": "a", "tags": [1]})


def test_scheduler_rejects_invalid_params_with_ledger(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        ctx = asm.sessions.build_work_context(conv_id, task_id)
        emitted: list[str] = []

        async def sink(event_type: str, payload: dict) -> None:
            emitted.append(event_type)
            await asm.store.append(
                task_run_id=task_id, conversation_id=conv_id,
                type=RunEventType(event_type), payload=payload)

        ctx.emit = sink
        result = await asm.approvals.scheduler.run(
            ToolInvocation(call_id=new_call_id(), name="read_file",
                           input={}),  # 缺必填 path
            ctx)
        assert result.ok is False and result.error == "INVALID_PARAMS"
        assert "tool.prepared" in emitted and "tool.failed" in emitted
        assert "tool.dispatched" not in emitted, "参数坏不派发、不问权限"
        row = asm.read("SELECT status FROM tool_calls"
                       " WHERE tool_name='read_file'")
        assert row[0] == "failed"
    asyncio.run(scenario())


def test_loop_counts_invalid_params_into_parse_retries():
    """缺参等 schema 不符 → 同一条解析重试通道（计数与流解析共享），
    超限 RUN_FAILED(unparseable)；坏参数以错误 tool_result 回填重说。"""
    calls = [ToolCall(id="c1", name="read_file", input={}),
             ToolCall(id="c2", name="read_file", input={}),
             ToolCall(id="c3", name="read_file", input={})]
    rounds = iter(calls + [None])

    async def scenario():
        async def request():
            call = next(rounds)
            if call is not None:
                yield StreamEvent(type="tool_call", tool_call=call)
            yield StreamEvent(type="usage", usage=Usage(1, 1))
            yield StreamEvent(
                type="done",
                stop_reason="tool_use" if call is not None else "end_turn")

        async def execute(call):
            return ToolResult(ok=False, error="INVALID_PARAMS",
                              details={"message": "缺少必填参数 path"})

        messages: list = []

        async def emit(event_type, payload):
            return None

        deps = LoopDeps(
            request=request, execute=execute, emit=emit,
            on_progress=lambda: None, gates=LoopGates(), model="m")
        result = await run_task(messages, deps)
        assert result.status == "failed" and result.reason == "unparseable", \
            "第三个坏参数触发 unparseable（共享计数上限 2）"
        # 前两轮坏参数以错误 tool_result 回填（模型可重说）
        feedback = [m for m in messages if m["role"] == "user"
                    and m["content"][0]["type"] == "tool_result"]
        assert len(feedback) == 2
    asyncio.run(scenario())


def test_settle_exempts_ask_user(asm: Assembly):
    """ask_user 豁免终态结清：取消时账本停 dispatched + 未回答状态
    （交互原语无副作用可核验；转待核验会永久堵死"停止后继续队列"，
    验证提交 API 属 C9）。"""
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        for etype, payload in (
                (T.TOOL_PREPARED, {
                    "call_id": "call_q", "tool_name": "ask_user",
                    "side_effect_class": "verifiable",
                    "input_hash": "h", "risk_level": "low",
                    "input": {}}),
                (T.TOOL_DISPATCHED, {"call_id": "call_q"})):
            await asm.store.append(
                task_run_id=task_id, conversation_id=conv_id,
                type=etype, payload=payload)
        await asm.run_manager._finish(
            task_id, conv_id, "run.completed",
            {"final_text": "x", "outcome": "completed"})
        assert asm.read("SELECT status FROM tool_calls WHERE call_id='call_q'"
                        )[0] == "dispatched", "ask_user 不转待核验"
    asyncio.run(scenario())
