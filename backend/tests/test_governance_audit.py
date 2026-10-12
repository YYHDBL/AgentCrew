"""M2-10：实际审计篡改、锚点、HTTP诊断与受控恢复。"""

import asyncio
import json
import logging
import os
import secrets
import signal
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

import httpx

import pytest

from agentcrew_server.db.audit import append_audit, recover_anchor, snapshot_chain_head, verify_with_anchor
from agentcrew_core.governance import RequestIdentity
from agentcrew_server.governance.backups import DiagnosticBackups, apply_pending_restore
from agentcrew_server.governance.audit import AuditService
from agentcrew_server.governance.resources import GovernanceError
from agentcrew_server.runtime import RuntimeState, DiagnosticInfo
from agentcrew_server.memory.store import MemoryIdentity
from test_governance_resources import resources
from test_governance_skills import governed
from test_governance_identity import server, request, demo
from test_governance_rules import authorized, request_card
from agentcrew_core.tools import ToolInvocation
from agentcrew_server.secrets import register_secret, redact
from agentcrew_server.governance.grants import Grants
from agentcrew_server.bus import EventBus
from agentcrew_server.memory.jobs import MemoryJobs
from agentcrew_server.run_manager import RunManager
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.questions import QuestionService
from agentcrew_core.tools import ToolScheduler, build_default_registry


def test_registered_anchor_cannot_disappear(governed):
    service, *_ = governed
    anchor = service.data_dir / "chain-head.txt"
    asyncio.run(service.events.channel.execute(lambda conn: snapshot_chain_head(conn, anchor)))
    assert verify_with_anchor(service.db.read_conn, anchor).ok
    anchor.unlink()
    result = verify_with_anchor(service.db.read_conn, anchor)
    assert not result.ok and "丢失" in result.reason


@pytest.mark.parametrize("value", ["-1 " + "a" * 64, "1 abc", "1 " + "g" * 64, "1 " + "a" * 64 + " extra"])
def test_anchor_format_is_strict(governed, value):
    service, *_ = governed
    anchor = service.data_dir / "chain-head.txt"
    anchor.write_text(value)
    result = verify_with_anchor(service.db.read_conn, anchor)
    assert not result.ok and "损坏" in result.reason


def test_audit_http_queries_role_and_two_level_verification(server):
    member = demo(server, "lilei")
    response = request(server, "GET", "/api/audit?limit=1")
    assert response.status_code == 200
    page = response.json()["data"]
    assert len(page["items"]) == 1 and page["next_after"]
    assert request(server, "GET", "/api/audit?limit=1&after=" + page["next_after"]).status_code == 200
    assert request(server, "GET", "/api/audit?actor=lilei&after=" + page["next_after"]).status_code == 422
    visible = request(server, "GET", "/api/audit", identity=member, user="lilei")
    assert visible.status_code == 200
    assert all(row["actor_id"] == "lilei" for row in visible.json()["data"]["items"])
    assert request(server, "POST", "/api/audit/verify", identity=member, user="lilei").status_code == 403
    verified = request(server, "POST", "/api/audit/verify")
    assert verified.status_code == 200
    assert verified.json()["data"]["internal"]["ok"]
    assert verified.json()["data"]["anchor"]["status"] == "verified"


@pytest.fixture
def backup_runtime(governed):
    service, memory, versions, sessions, skills = governed
    value = asyncio.run(memory.change(MemoryIdentity("office", "xiaowen"), "user", "owner", change_id="audit-backup-user",
        expected_revision=0, basis="所有者配置实际备份材料", operations=[{"action": "add", "text": "备份恢复时核对实际文件与审计。"}]))
    assert value.get("revision") == 1
    runtime = RuntimeState(log=logging.getLogger("audit-test"), data_dir=service.data_dir, db=service.db,
        write_channel=service.events.channel, event_store=service.events, governance=service, identities=sessions.identities,
        memory=memory, sessions=sessions, settings=sessions._settings)
    return runtime


