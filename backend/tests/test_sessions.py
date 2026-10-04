"""会话 FSM 与指令排队 API 测试（M0-C7：真实服务、真实文件、真库）。

覆盖：材料导入（3 真文件 + 1 不存在路径、重名改名、超限逐文件拒绝、全部
被拒 422）、client_request_id 幂等（建会话与指令两条路径）、指令直跑/入队/
审批挂起 409（真实闸门弹卡驱动 reducer 真值表）、queue/cancel 发送记录保留、
queue/continue 成功与各 409 原因码、state 快照含 at_global_seq 且续播跳过、
并发两条指令第二条必入队、S09 工作目录装配（相对路径 + bash cwd）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcrew_core.events import RunEventType as T
from agentcrew_core.tools import ToolInvocation, new_call_id
from agentcrew_server.api.app import create_app
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.bus import EventBus
from agentcrew_server.config import load_config
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.runtime import RuntimeState
from agentcrew_server.sessions import SessionService
from agentcrew_server.settings import SettingsService

TOKEN = "tok-sessions-1234567890"


class Assembly:
    """进程内服务装配（与 cli 同构 + 可选带闸门调度器）。"""

    def __init__(self, tmp_path: Path, name="t.db", env=None):
        self.data_dir = tmp_path
        self.db = Database(tmp_path / name)
        run_migrations(self.db.write_conn, tmp_path / "backups")
        self.channel = WriteChannel(self.db.write_conn)
        self.bus = EventBus()
        self.store = EventStore(self.channel, publisher=self.bus.publish)
        self.settings = SettingsService(
            load_config(tmp_path, env if env is not None else {}), tmp_path,
            self.channel, tmp_path / "chain-head.txt")
        self.sessions = SessionService(self.db, self.store, tmp_path,
                                       self.settings)

    def build_app(self):
        runtime = RuntimeState(
            log=logging.getLogger("t.sessions"), data_dir=self.data_dir,
            db=self.db, token=TOKEN, bus=self.bus, event_store=self.store,
            sessions=self.sessions, settings=self.settings,
        )
        return create_app(runtime)

    def client_app(self):
        """真实 FastAPI app（SSE 流式验证用真实 uvicorn，TestClient 不投递流字节）。"""
        return self.build_app()

    def client(self) -> TestClient:
        return TestClient(self.build_app())

    def close(self):
        self.channel.close()
        self.db.close()


@pytest.fixture
def asm(tmp_path):
    a = Assembly(tmp_path)
    yield a
    a.close()


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _make_source_files(tmp_path: Path) -> list[Path]:
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "a.txt").write_text("内容A")
    (src / "b.md").write_text("# B")
    return [src / "a.txt", src / "b.md"]


# ── 材料导入（F001）────────────────────────────────────────────────

def test_create_conversation_with_materials(asm, tmp_path):
    src = _make_source_files(tmp_path)
    with asm.client() as c:
        resp = c.post("/api/conversations", headers=AUTH, json={
            "instruction": "整理这些文件",
            "import_files": [str(p) for p in src]
            + [str(tmp_path / "不存在.pdf")],
        })
        assert resp.status_code == 201
        data = resp.json()["data"]
        conv_id = data["conversation"]["id"]
        ok = [m for m in data["materials"] if not m["error"]]
        bad = [m for m in data["materials"] if m["error"]]
        assert len(ok) == 2 and len(bad) == 1
        assert "不存在" in bad[0]["error"]
        # 文件真实复制进 materials/，原文件未动
        materials_dir = asm.data_dir / "conversations" / conv_id / "materials"
        assert (materials_dir / "a.txt").read_text() == "内容A"
        assert src[0].read_text() == "内容A"
        # task_materials 投影与 materials.imported 事件落库
        rows = asm.db.read_conn.execute(
            "SELECT original_path, stored_name, error FROM task_materials"
            " WHERE conversation_id = ?", (conv_id,)).fetchall()
        assert len(rows) == 3
        types = [r[0] for r in asm.db.read_conn.execute(
            "SELECT type FROM run_events WHERE conversation_id = ?"
            " ORDER BY global_seq", (conv_id,)).fetchall()]
        assert types == ["run.queued", "materials.imported"]
        # 无 runner：task_run 停留 queued 属预期
        status = asm.db.read_conn.execute(
            "SELECT status FROM task_runs WHERE conversation_id = ?",
            (conv_id,)).fetchone()[0]
        assert status == "queued"


def test_materials_rename_on_collision(asm, tmp_path):
    """两个目录下同名文件 → 重名自动生成可辨认新名。"""
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir(), d2.mkdir()
    (d1 / "报告.txt").write_text("第一份")
    (d2 / "报告.txt").write_text("第二份")
    with asm.client() as c:
        resp = c.post("/api/conversations", headers=AUTH, json={
            "instruction": "合并报告",
            "import_files": [str(d1 / "报告.txt"), str(d2 / "报告.txt")],
        })
        data = resp.json()["data"]
        stored = sorted(m["stored_name"] for m in data["materials"])
        assert stored == ["报告-2.txt", "报告.txt"]
        materials_dir = asm.data_dir / "conversations" / \
            data["conversation"]["id"] / "materials"
        assert (materials_dir / "报告.txt").read_text() == "第一份"
        assert (materials_dir / "报告-2.txt").read_text() == "第二份"


def test_materials_size_and_count_limits_rejected_per_file(asm, tmp_path):
    """逐文件拒绝：超大小（limits 可配小以便真实验证）与超数量上限。"""
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (2 * 1024 * 1024))  # 2MB > 1MB 上限
    files = [str(tmp_path / f"f{i}.txt") for i in range(3)]
    for f in files:
        Path(f).write_text("x")
    asm.settings._config.values["limits"]["max_file_mb"] = 1
    asm.settings._config.values["limits"]["max_files"] = 2
    with asm.client() as c:
        resp = c.post("/api/conversations", headers=AUTH, json={
            "instruction": "导入", "import_files": [str(big)] + files})
        data = resp.json()["data"]
        assert data["materials"][0]["error"] and "大小上限" in data["materials"][0]["error"]
        assert data["materials"][2]["error"] and "文件数上限" in data["materials"][2]["error"]
        assert data["materials"][3]["error"]  # 第 3 个文件（序号 2）起超数量


def test_all_materials_rejected_422(asm, tmp_path):
    with asm.client() as c:
        resp = c.post("/api/conversations", headers=AUTH, json={
            "instruction": "导入",
            "import_files": [str(tmp_path / "没有这个文件")]})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert asm.db.read_conn.execute(
            "SELECT count(*) FROM conversations").fetchone()[0] == 0


def test_folders_validation_and_scope(asm, tmp_path):
    folder = tmp_path / "资料夹"
    folder.mkdir()
    with asm.client() as c:
        resp = c.post("/api/conversations", headers=AUTH, json={
            "instruction": "处理资料夹",
            "folders": [str(folder), str(tmp_path / "不存在的夹")]})
        assert resp.status_code == 201
        conv_id = resp.json()["data"]["conversation"]["id"]
        scope = c.get(f"/api/conversations/{conv_id}/scope",
                      headers=AUTH).json()["data"]
        assert scope["folders"] == [{"path": str(folder), "access": "read_write"}]
        assert scope["materials_dir"].endswith(f"/materials")
        assert "/workspaces/" in scope["workspace_dir"]
        # folders_json 落库（进入任务读写范围）
        row = asm.db.read_conn.execute(
            "SELECT folders_json FROM conversations WHERE id = ?",
            (conv_id,)).fetchone()
        assert json.loads(row[0]) == [{"path": str(folder), "access": "read_write"}]
        assert c.get("/api/limits", headers=AUTH).json()["data"] == {
            "max_files": 20, "max_file_mb": 50, "max_folders": 5}


# ── 幂等（client_request_id）──────────────────────────────────────

def test_create_conversation_idempotent_retry(asm, tmp_path):
    src = _make_source_files(tmp_path)
    body = {"instruction": "任务X", "client_request_id": "req-1",
            "import_files": [str(src[0])]}
    with asm.client() as c:
        first = c.post("/api/conversations", headers=AUTH, json=body).json()["data"]
        second = c.post("/api/conversations", headers=AUTH, json=body).json()["data"]
        assert first["conversation"]["id"] == second["conversation"]["id"]
        assert first["task_run_id"] == second["task_run_id"]
        assert asm.db.read_conn.execute(
            "SELECT count(*) FROM conversations").fetchone()[0] == 1
        assert asm.db.read_conn.execute(
            "SELECT count(*) FROM task_runs").fetchone()[0] == 1
        assert asm.db.read_conn.execute(
            "SELECT count(*) FROM task_materials").fetchone()[0] == 1


# ── 指令：直跑 / 入队 / 审批 409 ──────────────────────────────────

def _create(asm, c, instruction="首个任务") -> str:
    return c.post("/api/conversations", headers=AUTH,
                  json={"instruction": instruction}).json()["data"]["conversation"]["id"]


def test_messages_pagination_and_cancelled_queue_history(asm):
    """真实指令写入投影，最新页与排他游标完整覆盖历史，取消保留消息。"""
    with asm.client() as c:
        conv_id = _create(asm, c, "整理工作资料")
        url = f"/api/conversations/{conv_id}/messages"
        for index in range(51):
            response = c.post(
                f"/api/conversations/{conv_id}/instructions", headers=AUTH,
                json={"text": f"核对第 {index + 1} 份资料"})
            assert response.status_code == 202
        expected = [dict(row) for row in asm.db.read_conn.execute(
            "SELECT id, role, content, task_run_id, created_at FROM messages"
            " WHERE conversation_id=? ORDER BY created_at, id",
            (conv_id,)).fetchall()]
        response = c.get(url, headers=AUTH)
        assert response.status_code == 200
        latest = response.json()["data"]
        assert latest == {"items": expected[-50:], "has_more": True}
        assert expected[0]["task_run_id"] is not None
        assert all(item["task_run_id"] is None for item in latest["items"])

        # 新指令到达后，已有游标读取更早消息仍然排他且无遗漏。
        assert c.post(f"/api/conversations/{conv_id}/instructions",
                      headers=AUTH, json={"text": "汇总全部核对结果"}).status_code == 202
        older = c.get(url, headers=AUTH, params={
            "before": latest["items"][0]["id"], "limit": 2}).json()["data"]
        assert older == {"items": expected[:2], "has_more": False}
        assert older["items"] + latest["items"] == expected
        assert c.get(url, headers=AUTH, params={
            "before": expected[0]["id"]}).json()["data"] == {
                "items": [], "has_more": False}
        newest = c.get(url, headers=AUTH, params={"limit": 1}).json()["data"]
        assert newest["has_more"] is True
        assert newest["items"][0]["content"] == "汇总全部核对结果"
        assert c.post(f"/api/conversations/{conv_id}/queue/cancel",
                      headers=AUTH, json={"all": True}).status_code == 200
        all_messages = c.get(url, headers=AUTH, params={"limit": 200}).json()["data"]
        assert all_messages == {
            "items": expected + newest["items"], "has_more": False}
        assert c.get(url).status_code == 401


def test_messages_missing_conversation_and_scoped_cursor(asm):
    with asm.client() as c:
        conv_id = _create(asm, c)
        other_id = _create(asm, c, "另一个会话的指令")
        other_cursor = asm.db.read_conn.execute(
            "SELECT id FROM messages WHERE conversation_id=?",
            (other_id,)).fetchone()[0]
        for path, params in [
            ("/api/conversations/unknown/messages", {}),
            (f"/api/conversations/{conv_id}/messages", {"before": "unknown"}),
            (f"/api/conversations/{conv_id}/messages", {"before": other_cursor}),
        ]:
            response = c.get(path, headers=AUTH, params=params)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": -1}, {"limit": 201}, {"limit": "invalid"},
    {"before": ""},
])
def test_messages_invalid_pagination(asm, params):
    with asm.client() as c:
        conv_id = _create(asm, c)
        response = c.get(f"/api/conversations/{conv_id}/messages",
                         headers=AUTH, params=params)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_second_instruction_queues_with_position(asm):
    with asm.client() as c:
        conv_id = _create(asm, c)
        state = c.get(f"/api/conversations/{conv_id}/state", headers=AUTH).json()["data"]
        assert state["state"] == "starting" and state["can_queue"] is True
        resp = c.post(f"/api/conversations/{conv_id}/instructions",
                      headers=AUTH, json={"text": "第二条指令"})
        assert resp.status_code == 202
        assert resp.json()["data"] == {"mode": "queued", "queue_position": 1,
                                       "task_run_id": None}
        state = c.get(f"/api/conversations/{conv_id}/state", headers=AUTH).json()["data"]
        assert len(state["queue"]) == 1
        assert state["queue"][0]["text"] == "第二条指令"
        # 发送记录已入 messages（task_run_id 为空——尚未建任务）
        rows = asm.db.read_conn.execute(
            "SELECT role, content, task_run_id FROM messages"
            " WHERE conversation_id = ? ORDER BY created_at", (conv_id,)).fetchall()
        assert tuple(rows[-1]) == ("user", "第二条指令", None)


def test_instruction_idempotent_retry(asm):
    with asm.client() as c:
        conv_id = _create(asm, c)
        r1 = c.post(f"/api/conversations/{conv_id}/instructions", headers=AUTH,
                    json={"text": "排队指令", "client_request_id": "ins-1"})
        r2 = c.post(f"/api/conversations/{conv_id}/instructions", headers=AUTH,
                    json={"text": "排队指令", "client_request_id": "ins-1"})
        assert r1.json() == r2.json()
        assert asm.db.read_conn.execute(
            "SELECT count(*) FROM messages WHERE conversation_id = ?",
            (conv_id,)).fetchone()[0] == 2  # 首指令 + 一条排队消息，无重复


def test_concurrent_instructions_second_must_queue(asm):
    """并发同会话两条指令：锁序化，第二条必入队（卡内失败状态）。
    前置：当前任务已终态（idle）——C7 无 runner，终态事件经真实 EventStore
    落库模拟 C8 runner 行为。"""
    async def scenario():
        with asm.client() as c:
            conv_id = _create(asm, c)
            await _finish_current(asm, conv_id, T.RUN_COMPLETED)
            assert asm.sessions.state_snapshot(conv_id)["state"] == "idle"
            results = await asyncio.gather(
                asm.sessions.send_instruction(conv_id, "并发A"),
                asm.sessions.send_instruction(conv_id, "并发B"),
            )
            modes = sorted(r["mode"] for r in results)
            assert modes == ["queued", "started"]
            snapshot = asm.sessions.state_snapshot(conv_id)
            assert len(snapshot["queue"]) == 1
            assert asm.db.read_conn.execute(
                "SELECT count(*) FROM task_runs WHERE conversation_id = ?",
                (conv_id,)).fetchone()[0] == 2  # 首任务 + 直发那条
    asyncio.run(scenario())


def test_instruction_409_while_approval_pending(asm, tmp_path):
    """审批挂起态 409：真实闸门弹卡（C6 机制）驱动 reducer 真值表。"""
    async def scenario():
        approvals = ApprovalService(asm.db, asm.store,
                                     tmp_path / "chain-head.txt")
        from agentcrew_core.tools import ToolScheduler, build_default_registry
        approvals.scheduler = ToolScheduler(build_default_registry(),
                                            gate=approvals.gate)
        with asm.client() as c:
            conv_id = _create(asm, c)
            # 模拟 C8 runner 的两个动作：run.started（FSM → running）+ 弹卡工具
            await asm.store.append(
                task_run_id=asm.sessions.state_snapshot(conv_id)
                ["current_task_run_id"],
                conversation_id=conv_id, type=T.RUN_STARTED,
                payload={"attempt_no": 1, "attempt_id": "att-1",
                         "kind": "initial"})
            ctx = asm.sessions.build_work_context(
                conv_id, asm.sessions.state_snapshot(conv_id)
                ["current_task_run_id"])
            task = asyncio.ensure_future(approvals.run_tool(
                task_run_id=ctx.task_run_id, conversation_id=conv_id,
                agent_id="default",
                invocation=ToolInvocation(
                    new_call_id(), "write_file",
                    {"path": str(Path(ctx.cwd) / "x.md"), "content": "x"}),
                ctx=ctx))
            for _ in range(100):
                if asm.sessions.state_snapshot(conv_id)["waiting_approvals"]:
                    break
                await asyncio.sleep(0.05)
            snapshot = asm.sessions.state_snapshot(conv_id)
            assert snapshot["waiting_approvals"] == 1
            assert snapshot["can_queue"] is False
            resp = c.post(f"/api/conversations/{conv_id}/instructions",
                          headers=AUTH, json={"text": "审批期间插话"})
            assert resp.status_code == 409
            assert resp.json()["error"]["code"] == "APPROVAL_PENDING"
            # 批准后恢复可排队
            call_id = asm.db.read_conn.execute(
                "SELECT json_extract(payload, '$.tool_call_id') FROM run_events"
                " WHERE type = 'permission.requested'").fetchone()[0]
            await approvals.submit(call_id, "allow_once")
            await asyncio.wait_for(task, timeout=10)
            snapshot = asm.sessions.state_snapshot(conv_id)
            assert snapshot["waiting_approvals"] == 0
            assert snapshot["can_queue"] is True
    asyncio.run(scenario())


# ── 排队控制（F006）────────────────────────────────────────────────

def _enqueue(c, conv_id, text) -> dict:
    return c.post(f"/api/conversations/{conv_id}/instructions", headers=AUTH,
                  json={"text": text}).json()["data"]


async def _finish_current(asm, conv_id, terminal=T.RUN_CANCELLED):
    """模拟 C8 runner 终态事件（真实 EventStore 落库）。异步体：同步测试用
    asyncio.run 包装，async 场景直接 await。"""
    task_id = asm.sessions.state_snapshot(conv_id)["current_task_run_id"]
    await asm.store.append(
        task_run_id=task_id, conversation_id=conv_id, type=terminal,
        payload={})


def test_queue_cancel_keeps_message_records(asm):
    with asm.client() as c:
        conv_id = _create(asm, c)
        _enqueue(c, conv_id, "排队1")
        _enqueue(c, conv_id, "排队2")
        # 从 state 取真实 item id 再取消（幂等语义：不在队列的 id 被忽略）
        state = c.get(f"/api/conversations/{conv_id}/state",
                      headers=AUTH).json()["data"]
        ids = [i["id"] for i in state["queue"]]
        resp = c.post(f"/api/conversations/{conv_id}/queue/cancel",
                      headers=AUTH, json={"item_ids": [ids[0], "不存在"]})
        assert resp.status_code == 200
        assert resp.json()["data"]["cancelled_ids"] == [ids[0]]
        state = c.get(f"/api/conversations/{conv_id}/state",
                      headers=AUTH).json()["data"]
        assert len(state["queue"]) == 1  # 剩排队2
        # 发送记录保留：两条排队消息都在 messages；取消项留痕于事件流
        contents = [r[0] for r in asm.db.read_conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ?"
            " ORDER BY created_at", (conv_id,)).fetchall()]
        assert contents == ["首个任务", "排队1", "排队2"]
        ev_types = [r[0] for r in asm.db.read_conn.execute(
            "SELECT type FROM run_events WHERE conversation_id = ?"
            " ORDER BY global_seq", (conv_id,)).fetchall()]
        assert "queue.item_cancelled" in ev_types


def test_queue_cancel_all(asm):
    with asm.client() as c:
        conv_id = _create(asm, c)
        _enqueue(c, conv_id, "A"); _enqueue(c, conv_id, "B")
        resp = c.post(f"/api/conversations/{conv_id}/queue/cancel",
                      headers=AUTH, json={"all": True})
        ids = resp.json()["data"]["cancelled_ids"]
        assert len(ids) == 2
        assert c.get(f"/api/conversations/{conv_id}/state",
                     headers=AUTH).json()["data"]["queue"] == []


def test_queue_continue_flow_and_409s(asm):
    with asm.client() as c:
        conv_id = _create(asm, c)
        # ① 未暂停（任务 starting 中）→ QUEUE_PAUSED（状态不符）
        resp = c.post(f"/api/conversations/{conv_id}/queue/continue",
                      headers=AUTH)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "QUEUE_PAUSED"
        # 模拟 runner：取消当前任务（队列非空 → 暂停）
        _enqueue(c, conv_id, "接着做")
        asyncio.run(_finish_current(asm, conv_id, T.RUN_CANCELLED))
        state = c.get(f"/api/conversations/{conv_id}/state",
                      headers=AUTH).json()["data"]
        assert state["state"] == "idle" and state["queue_paused"] is True
        assert state["can_continue_queue"] is True
        # ② 继续：202 无响应体，队首出队成新任务
        resp = c.post(f"/api/conversations/{conv_id}/queue/continue",
                      headers=AUTH)
        assert resp.status_code == 202 and resp.content == b""
        state = c.get(f"/api/conversations/{conv_id}/state",
                      headers=AUTH).json()["data"]
        assert state["state"] == "starting" and state["queue"] == []
        new_task = state["current_task_run_id"]
        assert new_task != ""
        row = asm.db.read_conn.execute(
            "SELECT instruction, status, client_request_id FROM task_runs"
            " WHERE id = ?", (new_task,)).fetchone()
        assert row[0] == "接着做" and row[1] == "queued"
        # 出队指令的消息不重复（入队时已写）
        contents = [r[0] for r in asm.db.read_conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ?",
            (conv_id,)).fetchall()]
        assert contents.count("接着做") == 1
        # ③ 再继续：未暂停（已恢复）→ 状态不符 QUEUE_PAUSED
        resp = c.post(f"/api/conversations/{conv_id}/queue/continue",
                      headers=AUTH)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "QUEUE_PAUSED"
        # ④ 暂停态下清空队列 → 继续时 QUEUE_EMPTY
        conv2 = _create(asm, c, "第二个会话")
        _enqueue(c, conv2, "将被取消")
        asyncio.run(_finish_current(asm, conv2, T.RUN_CANCELLED))
        cancel = c.post(f"/api/conversations/{conv2}/queue/cancel",
                        headers=AUTH, json={"all": True})
        assert cancel.json()["data"]["cancelled_ids"]
        resp = c.post(f"/api/conversations/{conv2}/queue/continue",
                      headers=AUTH)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "QUEUE_EMPTY"


def test_queue_continue_blocked_by_pending_verification(asm):
    with asm.client() as c:
        conv_id = _create(asm, c)
        _enqueue(c, conv_id, "后续")
        asyncio.run(_finish_current(asm, conv_id, T.RUN_CANCELLED))
        # 人为落一条 pending_verification 调用（对账后状态，C9 域产生的真实行）
        task_id = asm.db.read_conn.execute(
            "SELECT id FROM task_runs WHERE conversation_id = ?"
            " ORDER BY created_at LIMIT 1", (conv_id,)).fetchone()[0]
        asm.db.write_conn.execute(
            "INSERT INTO tool_calls (id, call_id, task_run_id, tool_name,"
            " side_effect_class, input_hash, status, risk_level, prepared_at,"
            " dispatched_at) VALUES ('tc1','call-1',?, 'bash',"
            " 'outcome_unknown','h','pending_verification','medium','t','t')",
            (task_id,))
        asm.db.write_conn.commit()
        resp = c.post(f"/api/conversations/{conv_id}/queue/continue",
                      headers=AUTH)
        assert resp.status_code == 409
        body = resp.json()["error"]
        assert body["code"] == "PENDING_VERIFICATION"
        assert body["detail"]["pending_verifications"][0]["call_id"] == "call-1"


# ── state 快照与 at_global_seq 配对续播 ───────────────────────────

def test_state_snapshot_at_global_seq_pairs_with_stream(tmp_path):
    """SSE 配对续播用真实 uvicorn + httpx（TestClient 不流式投递 SSE 字节，
    C3 同结论）：取快照 → 从 at_global_seq 续播 → ≤at 的事件被跳过。"""
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    asm = Assembly(tmp_path)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = uvicorn.Server(uvicorn.Config(
        asm.client_app(), host="127.0.0.1", port=port, log_config=None,
        lifespan="on", timeout_graceful_shutdown=3))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(timeout=5.0) as client:
            conv_id = client.post(
                f"{base}/api/conversations", headers=AUTH,
                json={"instruction": "快照配对"}).json()["data"]["conversation"]["id"]
            client.post(f"{base}/api/conversations/{conv_id}/instructions",
                        headers=AUTH, json={"text": "排队指令"})
            snapshot = client.get(f"{base}/api/conversations/{conv_id}/state",
                                  headers=AUTH).json()["data"]
            at = snapshot["at_global_seq"]
            assert at > 0
            # 快照后追加新事件（真实落库，走写通道与总线）
            asyncio.run(asm.store.append(
                task_run_id=None, conversation_id=conv_id,
                type=T.QUEUE_ITEM_ENQUEUED,
                payload={"item_id": "late", "text": "快照之后"}))
            # 续播：from=at 排他——读到目标帧即断开
            frames = []
            with client.stream(
                    "GET", f"{base}/api/conversations/{conv_id}/stream?from={at}",
                    headers=AUTH) as resp:
                assert resp.status_code == 200
                buf = ""
                for line in resp.iter_lines():
                    if line == "":
                        if buf:
                            frames.append(json.loads(buf))
                            break  # 收齐快照后的一帧，主动断开
                        continue
                    if line.startswith("data:"):
                        buf += line.split(":", 1)[1].strip()
            assert [f["global_seq"] for f in frames] == [at + 1]
            assert frames[0]["payload"]["text"] == "快照之后"
            assert "task_run_id" not in frames[0]  # 会话域事件无任务锚点
    finally:
        server.should_exit = True
        time.sleep(0.3)
        asm.close()


def test_state_snapshot_404_and_queue_detail(asm):
    with asm.client() as c:
        assert c.get("/api/conversations/无此会话/state",
                     headers=AUTH).status_code == 404
        assert c.post("/api/conversations/无此会话/instructions",
                      headers=AUTH,
                      json={"text": "x"}).status_code == 404


def test_conversations_list(asm):
    with asm.client() as c:
        _create(asm, c, "任务一")
        rows = c.get("/api/conversations", headers=AUTH).json()["data"]
        assert len(rows) == 1
        assert rows[0]["state_badge"] == "running"  # starting → 徽章 running
        assert rows[0]["agent_name"] == "default"


# ── 外审回稿回归（C7 第四轮）─────────────────────────────────────

def test_compound_closures_publish_in_write_thread_in_order(asm):
    """外审回稿致命项：创建/继续队列的复合闭包必须在写通道线程内、COMMIT
    成功后、闭包返回前发布（对齐 EventStore publisher-in-closure）——闭包外
    发布存在乱序窗口：写线程先发布更大 global_seq，SSE 去重把后到的较小
    序号永久丢弃，客户端拿大游标重连也补不回。"""
    with asm.client() as c:
        conv_id = _create(asm, c)
        _enqueue(c, conv_id, "排队项")
        asyncio.run(_finish_current(asm, conv_id, T.RUN_CANCELLED))

        recorded: list[tuple[int, int]] = []
        main_thread = threading.get_ident()
        bus_publish = asm.bus.publish

        def probe(event):
            recorded.append((event.global_seq, threading.get_ident()))
            bus_publish(event)  # 真总线照常扇出，仅记录线程与序号

        asm.store.set_publisher(probe)

        async def spam():
            for _ in range(4):
                await asm.store.append(
                    task_run_id=None, conversation_id=conv_id,
                    type=T.QUEUE_PAUSED, payload={})
                await asyncio.sleep(0)

        async def scenario():
            # 继续队列（复合闭包）与连续单事件追加并发；随后再走一次创建
            await asyncio.gather(asm.sessions.continue_queue(conv_id), spam())
            await asyncio.gather(
                asm.sessions.create_conversation(instruction="并发创建"),
                spam())

        asyncio.run(scenario())

        seqs = [g for g, _ in recorded]
        assert seqs == sorted(seqs), "发布顺序必须等于提交顺序"
        assert len(seqs) == len(set(seqs))
        assert main_thread not in {t for _, t in recorded}, \
            "复合闭包事件必须在写线程内发布（闭包外发布有乱序窗口）"


def test_stale_permission_resolution_keeps_next_task_waiting(asm):
    """外审回稿：任务 A 等审批被中断后，A 的旧决定不清零任务 B 的未决审批
    （真实事件链 + state_snapshot + 指令 409 全路径；决定以原任务为锚——
    与 approvals.submit 的落库方式一致）。"""
    async def scenario():
        with asm.client() as c:
            conv_id = _create(asm, c)
            task_a = asm.sessions.state_snapshot(conv_id)["current_task_run_id"]
            await asm.store.append(
                task_run_id=task_a, conversation_id=conv_id, type=T.RUN_STARTED,
                payload={"attempt_no": 1, "attempt_id": "att-a", "kind": "initial"})
            await asm.store.append(
                task_run_id=task_a, conversation_id=conv_id,
                type=T.PERMISSION_REQUESTED, payload={"tool_call_id": "call-a"})
            await asm.store.append(
                task_run_id=task_a, conversation_id=conv_id,
                type=T.RUN_INTERRUPTED, payload={})
            # 重启恢复后新任务 B：直发 → running → 等审批
            started = await asm.sessions.send_instruction(conv_id, "任务B")
            task_b = started["task_run_id"]
            await asm.store.append(
                task_run_id=task_b, conversation_id=conv_id, type=T.RUN_STARTED,
                payload={"attempt_no": 1, "attempt_id": "att-b", "kind": "initial"})
            await asm.store.append(
                task_run_id=task_b, conversation_id=conv_id,
                type=T.PERMISSION_REQUESTED, payload={"tool_call_id": "call-b"})
            # A 的旧决定到达——修复前会清零 B 的计数使 can_queue 错误变 true
            await asm.store.append(
                task_run_id=task_a, conversation_id=conv_id,
                type=T.PERMISSION_RESOLVED,
                payload={"tool_call_id": "call-a", "decision": "allow_once"})
            snap = asm.sessions.state_snapshot(conv_id)
            assert snap["current_task_run_id"] == task_b
            assert snap["waiting_approvals"] == 1
            assert snap["can_queue"] is False
            resp = c.post(f"/api/conversations/{conv_id}/instructions",
                          headers=AUTH, json={"text": "插话"})
            assert resp.status_code == 409
            assert resp.json()["error"]["code"] == "APPROVAL_PENDING"
            # B 自己的决定正常清零
            await asm.store.append(
                task_run_id=task_b, conversation_id=conv_id,
                type=T.PERMISSION_RESOLVED,
                payload={"tool_call_id": "call-b", "decision": "allow_once"})
            snap2 = asm.sessions.state_snapshot(conv_id)
            assert snap2["waiting_approvals"] == 0 and snap2["can_queue"] is True
    asyncio.run(scenario())


def test_cancelled_queue_item_holds_idempotency_key(asm):
    """外审回稿：已取消的排队项继续占有幂等键——同键重试返回明确的已取消
    结果，不重新入队/建任务（修复前查重漏掉已取消项，重试会重新入队）。"""
    with asm.client() as c:
        conv_id = _create(asm, c)
        r1 = c.post(f"/api/conversations/{conv_id}/instructions",
                    headers=AUTH,
                    json={"text": "排队后取消", "client_request_id": "cx-1"})
        assert r1.json()["data"]["mode"] == "queued"
        state = c.get(f"/api/conversations/{conv_id}/state",
                      headers=AUTH).json()["data"]
        item_id = state["queue"][0]["id"]
        cancel = c.post(f"/api/conversations/{conv_id}/queue/cancel",
                        headers=AUTH, json={"item_ids": [item_id]})
        assert cancel.json()["data"]["cancelled_ids"] == [item_id]
        # 同键重试：明确已取消，不再入队
        r2 = c.post(f"/api/conversations/{conv_id}/instructions",
                    headers=AUTH,
                    json={"text": "排队后取消", "client_request_id": "cx-1"})
        assert r2.status_code == 202
        assert r2.json()["data"] == {"mode": "cancelled", "queue_position": None,
                                     "task_run_id": None}
        state2 = c.get(f"/api/conversations/{conv_id}/state",
                       headers=AUTH).json()["data"]
        assert state2["queue"] == []
        assert asm.db.read_conn.execute(
            "SELECT count(*) FROM task_runs WHERE conversation_id = ?",
            (conv_id,)).fetchone()[0] == 1  # 仍只有首任务，重试未建任务
        contents = [r[0] for r in asm.db.read_conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ?"
            " ORDER BY created_at", (conv_id,)).fetchall()]
        assert contents.count("排队后取消") == 1  # 无重复发送记录


def test_create_conversation_same_key_race_serialized(asm, tmp_path):
    """外审回稿：并发同键创建——查重+材料导入+创建按幂等键串行，后到者读
    首次结果（修复前：双份材料目录 + 后到者撞唯一索引 500）。写通道被占住
    期间两个请求的查重都先于任何 COMMIT 完成，竞争窗口必然打开。"""
    src = _make_source_files(tmp_path)

    async def scenario():
        release = threading.Event()
        blocker = asyncio.ensure_future(asm.channel.execute(
            lambda _conn: release.wait(5)))  # 占住写线程，压住全部 COMMIT
        await asyncio.sleep(0)

        async def releaser():
            await asyncio.sleep(0.2)  # 两个请求都已过查重点后再放行
            release.set()

        body = {"instruction": "任务Y", "client_request_id": "race-1",
                "import_files": [str(src[0])]}
        results = await asyncio.gather(
            asm.sessions.create_conversation(**body),
            asm.sessions.create_conversation(**dict(body, instruction="任务Y改")),
            releaser(),
        )
        await blocker
        assert results[0]["conversation"]["id"] == results[1]["conversation"]["id"]
        assert asm.db.read_conn.execute(
            "SELECT count(*) FROM conversations").fetchone()[0] == 1
        assert asm.db.read_conn.execute(
            "SELECT count(*) FROM task_runs").fetchone()[0] == 1
        conv_dirs = list((tmp_path / "conversations").iterdir())
        assert len(conv_dirs) == 1, "不得留下无归属材料目录"
    asyncio.run(scenario())


def test_folders_per_item_results_persisted_and_returned(asm, tmp_path):
    """外审回稿：folders 逐项结果完整保存（materials.imported 载荷），
    创建响应与幂等重试一致返回（修复前算了不存、响应缺 folders 字段）。"""
    folder = tmp_path / "授权夹"
    folder.mkdir()
    missing = tmp_path / "没有这夹"
    body = {"instruction": "带夹任务", "client_request_id": "fold-1",
            "folders": [str(folder), str(missing)]}
    with asm.client() as c:
        first = c.post("/api/conversations", headers=AUTH,
                       json=body).json()["data"]
        assert first["folders"] == [
            {"path": str(folder), "error": None},
            {"path": str(missing), "error": f"文件夹不存在：{missing}"},
        ]
        retry = c.post("/api/conversations", headers=AUTH,
                       json=body).json()["data"]
        assert retry == first  # 幂等重试逐项一致
        payload = asm.db.read_conn.execute(
            "SELECT payload FROM run_events WHERE type = 'materials.imported'"
        ).fetchone()[0]
        assert json.loads(payload)["folders"] == first["folders"]
        # 纯 folders（无 import_files）也持久化逐项结果
        only = c.post("/api/conversations", headers=AUTH,
                      json={"instruction": "只有夹",
                            "folders": [str(folder)]}).json()["data"]
        assert only["folders"] == [{"path": str(folder), "error": None}]


def test_can_continue_queue_snapshot_matches_endpoint(asm):
    """已中断尝试的历史审批失效；待核验调用阻止暂停队列继续。"""
    with asm.client() as c:
        conv_id = _create(asm, c)
        _enqueue(c, conv_id, "后续")
        task_id = asm.db.read_conn.execute(
            "SELECT id FROM task_runs WHERE conversation_id = ?"
            " ORDER BY created_at LIMIT 1", (conv_id,)).fetchone()[0]

        async def setup_paused_with_unresolved_approval():
            await asm.store.append(
                task_run_id=task_id, conversation_id=conv_id,
                type=T.RUN_STARTED,
                payload={"attempt_no": 1, "attempt_id": "att-1", "kind": "initial"})
            await asm.store.append(
                task_run_id=task_id, conversation_id=conv_id,
                type=T.PERMISSION_REQUESTED, attempt_no=1, payload={"tool_call_id": "call-y"})
            await asm.store.append(
                task_run_id=task_id, conversation_id=conv_id,
                type=T.RUN_INTERRUPTED, payload={})
            await asm.store.append(
                task_run_id=None, conversation_id=conv_id,
                type=T.QUEUE_PAUSED, payload={})
        asyncio.run(setup_paused_with_unresolved_approval())
        state = c.get(f"/api/conversations/{conv_id}/state",
                      headers=AUTH).json()["data"]
        assert state["state"] == "idle" and state["queue_paused"] is True
        assert state["queue"], "前置：暂停 + 有排队项"

        assert state["can_continue_queue"] is True

        # 待核验调用阻止新的执行。
        asm.db.write_conn.execute(
            "INSERT INTO tool_calls (id, call_id, task_run_id, tool_name,"
            " side_effect_class, input_hash, status, risk_level, prepared_at)"
            " VALUES ('tcx','call-x',?, 'bash','outcome_unknown','h',"
            " 'pending_verification','medium','t')", (task_id,))
        asm.db.write_conn.commit()
        state = c.get(f"/api/conversations/{conv_id}/state",
                      headers=AUTH).json()["data"]
        assert state["can_continue_queue"] is False
        resp = c.post(f"/api/conversations/{conv_id}/queue/continue",
                      headers=AUTH)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "PENDING_VERIFICATION"
        asm.db.write_conn.execute("DELETE FROM tool_calls WHERE call_id='call-x'")
        asm.db.write_conn.commit()

        # 核验结束后，历史审批不会阻止队列继续。
        asyncio.run(asm.store.append(
            task_run_id=task_id, conversation_id=conv_id,
            type=T.PERMISSION_RESOLVED,
            payload={"tool_call_id": "call-y", "decision": "allow_once"}))
        state = c.get(f"/api/conversations/{conv_id}/state",
                      headers=AUTH).json()["data"]
        assert state["can_continue_queue"] is True
        resp = c.post(f"/api/conversations/{conv_id}/queue/continue",
                      headers=AUTH)
        assert resp.status_code == 202


# ── S09：任务工作目录（cwd 与 scope 在会话装配处统一提供）─────────

def test_work_context_cwd_and_relative_paths(asm, tmp_path):
    (tmp_path / "授权夹").mkdir(exist_ok=True)
    with asm.client() as c:  # lifespan 关库前完成全部查询与工具执行
        resp = c.post("/api/conversations", headers=AUTH, json={
            "instruction": "处理", "folders": [str(tmp_path / "授权夹")]})
        conv_id = resp.json()["data"]["conversation"]["id"]
        task_id = asm.db.read_conn.execute(
            "SELECT id FROM task_runs WHERE conversation_id = ?",
            (conv_id,)).fetchone()[0]
        ctx = asm.sessions.build_work_context(conv_id, task_id)
        workspace = Path(ctx.cwd)
        assert workspace == (asm.data_dir / "workspaces" / "default").resolve()
        assert workspace.is_dir()

        async def scenario():
            from agentcrew_core.tools import ToolInvocation, build_default_registry
            from agentcrew_core.tools.scheduler import ToolScheduler
            sched = ToolScheduler(build_default_registry())
            r1 = await sched.run(ToolInvocation(new_call_id(), "write_file",
                                                {"path": "笔记.md", "content": "相对"}),
                                 ctx)
            assert r1.ok and (workspace / "笔记.md").read_text() == "相对"
            # 相对路径读取
            r2 = await sched.run(ToolInvocation(new_call_id(), "read_file",
                                                {"path": "笔记.md"}), ctx)
            assert r2.ok and r2.output == "相对"
            # bash 子进程 cwd = 工作目录（pwd 真实执行）
            r3 = await sched.run(ToolInvocation(new_call_id(), "bash",
                                                {"command": "pwd"}), ctx)
            assert r3.ok and r3.output.strip() == str(workspace)
            # 授权文件夹真实进 scope：夹内绝对路径可读
            target = tmp_path / "授权夹" / "材料.txt"
            target.write_text("夹内材料")
            r4 = await sched.run(ToolInvocation(
                new_call_id(), "read_file", {"path": str(target)}), ctx)
            assert r4.ok and r4.output == "夹内材料"
            # 工作目录外、scope 外 → OUT_OF_SCOPE
            outside = tmp_path / "外面.txt"
            outside.write_text("x")
            r5 = await sched.run(ToolInvocation(
                new_call_id(), "read_file", {"path": str(outside)}), ctx)
            assert not r5.ok and r5.error.startswith("OUT_OF_SCOPE")
        asyncio.run(scenario())
