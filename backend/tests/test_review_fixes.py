"""C6 第三轮外审回稿回归测试（F01–F10 / S01–S06 / S08 / S11–S13 / S15）。

纪律同 ADR-006：真实库、真实事件、真实审计链、真实进程（bash 用真
sandbox-exec）；唯一桩是本地 HTTP 服务（S02 用真监听 socket）。
"""

import asyncio
import json
import logging
import sqlite3
import sys
from pathlib import Path

import pytest

from agentcrew_core.events import RunEventType as T
from agentcrew_core.tools import ToolInvocation, WorkContext, new_call_id
from agentcrew_core.tools.builtin import seatbelt_profile
from agentcrew_core.tools.judgment import (
    bash_readonly,
    build_protected_paths,
)
from agentcrew_core.tools.scheduler import ToolScheduler, input_hash
from agentcrew_core.tools import build_default_registry
from agentcrew_server.approvals import ApprovalService, ApprovalStale
from agentcrew_server.bus import EventBus
from agentcrew_server.config import ConfigError, load_config
from agentcrew_server.db.audit import (
    GENESIS_PREV_HASH,
    compute_hash,
    snapshot_chain_head,
    verify_with_anchor,
)
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.runtime import RuntimeState

CONV, RUN, AGENT = "conv-1", "run-1", "agent-1"


