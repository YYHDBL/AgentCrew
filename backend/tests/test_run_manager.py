"""RunManager/QuestionService 集成测试（M0-C8）：真库真事件，不碰 GLM
（GLM 端到端在 scripts/loop/c8_demo.py，ADR-006）。

覆盖：auto_dequeue_next（终态接续/暂停不接续/空队列/与用户直发竞态）、
request_cancel 无 runner 路径（queued 任务直接落 run.cancelled 且队列暂停）、
question.* 投影往返翻转 task_runs.waiting_user、run.started 的
context_fingerprint 落 run_attempts、提问回答链（live 唤醒 / 孤儿补答 /
重复提交 409 / answer=null）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agentcrew_core.events import RunEventType as T
from agentcrew_core.tools import ToolScheduler, build_default_registry
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.bus import EventBus
from agentcrew_server.config import load_config
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.questions import (
    QuestionNotFound,
    QuestionService,
    QuestionStale,
)
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


async def _make_running_task(asm: Assembly) -> tuple[str, str]:
    """真实创建会话（首任务 queued）→ 追加 run.started 进入 running。"""
    created = await asm.sessions.create_conversation(
        instruction="任务一", client_request_id="c1")
    conv_id = created["conversation"]["id"]
    task_id = created["task_run_id"]
    await asm.store.append(
        task_run_id=task_id, conversation_id=conv_id,
        type=T.RUN_STARTED,
        payload={"attempt_no": 1, "attempt_id": "att-1", "kind": "initial"})
    return conv_id, task_id


# ── auto_dequeue_next（§4 接续规则）─────────────────────────────────

def test_auto_dequeue_after_terminal(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _make_running_task(asm)
        queued = await asm.sessions.send_instruction(conv_id, "排队指令")
        assert queued["mode"] == "queued"
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.RUN_COMPLETED, payload={"final_text": "完成"})
        next_id = await asm.sessions.auto_dequeue_next(conv_id)
        assert next_id is not None
        status = asm.read(
            "SELECT status, instruction FROM task_runs WHERE id=?", (next_id,))
        assert status[0] == "queued" and status[1] == "排队指令"
        item = json.loads(asm.read(
            "SELECT pending_queue FROM conversations WHERE id=?",
            (conv_id,))[0])
        assert item[0]["state"] == "dequeued"
    asyncio.run(scenario())


def test_auto_dequeue_skipped_when_paused_or_busy(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _make_running_task(asm)
        await asm.sessions.send_instruction(conv_id, "排队指令")
        # cancelled → reducer 置 queue_paused：不接续
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.RUN_CANCELLED, payload={})
        assert await asm.sessions.auto_dequeue_next(conv_id) is None
        # 队列空：不接续
        conv2 = await asm.sessions.create_conversation(instruction="独立任务")
        assert await asm.sessions.auto_dequeue_next(
            conv2["conversation"]["id"]) is None
    asyncio.run(scenario())


def test_auto_dequeue_race_with_direct_send(asm: Assembly):
    """用户直发抢先占用 idle → 自动接续让位（返回 None，队列保序）。"""
    async def scenario():
        conv_id, task_id = await _make_running_task(asm)
        await asm.sessions.send_instruction(conv_id, "排队指令")
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.RUN_COMPLETED, payload={"final_text": "完成"})
        # 用户抢先直发新任务（FSM 离开 idle）
        await asm.sessions.send_instruction(conv_id, "用户插队指令")
        assert await asm.sessions.auto_dequeue_next(conv_id) is None
    asyncio.run(scenario())


# ── request_cancel（无 runner：queued 任务直接落终态）───────────────

def test_request_cancel_without_runner(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _make_running_task(asm)
        await asm.sessions.send_instruction(conv_id, "排队指令")
        result = await asm.run_manager.request_cancel(task_id)
        assert result["status"] == "cancelled"
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "cancelled"
        # reducer 语义：队列非空 → queue_paused=1
        assert asm.read("SELECT queue_paused FROM conversations WHERE id=?",
                        (conv_id,))[0] == 1
        # 终态后重复取消：幂等 no-op（不再发事件，FSM 不炸）
        again = await asm.run_manager.request_cancel(task_id)
        assert again["already_terminal"] is True
        events = asm.read(
            "SELECT count(*) FROM run_events WHERE type=? AND task_run_id=?",
            ("run.cancelled", task_id))[0]
        assert events == 1
    asyncio.run(scenario())


def test_request_cancel_unknown_task(asm: Assembly):
    with pytest.raises(LookupError):
        asyncio.run(asm.run_manager.request_cancel("no-such-task"))


# ── question.* 投影：task_runs waiting_user 派生态 ──────────────────

def test_question_projection_flips_waiting_user(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _make_running_task(asm)
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.QUESTION_REQUESTED,
            payload={"request_id": "q1", "question": "用哪个版本？",
                     "options": []})
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "waiting_user"
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.QUESTION_ANSWERED,
            payload={"request_id": "q1", "answer": "v2"})
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "running"
    asyncio.run(scenario())


# ── run.started 的 context_fingerprint 落 run_attempts ──────────────

def test_attempt_fingerprint_persisted(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _make_running_task(asm)
        # resume = 新 attempt（run.resumed 事件，kind 由事件类型决定）
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.RUN_RESUMED,
            payload={"attempt_no": 2, "attempt_id": "att-2",
                     "resume_reason": "crash",
                     "context_fingerprint": {"system_prompt_hash": "h1",
                                             "tools_schema_hash": "h2",
                                             "model_config": {"model": "glm"}}})
        row = asm.read(
            "SELECT kind,"
            " json_extract(context_fingerprint, '$.model_config.model')"
            " FROM run_attempts WHERE task_run_id=? AND attempt_no=2",
            (task_id,))
        assert tuple(row) == ("resume", "glm")
    asyncio.run(scenario())


# ── 提问回答链（QuestionService）───────────────────────────────────

def test_question_submit_wakes_live_future(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _make_running_task(asm)
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.QUESTION_REQUESTED,
            payload={"request_id": "q1", "question": "问题", "options": ["A"]})
        resolver = asm.questions.make_resolver(task_id, conv_id)
        waiting = asyncio.ensure_future(resolver("q1"))
        await asyncio.sleep(0)  # 让 resolver 注册 future
        result = await asm.questions.submit("q1", "回答A")
        assert result["request_id"] == "q1"
        assert await asyncio.wait_for(waiting, timeout=2) == "回答A"
        # live 路径的 answered 事件由 _ask_user 发射（本用例不经工具，不落事件）
        assert asm.read("SELECT count(*) FROM run_events WHERE type=?",
                        ("question.answered",))[0] == 0
    asyncio.run(scenario())


def test_question_answer_null_reaches_resolver_as_none(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _make_running_task(asm)
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.QUESTION_REQUESTED,
            payload={"request_id": "q2", "question": "可跳过吗", "options": []})
        resolver = asm.questions.make_resolver(task_id, conv_id)
        waiting = asyncio.ensure_future(resolver("q2"))
        await asyncio.sleep(0)
        await asm.questions.submit("q2", None)  # answer=null = 用户取消回答
        assert await asyncio.wait_for(waiting, timeout=2) is None
    asyncio.run(scenario())


def test_question_submit_orphan_appends_event(asm: Assembly):
    async def scenario():
        """无 live future（重启后孤儿提问）：submit 直接落 question.answered。"""
        conv_id, task_id = await _make_running_task(asm)
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.QUESTION_REQUESTED,
            payload={"request_id": "q3", "question": "孤儿", "options": []})
        assert len(asm.questions.pending_questions(conv_id)) == 1
        await asm.questions.submit("q3", "补答")
        row = asm.read(
            "SELECT json_extract(payload, '$.answer') FROM run_events"
            " WHERE type='question.answered'")
        assert row[0] == "补答"
        assert asm.questions.pending_questions(conv_id) == []
        # 已回答（无 live）→ 再提交 409 QUESTION_STALE
        with pytest.raises(QuestionStale):
            await asm.questions.submit("q3", "再答")
    asyncio.run(scenario())


def test_question_submit_unknown(asm: Assembly):
    with pytest.raises(QuestionNotFound):
        asyncio.run(asm.questions.submit("nope", "x"))


def test_pending_questions_excludes_terminal_tasks(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _make_running_task(asm)
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.QUESTION_REQUESTED,
            payload={"request_id": "q4", "question": "会被取消吗",
                     "options": []})
        assert len(asm.questions.pending_questions(conv_id)) == 1
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.RUN_CANCELLED, payload={})
        # 任务终态后：孤儿提问不再出现在可回答列表
        assert asm.questions.pending_questions(conv_id) == []
    asyncio.run(scenario())
