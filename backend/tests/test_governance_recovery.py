"""M2-09：真实副作用停止、当前授权核验及恢复状态。"""

import asyncio
import hashlib
from pathlib import Path

import pytest

from agentcrew_core.governance import RequestIdentity
from agentcrew_core.tools import ToolInvocation, build_default_registry
from agentcrew_server.questions import QuestionService
from agentcrew_server.recovery import RecoveryService
from agentcrew_server.run_manager import RunManager
from agentcrew_server.sessions import SessionError
from agentcrew_core.events import RunEventType as T
from agentcrew_core.tools.scheduler import input_hash
from agentcrew_server.bus import EventBus, Topic
from agentcrew_server.memory.jobs import MemoryJobs
from agentcrew_server.memory.store import MemoryStore
from agentcrew_server.memory.store import MemoryIdentity
from test_governance_rules import authorized, request_card
from test_governance_skills import governed
from test_governance_resources import resources


@pytest.fixture
def stopped_effect(authorized):
    service, sessions, approvals, context, created = authorized
    marker = Path(context.cwd) / "recovery-effect.txt"
    delayed = Path(context.cwd) / "recovery-delayed.txt"
    async def prepare():
        invocation = ToolInvocation("recovery-actual-bash", "bash", {"command": "printf '实际中断效果' > recovery-effect.txt; sleep 30; printf '禁止迟到效果' > recovery-delayed.txt"})
        task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen", invocation=invocation, ctx=context))
        card = await request_card(service, invocation.call_id)
        await approvals.submit(invocation.call_id, "allow_once", card["input_hash"], RequestIdentity("owner", "owner"))
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(0.01)
        assert marker.read_text() == "实际中断效果"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not delayed.exists()
    asyncio.run(prepare())
    recovery = RecoveryService(service.db, service.events, sessions, service.data_dir, registry=build_default_registry())
    recovery.identities = sessions.identities
    manager = RunManager(db=service.db, event_store=service.events, bus=None, settings=sessions._settings, sessions=sessions,
        approvals=approvals, scheduler=approvals.scheduler, questions=QuestionService(service.db, service.events))
    manager.grants = approvals.grants
    manager.wire(recovery)
    recovery.wire(manager)
    signature = {"sha256": hashlib.sha256(marker.read_bytes()).hexdigest(), "mtime_ns": marker.stat().st_mtime_ns}
    yield service, sessions, recovery, manager, created, marker, signature


def test_unknown_effect_preserves_recoverable_waiting_state(stopped_effect):
    service, sessions, recovery, manager, created, marker, signature = stopped_effect
    async def check():
        await manager._finish(created["task_run_id"], created["conversation"]["id"], "run.failed", {"reason": "AUTHORIZATION_REVOKED"}, attempt_no=1)
        status = service.db.read_conn.execute("SELECT status FROM task_runs WHERE id=?", (created["task_run_id"],)).fetchone()[0]
        assert status == "waiting_verification"
        assert signature == {"sha256": hashlib.sha256(marker.read_bytes()).hexdigest(), "mtime_ns": marker.stat().st_mtime_ns}
        assert recovery.pending_verifications(created["conversation"]["id"])[0]["call_id"] == "recovery-actual-bash"
    asyncio.run(check())


def test_pending_effect_blocks_following_conversation_instruction(stopped_effect):
    service, sessions, recovery, manager, created, marker, signature = stopped_effect
    async def check():
        await manager._finish(created["task_run_id"], created["conversation"]["id"], "run.failed", {"reason": "中断效果需核验"}, attempt_no=1)
        with pytest.raises(SessionError) as raised:
            await sessions.send_instruction(created["conversation"]["id"], "核验之前禁止新派发", request_identity=RequestIdentity("owner", "owner"))
        assert raised.value.code.value == "PENDING_VERIFICATION"
    asyncio.run(check())


def test_final_verification_restores_interrupted_state(stopped_effect):
    service, sessions, recovery, manager, created, marker, signature = stopped_effect
    async def check():
        await manager._finish(created["task_run_id"], created["conversation"]["id"], "run.failed", {"reason": "效果核验"}, attempt_no=1)
        verified = await recovery.submit_verification("recovery-actual-bash", "confirmed_executed", "实际核查文件及已退出子进程", RequestIdentity("owner", "owner"))
        assert verified["status"] == "completed"
        assert service.db.read_conn.execute("SELECT status FROM task_runs WHERE id=?", (created["task_run_id"],)).fetchone()[0] == "interrupted"
        assert signature == {"sha256": hashlib.sha256(marker.read_bytes()).hexdigest(), "mtime_ns": marker.stat().st_mtime_ns}
    asyncio.run(check())


