"""审批闸门全链测试（进程内真实服务：真库、真事件、真审计链、真工具执行）。

场景：触发 write_file 弹卡 → curl（TestClient）批准 → 工具真实执行、
PERMISSION_RESOLVED 落库、审计链追加；幂等重试 / 不同决定 409 / input_hash
不符 409；allow_always 写规则且同类第二次不弹卡；reject 不执行；
重启后（重建服务）pending 可恢复并可继续决定。
"""

import asyncio
import json
import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcrew_core.events import RunEventType as T
from agentcrew_core.tools import ToolInvocation, WorkContext, new_call_id
from agentcrew_server.api.app import create_app
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.bus import EventBus
from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.runtime import RuntimeState

TOKEN = "tok-approval-1234567890"
CONV, RUN, AGENT = "conv-1", "run-1", "agent-1"


class Assembly:
    """进程内服务装配（与 cli 同构：EventStore → ApprovalService → 带闸门调度器）。"""

    def __init__(self, tmp_path: Path, name="t.db"):
        self.db = Database(tmp_path / name)
        run_migrations(self.db.write_conn, tmp_path / "backups")
        self.db.write_conn.execute(
            "INSERT OR IGNORE INTO conversations (id, workspace_id, agent_id,"
            " created_at, updated_at) VALUES (?, 'ws-1', ?, '2026-01-01',"
            " '2026-01-01')",
            (CONV, AGENT),
        )
        self.db.write_conn.execute(
            "INSERT OR IGNORE INTO task_runs (id, conversation_id, instruction,"
            " status, created_at, updated_at) VALUES (?, ?, 'demo', 'running',"
            " '2026-01-01', '2026-01-01')", (RUN, CONV),
        )
        self.channel = WriteChannel(self.db.write_conn)
        self.bus = EventBus()
        self.store = EventStore(self.channel, publisher=self.bus.publish)
        self.approvals = ApprovalService(self.db, self.store,
                                          tmp_path / "chain-head.txt")
        from agentcrew_core.tools import ToolScheduler, build_default_registry

        self.scheduler = ToolScheduler(build_default_registry(),
                                       gate=self.approvals.gate)
        self.approvals.scheduler = self.scheduler

    def close(self):
        self.channel.close()
        self.db.close()


@pytest.fixture
def asm(tmp_path):
    a = Assembly(tmp_path)
    yield a
    a.close()


def _ctx(a: Assembly, tmp_path: Path) -> WorkContext:
    scope = tmp_path / "ws"
    scope.mkdir(exist_ok=True)
    return WorkContext(
        scope=[scope], protected=[], artifacts_dir=tmp_path / "art",
        task_run_id=RUN,
    )


def _fire_and_forget_tool(a: Assembly, tmp_path: Path, tool: str,
                          inp: dict) -> asyncio.Task:
    """后台启动带闸门工具执行（弹卡时它会挂起，由测试侧决定）。"""
    loop = asyncio.get_event_loop() if False else None

    async def _run():
        inv = ToolInvocation(new_call_id(), tool, inp)
        return await a.approvals.run_tool(
            task_run_id=RUN, conversation_id=CONV, agent_id=AGENT,
            invocation=inv, ctx=_ctx(a, tmp_path))
    return asyncio.ensure_future(_run())


def _sync(coro_fn):
    return asyncio.new_event_loop().run_until_complete if False else asyncio.run


def test_full_chain_allow_once(tmp_path):
    """全链：弹卡 → 批准 → 工具真实执行 → RESOLVED 落库 → 审计链追加。"""
    async def scenario():
        a = Assembly(tmp_path)
        target = tmp_path / "ws" / "out.md"
        task = asyncio.ensure_future(
            a.approvals.run_tool(
                task_run_id=RUN, conversation_id=CONV, agent_id=AGENT,
                invocation=ToolInvocation(new_call_id(), "write_file",
                                          {"path": str(target), "content": "hi"}),
                ctx=_ctx(a, tmp_path)))
        # 等弹卡（事件落库）
        call_id = await _wait_for_request(a, timeout=5)
        result_pre = await a.approvals.list_approvals(RUN, "pending")
        assert len(result_pre) == 1
        card = result_pre[0]
        assert card["tool"] == "write_file" and card["risk"] == "medium"
        assert card["always_scope_preview"] == str(target.parent)
        assert card["input_hash"]
        # 批准
        decision = await a.approvals.submit(call_id, "allow_once")
        assert decision["idempotent_replay"] is False
        tool_result = await asyncio.wait_for(task, timeout=10)
        assert tool_result.ok and target.read_text() == "hi"
        # 事件断言：REQUESTED + RESOLVED 落库，顺序正确
        events = _events(a)
        types = [e[0] for e in events]
        assert "permission.requested" in types and "permission.resolved" in types
        resolved = next(e for e in events if e[0] == "permission.resolved")
        assert resolved[1]["decision"] == "allow_once"
        # 审计链：requested + resolved + medium 工具完成（S01 覆盖）三条且链一致
        actions = [r[0] for r in a.db.read_conn.execute(
            "SELECT action FROM audit_log ORDER BY seq").fetchall()]
        assert actions == ["permission.requested",
                           "permission.resolved:allow_once",
                           "tool.completed"]
        ok = verify_with_anchor(a.db.write_conn, tmp_path / "chain-head.txt")
        assert ok.ok
        a.close()
    asyncio.run(scenario())