class Assembly:
    def __init__(self, tmp_path: Path, name="t.db"):
        self.db = Database(tmp_path / name)
        run_migrations(self.db.write_conn, tmp_path / "backups")
        self.db.write_conn.execute(
            "INSERT OR IGNORE INTO conversations (id, workspace_id, agent_id,"
            " created_at, updated_at) VALUES (?, 'ws-1', ?, '2026-01-01',"
            " '2026-01-01')", (CONV, AGENT),
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
        self.scheduler = ToolScheduler(build_default_registry(),
                                       gate=self.approvals.gate)
        self.approvals.scheduler = self.scheduler

    def close(self):
        self.channel.close()
        self.db.close()


def _ctx(a: Assembly, tmp_path: Path, **kw) -> WorkContext:
    scope = tmp_path / "ws"
    scope.mkdir(exist_ok=True)
    return WorkContext(
        scope=[scope], protected=[], artifacts_dir=tmp_path / "art",
        task_run_id=RUN, **kw,
    )


def _start_tool(a: Assembly, tmp_path: Path, tool: str, inp: dict):
    inv = ToolInvocation(new_call_id(), tool, inp)
    return inv, asyncio.ensure_future(a.approvals.run_tool(
        task_run_id=RUN, conversation_id=CONV, agent_id=AGENT,
        invocation=inv, ctx=_ctx(a, tmp_path)))


async def _wait_for_request(a: Assembly, timeout=5) -> str:
    """等待**尚未决定**的审批卡（库里可能已有历史 requested）。"""
    import time as _t

    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        rows = a.db.read_conn.execute(
            "SELECT json_extract(payload, '$.tool_call_id') FROM run_events r"
            " WHERE type = 'permission.requested' AND NOT EXISTS ("
            "  SELECT 1 FROM run_events x WHERE x.type = 'permission.resolved'"
            "  AND json_extract(x.payload, '$.tool_call_id') ="
            "      json_extract(r.payload, '$.tool_call_id'))").fetchall()
        if rows:
            return rows[0][0]
        await asyncio.sleep(0.05)
    raise AssertionError("等待审批卡超时")


def _resolutions(a: Assembly, call_id: str):
    return a.db.read_conn.execute(
        "SELECT payload, created_at FROM run_events WHERE"
        " type = 'permission.resolved'"
        " AND json_extract(payload, '$.tool_call_id') = ?"
        " ORDER BY global_seq", (call_id,)).fetchall()


# ── F02/F03：单事务决定（查重+事件+审计+规则原子提交）──────────────

def test_f02_concurrent_submit_single_resolution(tmp_path):
    """16 个并发决定：库中恰 1 条 resolution，全部响应同一 decided_at。"""

    async def scenario():
        a = Assembly(tmp_path)
        inv, task = _start_tool(a, tmp_path, "write_file",
                                {"path": str(tmp_path / "ws" / "c.txt"),
                                 "content": "c"})
        call_id = await _wait_for_request(a)
        results = await asyncio.gather(*[
            a.approvals.submit(call_id, "allow_once") for _ in range(16)
        ], return_exceptions=True)
        awaited = await asyncio.wait_for(task, timeout=10)
        assert awaited.ok
        rows = _resolutions(a, call_id)
        assert len(rows) == 1, f"并发提交产生了 {len(rows)} 条决定"
        decided = {(r["decision"], r["decided_at"])
                   for r in results if isinstance(r, dict)}
        assert decided == {("allow_once", json.loads(rows[0][0])["decision"]
                            and rows[0][1])}
        # 审计链恰好一条 resolved
        actions = [r[0] for r in a.db.read_conn.execute(
            "SELECT action FROM audit_log ORDER BY seq").fetchall()]
        assert actions.count("permission.resolved:allow_once") == 1
        a.close()

    asyncio.run(scenario())


def test_f03_audit_failure_rolls_back_and_retry_completes(tmp_path):
    """审计被真实 trigger 阻断：决定事件不得落库；解除后重试补齐规则。"""

    async def scenario():
        a = Assembly(tmp_path)
        inv, task = _start_tool(a, tmp_path, "write_file",
                                {"path": str(tmp_path / "ws" / "d.txt"),
                                 "content": "d"})
        call_id = await _wait_for_request(a)
        a.db.write_conn.execute(
            "CREATE TRIGGER block_audit BEFORE INSERT ON audit_log"
            " BEGIN SELECT RAISE(ABORT, 'audit blocked'); END")
        with pytest.raises(sqlite3.IntegrityError):
            await a.approvals.submit(call_id, "allow_always")
        # 决定事件必须已回滚（F03 的核心：不允许"事件在、规则永缺"的半提交）
        assert _resolutions(a, call_id) == []
        rules = a.db.read_conn.execute(
            "SELECT count(*) FROM agent_permission_rules").fetchone()[0]
        assert rules == 0
        # 幂等重试在解除阻断后必须**完整**补齐（事件+审计+规则）并唤醒
        a.db.write_conn.execute("DROP TRIGGER block_audit")
        decision = await a.approvals.submit(call_id, "allow_always")
        assert decision["idempotent_replay"] is False
        assert len(_resolutions(a, call_id)) == 1
        rules2 = a.db.read_conn.execute(
            "SELECT count(*) FROM agent_permission_rules").fetchone()[0]
        assert rules2 == 1
        assert (await asyncio.wait_for(task, timeout=10)).ok
        a.close()

    asyncio.run(scenario())


def test_f01_no_transaction_conflict_under_mixed_writes(tmp_path):
    """决定写入与事件写入并发走同一写通道：无 transaction 嵌套错误。"""

    async def scenario():
        a = Assembly(tmp_path)
        inv, task = _start_tool(a, tmp_path, "write_file",
                                {"path": str(tmp_path / "ws" / "e.txt"),
                                 "content": "e"})
        call_id = await _wait_for_request(a)
        # 决定提交与另一路事件追加并发（PERMISSION_REQUESTED 投影为 no-op）
        other = asyncio.ensure_future(a.store.append(
            task_run_id=RUN, conversation_id=CONV,
            type=T.PERMISSION_REQUESTED,
            payload={"tool_call_id": "concurrent-1", "tool": "write_file",
                     "risk": "medium", "options": [], "input_hash": "",
                     "target": "", "always_scope_preview": ""}))
        decision = await a.approvals.submit(call_id, "allow_once")
        await other
        await asyncio.wait_for(task, timeout=10)
        assert decision["decision"] == "allow_once"
        ok = verify_with_anchor(a.db.write_conn, tmp_path / "chain-head.txt")
        assert ok.ok
        a.close()

    asyncio.run(scenario())


# ── F04：审批哈希绑定不可变快照 ────────────────────────────────────

def test_f04_input_mutation_during_pending_does_not_hijack(tmp_path):
    """挂起期间调用方改写 input dict：执行的仍是审批卡哈希对应的内容。"""

    async def scenario():
        a = Assembly(tmp_path)
        approved = tmp_path / "ws" / "approved.txt"
        inv, task = _start_tool(a, tmp_path, "write_file",
                                {"path": str(approved), "content": "approved"})
        call_id = await _wait_for_request(a)
        card = (await a.approvals.list_approvals(RUN, "pending"))[0]
        # 等待期间"模型线程"改参数（同 dict 对象原地改）
        inv.input["path"] = str(tmp_path / "ws" / "unapproved.txt")
        inv.input["content"] = "PWNED"
        await a.approvals.submit(call_id, "allow_once",
                                 client_input_hash=card["input_hash"])
        result = await asyncio.wait_for(task, timeout=10)
        assert result.ok
        # 批的是 approved.txt 就只能写 approved.txt
        assert approved.read_text() == "approved"
        assert not (tmp_path / "ws" / "unapproved.txt").exists()
        a.close()

    asyncio.run(scenario())


# ── F05：bash 永久授权范围 ─────────────────────────────────────────

def test_f05_allow_always_bash_does_not_cover_appended_commands(tmp_path):
    """allow_always echo 之后：追加命令与变形命令都必须重新弹卡。"""

    async def scenario():
        a = Assembly(tmp_path)
        _, t1 = _start_tool(a, tmp_path, "bash", {"command": "echo hi"})
        call_id = await _wait_for_request(a)
        await a.approvals.submit(call_id, "allow_always")
        assert (await asyncio.wait_for(t1, timeout=10)).ok
        rules = a.db.read_conn.execute(
            "SELECT pattern FROM agent_permission_rules").fetchall()
        assert rules == [("echo hi",)]  # pattern = 完整命令，非首词
        # 追加命令：不命中规则 → 重新弹卡
        _, t2 = _start_tool(a, tmp_path, "bash",
                            {"command": "echo okay; touch pwned"})
        call2 = await _wait_for_request(a)
        await a.approvals.submit(call2, "reject_once")
        assert not (await asyncio.wait_for(t2, timeout=10)).ok
        assert not (tmp_path / "ws" / "pwned").exists()
        a.close()

    asyncio.run(scenario())


# ── F06：只读判定绕过 ──────────────────────────────────────────────

def test_f06_single_ampersand_and_shadow_script_not_readonly(tmp_path):
    ok, reason = bash_readonly("cat /dev/null & touch /tmp/x")
    assert not ok and "&" in reason
    ok2, _ = bash_readonly("echoSomething")
    assert not ok2
    # 工作区内名为 cat 的可执行脚本：含路径成分 → 非只读
    ok3, reason3 = bash_readonly("./cat data")
    assert not ok3
    # 白名单本体仍放行
    ok4, _ = bash_readonly("cat readme.md")
    assert ok4


# ── F07：辅助文件保护 + 不存在路径保留 ────────────────────────────

def test_f07_wal_shm_protected(tmp_path):
    protected = {str(p) for p in build_protected_paths(tmp_path)}
    assert str(tmp_path / "agentcrew.db-wal") in protected
    assert str(tmp_path / "agentcrew.db-shm") in protected


@pytest.mark.skipif(sys.platform != "darwin",
                    reason="Seatbelt 仅 macOS")
def test_f07_sandbox_denies_nonexistent_protected_path(tmp_path):
    """受保护但尚未创建的路径：bash 沙盒内创建即被拒（真 sandbox-exec）。"""

    async def scenario():
        a = Assembly(tmp_path)
        scope = tmp_path / "ws"
        secret = scope / "notyet-config.json"
        ctx = WorkContext(scope=[scope], protected=[secret],
                          artifacts_dir=tmp_path / "art", task_run_id=RUN)
        from agentcrew_core.tools.builtin import _bash
        result = await _bash(
            ToolInvocation(new_call_id(), "bash",
                           {"command": f"echo pwned > '{secret}'"}), ctx)
        assert not result.ok, "受保护路径被写入"
        assert not secret.exists()
        a.close()

    asyncio.run(scenario())


# ── F08：write_file 符号链接竞争 ───────────────────────────────────

def test_f08_parent_dir_swap_during_event_wait(tmp_path):
    """artifact.created 落库窗口内父目录被换成指向 scope 外的符号链接：
    写入必须失败（绝不落进 scope 外）。"""

    async def scenario():
        a = Assembly(tmp_path)
        scope = tmp_path / "ws"
        target_dir = scope / "sub"
        target_dir.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        target = target_dir / "out.txt"
        ctx = WorkContext(scope=[scope], protected=[],
                          artifacts_dir=tmp_path / "art", task_run_id=RUN)
        inv = ToolInvocation(new_call_id(), "write_file",
                             {"path": str(target), "content": "hi"})

        async def evil_emit(event_type, payload):
            if event_type == "artifact.created":  # 检查后、写入前的窗口
                target_dir.rename(tmp_path / "sub-moved")
                target_dir.symlink_to(outside, target_is_directory=True)

        ctx.emit = evil_emit
        from agentcrew_core.tools.builtin import _write_file
        result = await _write_file(inv, ctx)
        assert not result.ok and "PATH_CHANGED" in result.error
        assert not (outside / "out.txt").exists(), "写进了 scope 外目录"
        a.close()

    asyncio.run(scenario())


# ── F09：完整结果入事件 ────────────────────────────────────────────

def test_f09_full_output_persisted_in_terminal_event(tmp_path):
    """5000 字符输出：事件载荷带全文与 details（不只有 2000 字摘要）。"""

    async def scenario():
        events = []

        async def emit(event_type, payload):
            events.append((event_type, payload))

        ctx = WorkContext(scope=[tmp_path], protected=[],
                          artifacts_dir=tmp_path / "art", task_run_id=RUN,
                          emit=emit)
        big = tmp_path / "big.txt"
        big.write_text("x" * 5000)
        scheduler = ToolScheduler(build_default_registry())
        result = await scheduler.run(
            ToolInvocation(new_call_id(), "read_file", {"path": str(big)}), ctx)
        assert result.ok and len(result.output) == 5000
        completed = next(p for t, p in events if t == "tool.completed")
        assert completed["output"] == "x" * 5000
        assert len(completed["output_summary"]) == 2000
        assert completed["details"]["total_lines"] == 1

    asyncio.run(scenario())


# ── F10：诊断模式不覆盖锚点 ────────────────────────────────────────

def test_f10_diagnostic_shutdown_keeps_anchor(tmp_path):
    a = Assembly(tmp_path, "anchor.db")
    a.db.write_conn.execute(
        "INSERT INTO audit_log (seq, ts, actor_type, actor_id, action,"
        " resource_type, resource_id, detail, prev_hash, hash)"
        " VALUES (1, '2026-01-01', 'system', 'gate', 'permission.requested',"
        " 'tool_call', 'c1', '{}', ?, ?)",
        (GENESIS_PREV_HASH,
         compute_hash(1, "2026-01-01", "system", "gate", "permission.requested",
                      "tool_call", "c1", "{}", GENESIS_PREV_HASH)),
    )
    snapshot_chain_head(a.db.write_conn, tmp_path / "chain-head.txt")
    anchor_before = (tmp_path / "chain-head.txt").read_text()
    # 篡改库内链（模拟只改数据库不改锚点文件的攻击）
    a.db.write_conn.execute(
        "UPDATE audit_log SET action='permission.resolved' WHERE seq=1")
    runtime = RuntimeState(log=logging.getLogger("t"), data_dir=tmp_path,
                           db=a.db, diagnostic=object())  # 诊断模式
    asyncio.run(runtime.shutdown())
    assert (tmp_path / "chain-head.txt").read_text() == anchor_before, \
        "诊断模式退出了也不得覆盖可信锚点"
    # 正常模式下链已失守：同样不得快照
    runtime2 = RuntimeState(log=logging.getLogger("t2"), data_dir=tmp_path,
                            db=Database(tmp_path / "anchor.db"))
    asyncio.run(runtime2.shutdown())
    assert (tmp_path / "chain-head.txt").read_text() == anchor_before
    runtime2.db.close()
    a.close()


# ── S02：人工批准接通 http_request ─────────────────────────────────

def test_s02_approved_http_passes_host_gate(tmp_path):
    """allowed_hosts 为空 + 人工 allow_once：请求真实发出（本地真服务）。"""

    async def scenario():
        from http.server import BaseHTTPRequestHandler, HTTPServer

        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"pong")

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        serve = asyncio.ensure_future(asyncio.to_thread(
            server.handle_request))  # 处理一次后退出

        a = Assembly(tmp_path)
        _, task = _start_tool(a, tmp_path, "http_request",
                              {"url": f"http://127.0.0.1:{port}/ping"})
        call_id = await _wait_for_request(a)
        await a.approvals.submit(call_id, "allow_once")
        result = await asyncio.wait_for(task, timeout=10)
        await asyncio.wait_for(serve, timeout=5)
        assert result.ok, f"人工批准后仍被拒：{result.error}"
        assert seen == ["/ping"]
        server.server_close()
        a.close()

    asyncio.run(scenario())