def test_backup_is_verified_and_idempotent(backup_runtime):
    runtime = backup_runtime
    backups = DiagnosticBackups(runtime)
    async def check():
        first = await backups.create(RequestIdentity("owner", "owner"), "backup-real-once", "directory")
        assert first["verified"] and first["kind"] == "directory"
        assert await backups.create(RequestIdentity("owner", "owner"), "backup-real-once", "directory") == first
        with pytest.raises(GovernanceError, match="change_id"):
            await backups.create(RequestIdentity("owner", "owner"), "backup-real-once", "database")
        assert len(backups.list(RequestIdentity("owner", "owner"))) == 1
        assert runtime.db.read_conn.execute("SELECT count(*) FROM audit_log WHERE action='governance.backup_created'").fetchone()[0] == 1
    asyncio.run(check())


def test_directory_restore_preserves_damage_and_revalidates(backup_runtime):
    runtime = backup_runtime
    backups = DiagnosticBackups(runtime)
    async def check():
        first = await backups.create(RequestIdentity("owner", "owner"), "restore-real-dir", "directory")
        before = (runtime.data_dir / "USER.md").read_bytes()
        (runtime.data_dir / "USER.md").write_text("真实变更后的文件")
        await runtime.write_channel.execute(lambda conn: conn.execute("UPDATE audit_log SET action='damaged-record' WHERE seq=2"))
        runtime.diagnostic = DiagnosticInfo(reason="真实审计篡改")
        runtime.write_channel.read_only = True
        restored = await backups.restore(RequestIdentity("owner", "owner"), first["id"], "restore-real-request")
        assert restored["restart_required"]
        assert await backups.restore(RequestIdentity("owner", "owner"), first["id"], "restore-real-request") == restored
        runtime.write_channel.close()
        runtime.db.close()
        apply_pending_restore(runtime.data_dir)
        assert (runtime.data_dir / "USER.md").read_bytes() == before
        preserved = runtime.data_dir / restored["preserved_path"]
        assert (preserved / "USER.md").read_text() == "真实变更后的文件"
        damaged = sqlite3.connect(preserved / "agentcrew.db")
        assert damaged.execute("SELECT action FROM audit_log WHERE seq=2").fetchone()[0] == "damaged-record"
        damaged.close()
        actual = sqlite3.connect(runtime.data_dir / "agentcrew.db")
        assert verify_with_anchor(actual, runtime.data_dir / "chain-head.txt").ok
        actual.close()
        assert apply_pending_restore(runtime.data_dir) is None
    asyncio.run(check())


def test_database_restore_requires_matching_files(backup_runtime):
    runtime = backup_runtime
    backups = DiagnosticBackups(runtime)
    async def check():
        first = await backups.create(RequestIdentity("owner", "owner"), "restore-real-db", "database")
        (runtime.data_dir / "USER.md").write_text("数据库备份之后的真实文件变更")
        runtime.diagnostic = DiagnosticInfo(reason="待恢复")
        runtime.write_channel.read_only = True
        with pytest.raises(GovernanceError, match="文件"):
            await backups.restore(RequestIdentity("owner", "owner"), first["id"], "restore-db-mismatch")
        assert not (runtime.data_dir / "pending-restore.json").exists()
    asyncio.run(check())


def test_backup_modified_after_creation_is_rejected(backup_runtime):
    runtime = backup_runtime
    backups = DiagnosticBackups(runtime)
    async def check():
        first = await backups.create(RequestIdentity("owner", "owner"), "backup-real-tamper", "directory")
        payload = runtime.data_dir / "backups" / "governance" / first["id"] / "payload"
        (payload / "USER.md").write_text("实际篡改备份")
        runtime.diagnostic = DiagnosticInfo(reason="待恢复")
        runtime.write_channel.read_only = True
        with pytest.raises(GovernanceError, match="备份"):
            await backups.restore(RequestIdentity("owner", "owner"), first["id"], "restore-tampered-backup")
    asyncio.run(check())


@pytest.mark.parametrize("damage", ["UPDATE audit_log SET detail='实际损坏JSON' WHERE seq=2",
    "DELETE FROM audit_log WHERE seq=2", "DELETE FROM audit_log WHERE seq>=2"])
