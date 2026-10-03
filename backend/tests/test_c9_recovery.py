"""C9 装配级测试（真库真事件，不碰 GLM——kill -9 端到端在
scripts/recovery/c9_demo.py，ADR-006）：启动对账（口径/豁免/verifiable
自动核验/不双中断）、核验提交（事件+审计链/409）、resume 校验（409 带
清单/损坏行/成功发 run.resumed）、artifacts 惰性探测、取消 interrupted
任务（reducer idle 合法 + FSM replay 不死）、投影 rebuild 一致性。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn

from agentcrew_core.events import RunEventType as T
from agentcrew_core.events.reducer import INITIAL_STATE, reduce
from agentcrew_core.tools import ToolInvocation, ToolScheduler, build_default_registry
from agentcrew_server.api.app import create_app
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.bus import EventBus
from agentcrew_server.config import load_config
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.projections import rebuild_projections
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.questions import QuestionService
from agentcrew_server.recovery import RecoveryService
from agentcrew_server.run_manager import RunManager
from agentcrew_server.runtime import RuntimeState
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
        instruction=instruction, client_request_id="c9")
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


# ── 启动对账 ───────────────────────────────────────────────────────

def test_reconcile_running_task_with_dispatched_bash(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await _dispatched_call(asm, conv_id, task_id, tool="bash",
                               effect="outcome_unknown")

        summary = await asm.recovery.reconcile()

        assert summary["tasks"] == 1 and summary["pending_verification"] == 1
        assert asm.read("SELECT status FROM tool_calls WHERE call_id='call-b1'"
                        )[0] == "pending_verification"
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "waiting_verification", \
            "待核验存在 → waiting_verification（先 interrupt 后结清的事件序）"
        assert asm.read("SELECT type FROM run_events WHERE task_run_id=?"
                        " AND type='run.interrupted'", (task_id,)) is not None
    asyncio.run(scenario())


def test_reconcile_pending_flip_to_waiting_verification(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await _dispatched_call(asm, conv_id, task_id)
        await asm.recovery.reconcile()
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "waiting_verification"
        # 会话 FSM replay 存活（run.interrupted 收敛回 idle）
        fsm = asm.sessions.sync_fsm(conv_id)
        assert fsm.state == "idle"
    asyncio.run(scenario())


def test_reconcile_covers_queued_leftover(asm: Assembly):
    """C8 外审 F1 口径：优雅关闭把名额等待中的 queued 任务留下——
    只扫 running/waiting_user 会永久卡死会话；queued 也必须对账。"""
    async def scenario():
        created = await asm.sessions.create_conversation(
            instruction="排队任务", client_request_id="q1")
        task_id = created["task_run_id"]
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "queued"
        await asm.recovery.reconcile()
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "interrupted"
        fsm = asm.sessions.sync_fsm(created["conversation"]["id"])
        assert fsm.state == "idle", "starting→run.interrupted→idle 合法"
    asyncio.run(scenario())


def test_reconcile_ask_user_exempt_stays_dispatched(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await _dispatched_call(asm, conv_id, task_id, tool="ask_user",
                               effect="verifiable", call_id="call-q")
        await asm.recovery.reconcile()
        assert asm.read("SELECT status FROM tool_calls WHERE"
                        " call_id='call-q'")[0] == "dispatched", \
            "§6.2 v1.8 豁免：账本停 dispatched，不转待核验"
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "interrupted", \
            "无待核验 → 任务停在 interrupted（可直接 resume）"
    asyncio.run(scenario())


def test_reconcile_write_file_hash_match_auto_completes(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        ws = asm.sessions.scope_of(
            asm.sessions.conversation_or_404(conv_id))["workspace_dir"]
        target = Path(ws) / "report.md"
        content = "C9 已写入"
        sha = hashlib.sha256(content.encode()).hexdigest()
        await _dispatched_call(
            asm, conv_id, task_id, tool="write_file", effect="verifiable",
            call_id="call-w1",
            input_obj={"path": "report.md", "content": content},
            extra_prepared={"content_sha256": sha})
        # 文件确实已落盘（kill 在 rename 之后、事件之前）
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        summary = await asm.recovery.reconcile()

        assert summary["auto_completed"] == 1
        assert asm.read("SELECT status FROM tool_calls WHERE"
                        " call_id='call-w1'")[0] == "completed"
        assert asm.read("SELECT artifact_path FROM tool_calls WHERE"
                        " call_id='call-w1'")[0] == str(target)
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "interrupted", \
            "自动补完 → 无待核验 → 可直接 resume"
    asyncio.run(scenario())


def test_reconcile_write_file_hash_mismatch_goes_pending(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        ws = asm.sessions.scope_of(
            asm.sessions.conversation_or_404(conv_id))["workspace_dir"]
        target = Path(ws) / "report.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("被改过的内容", encoding="utf-8")
        await _dispatched_call(
            asm, conv_id, task_id, tool="write_file", effect="verifiable",
            call_id="call-w1",
            input_obj={"path": "report.md", "content": "原始内容"},
            extra_prepared={"content_sha256": "0" * 64})
        await asm.recovery.reconcile()
        assert asm.read("SELECT status FROM tool_calls WHERE"
                        " call_id='call-w1'")[0] == "pending_verification"
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "waiting_verification"
    asyncio.run(scenario())


def test_reconcile_no_double_interrupt_for_waiting_verification(asm):
    """二连崩：已对账过的 waiting_verification 任务重启不得再补
    run.interrupted（FSM 已 idle，再来一条 = InvalidTransition 打死会话）。"""
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await _dispatched_call(asm, conv_id, task_id)
        await asm.recovery.reconcile()
        assert asm.rows("SELECT 1 FROM run_events WHERE task_run_id=?"
                        " AND type='run.interrupted'", (task_id,)) != []
        await asm.recovery.reconcile()  # 模拟第二次重启
        count = asm.rows("SELECT 1 FROM run_events WHERE task_run_id=?"
                         " AND type='run.interrupted'", (task_id,))
        assert len(count) == 1, "不重复补中断"
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "waiting_verification"
        assert asm.sessions.sync_fsm(conv_id).state == "idle"
    asyncio.run(scenario())


def test_projections_rebuild_identity_after_reconciliation(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await _dispatched_call(asm, conv_id, task_id, tool="bash")
        await _dispatched_call(asm, conv_id, task_id, tool="write_file",
                               effect="verifiable", call_id="call-w2",
                               input_obj={"path": "x.md", "content": "X"},
                               extra_prepared={"content_sha256": "1" * 64})
        await asm.recovery.reconcile()
        await asm.recovery.submit_verification(
            "call-b1", "confirmed_not_executed", None)
        before = asm.rows("SELECT id, status FROM task_runs")
        before_calls = asm.rows(
            "SELECT call_id, status, completed_at, output_summary"
            " FROM tool_calls ORDER BY call_id")
        rebuild_projections(asm.db.write_conn)
        assert asm.rows("SELECT id, status FROM task_runs") == before, \
            "waiting_verification 是事件派生态（rebuild 稳定）"
        assert asm.rows("SELECT call_id, status, completed_at, output_summary"
                        " FROM tool_calls ORDER BY call_id") == before_calls
    asyncio.run(scenario())


# ── 核验提交（v1.4 F003）───────────────────────────────────────────

def test_submit_verification_outcomes_and_audit(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await _dispatched_call(asm, conv_id, task_id, tool="bash",
                               call_id="call-b1")
        await asm.recovery.reconcile()

        result = await asm.recovery.submit_verification(
            "call-b1", "confirmed_not_executed", "查了，没有执行")
        assert result["status"] == "not_executed"
        row = asm.read("SELECT status, completed_at, output_summary"
                       " FROM tool_calls WHERE call_id='call-b1'")
        assert row[0] == "not_executed" and row[1] is not None
        assert "confirmed_not_executed" in row[2]
        ev = asm.read("SELECT payload FROM run_events WHERE type="
                      "'tool.verification_submitted'")
        p = json.loads(ev[0])
        assert p["verdict"] == "confirmed_not_executed" and p["note"]
        audit = asm.read("SELECT actor_type, action FROM audit_log"
                         " WHERE action LIKE 'tool.verification%'")
        assert audit is not None, "审计链成对"

        # 再提交（已非待核验态）→ 409
        with pytest.raises(SessionError) as e:
            await asm.recovery.submit_verification(
                "call-b1", "confirmed_executed", None)
        assert e.value.code.value == "INVALID_TRANSITION"

        # confirmed_executed 路径
        await _dispatched_call(asm, conv_id, task_id, tool="bash",
                               call_id="call-b2")
        await asm.recovery.reconcile()
        result = await asm.recovery.submit_verification(
            "call-b2", "confirmed_executed", None)
        assert result["status"] == "completed"
        assert asm.read("SELECT status FROM tool_calls WHERE"
                        " call_id='call-b2'")[0] == "completed"
    asyncio.run(scenario())


def test_pending_verifications_listing_with_evidence(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await _dispatched_call(asm, conv_id, task_id, tool="bash",
                               input_obj={"command": "sleep 30"})
        await _dispatched_call(
            asm, conv_id, task_id, tool="write_file", effect="verifiable",
            call_id="call-w1",
            input_obj={"path": "g.md", "content": "G"},
            extra_prepared={"content_sha256": "2" * 64})
        # g.md 不存在 → verifiable 核验失败 → 待核验，证据写明"不存在"
        await asm.recovery.reconcile()

        listing = asm.recovery.pending_verifications(conv_id)
        by_id = {item["call_id"]: item for item in listing}
        assert set(by_id) == {"call-b1", "call-w1"}
        b = by_id["call-b1"]
        assert b["tool"] == "bash" and b["input"] == {"command": "sleep 30"}
        assert b["dispatched_at"] is not None
        assert "outcome_unknown" in b["evidence"]
        assert "不存在" in by_id["call-w1"]["evidence"], \
            "verifiable 类核验结果写进证据"
    asyncio.run(scenario())


# ── resume 校验 ───────────────────────────────────────────────────

def test_resume_blocked_by_pending_verification_with_list(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await _dispatched_call(asm, conv_id, task_id, tool="bash")
        await asm.recovery.reconcile()
        with pytest.raises(SessionError) as e:
            await asm.recovery.resume(task_id, None)
        assert e.value.code.value == "PENDING_VERIFICATION"
        assert e.value.detail["pending_verifications"][0]["call_id"] \
            == "call-b1", "409 带清单"
    asyncio.run(scenario())


def test_resume_blocked_by_corrupt_event_row(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await asm.recovery.reconcile()  # → interrupted（无待核验）
        # 直接写一行损坏载荷（模拟库/盘损坏）
        asm.db.write_conn.execute(
            "INSERT INTO run_events (id, task_run_id, seq, conversation_id,"
            " attempt_no, type, payload, created_at)"
            " VALUES ('bad', ?, 99, ?, 1, 'llm.request_done', '{broken',"
            " '2026-01-01')", (task_id, conv_id))
        asm.db.write_conn.commit()
        with pytest.raises(SessionError) as e:
            await asm.recovery.resume(task_id, None)
        assert e.value.code.value == "REPLAY_CORRUPT"
        assert any("损坏" in w for w in e.value.detail["warnings"])
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "interrupted", "任务不动，交人工"
    asyncio.run(scenario())


def test_resume_http_rejects_modified_and_missing_large_artifact(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        source = asm.data_dir / "workspaces" / "default" / "large.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("真实恢复工件内容。" * 8000, encoding="utf-8")
        context = asm.sessions.build_work_context(conv_id, task_id)
        result = await asm.approvals.run_tool(task_run_id=task_id,
            conversation_id=conv_id, agent_id="default",
            invocation=ToolInvocation("large-artifact-07", "read_file",
                                      {"path": str(source)}), ctx=context)
        assert result.ok and result.artifact_path
        artifact = Path(result.artifact_path)
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == result.details["sha256"]
        await asm.store.append(task_run_id=task_id, conversation_id=conv_id,
                               type=T.RUN_INTERRUPTED, payload={"reason": "进程实际停止后等待恢复"})

        runtime = RuntimeState(log=logging.getLogger("test.artifact.resume"),
            data_dir=asm.data_dir, db=asm.db, write_channel=asm.channel,
            token="real-http-artifact-test", bus=asm.bus, event_store=asm.store,
            approvals=asm.approvals, scheduler=asm.approvals.scheduler,
            settings=asm.settings, sessions=asm.sessions, questions=asm.questions,
            run_manager=asm.run_manager, recovery=asm.recovery)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(runtime), host="127.0.0.1",
                                               port=port, log_level="error"))
        running = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.05)
            assert server.started
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                    headers={"Authorization": "Bearer real-http-artifact-test"}) as client:
                artifact.write_text("外部修改", encoding="utf-8")
                modified = await client.post(f"/api/task-runs/{task_id}/resume")
                assert modified.status_code == 409
                assert modified.json()["error"]["code"] == "REPLAY_CORRUPT"
                assert modified.json()["error"]["detail"]["reason"] == "ARTIFACT_HASH_MISMATCH"
                artifact.unlink()
                missing = await client.post(f"/api/task-runs/{task_id}/resume")
                assert missing.status_code == 409
                assert missing.json()["error"]["code"] == "REPLAY_CORRUPT"
                assert missing.json()["error"]["detail"]["reason"] == "ARTIFACT_MISSING"
                assert asm.read("SELECT status FROM task_runs WHERE id=?", (task_id,))[0] == "interrupted"
        finally:
            server.should_exit = True
            await running
            listener.close()

    asyncio.run(scenario())


def test_resume_rejects_wrong_status(asm: Assembly):
    async def scenario():
        created = await asm.sessions.create_conversation(
            instruction="跑着的任务", client_request_id="r1")
        task_id = created["task_run_id"]  # queued
        with pytest.raises(SessionError) as e:
            await asm.recovery.resume(task_id, None)
        assert e.value.code.value == "INVALID_TRANSITION"
    asyncio.run(scenario())


def test_resume_success_emits_run_resumed_with_fingerprint(asm: Assembly):
    """校验通过 → run.resumed（attempt_no=2、kind=resume、指纹落
    run_attempts）。runner 的 GLM 环节不在单测（c9_demo 真实验收）。"""
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.QUESTION_REQUESTED, payload={"request_id": "q1",
                                                "question": "继续？"})
        await asm.recovery.reconcile()  # → interrupted（ask_user 豁免无待核验）

        await asm.recovery.resume(task_id, "user_requested")

        ev = asm.read("SELECT payload, attempt_no FROM run_events"
                      " WHERE type='run.resumed' AND task_run_id=?",
                      (task_id,))
        p = json.loads(ev[0])
        assert p["attempt_no"] == 2 and p["resume_reason"] == "user_requested"
        assert p["context_fingerprint"]["system_prompt_hash"]
        att = asm.read("SELECT attempt_no, kind, resume_reason, status,"
                       " context_fingerprint FROM run_attempts"
                       " WHERE task_run_id=? AND attempt_no=2", (task_id,))
        assert att[1] == "resume" and json.loads(att[4])["model_config"]
        assert tuple(asm.read("SELECT status, current_attempt_no FROM task_runs"
                              " WHERE id=?", (task_id,))) == ("running", 2)
    asyncio.run(scenario())


# ── artifacts 惰性探测 ─────────────────────────────────────────────

def test_artifacts_lazy_missing_detection(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        path = asm.data_dir / "artifacts" / task_id / "x.out"
        path.parent.mkdir(parents=True)
        path.write_text("ok", encoding="utf-8")
        # 工件行的外键父：tool_calls 先落一行（真实流程由 write_file 产生）
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
        items = await asm.recovery.list_artifacts(conv_id, None)
        assert items[0]["status"] == "ready"

        path.unlink()  # 文件消失
        items = await asm.recovery.list_artifacts(conv_id, None)
        assert items[0]["status"] == "missing"
        assert asm.read("SELECT type FROM run_events WHERE type="
                        "'artifact.missing_detected'") is not None
        # 再列：已 missing，不再重复发事件
        await asm.recovery.list_artifacts(conv_id, None)
        assert len(asm.rows("SELECT 1 FROM run_events WHERE type="
                            "'artifact.missing_detected'")) == 1
    asyncio.run(scenario())


# ── 取消 interrupted / waiting_verification 任务 ───────────────────

def test_cancel_interrupted_task_lands_cancelled(asm: Assembly):
    async def scenario():
        conv_id, task_id = await _running_task(asm)
        await asm.recovery.reconcile()
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "interrupted"
        result = await asm.run_manager.request_cancel(task_id)
        assert result["status"] == "cancelled"
        assert asm.read("SELECT status FROM task_runs WHERE id=?",
                        (task_id,))[0] == "cancelled"
        assert asm.sessions.sync_fsm(conv_id).state == "idle", \
            "idle 下 run.cancelled 合法（C9 reducer 扩展）——replay 不死"
    asyncio.run(scenario())


def test_reducer_accepts_cancel_from_idle_after_c9():
    from agentcrew_core.events import Event

    def ev(etype, payload=None, task_run_id="t"):
        return Event(global_seq=1, id="e", task_run_id=task_run_id, seq=1,
                     conversation_id="c", type=etype,
                     payload=payload or {}, attempt_no=None, ts="2026-01-01")

    state = reduce(INITIAL_STATE, ev(T.RUN_QUEUED))
    state = reduce(state, ev(T.RUN_INTERRUPTED))
    assert state.state == "idle"
    # idle + 空队列：run.cancelled 合法且保持 idle
    assert reduce(state, ev(T.RUN_CANCELLED)).state == "idle"
    # idle + 有排队项：取消同样触发队列暂停（用户显式停止语义一致）
    state2 = reduce(state, ev(T.QUEUE_ITEM_ENQUEUED,
                              {"item_id": "i1", "text": "x"},
                              task_run_id=None))
    assert reduce(state2, ev(T.RUN_CANCELLED)).queue_paused is True