# ── S11：投影不填未发生的时间 ──────────────────────────────────────

def test_s11_denied_call_has_no_dispatched_at(tmp_path):
    """prepared 后被拒：dispatched_at/completed_at 都必须是 NULL。"""

    async def scenario():
        a = Assembly(tmp_path)
        _, task = _start_tool(a, tmp_path, "write_file",
                              {"path": str(tmp_path / "ws" / "no.txt"),
                               "content": "n"})
        call_id = await _wait_for_request(a)
        await a.approvals.submit(call_id, "reject_once")
        await asyncio.wait_for(task, timeout=10)
        row = a.db.read_conn.execute(
            "SELECT status, dispatched_at, completed_at FROM tool_calls"
            " WHERE call_id = ?", (call_id,)).fetchone()
        assert row[0] == "failed"
        assert row[1] is None, "未派发的调用被填了 dispatched_at"
        assert row[2] is not None  # failed 是终态：completed_at 有值
        a.close()

    asyncio.run(scenario())


# ── S13：任务 SSE 排他游标 ─────────────────────────────────────────

def test_s13_task_stream_from_head_no_duplicate(tmp_path):
    """from=head 时实时段不得重发 ≤ 游标的旧事件。"""

    async def scenario():
        from fastapi.testclient import TestClient

        from agentcrew_server.api.app import create_app

        a = Assembly(tmp_path, "sse.db")
        await a.store.append(task_run_id=RUN, conversation_id=CONV,
                             type=T.QUESTION_REQUESTED, payload={"q": 1})
        ev2 = await a.store.append(task_run_id=RUN, conversation_id=CONV,
                                   type=T.QUESTION_ANSWERED, payload={"a": 2})
        runtime = RuntimeState(log=logging.getLogger("t"), data_dir=tmp_path,
                               db=a.db, token="tk", bus=a.bus,
                               event_store=a.store, approvals=a.approvals)
        app = create_app(runtime)
        with TestClient(app) as client:
            with client.stream(
                "GET", f"/api/task-runs/{RUN}/stream?from={ev2.seq}",
                headers={"Authorization": "Bearer tk"},
            ) as resp:
                seqs = []
                for line in resp.iter_lines():
                    if line.startswith("data:"):
                        seqs.append(json.loads(line[5:])["seq"])
        assert seqs == [], f"排他游标被违反：from={ev2.seq} 却收到 {seqs}"
        a.close()

    asyncio.run(scenario())