def test_failed_verification_stops_writes_and_preserves_chain(backup_runtime, damage):
    runtime = backup_runtime
    audit = AuditService(runtime)
    owner = RequestIdentity("owner", "owner")
    async def check():
        await runtime.write_channel.execute(lambda conn: snapshot_chain_head(conn, runtime.data_dir / "chain-head.txt"))
        external = sqlite3.connect(runtime.data_dir / "agentcrew.db")
        external.execute(damage)
        external.commit()
        external.close()
        before = [tuple(row) for row in runtime.db.read_conn.execute("SELECT * FROM audit_log ORDER BY seq")]
        result = await audit.verify(owner)
        assert not result["ok"] and runtime.diagnostic is not None
        assert runtime.write_channel.read_only
        with pytest.raises(RuntimeError, match="DIAGNOSTIC_MODE"):
            await runtime.write_channel.execute(lambda conn: conn.execute("UPDATE agents SET name='诊断禁止修改' WHERE id='xiaowen'"))
        assert [tuple(row) for row in runtime.db.read_conn.execute("SELECT * FROM audit_log ORDER BY seq")] == before
        assert audit.query(owner)["items"]
        assert not (await audit.verify(owner))["ok"]
        assert [tuple(row) for row in runtime.db.read_conn.execute("SELECT * FROM audit_log ORDER BY seq")] == before
    asyncio.run(check())


def test_verification_event_and_audit_share_one_transaction(backup_runtime):
    runtime = backup_runtime
    audit = AuditService(runtime)
    async def check():
        await runtime.write_channel.execute(lambda conn: snapshot_chain_head(conn, runtime.data_dir / "chain-head.txt"))
        assert (await audit.verify(RequestIdentity("owner", "owner")))["ok"]
        event = runtime.db.read_conn.execute("SELECT payload FROM run_events WHERE type='governance.audit_verified'").fetchone()
        body = json.loads(event[0])
        record = runtime.db.read_conn.execute("SELECT action,actor_id FROM audit_log WHERE seq=?", (body["audit_seq"],)).fetchone()
        assert tuple(record) == ("governance.audit_verified", "owner")
        assert body["internal"]["ok"] and body["anchor"]["ok"]
    asyncio.run(check())


def test_tool_result_and_audit_are_atomic(authorized):
    service, sessions, approvals, context, created = authorized
    async def check():
        def install(conn):
            conn.execute("CREATE TRIGGER reject_tool_audit BEFORE INSERT ON audit_log "
                "WHEN NEW.action='tool.completed' AND NEW.resource_id='atomic-audit-tool' "
                "BEGIN SELECT RAISE(ABORT,'实际审计写入拒绝'); END")
        await service.events.channel.execute(install)
        invocation = ToolInvocation("atomic-audit-tool", "write_file", {"path": "audit-real.txt", "content": "实际文件效果"})
        task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"],
            agent_id="xiaowen", invocation=invocation, ctx=context))
        card = await request_card(service, invocation.call_id)
        await approvals.submit(invocation.call_id, "allow_once", card["input_hash"], RequestIdentity("owner", "owner"))
        with pytest.raises(RuntimeError, match="EVENT_PERSIST_FAILED"):
            await task
        assert service.db.read_conn.execute("SELECT count(*) FROM run_events WHERE type='tool.completed' AND json_extract(payload,'$.call_id')=?", (invocation.call_id,)).fetchone()[0] == 0
        assert service.db.read_conn.execute("SELECT status FROM tool_calls WHERE call_id=?", (invocation.call_id,)).fetchone()[0] == "pending_verification"
    asyncio.run(check())


@pytest.fixture
def audit_live(tmp_path):
    root = tmp_path
    shutil.copy2(Path(os.environ["AGENTCREW_TEST_CONFIG_SOURCE"]), root / "config.json")
    token = secrets.token_urlsafe(32)
    register_secret(token)
    state = {"root": root, "token": token, "requests": [], "process": None, "client": None}
    log = (root / "audit-service.log").open("w")
    def start():
        process = subprocess.Popen([sys.executable, "-m", "agentcrew_server", "--data-dir", str(root), "--port", "8859"],
            cwd=Path(__file__).resolve().parents[1], env={**os.environ, "AGENTCREW_TOKEN": token}, stdout=subprocess.PIPE, stderr=log, text=True)
        state["process"] = process
        line = process.stdout.readline().strip()
        assert line.startswith("AGENTCREW_READY "), redact((root / "audit-service.log").read_text())
        port = json.loads(line.split(" ", 1)[1])["port"]
        state["client"] = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": "Bearer " + token}, timeout=60)
    def stop():
        state["client"].close()
        state["process"].terminate()
        assert state["process"].wait(15) == 0
    state.update(start=start, stop=stop)
    start()
    yield state
    stop()
    log.close()
    (root / "http-evidence.json").write_text(redact(json.dumps(state["requests"], ensure_ascii=False, indent=2)) + "\n")