async def _wait_for_request(a: Assembly, timeout=5):
    import time as _t

    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        rows = a.db.read_conn.execute(
            "SELECT json_extract(payload, '$.tool_call_id') FROM run_events"
            " WHERE type = 'permission.requested'").fetchall()
        if rows:
            return rows[0][0]
        await asyncio.sleep(0.05)
    raise AssertionError("等待审批卡超时")


def _events(a: Assembly):
    rows = a.db.read_conn.execute(
        "SELECT type, payload FROM run_events ORDER BY global_seq").fetchall()
    return [(r[0], json.loads(r[1])) for r in rows]


def test_idempotent_and_stale(tmp_path):
    async def scenario():
        a = Assembly(tmp_path)
        task = asyncio.ensure_future(a.approvals.run_tool(
            task_run_id=RUN, conversation_id=CONV, agent_id=AGENT,
            invocation=ToolInvocation(new_call_id(), "write_file",
                                      {"path": str(tmp_path / "ws" / "x.txt"),
                                       "content": "x"}),
            ctx=_ctx(a, tmp_path)))
        call_id = await _wait_for_request(a)
        first = await a.approvals.submit(call_id, "allow_once")
        await asyncio.wait_for(task, timeout=10)
        # 同决定重试 → 幂等返回首次结果
        replay = await a.approvals.submit(call_id, "allow_once")
        assert replay["idempotent_replay"] is True
        assert replay["decision"] == first["decision"]
        assert replay["decided_at"] == first["decided_at"]
        # 不同决定 → 409 语义（ApprovalStale）
        from agentcrew_server.approvals import ApprovalStale

        with pytest.raises(ApprovalStale):
            await a.approvals.submit(call_id, "reject_once")
        a.close()
    asyncio.run(scenario())


def test_input_hash_mismatch_stale(tmp_path):
    async def scenario():
        a = Assembly(tmp_path)
        task = asyncio.ensure_future(a.approvals.run_tool(
            task_run_id=RUN, conversation_id=CONV, agent_id=AGENT,
            invocation=ToolInvocation(new_call_id(), "write_file",
                                      {"path": str(tmp_path / "ws" / "y.txt"),
                                       "content": "y"}),
            ctx=_ctx(a, tmp_path)))
        call_id = await _wait_for_request(a)
        from agentcrew_server.approvals import ApprovalStale

        with pytest.raises(ApprovalStale):
            await a.approvals.submit(call_id, "allow_once",
                                     client_input_hash="wrong-hash")
        # 正确哈希可通过（从卡上取）
        card = (await a.approvals.list_approvals(RUN, "pending"))[0]
        decision = await a.approvals.submit(
            call_id, "allow_once", client_input_hash=card["input_hash"])
        assert decision["decision"] == "allow_once"
        await asyncio.wait_for(task, timeout=10)
        a.close()
    asyncio.run(scenario())


def test_allow_always_writes_rule_and_second_call_skips_gate(tmp_path):
    async def scenario():
        a = Assembly(tmp_path)
        ws = tmp_path / "ws"
        t1 = asyncio.ensure_future(a.approvals.run_tool(
            task_run_id=RUN, conversation_id=CONV, agent_id=AGENT,
            invocation=ToolInvocation(new_call_id(), "write_file",
                                      {"path": str(ws / "a.txt"), "content": "1"}),
            ctx=_ctx(a, tmp_path)))
        call_id = await _wait_for_request(a)
        await a.approvals.submit(call_id, "allow_always")
        assert (await asyncio.wait_for(t1, timeout=10)).ok
        rules = a.db.read_conn.execute(
            "SELECT tool_name, pattern, effect FROM agent_permission_rules"
        ).fetchall()
        assert rules == [("write_file", str(ws), "allow")]
        # 同目录第二次写：不弹卡直接放行
        t2 = await a.approvals.run_tool(
            task_run_id=RUN, conversation_id=CONV, agent_id=AGENT,
            invocation=ToolInvocation(new_call_id(), "write_file",
                                      {"path": str(ws / "b.txt"), "content": "2"}),
            ctx=_ctx(a, tmp_path))
        assert t2.ok and (ws / "b.txt").read_text() == "2"
        assert not (await a.approvals.list_approvals(RUN, "pending"))
        a.close()
    asyncio.run(scenario())