# ── S15：流终态与结构校验 ──────────────────────────────────────────

def test_s15_non_object_data_is_provider_error():
    from agentcrew_core.provider.glm_anthropic import GLMAnthropicProvider
    from agentcrew_core.provider.types import ErrorClass

    async def scenario():
        async def chunks():
            yield b'data: []\n\n'

        provider = GLMAnthropicProvider(
            {"main": __import__(
                "agentcrew_core.provider.glm_anthropic", fromlist=["SlotConfig"]
            ).SlotConfig(model="m", api_key="k")},
            max_retries=0,
        )
        with pytest.raises(Exception) as ei1:
            async for _ in provider._consume(chunks()):
                pass
        assert "非事件对象" in str(ei1.value)

        async def chunks2():
            yield (b'data: {"type":"content_block_start","index":0,'
                   b'"content_block":{"type":"tool_use","id":"t1","name":"f"}}\n\n')
            yield b'data: {"type":"message_stop"}\n\n'

        got = []
        with pytest.raises(Exception) as ei:
            async for e in provider._consume(chunks2()):
                got.append(e)
        # tool_call_started（"进行中"信号）合法；禁止的是 usage/done 假装成功
        assert all(e.type == "tool_call_started" for e in got)
        assert not any(e.type in ("usage", "done", "tool_call") for e in got)
        assert "未关闭" in str(ei.value)

    asyncio.run(scenario())