@pytest.mark.parametrize("damage", ["middle", "delete", "truncate", "missing-anchor", "malformed-anchor"])
def test_actual_http_diagnostic_backup_restart_and_model(audit_live, damage):
    live = audit_live
    root = live["root"]
    member = demo(live, "lilei")
    assert request(live, "POST", "/api/diagnostics/backups", identity=member, user="lilei",
        body={"change_id": "member-backup", "kind": "directory"}).status_code == 403
    configured = request(live, "POST", "/api/memory/stores/soul/xiaowen?workspace_id=office&agent_id=xiaowen",
        body={"change_id": "audit-soul", "expected_revision": 0, "basis": "所有者配置恢复后的真实模型岗位",
            "text": "我是负责核查办公材料的数字员工，执行任务时使用当前授权、合法路径和真实工具结果。"})
    assert configured.status_code == 200
    verified = request(live, "POST", "/api/audit/verify")
    assert verified.status_code == 200 and verified.json()["data"]["anchor"]["status"] == "verified"
    created = request(live, "POST", "/api/diagnostics/backups", body={"change_id": "audit-directory-backup", "kind": "directory"})
    assert created.status_code == 200, created.text
    backup = created.json()["data"]
    live["stop"]()
    connection = sqlite3.connect(root / "agentcrew.db")
    if damage == "middle":
        connection.execute("UPDATE audit_log SET detail='实际篡改的非法JSON' WHERE seq=2")
    elif damage == "delete":
        connection.execute("DELETE FROM audit_log WHERE seq=2")
    elif damage == "truncate":
        connection.execute("DELETE FROM audit_log WHERE seq>=2")
    elif damage == "missing-anchor":
        (root / "chain-head.txt").unlink()
    elif damage == "malformed-anchor":
        (root / "chain-head.txt").write_text("实际损坏锚点")
    connection.commit()
    before = connection.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    before_events = connection.execute("SELECT count(*) FROM run_events").fetchone()[0]
    connection.close()
    live["start"]()
    diagnostics = request(live, "GET", "/api/diagnostics")
    assert diagnostics.status_code == 200 and diagnostics.json()["data"]["mode"] == "diagnostic"
    assert request(live, "GET", "/api/audit/export?limit=200").status_code == 200
    failed = request(live, "POST", "/api/audit/verify")
    assert failed.status_code == 200 and not failed.json()["data"]["ok"]
    assert request(live, "POST", "/api/conversations", body={"instruction": "诊断禁止执行"}).status_code == 503
    assert request(live, "POST", "/api/identity/demo", body={"change_id": "diagnostic-demo", "user_id": "wangming"}).status_code == 503
    assert request(live, "POST", "/api/diagnostics/backups", body={"change_id": "diagnostic-new-backup", "kind": "directory"}).status_code == 503
    with httpx.Client(base_url=str(live["client"].base_url)) as anonymous:
        assert anonymous.get("/api/audit").status_code == 401
    connection = sqlite3.connect(root / "agentcrew.db")
    assert connection.execute("SELECT count(*) FROM audit_log").fetchone()[0] == before
    assert connection.execute("SELECT count(*) FROM run_events").fetchone()[0] == before_events
    connection.close()
    backups = request(live, "GET", "/api/diagnostics/backups")
    assert backups.status_code == 200 and backups.json()["data"][0]["verified"]
    restore = request(live, "POST", "/api/diagnostics/restore", body={"backup_id": backup["id"], "change_id": "audit-restore"})
    assert restore.status_code == 200 and restore.json()["data"]["restart_required"]
    assert request(live, "POST", "/api/conversations", body={"instruction": "重启之前仍然禁止执行"}).status_code == 503
    live["stop"]()
    live["start"]()
    assert request(live, "GET", "/api/diagnostics").json()["data"]["mode"] == "normal"
    result = request(live, "POST", "/api/audit/verify").json()["data"]
    assert result["ok"] and result["internal"]["ok"] and result["anchor"]["ok"]
    task = request(live, "POST", "/api/conversations", body={"instruction": "仅用简短文字确认收到本条指令。", "workspace_id": "office", "agent_id": "xiaowen", "client_request_id": uuid.uuid4().hex})
    assert task.status_code == 201
    task_id = task.json()["data"]["task_run_id"]
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        runs = request(live, "GET", f'/api/conversations/{task.json()["data"]["conversation"]["id"]}/task-runs').json()["data"]
        state = next(row for row in runs if row["id"] == task_id)
        if state["status"] in {"completed", "failed"}:
            break
        time.sleep(0.1)
    assert state["status"] == "completed", state
    (root / "audit-recovery.json").write_text(redact(canonical_evidence({"damage": damage, "backup": backup,
        "restore": restore.json()["data"], "verification": result, "task_run_id": task_id, "task_status": state["status"]})))


