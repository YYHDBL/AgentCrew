"""C9 外审回稿修复回归（F1/S1/S2/S3/S4/S5/K1/K4/K5）。

真库真事件（不碰 GLM——kill -9 端到端在 scripts/recovery/c9_demo.py，ADR-006）：
- F1：resume 成功后再崩，对账必须按**当前收敛状态**补第二条 run.interrupted
  （旧实现"历史存在即跳过"→ 任务停 running、resume 409、队列排不动）；
- S1：核验提交的待核验校验并入 BEGIN IMMEDIATE 事务（并发双提交恰一胜）；
  artifacts 惰性探测套会话锁（并发 GET 不重复发 missing 事件）；
- S2：resume 前置校验在会话锁内重读（cancel 先提交则 409，不被无声复活）；
- S3：工件重建按 artifact_path 字段驱动（read_file 超限/failed 带工件）；
- S4：账本对已 answered 的 ask_user 用真实事实（与重放单一事实源）；
- S5：resume 首部注入同会话已完成任务历史（与 initial 同口径）；
- K1：resume 非 409 路径把重放 warnings 返回调用方；
- K4：结清豁免 read_only 工具（纯读无副作用可核验，占位降级即可）；
- K5：写通道 busy 重试回滚路径清空 events（已提交事件不得重复发布）。
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from agentcrew_core.events import RunEventType as T
from agentcrew_core.loop import LoopResult
from agentcrew_core.tools import ToolScheduler, build_default_registry
from agentcrew_server import recovery as recovery_module
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.bus import EventBus
from agentcrew_server.config import load_config
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.questions import QuestionService
from agentcrew_server.recovery import RecoveryService
from agentcrew_server.run_manager import RunManager
from agentcrew_server.sessions import SessionError, SessionService
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
        registry = build_default_registry()
        self.approvals.scheduler = ToolScheduler(
            registry, gate=self.approvals.gate)
        self.questions = QuestionService(self.db, self.store)
        self.run_manager = RunManager(
            db=self.db, event_store=self.store, bus=self.bus,
            settings=self.settings, sessions=self.sessions,
            approvals=self.approvals, scheduler=self.approvals.scheduler,
            questions=self.questions)
        self.recovery = RecoveryService(self.db, self.store, self.sessions,
                                        tmp_path, registry=registry)
        self.recovery.wire(self.run_manager)
        self.run_manager.wire(self.recovery)

    def read(self, sql: str, params=()):
        return self.db.read_conn.execute(sql, params).fetchone()

    def rows(self, sql: str, params=()):
        return self.db.read_conn.execute(sql, params).fetchall()


@pytest.fixture()
def asm(tmp_path: Path) -> Assembly:
    return Assembly(tmp_path)


async def _running_task(asm: Assembly, instruction="任务") -> tuple[str, str]:
    created = await asm.sessions.create_conversation(
        instruction=instruction, client_request_id="c9r")
    conv_id = created["conversation"]["id"]
    task_id = created["task_run_id"]
    await asm.store.append(
        task_run_id=task_id, conversation_id=conv_id,
        type=T.RUN_STARTED,
        payload={"attempt_no": 1, "attempt_id": "att-1", "kind": "initial"})
    return conv_id, task_id


async def _dispatched_call(asm: Assembly, conv_id, task_id, *,
                           tool="bash", effect="outcome_unknown",
                           extra_prepared=None, call_id="call-b1",
                           input_obj=None):
    payload = {
        "call_id": call_id, "tool_name": tool,
        "side_effect_class": effect, "input_hash": "h",
        "risk_level": "medium",
        "input": input_obj if input_obj is not None else {},
    }
    if extra_prepared:
        payload.update(extra_prepared)
    await asm.store.append(
        task_run_id=task_id, conversation_id=conv_id,
        type=T.TOOL_PREPARED, payload=payload)
    await asm.store.append(
        task_run_id=task_id, conversation_id=conv_id,
        type=T.TOOL_DISPATCHED, payload={"call_id": call_id})


def _spawn(asm: Assembly, task_id: str, conv_id: str, *, resume=False):
    from agentcrew_server.run_manager import _Run
    fut = asyncio.ensure_future(
        asm.run_manager._runner(task_id, conv_id, resume=resume))
    asm.run_manager._runs[task_id] = _Run(
        task_run_id=task_id, conversation_id=conv_id, task=fut)
    return fut


async def _wait_done(fut, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        if fut.done():
            return
        await asyncio.sleep(0.05)


# ── F1：resume 后二连崩，对账按当前收敛状态补中断 ────────────────────

def test_reconcile_refires_interrupt_after_resume(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await asm.recovery.reconcile()
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "interrupted"

        # 第一次 resume 成功 → 状态回 running（真实 resume 事件链）
        await asm.recovery.resume(task_id, None)
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "running"

        # 再次崩溃 → 对账必须补第二条 run.interrupted（历史里已有一条）
        await asm.recovery.reconcile()
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "interrupted", \
            "旧实现见历史事件即跳过 → 任务永久停 running"
        n = asm.rows("SELECT 1 FROM run_events WHERE task_run_id=?"
                     " AND type='run.interrupted'", (task_id,))
        assert len(n) == 2, "resume 后再崩必须补第二条中断"

        # 还能再次 resume（旧代码此处 409，队列永久卡死）
        await asm.recovery.resume(task_id, None)
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "running"
        attempts = asm.rows(
            "SELECT attempt_no FROM run_attempts WHERE task_run_id=?"
            " AND kind='resume' ORDER BY attempt_no", (task_id,))
        assert [a[0] for a in attempts] == [2, 3]
    asyncio.run(scenario())


# ── S1：核验提交事务内查状态（并发双提交恰一胜）─────────────────────

def test_concurrent_verification_single_verdict(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await _dispatched_call(asm, conv_id, task_id, call_id="call-b1")
        await asm.recovery.reconcile()  # → pending_verification

        gate = asyncio.Event()

        def blocker(conn):
            while not gate.is_set():
                time.sleep(0.005)

        occupy = asyncio.ensure_future(asm.channel.execute(blocker))
        await asyncio.sleep(0.05)  # 占位写通道：两个提交都能先过旧校验
        t1 = asyncio.ensure_future(asm.recovery.submit_verification(
            "call-b1", "confirmed_executed", None))
        t2 = asyncio.ensure_future(asm.recovery.submit_verification(
            "call-b1", "confirmed_not_executed", None))
        await asyncio.sleep(0.2)  # 均已读到 pending、排到写通道
        gate.set()
        results = await asyncio.gather(t1, t2, return_exceptions=True)
        await occupy

        oks = [r for r in results if isinstance(r, dict)]
        errs = [r for r in results if isinstance(r, SessionError)]
        assert len(oks) == 1 and len(errs) == 1, \
            f"并发双提交必须恰一胜：{results}"
        assert errs[0].code.value == "INVALID_TRANSITION"
        assert len(asm.rows("SELECT 1 FROM run_events WHERE type="
                            "'tool.verification_submitted'")) == 1, \
            "不得产生矛盾 verdict 双事件"
        assert len(asm.rows("SELECT 1 FROM audit_log WHERE action LIKE"
                            " 'tool.verification%'")) == 1, "审计不双写"
    asyncio.run(scenario())


# ── S1 同类：artifacts 惰性探测并发去重 ─────────────────────────────

def test_concurrent_artifact_probe_single_event(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        path = asm.data_dir / "artifacts" / task_id / "x.out"
        path.parent.mkdir(parents=True)
        path.write_text("ok", encoding="utf-8")
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.TOOL_PREPARED,
            payload={"call_id": "call-o1", "tool_call_id": "call-o1",
                     "tool_name": "bash", "side_effect_class": "outcome_unknown",
                     "input_hash": "h", "risk_level": "medium", "input": {}})
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.ARTIFACT_CREATED,
            payload={"artifact_id": "call-o1", "task_run_id": task_id,
                     "tool_call_id": "call-o1", "path": str(path),
                     "name": "x.out", "ext": ".out"})
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.ARTIFACT_READY, payload={"artifact_id": "call-o1",
                                            "size_bytes": 2})
        path.unlink()

        # 让两个并发 GET 都在"读到 ready 行"之后再进入探测（旧实现
        # 两个调用都据陈旧行发事件 → 重复）：线程内屏障对齐两次行读
        barrier = threading.Barrier(2)
        real_rows = asm.recovery._artifact_rows

        def gated_rows(conversation_id, task_run_id_):
            rows = real_rows(conversation_id, task_run_id_)
            try:
                barrier.wait(timeout=1.0)
            except threading.BrokenBarrierError:
                pass  # 修复后持锁者单到：放行（后续按锁内重读去重）
            return rows

        asm.recovery._artifact_rows = gated_rows
        await asyncio.gather(
            asm.recovery.list_artifacts(conv_id, None),
            asm.recovery.list_artifacts(conv_id, None))
        n = asm.rows("SELECT 1 FROM run_events WHERE type="
                     "'artifact.missing_detected'")
        assert len(n) == 1, "并发 GET 不得重复发 missing 事件"
    asyncio.run(scenario())


# ── S2：cancel 先提交则 resume 不得无声复活 ─────────────────────────

def test_cancel_committed_first_resume_rejected(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await asm.recovery.reconcile()  # interrupted
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "interrupted"

        gate = asyncio.Event()
        real_lock_for = asm.sessions.lock_for

        async def gated_lock_for(conversation_id):
            await gate.wait()  # 放大 resume 的"读→锁"窗口
            return await real_lock_for(conversation_id)

        asm.sessions.lock_for = gated_lock_for
        resume_task = asyncio.ensure_future(
            asm.recovery.resume(task_id, None))
        await asyncio.sleep(0.1)  # resume 已过锁外阶段、挂在锁前
        await asm.run_manager.request_cancel(task_id)  # cancel 先拿锁提交
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "cancelled"
        gate.set()
        with pytest.raises(SessionError) as e:
            await resume_task
        assert e.value.code.value == "INVALID_TRANSITION", \
            "锁内重读应见 cancelled（旧代码用陈旧读继续发 run.resumed）"
        assert asm.rows("SELECT 1 FROM run_events WHERE task_run_id=?"
                        " AND type='run.resumed'", (task_id,)) == []
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "cancelled", "不得复活开跑"
    asyncio.run(scenario())


# ── S5：resume 与 initial 同口径注入会话历史 ────────────────────────

def test_resume_includes_conversation_history(asm: Assembly, monkeypatch):
    async def scenario():
        # 同会话先完成一个任务（历史交换）
        created = await asm.sessions.create_conversation(
            instruction="旧任务", client_request_id="h1")
        conv_id = created["conversation"]["id"]
        old_id = created["task_run_id"]
        await asm.store.append(
            task_run_id=old_id, conversation_id=conv_id,
            type=T.RUN_STARTED,
            payload={"attempt_no": 1, "attempt_id": "att-old",
                     "kind": "initial"})
        await asm.store.append(
            task_run_id=old_id, conversation_id=conv_id,
            type=T.RUN_COMPLETED,
            payload={"final_text": "旧回复", "outcome": "completed"})

        sent = await asm.sessions.send_instruction(conv_id, "中断任务")
        task_id = sent["task_run_id"]
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.RUN_STARTED,
            payload={"attempt_no": 1, "attempt_id": "att-new",
                     "kind": "initial"})
        await asm.recovery.reconcile()  # interrupted
        await asm.recovery.resume(task_id, None)

        captured: dict = {}

        async def fake_run_task(messages, deps):
            captured["messages"] = messages
            return LoopResult("completed", final_text="done")

        monkeypatch.setattr("agentcrew_server.run_manager.run_task",
                            fake_run_task)
        asm.run_manager._loop = asyncio.get_running_loop()
        fut = _spawn(asm, task_id, conv_id, resume=True)
        await _wait_done(fut)

        texts = []
        for m in captured["messages"]:
            for block in m["content"]:
                if block.get("type") == "text":
                    texts.append(block["text"])
        assert texts[0] == "旧任务" and texts[1] == "旧回复", \
            f"resume 首部必须注入同会话历史（与 initial 同口径）：{texts}"
        assert texts[2] == "中断任务"
        asm.run_manager._runs.pop(task_id, None)
    asyncio.run(scenario())


# ── K1：resume 非 409 路径返回重放 warnings ─────────────────────────

def test_resume_returns_replay_warnings(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.LLM_REQUEST_DONE,
            payload={"llm_call_id": "l1", "step_id": "s1",
                     "prompt_tokens": 1, "completion_tokens": 1,
                     "latency_ms": 1, "text": "",
                     "tool_uses": [{"id": "b1", "name": "bash",
                                    "input": {"command": "cat big"}}],
                     "thinking_blocks": [], "stop_reason": "tool_use"})
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.TOOL_COMPLETED,
            payload={"call_id": "b1",
                     "output": "[输出超限，已外部化] /nonexistent/b1.out",
                     "artifact_path": "/nonexistent/b1.out"})
        await asm.recovery.reconcile()  # interrupted（无 dispatched）

        out = await asm.recovery.resume(task_id, None)
        assert out["warnings"] and any("工件缺失" in w
                                       for w in out["warnings"]), \
            "非 409 路径的降级告警必须返回调用方（不止进日志）"
    asyncio.run(scenario())


# ── K4：结清豁免 read_only 工具 ─────────────────────────────────────

def test_settle_exempts_read_only_tools(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await _dispatched_call(
            asm, conv_id, task_id, tool="read_file", effect="verifiable",
            call_id="call-r1", input_obj={"path": "a.txt"})
        summary = await asm.recovery.reconcile()

        assert summary["pending_verification"] == 0
        assert asm.read("SELECT status FROM tool_calls WHERE"
                        " call_id='call-r1'")[0] == "dispatched", \
            "纯读工具不转待核验（重放占位=结果未记录即可）"
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "interrupted", \
            "无待核验闸 → 可直接 resume"

        # 运行中终态结清路径同一豁免；非只读仍转待核验（防豁免过宽）
        await _dispatched_call(asm, conv_id, task_id, tool="bash",
                               call_id="call-b2")
        await asm.run_manager._settle_dispatched(task_id, conv_id)
        assert asm.read("SELECT status FROM tool_calls WHERE"
                        " call_id='call-r1'")[0] == "dispatched"
        assert asm.read("SELECT status FROM tool_calls WHERE"
                        " call_id='call-b2'")[0] == "pending_verification"
    asyncio.run(scenario())


# ── K5：写通道 busy 重试不得重复发布已回滚事件 ──────────────────────

def test_busy_retry_does_not_republish(asm: Assembly, monkeypatch):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await _dispatched_call(asm, conv_id, task_id, call_id="call-b1")
        await asm.recovery.reconcile()

        published: list = []
        asm.store.publish = published.append
        # 事件已 append 进 events 列表后、COMMIT 前 busy（如审计写入时）：
        # 事务整体回滚、写通道退避重试——重试前必须清空旧 events
        real_audit = recovery_module.append_audit
        calls = {"n": 0}

        def flaky_audit(conn, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return real_audit(conn, **kwargs)

        monkeypatch.setattr(recovery_module, "append_audit", flaky_audit)
        result = await asm.recovery.submit_verification(
            "call-b1", "confirmed_executed", None)
        assert result["status"] == "completed" and calls["n"] == 2
        assert len(published) == 1, \
            f"回滚路径必须清空 events（旧实现把回滚事件一并发布）：{published}"
        assert published[0].payload["verdict"] == "confirmed_executed"
    asyncio.run(scenario())