# ── S10：哈希规范化无歧义 ──────────────────────────────────────────

def test_s10_hash_no_field_boundary_ambiguity():
    h1 = compute_hash(1, "t", "user", "owner", "permission|resolved",
                      "tool_call", "c1", "{}", GENESIS_PREV_HASH)
    h2 = compute_hash(1, "t", "user", "owner|permission", "resolved",
                      "tool_call", "c1", "{}", GENESIS_PREV_HASH)
    assert h1 != h2, "字段边界歧义：相邻字段互换必须改变哈希"


# ── S12：配置值非法明确拒绝 ────────────────────────────────────────

def test_s12_bad_config_rejected_at_load(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"models": {"main": "glm-4.6"}}', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path, env={})
    (tmp_path / "config.json").write_text(
        '{"gates": {"max_steps": -5}}', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path, env={})


# ── S04：读取限额与外部化缺失 ──────────────────────────────────────

def test_s04_read_file_externalization_missing(tmp_path):
    """artifacts_dir 未配置 + 超限输出：明确报错而非内联。"""

    async def scenario():
        from agentcrew_core.tools.builtin import _read_file

        big = tmp_path / "big2.txt"
        big.write_text("y" * 40000)
        ctx = WorkContext(scope=[tmp_path], protected=[],
                          artifacts_dir=None, task_run_id=RUN)
        result = await _read_file(
            ToolInvocation(new_call_id(), "read_file",
                           {"path": str(big), "limit": 10_000_000}), ctx)
        assert not result.ok and result.error == "EXTERNALIZATION_UNAVAILABLE"

    asyncio.run(scenario())