def canonical_evidence(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def test_restore_sigkill_keeps_original_and_commits_once(backup_runtime):
    runtime = backup_runtime
    root = runtime.data_dir
    files = root / "artifacts" / "restore-persistence"
    files.mkdir(parents=True)
    for index in range(8000):
        (files / f"actual-{index:05d}.txt").write_text(f"真实持久化恢复材料{index}\n")
    async def prepare():
        backups = DiagnosticBackups(runtime)
        backup = await backups.create(RequestIdentity("owner", "owner"), "restore-sigkill-backup", "directory")
        runtime.diagnostic = DiagnosticInfo(reason="恢复过程真实中断验收")
        runtime.write_channel.read_only = True
        return await backups.restore(RequestIdentity("owner", "owner"), backup["id"], "restore-sigkill-request")
    plan = asyncio.run(prepare())
    runtime.write_channel.close()
    runtime.db.close()
    with (root / "restore-process.log").open("w") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("governance_restore_process.py")), str(root)],
            cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=log)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (root / plan["preserved_path"] / "agentcrew.db").exists():
            assert process.poll() is None, (root / "restore-process.log").read_text()
            time.sleep(0.001)
        assert (root / plan["preserved_path"] / "agentcrew.db").exists()
        process.send_signal(signal.SIGKILL)
        assert process.wait(15) == -9
    assert (root / "pending-restore.json").exists()
    result = apply_pending_restore(root)
    assert result["preserved_path"] == plan["preserved_path"]
    assert len(list(files.glob("*.txt"))) == 8000
    assert len(list((root / plan["preserved_path"] / "artifacts" / "restore-persistence").glob("*.txt"))) == 8000
    assert apply_pending_restore(root) is None
    assert len(list((root / "backups" / "governance").glob("preserved-*"))) == 1
    (root / "restore-sigkill.json").write_text(canonical_evidence({"sigkill_exit": -9, "restored": result,
        "restored_file_count": 8000, "preserved_file_count": 8000, "pending_after_restore": False}))


@pytest.mark.parametrize("link", ["directory", "dangling-file"])
def test_restore_staging_cannot_write_outside_managed_directory(backup_runtime, link):
    runtime = backup_runtime
    root = runtime.data_dir
    async def prepare():
        backups = DiagnosticBackups(runtime)
        backup = await backups.create(RequestIdentity("owner", "owner"), "restore-symlink-backup", "directory")
        runtime.diagnostic = DiagnosticInfo(reason="恢复范围核查")
        runtime.write_channel.read_only = True
        await backups.restore(RequestIdentity("owner", "owner"), backup["id"], "restore-symlink-request")
    asyncio.run(prepare())
    runtime.write_channel.close()
    runtime.db.close()
    plan = json.loads((root / "pending-restore.json").read_text())
    install = root / "backups" / "governance" / ("install-" + plan["input_hash"])
    outside = root.parent / ("outside-install-" + link)
    outside.mkdir()
    if link == "directory":
        install.symlink_to(outside, target_is_directory=True)
    else:
        install.mkdir()
        (install / "agentcrew.db").symlink_to(outside / "unexpected.sqlite")
    with pytest.raises(GovernanceError, match="暂存"):
        apply_pending_restore(root)
    assert list(outside.iterdir()) == []