def test_pending_effect_blocks_automatic_queue_dequeue(stopped_effect):
    service, sessions, recovery, manager, created, marker, signature = stopped_effect
    async def check():
        queued = await sessions.send_instruction(created["conversation"]["id"], "真实排队项等待核验", request_identity=RequestIdentity("owner", "owner"))
        assert queued["mode"] == "queued"
        await manager._finish(created["task_run_id"], created["conversation"]["id"], "run.failed", {"reason": "队列等待效果核验"}, attempt_no=1)
        assert await sessions.auto_dequeue_next(created["conversation"]["id"]) is None
        assert sessions.state_snapshot(created["conversation"]["id"])["can_continue_queue"] is False
    asyncio.run(check())


def test_auto_verification_never_reads_current_scope_outside_target(authorized):
    service, sessions, approvals, context, created = authorized
    recovery = RecoveryService(service.db, service.events, sessions, service.data_dir, registry=build_default_registry())
    outside = service.data_dir / "outside-recovery.txt"
    outside.write_text("范围外核验目标")
    async def check():
        inputs = {"path": str(outside), "content": "范围外核验目标"}
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.TOOL_PREPARED,
            payload={"call_id": "outside-recovery", "tool_name": "write_file", "input": inputs, "input_hash": input_hash(inputs), "risk_level": "medium", "side_effect_class": "verifiable",
                "content_sha256": hashlib.sha256(outside.read_bytes()).hexdigest()})
        assert recovery._verify_write_file(created["task_run_id"], created["conversation"]["id"], "outside-recovery") is None
    asyncio.run(check())


def test_verification_evidence_respects_current_scope(authorized):
    service, sessions, approvals, context, created = authorized
    recovery = RecoveryService(service.db, service.events, sessions, service.data_dir, registry=build_default_registry())
    outside = service.data_dir / "outside-evidence.txt"
    outside.write_text("证据生成也需要范围授权")
    async def check():
        inputs = {"path": str(outside), "content": "证据生成也需要范围授权"}
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.TOOL_PREPARED,
            payload={"call_id": "outside-evidence", "tool_name": "write_file", "input": inputs, "input_hash": input_hash(inputs), "risk_level": "medium", "side_effect_class": "verifiable",
                "content_sha256": hashlib.sha256(outside.read_bytes()).hexdigest()})
        assert "合法范围" in recovery._evidence(created["task_run_id"], "outside-evidence", "write_file", "verifiable", inputs)
    asyncio.run(check())


def test_unverified_completion_keeps_background_subscription_alive(stopped_effect):
    service, sessions, recovery, manager, created, marker, signature = stopped_effect
    bus = EventBus()
    service.events.set_publisher(bus.publish)
    jobs = MemoryJobs(MemoryStore(service.db, service.events, service.data_dir, sessions._settings), bus, sessions._settings)
    async def check():
        jobs._sub = bus.subscribe(Topic("all"), internal=True)
        jobs._dispatch_task = asyncio.create_task(jobs._dispatch())
        await manager._finish(created["task_run_id"], created["conversation"]["id"], "run.completed", {"final_text": "实际执行已停止，效果仍需核验"}, attempt_no=1)
        await asyncio.sleep(0.1)
        try:
            assert not jobs._dispatch_task.done()
            assert service.db.read_conn.execute("SELECT count(*) FROM memory_jobs WHERE task_run_id=?", (created["task_run_id"],)).fetchone()[0] == 0
            await jobs.recover()
            await recovery.submit_verification("recovery-actual-bash", "confirmed_executed", "真实文件效果已经核验", RequestIdentity("owner", "owner"))
            for _ in range(100):
                if service.db.read_conn.execute("SELECT count(*) FROM memory_jobs WHERE task_run_id=?", (created["task_run_id"],)).fetchone()[0]:
                    break
                await asyncio.sleep(0.01)
            assert not jobs._dispatch_task.done()
            assert service.db.read_conn.execute("SELECT count(*) FROM memory_jobs WHERE task_run_id=? AND kind='summary'", (created["task_run_id"],)).fetchone()[0] == 1
            assert service.db.read_conn.execute("SELECT finished_at FROM task_runs WHERE id=?", (created["task_run_id"],)).fetchone()[0] is not None
            source = service.db.read_conn.execute("SELECT global_seq FROM run_events WHERE task_run_id=? AND type='run.completed'", (created["task_run_id"],)).fetchone()[0]
            await jobs.enqueue(source)
            assert service.db.read_conn.execute("SELECT count(*) FROM memory_jobs WHERE task_run_id=? AND kind='summary'", (created["task_run_id"],)).fetchone()[0] == 1
        finally:
            await jobs.shutdown()
    asyncio.run(check())