# ── S05：正常退出也收割后台子进程 ──────────────────────────────────

@pytest.mark.skipif(sys.platform != "darwin",
                    reason="sandbox-exec 路径仅 macOS 验证")
def test_s05_background_child_reaped_after_normal_exit(tmp_path):
    import subprocess

    async def scenario():
        from agentcrew_core.tools.builtin import _bash

        ctx = WorkContext(scope=[tmp_path], protected=[],
                          artifacts_dir=tmp_path / "art", task_run_id=RUN)
        result = await _bash(
            ToolInvocation(new_call_id(), "bash",
                           {"command": "sleep 30 & echo started"}), ctx)
        assert result.ok
        await asyncio.sleep(0.3)  # 留出清理窗口
        leftover = subprocess.run(
            ["pgrep", "-f", "sleep 30"], capture_output=True, text=True)
        assert leftover.returncode != 0, \
            f"后台子进程未被收割：{leftover.stdout}"

    asyncio.run(scenario())


# ── S06：SBPL 转义 ─────────────────────────────────────────────────

def test_s06_sbpl_profile_escapes_paths(tmp_path):
    weird = str(tmp_path / 'qu"ote')
    profile = seatbelt_profile([weird], [weird])
    assert '\\"' in profile and f'"{weird}"' not in profile


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt 仅 macOS")
def test_s06_sbpl_escaped_profile_matches_real_dir(tmp_path):
    """真 sandbox-exec：转义后的引号目录 allow 生效（写入成功）。"""
    import os
    import subprocess

    async def scenario():
        d = tmp_path / 'qu"ote'
        d.mkdir()
        real = str(d.resolve())
        profile = seatbelt_profile([real], [])
        probe = d / "probe.txt"
        r = subprocess.run(
            ["/usr/bin/sandbox-exec", "-p", profile, "/usr/bin/touch",
             str(probe)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert probe.exists()

    asyncio.run(scenario())


# ── S08：dispatched 紧邻执行 ───────────────────────────────────────

def test_s08_dispatched_after_slots(tmp_path):
    """串行锁被占时：dispatched 事件不得先于执行出现。"""

    async def scenario():
        order = []

        async def emit(event_type, payload):
            order.append(event_type)

        ctx = WorkContext(scope=[tmp_path], protected=[],
                          artifacts_dir=tmp_path / "art", task_run_id=RUN,
                          emit=emit)
        scheduler = ToolScheduler(build_default_registry(), max_concurrency=1)
        slow = asyncio.ensure_future(scheduler.run(
            ToolInvocation(new_call_id(), "bash",
                           {"command": "sleep 1"}), ctx))
        await asyncio.sleep(0.2)  # 第一个调用已取得名额
        second = asyncio.ensure_future(scheduler.run(
            ToolInvocation(new_call_id(), "bash",
                           {"command": "echo hi"}), ctx))
        await asyncio.sleep(0.2)  # 第二个在等名额
        # 等待中的调用：只有 prepared，没有 dispatched
        pending_dispatched = order.count("tool.dispatched")
        await asyncio.gather(slow, second)
        assert pending_dispatched == 1, \
            f"等待执行名额的调用被提前记为 dispatched：{order}"
        assert order.count("tool.dispatched") == 2

    asyncio.run(scenario())