def test_damaged_grant_audit_remains_exportable(backup_runtime):
    runtime = backup_runtime
    grants = Grants(runtime.governance, runtime.identities)
    row = runtime.db.read_conn.execute("SELECT id,revision FROM grants WHERE resource_type='connector' AND revoked_at IS NULL LIMIT 1").fetchone()
    asyncio.run(grants.revoke(RequestIdentity("owner", "owner"), row[0], {"expected_revision": row[1], "change_id": "audit-grant-revoke"}))
    runtime.db.write_conn.execute("UPDATE audit_log SET detail='实际损坏Grant记录' WHERE resource_type='grant'")
    rows = AuditService(runtime).query(RequestIdentity("owner", "owner"))["items"]
    assert any(row["resource_type"] == "grant" and row["detail"].get("damaged_detail") for row in rows)


def test_diagnostic_stop_with_queued_job_still_closes_runner(backup_runtime):
    runtime = backup_runtime
    runtime.bus = EventBus()
    jobs = MemoryJobs(runtime.memory, runtime.bus, runtime.settings)
    runtime.memory_jobs = jobs
    approvals = ApprovalService(runtime.db, runtime.event_store, runtime.data_dir / "chain-head.txt")
    approvals.scheduler = ToolScheduler(build_default_registry(), gate=approvals.gate)
    manager = RunManager(db=runtime.db, event_store=runtime.event_store, bus=runtime.bus,
        settings=runtime.settings, sessions=runtime.sessions, approvals=approvals, scheduler=approvals.scheduler,
        questions=QuestionService(runtime.db, runtime.event_store))
    runtime.run_manager = manager
    async def check():
        job = await jobs.curator.enqueue("office", "xiaowen", "diagnostic-queued-job")
        assert isinstance(job, str) and jobs.get(job)["status"] == "queued"
        await runtime.write_channel.execute(lambda conn: snapshot_chain_head(conn, runtime.data_dir / "chain-head.txt"))
        runtime.db.write_conn.execute("UPDATE audit_log SET action='actual-damage' WHERE seq=2")
        assert not (await AuditService(runtime).verify(RequestIdentity("owner", "owner")))["ok"]
        assert manager._shutting_down and manager._http.is_closed
        assert jobs._closed and jobs._http.is_closed
        assert jobs.get(job)["status"] == "queued"
    asyncio.run(check())


def test_anchor_sigkill_between_intent_and_file_is_recoverable(backup_runtime):
    runtime = backup_runtime
    root = runtime.data_dir
    async def prepare():
        await runtime.write_channel.execute(lambda conn: snapshot_chain_head(conn, root / "chain-head.txt"))
        await runtime.write_channel.execute(lambda conn: append_audit(conn, ts=datetime.now(timezone.utc).isoformat(), actor_type="system", actor_id="audit-test",
            action="actual-anchor-boundary", detail="{}"))
    asyncio.run(prepare())
    previous = (root / "chain-head.txt").read_bytes()
    process = subprocess.run([sys.executable, str(Path(__file__).with_name("governance_anchor_crash_process.py")), str(root)],
        cwd=Path(__file__).resolve().parents[1])
    assert process.returncode == -9
    assert (root / "chain-head.txt").read_bytes() == previous
    assert runtime.db.read_conn.execute("SELECT count(*) FROM audit_anchor_intents").fetchone()[0] == 1
    result = asyncio.run(runtime.write_channel.execute(lambda conn: recover_anchor(conn, root / "chain-head.txt")))
    assert result.ok and verify_with_anchor(runtime.db.read_conn, root / "chain-head.txt").ok
    assert runtime.db.read_conn.execute("SELECT count(*) FROM audit_anchor_intents").fetchone()[0] == 0
    after = (root / "chain-head.txt").read_bytes()
    assert after != previous
    assert asyncio.run(runtime.write_channel.execute(lambda conn: recover_anchor(conn, root / "chain-head.txt"))).ok
    assert (root / "chain-head.txt").read_bytes() == after
    (root / "anchor-sigkill.json").write_text(canonical_evidence({"sigkill_exit": process.returncode,
        "before_anchor": previous.decode().strip(), "after_anchor": after.decode().strip(),
        "audit_sequence": runtime.db.read_conn.execute("SELECT max(seq) FROM audit_log").fetchone()[0],
        "event_watermark": runtime.db.read_conn.execute("SELECT max(global_seq) FROM run_events").fetchone()[0],
        "remaining_intents": runtime.db.read_conn.execute("SELECT count(*) FROM audit_anchor_intents").fetchone()[0],
        "verification": {"ok": result.ok, "checked_rows": result.checked_count}}))


