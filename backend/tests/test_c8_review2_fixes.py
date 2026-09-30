"""C8 第二轮外审回稿修复的回归测试（F2 + 建议2/4/5）。

反证纪律：断言修复后语义，在修复前代码上运行应失败。F2 用写通道占位
精确放大"终态提交窗口"；run_task 以本地桩注入（测 RunManager 的取消
时序——自家代码，外审已认可该手法，非 mock 外部系统）；建议2 用构造
StreamEvent 驱动自有循环（C4 _consume 同款）。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from agentcrew_core.events import RunEventType as T
from agentcrew_core.loop import LoopDeps, LoopGates, run_task
from agentcrew_core.provider.types import (
    ErrorClass,
    ProviderError,
    StreamEvent,
    ToolCall,
    Usage,
)
from agentcrew_core.tools import ToolScheduler, build_default_registry
from agentcrew_core.tools.metadata import ToolResult
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.bus import EventBus
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
    def __init__(self, tmp_path: Path, gates_patch: dict | None = None):
        self.data_dir = tmp_path
        if gates_patch:
            (tmp_path / "config.json").write_text(
                json.dumps({"gates": gates_patch}))
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


def _spawn(asm: Assembly, task_id: str, conv_id: str):
    from agentcrew_server.run_manager import _Run
    fut = asyncio.ensure_future(asm.run_manager._runner(task_id, conv_id))
    asm.run_manager._runs[task_id] = _Run(
        task_run_id=task_id, conversation_id=conv_id, task=fut)
    return fut


async def _wait_done(fut, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        if fut.done():
            return
        await asyncio.sleep(0.05)


# ── F2：取消撞终态提交窗口 → 恰好一条终态（不双发）──────────────────

def test_cancel_during_terminal_commit_yields_single_terminal(
        asm: Assembly, monkeypatch):
    """写通道占位放大 _finish 提交窗口，取消精确落在窗口内：
    终态事件恰好一条（在途的 run.completed），replay 正常，state 不 500。"""
    from agentcrew_core.loop import LoopResult

    async def scenario():
        at_loop = asyncio.Event()   # 桩 run_task 已进入（即将返回→_finish）
        release_loop = asyncio.Event()
        channel_gate = asyncio.Event()  # 占位第二个写 job（run.completed）

        async def fake_run_task(messages, deps):
            at_loop.set()
            await release_loop.wait()  # 等测试把占位布好再进 _finish 窗口
            return LoopResult("completed", final_text="done")

        monkeypatch.setattr("agentcrew_server.run_manager.run_task",
                            fake_run_task)
        asm.run_manager._loop = asyncio.get_running_loop()  # watchdog 派发用
        created = await asm.sessions.create_conversation(
            instruction="F2 演示", client_request_id="f2")
        conv_id = created["conversation"]["id"]
        task_id = created["task_run_id"]

        def blocker(conn):
            while not channel_gate.is_set():
                time.sleep(0.005)

        fut = _spawn(asm, task_id, conv_id)
        await asyncio.wait_for(at_loop.wait(), timeout=10)
        # 占位写通道（FIFO：排在 run.completed 之前）→ 放行桩 → runner 挂在
        # _finish 的 await 上，终态 job 已提交未执行
        occupy = asyncio.ensure_future(asm.channel.execute(blocker))
        await asyncio.sleep(0.1)  # 让占位 job 真正排到写线程
        release_loop.set()
        await asyncio.sleep(0.2)  # runner 进入 _finish await（窗口内）
        fut.cancel()
        channel_gate.set()  # 放行：在途 run.completed 落库
        await asyncio.gather(occupy, return_exceptions=True)
        await _wait_done(fut)

        terminals = asm.read(
            "SELECT count(*) FROM run_events WHERE task_run_id=?"
            " AND type IN ('run.completed','run.failed','run.cancelled')",
            (task_id,))[0]
        assert terminals == 1, "取消撞终态窗口不得产生第二条终态"
        status = asm.read("SELECT status FROM task_runs WHERE id=?",
                          (task_id,))[0]
        assert status == "completed", "在途终态必须落定为 completed"
        cancelled_n = asm.read(
            "SELECT count(*) FROM run_events WHERE type='run.cancelled'"
            " AND task_run_id=?", (task_id,))[0]
        assert cancelled_n == 0
        state = asm.sessions.state_snapshot(conv_id)  # replay 正常（不 500）
        assert state["state"] == "idle"
        asm.run_manager._runs.pop(task_id, None)
    asyncio.run(scenario())


# ── 建议2：整轮重发仅限"已产出部分输出"────────────────────────────

def _retryable_error_event():
    return StreamEvent(type="error", error=ProviderError(
        ErrorClass.NETWORK, "流被截断：未收到 message_stop", retryable=True))


def test_zero_output_retryable_error_no_resend():
    """零产出可重试错误（适配层内部已退避耗尽后上交）不得再触发整轮
    重发——直接 RUN_FAILED(provider:*)，请求次数恰 1。"""
    async def scenario():
        calls = {"n": 0}

        async def request():
            calls["n"] += 1
            yield _retryable_error_event()

        async def execute(call):
            raise AssertionError("不应有工具执行")

        async def emit(event_type, payload):
            return None

        deps = LoopDeps(request=request, execute=execute, emit=emit,
                        on_progress=lambda: None, gates=LoopGates(),
                        model="m")
        result = await run_task([], deps)
        assert result.status == "failed"
        assert result.reason == "provider:network"
        assert calls["n"] == 1, "零产出可重试错误不重发（守门总表上限语义）"
    asyncio.run(scenario())


def test_partial_output_retryable_error_resends_once_and_discards():
    """已产出部分输出后的可重试中断：恰重发一次，且部分输出被丢弃。"""
    async def scenario():
        calls = {"n": 0}

        async def request():
            calls["n"] += 1
            if calls["n"] == 1:
                yield StreamEvent(type="text_delta", text="半截输出")
                yield _retryable_error_event()
            else:
                yield StreamEvent(type="text_delta", text="完整回复内容")
                yield StreamEvent(type="usage", usage=Usage(3, 5))
                yield StreamEvent(type="done", stop_reason="end_turn")

        async def execute(call):
            raise AssertionError("不应有工具执行")

        async def emit(event_type, payload):
            return None

        deps = LoopDeps(request=request, execute=execute, emit=emit,
                        on_progress=lambda: None, gates=LoopGates(),
                        model="m")
        result = await run_task([], deps)
        assert result.status == "completed"
        assert result.final_text == "完整回复内容", "重发前必须丢弃部分输出"
        assert calls["n"] == 2, "恰重发一次"
    asyncio.run(scenario())


# ── 建议4：global_concurrency PATCH 响应明示 restart_required ──────

def test_patch_global_concurrency_reports_restart_required(asm: Assembly):
    async def scenario():
        r = await asm.settings.patch({"gates": {"global_concurrency": 4}})
        assert r["restart_required"] == ["gates.global_concurrency"], \
            "触碰该字段的 PATCH 必须明示重启生效"
        # 不触碰该字段：列表为空
        r2 = await asm.settings.patch({"gates": {"max_steps": 11}})
        assert r2["restart_required"] == []
        # GET 显示当前生效值（config 已持久化，重启后生效）
        view = asm.settings.get_view()
        assert view["gates"]["global_concurrency"] == 4
    asyncio.run(scenario())


# ── 建议5：stalled 看门狗真实触发（小阈值 + 真实等待）───────────────

def test_watchdog_stalled_real_trigger(tmp_path: Path, monkeypatch):
    """stall_seconds=1 + 桩 run_task 阻塞不刷心跳 → 真实 1s 后看门狗
    取消 → RUN_FAILED(stalled)。"""
    async def scenario():
        asm = Assembly(tmp_path, gates_patch={"stall_seconds": 1})

        async def stuck_run_task(messages, deps):
            await asyncio.sleep(30)  # 阻塞且不调 on_progress
            raise AssertionError("不应走到这里")

        monkeypatch.setattr("agentcrew_server.run_manager.run_task",
                            stuck_run_task)
        asm.run_manager._loop = asyncio.get_running_loop()
        created = await asm.sessions.create_conversation(
            instruction="会卡住的任务", client_request_id="st")
        conv_id = created["conversation"]["id"]
        task_id = created["task_run_id"]
        fut = _spawn(asm, task_id, conv_id)
        started = time.monotonic()
        await _wait_done(fut, timeout=15)
        elapsed = time.monotonic() - started
        reason = asm.read(
            "SELECT json_extract(payload,'$.reason') FROM run_events"
            " WHERE task_run_id=? AND type='run.failed'", (task_id,))[0]
        assert reason == "stalled"
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "failed"
        assert 0.5 < elapsed < 12, f"应在阈值后不久触发（实际 {elapsed:.1f}s）"
        asm.run_manager._runs.pop(task_id, None)
    asyncio.run(scenario())