def test_old_attempt_approval_does_not_block_queue_control(authorized):
    service, sessions, approvals, context, created = authorized
    async def check():
        task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
            invocation=ToolInvocation("stale-queue-approval", "write_file", {"path": str(Path(context.cwd) / "stale.txt"), "content": "审批停止前不写入"}), ctx=context))
        await request_card(service, "stale-queue-approval")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.RUN_INTERRUPTED, payload={"reason": "实际等待方已取消"})
        assert sessions._unresolved_approvals(created["conversation"]["id"]) == []
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.RUN_RESUMED, attempt_no=2,
            payload={"attempt_no": 2, "attempt_id": "fresh-queue-attempt", "resume_reason": "user_requested"})
        assert sessions._unresolved_approvals(created["conversation"]["id"]) == []
    asyncio.run(check())


def test_result_persistence_failure_stops_execution_immediately(authorized):
    service, sessions, approvals, context, created = authorized
    target = Path(context.cwd) / "persisted-effect.txt"
    async def check():
        def reject(conn):
            with conn:
                conn.execute("CREATE TRIGGER reject_result BEFORE INSERT ON run_events "
                    "WHEN NEW.type='tool.completed' AND json_extract(NEW.payload,'$.call_id')='persist-failure' "
                    "BEGIN SELECT RAISE(ABORT,'实际SQLite结果写入拒绝'); END")
        await service.events.channel.execute(reject)
        task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
            invocation=ToolInvocation("persist-failure", "write_file", {"path": str(target), "content": "实际效果已发生"}), ctx=context))
        card = await request_card(service, "persist-failure")
        await approvals.submit("persist-failure", "allow_once", card["input_hash"], RequestIdentity("owner", "owner"))
        with pytest.raises(RuntimeError, match="EVENT_PERSIST_FAILED"):
            await task
        assert target.read_text() == "实际效果已发生"
        assert service.db.read_conn.execute("SELECT status FROM tool_calls WHERE call_id='persist-failure'").fetchone()[0] == "pending_verification"
        recovery = RecoveryService(service.db, service.events, sessions, service.data_dir, registry=build_default_registry())
        manager = RunManager(db=service.db, event_store=service.events, bus=None, settings=sessions._settings, sessions=sessions,
            approvals=approvals, scheduler=approvals.scheduler, questions=QuestionService(service.db, service.events))
        manager.wire(recovery)
        await manager._finish(created["task_run_id"], created["conversation"]["id"], "run.failed", {"reason": "EVENT_PERSIST_FAILED"}, attempt_no=1)
        assert service.db.read_conn.execute("SELECT status FROM task_runs WHERE id=?", (created["task_run_id"],)).fetchone()[0] == "waiting_verification"
    asyncio.run(check())


def test_runtime_settlement_uses_committed_skill_intent(authorized, governed):
    service, sessions, approvals, context, created = authorized
    _, memory, versions, _, skills = governed
    recovery = RecoveryService(service.db, service.events, sessions, service.data_dir, registry=build_default_registry())
    manager = RunManager(db=service.db, event_store=service.events, bus=None, settings=sessions._settings, sessions=sessions,
        approvals=approvals, scheduler=approvals.scheduler, questions=QuestionService(service.db, service.events))
    manager.grants = approvals.grants
    manager.wire(recovery)
    async def check():
        inputs = {"action": "edit", "name": "文档模板", "expected_revision": 1, "basis": "真实正文与意图核查", "text": "实际恢复核查完整正文。"}
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.TOOL_PREPARED,
            payload={"call_id": "committed-recovery-skill", "tool_name": "skill_patch", "input": inputs, "input_hash": input_hash(inputs), "risk_level": "medium", "side_effect_class": "verifiable"})
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.TOOL_DISPATCHED, payload={"call_id": "committed-recovery-skill"})
        result = await skills.change(MemoryIdentity("office", "xiaowen"), "文档模板", action="edit", change_id="committed-recovery-skill",
            expected_revision=1, basis=inputs["basis"], text=inputs["text"])
        assert result["version_no"] == 2
        await manager._settle_dispatched(created["task_run_id"], created["conversation"]["id"])
        assert service.db.read_conn.execute("SELECT status FROM tool_calls WHERE call_id='committed-recovery-skill'").fetchone()[0] == "completed"
        assert service.db.read_conn.execute("SELECT count(*) FROM skill_versions WHERE change_id='committed-recovery-skill'").fetchone()[0] == 1
    asyncio.run(check())