def test_actual_diagnostic_cancels_bash_with_background_queue(audit_live):
    live = audit_live
    root = live["root"]
    configured = request(live, "POST", "/api/memory/stores/soul/xiaowen?workspace_id=office&agent_id=xiaowen",
        body={"change_id": "cancel-diagnostic-soul", "expected_revision": 0, "basis": "所有者配置实际诊断取消验收",
            "text": "执行当前用户明确要求的合法工具调用，依据真实文件结果说明状态，遵守当前授权和审批。"})
    assert configured.status_code == 200
    command = "printf '%s' \"$$\" > diagnostic-pid.txt; printf 'actual' > diagnostic-effect.txt; sleep 120; printf 'forbidden' > diagnostic-delayed.txt"
    created = request(live, "POST", "/api/conversations", body={"workspace_id": "office", "agent_id": "xiaowen",
        "instruction": f"只调用bash执行下面这条完整命令，在要求审批时等待决定：{command}", "client_request_id": uuid.uuid4().hex})
    assert created.status_code == 201
    task_id = created.json()["data"]["task_run_id"]
    marker = root / "workspaces" / "office" / "files" / "diagnostic-effect.txt"
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and not marker.exists():
        cards = request(live, "GET", f"/api/task-runs/{task_id}/approvals").json()["data"]
        for card in cards:
            if not card["stale"]:
                assert request(live, "POST", f'/api/tool-approvals/{card["call_id"]}',
                    body={"decision": "allow_once", "input_hash": card["input_hash"]}).status_code == 200
        time.sleep(0.05)
    assert marker.read_text() == "actual"
    pid = int((marker.parent / "diagnostic-pid.txt").read_text())
    other = request(live, "POST", "/api/conversations", body={"workspace_id": "office", "agent_id": "xiaowen",
        "instruction": "仅用简短文字确认收到这条消息。", "client_request_id": uuid.uuid4().hex})
    assert other.status_code == 201
    connection = sqlite3.connect(root / "agentcrew.db")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if connection.execute("SELECT 1 FROM memory_jobs WHERE status='queued' LIMIT 1").fetchone():
            break
        time.sleep(0.05)
    assert connection.execute("SELECT 1 FROM memory_jobs WHERE status='queued' LIMIT 1").fetchone()
    connection.execute("UPDATE audit_log SET action='actual-active-damage' WHERE seq=2")
    connection.commit()
    result = request(live, "POST", "/api/audit/verify")
    assert result.status_code == 200 and not result.json()["data"]["ok"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not (marker.parent / "diagnostic-delayed.txt").exists()
    assert connection.execute("SELECT 1 FROM memory_jobs WHERE status='queued' LIMIT 1").fetchone()
    assert request(live, "POST", "/api/conversations", body={"instruction": "诊断中不能派发"}).status_code == 503
    (root / "diagnostic-active-cancel.json").write_text(canonical_evidence({"task_run_id": task_id,
        "other_task_run_id": other.json()["data"]["task_run_id"], "pid": pid, "process_gone": True,
        "delayed_file_exists": False, "verification": result.json()["data"]}))
    connection.close()


def test_restore_change_id_cannot_reuse_governance_mutation(backup_runtime):
    runtime = backup_runtime
    async def check():
        backups = DiagnosticBackups(runtime)
        created = await backups.create(RequestIdentity("owner", "owner"), "shared-backup-change-id", "directory")
        runtime.diagnostic = DiagnosticInfo(reason="恢复幂等核查")
        runtime.write_channel.read_only = True
        with pytest.raises(GovernanceError, match="change_id"):
            await backups.restore(RequestIdentity("owner", "owner"), created["id"], "shared-backup-change-id")
        assert not (runtime.data_dir / "pending-restore.json").exists()
    asyncio.run(check())