def test_reject_does_not_execute(tmp_path):
    async def scenario():
        a = Assembly(tmp_path)
        target = tmp_path / "ws" / "rejected.txt"
        task = asyncio.ensure_future(a.approvals.run_tool(
            task_run_id=RUN, conversation_id=CONV, agent_id=AGENT,
            invocation=ToolInvocation(new_call_id(), "write_file",
                                      {"path": str(target), "content": "no"}),
            ctx=_ctx(a, tmp_path)))
        call_id = await _wait_for_request(a)
        await a.approvals.submit(call_id, "reject_once")
        result = await asyncio.wait_for(task, timeout=10)
        assert not result.ok and result.error == "PERMISSION_DENIED"
        assert not target.exists()
        # tool.failed 事件记录拒绝
        types = [e[0] for e in _events(a)]
        assert types.count("permission.resolved") == 1
        a.close()
    asyncio.run(scenario())


def test_readonly_tool_no_gate(tmp_path):
    async def scenario():
        a = Assembly(tmp_path)
        target = tmp_path / "ws" / "readme.txt"
        target.parent.mkdir(exist_ok=True)
        target.write_text("hello")
        result = await a.approvals.run_tool(
            task_run_id=RUN, conversation_id=CONV, agent_id=AGENT,
            invocation=ToolInvocation(new_call_id(), "read_file",
                                      {"path": str(target)}),
            ctx=_ctx(a, tmp_path))
        assert result.ok and result.output == "hello"
        assert not (await a.approvals.list_approvals(RUN, "pending"))
        a.close()
    asyncio.run(scenario())


def test_restart_recovers_pending_card(tmp_path):
    """重启进程（重建 Assembly）后：pending 卡可查、决定可提交并落库。"""
    async def scenario():
        a1 = Assembly(tmp_path, "restart.db")
        target = tmp_path / "ws" / "later.txt"
        task = asyncio.ensure_future(a1.approvals.run_tool(
            task_run_id=RUN, conversation_id=CONV, agent_id=AGENT,
            invocation=ToolInvocation(new_call_id(), "write_file",
                                      {"path": str(target), "content": "later"}),
            ctx=_ctx(a1, tmp_path)))
        call_id = await _wait_for_request(a1)
        task.cancel()  # 模拟进程中断（执行协程消亡）
        try:
            await task
        except asyncio.CancelledError:
            pass
        a1.close()
        # 重建服务（同一 data-dir）：pending 可恢复
        a2 = Assembly(tmp_path, "restart.db")
        pending = await a2.approvals.list_approvals(RUN, "pending")
        assert len(pending) == 1 and pending[0]["call_id"] == call_id
        decision = await a2.approvals.submit(call_id, "allow_once")
        assert decision["decision"] == "allow_once"
        resolved = await a2.approvals.list_approvals(RUN, "resolved")
        assert len(resolved) == 1
        a2.close()
    asyncio.run(scenario())


def test_approvals_api_endpoints(tmp_path):
    """API 层：POST 决定（信封 + 幂等 + 409 + 404）；GET pending。"""
    async def scenario():
        a = Assembly(tmp_path)
        runtime = RuntimeState(
            log=logging.getLogger("t"), data_dir=tmp_path, db=a.db,
            token=TOKEN, approvals=a.approvals, bus=a.bus,
            event_store=a.store,
        )
        app = create_app(runtime)
        task = None
        with TestClient(app, raise_server_exceptions=False) as client:
            headers = {"Authorization": f"Bearer {TOKEN}"}
            task = asyncio.ensure_future(a.approvals.run_tool(
                task_run_id=RUN, conversation_id=CONV, agent_id=AGENT,
                invocation=ToolInvocation(new_call_id(), "write_file",
                                          {"path": str(tmp_path / "ws" / "api.txt"),
                                           "content": "api"}),
                ctx=_ctx(a, tmp_path)))
            call_id = await _wait_for_request(a)
            pending = client.get(f"/api/task-runs/{RUN}/approvals",
                                 headers=headers)
            assert pending.status_code == 200
            cards = pending.json()["data"]
            assert len(cards) == 1 and cards[0]["call_id"] == call_id
            # 批准
            resp = client.post(f"/api/tool-approvals/{call_id}", headers=headers,
                               json={"decision": "allow_once"})
            assert resp.status_code == 200
            assert resp.json()["data"]["idempotent_replay"] is False
            await asyncio.wait_for(task, timeout=10)
            # 幂等重放
            resp2 = client.post(f"/api/tool-approvals/{call_id}", headers=headers,
                                json={"decision": "allow_once"})
            assert resp2.status_code == 200
            assert resp2.json()["data"]["idempotent_replay"] is True
            # 不同决定 → 409 APPROVAL_STALE
            resp3 = client.post(f"/api/tool-approvals/{call_id}", headers=headers,
                                json={"decision": "reject_once"})
            assert resp3.status_code == 409
            assert resp3.json()["error"]["code"] == "APPROVAL_STALE"
            # 不存在的审批 → 404
            resp4 = client.post("/api/tool-approvals/nonexistent",
                                headers=headers, json={"decision": "allow_once"})
            assert resp4.status_code == 404
            # 不存在的任务 → 404
            resp5 = client.get("/api/task-runs/nope/approvals", headers=headers)
            assert resp5.status_code == 404
        a.close()
    asyncio.run(scenario())
