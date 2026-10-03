"""M2-05：真实Grant请求、模型能力声明、串行派发及实时撤销。"""

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

from agentcrew_core.events import RunEventType as T
from agentcrew_core.governance import RequestIdentity
from agentcrew_core.tools import ToolInvocation, ToolScheduler, build_default_registry
from agentcrew_core.tools.scheduler import input_hash
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.governance.grants import Grants
from agentcrew_server.governance.resources import GovernanceError
from agentcrew_server.memory.store import MemoryIdentity
from agentcrew_server.secrets import register_secret, redact
from test_governance_skills import governed
from test_governance_resources import resources


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    root = tmp_path_factory.mktemp("grant-http")
    shutil.copy2(Path(os.environ["AGENTCREW_TEST_CONFIG_SOURCE"]), root / "config.json")
    token = secrets.token_urlsafe(32)
    register_secret(token)
    observer = Path(__file__).resolve().parents[2] / "scripts/memory/observed_server.py"
    log = (root / "stderr.log").open("w")
    process = subprocess.Popen([sys.executable, str(observer), "--data-dir", str(root), "--port", "8841"],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, "AGENTCREW_TOKEN": token,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "MEMORY_REQUEST_EVIDENCE": str(root / "requests.jsonl")},
        stdout=subprocess.PIPE, stderr=log, text=True)
    try:
        ready = process.stdout.readline().strip()
        assert ready.startswith("AGENTCREW_READY "), redact((root / "stderr.log").read_text())
        port = json.loads(ready.split(" ", 1)[1])["port"]
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": "Bearer " + token}, timeout=30)
        evidence = {"root": root, "client": client, "requests": [], "process": process,
            "command": [sys.executable, str(observer), "--data-dir", str(root), "--port", str(port)]}
        for employee, workspace, text in (("xiaogang", "analytics", "我是负责核查数据来源与查询结果的数据分析员工。执行时遵守当前工具授权，核对真实输出并记录依据。"),
                ("xiaowen", "office", "我是负责整理材料与核查输出的办公助理。执行时读取当前授权，按实际文件和工具结果说明工作情况。")):
            response = http(evidence, "POST", f"/api/memory/stores/soul/{employee}?workspace_id={workspace}&agent_id={employee}",
                {"change_id": "configure-soul-" + employee, "expected_revision": 0, "basis": "所有者为治理验收明确配置岗位自我认知", "text": text})
            assert response.status_code == 200, response.text
        yield evidence
        (root / "http-evidence.json").write_text(json.dumps(evidence["requests"], ensure_ascii=False, indent=2) + "\n")
        client.close()
    finally:
        active = evidence["process"] if "evidence" in locals() else process
        if active.poll() is None:
            active.terminate()
        active.wait(15)
        log.close()


def http(live, method, path, body=None):
    response = live["client"].request(method, path, json=body)
    live["requests"].append({"method": method, "path": path, "request": body, "status": response.status_code,
        "response": json.loads(redact(response.text)) if response.content else None})
    return response


def sql(live, statement, params=()):
    connection = sqlite3.connect(live["root"] / "agentcrew.db")
    connection.row_factory = sqlite3.Row
    rows = [dict(row) for row in connection.execute(statement, params)]
    connection.close()
    return rows


def until(live, query, params=(), seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        rows = sql(live, query, params)
        if rows:
            return rows
        time.sleep(0.1)
    raise AssertionError("实际状态未到达：" + query)


def task(live, agent, workspace, instruction):
    response = http(live, "POST", "/api/conversations", {"workspace_id": workspace, "agent_id": agent,
        "instruction": instruction, "client_request_id": uuid.uuid4().hex})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_actual_model_declares_no_http_for_xiaogang(live):
    run = task(live, "xiaogang", "analytics", "请说明你当前是否具有HTTP工具，直接回复实际情况，无需调用其他工具。")
    task_id = run["task_run_id"]
    terminal = until(live, "SELECT status FROM task_runs WHERE id=? AND status IN ('completed','failed')", (task_id,))
    assert terminal[0]["status"] == "completed", sql(live, "SELECT type,payload FROM run_events WHERE task_run_id=?", (task_id,))
    requests = [json.loads(line) for line in (live["root"] / "requests.jsonl").read_text().splitlines()]
    actual = [item for item in requests if item["session_id"] == run["conversation"]["id"] and item["body"]["messages"][0]["content"].startswith("你是 AgentCrew")]
    assert actual
    assert all("http_request" not in [tool["function"]["name"] for tool in item["body"]["tools"]] for item in actual)
    record = {"task_run_id": task_id, "conversation_id": run["conversation"]["id"], "status": terminal[0]["status"],
        "actual_requests": actual, "reply": sql(live, "SELECT content FROM messages WHERE task_run_id=? AND role='assistant'", (task_id,))}
    (live["root"] / "no-http-model.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def test_grant_http_conflicts_pagination_regrant_and_scope(live):
    grants = http(live, "GET", "/api/grants?workspace_id=office").json()["data"]["items"]
    original = next(item for item in grants if item["resource_type"] == "skill" and item["grantee_id"] == "xiaowen")
    body = {"change_id": "revoke-skill-http", "expected_revision": original["revision"]}
    revoked = http(live, "DELETE", f"/api/grants/{original['id']}", body)
    assert revoked.status_code == 200, revoked.text
    assert http(live, "DELETE", f"/api/grants/{original['id']}", body).json() == revoked.json()
    assert http(live, "DELETE", f"/api/grants/{original['id']}", {**body, "change_id": "stale-revocation"}).status_code == 409
    assert http(live, "POST", "/api/grants", {"change_id": "cross-workspace", "resource_type": "skill", "resource_id": original["resource_id"],
        "grantee_type": "agent", "grantee_id": "xiaogang"}).status_code == 403
    request = {"change_id": "regrant-skill-http", "resource_type": "skill", "resource_id": original["resource_id"], "grantee_type": "agent", "grantee_id": "xiaowen"}
    new = http(live, "POST", "/api/grants", request)
    assert new.status_code == 200 and new.json()["data"]["id"] != original["id"]
    assert http(live, "POST", "/api/grants", request).json() == new.json()
    first = http(live, "GET", "/api/grants?limit=1").json()["data"]
    assert first["next_after"]
    second = http(live, "GET", "/api/grants?limit=1&after=" + first["next_after"]).json()["data"]
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert http(live, "GET", "/api/grants?limit=1&workspace_id=office&after=" + first["next_after"]).status_code == 422


def test_waiting_approval_revocation_prevents_dispatch(live):
    run = task(live, "xiaowen", "office", "请使用http_request向https://example.com发送POST，正文为grant-revocation-test。使用当前已授权HTTP连接器。")
    task_id = run["task_run_id"]
    requested = until(live, "SELECT global_seq,payload FROM run_events WHERE task_run_id=? AND type='permission.requested'", (task_id,))
    approval = json.loads(requested[-1]["payload"])
    grants = http(live, "GET", "/api/grants?workspace_id=office").json()["data"]["items"]
    grant = next(item for item in grants if item["resource_type"] == "connector" and item["grantee_id"] == "xiaowen")
    revoked = http(live, "DELETE", f"/api/grants/{grant['id']}", {"change_id": "revoke-waiting-http", "expected_revision": grant["revision"]})
    assert revoked.status_code == 200
    until(live, "SELECT status FROM task_runs WHERE id=? AND status IN ('failed','cancelled')", (task_id,))
    submitted = http(live, "POST", f"/api/tool-approvals/{approval['tool_call_id']}", {"decision": "allow_once", "input_hash": approval["input_hash"]})
    assert submitted.status_code in {403, 409}
    assert sql(live, "SELECT call_id FROM tool_calls WHERE task_run_id=? AND dispatched_at IS NOT NULL", (task_id,)) == []
    binding = sql(live, "SELECT agent_spec,skill_versions FROM task_governance WHERE task_run_id=?", (task_id,))[0]
    assert "office-http" in json.loads(binding["agent_spec"])["connector_ids"]
    fresh = task(live, "xiaowen", "office", "请直接回复你当前HTTP工具是否可用，无需调用任何工具。")
    until(live, "SELECT status FROM task_runs WHERE id=? AND status='completed'", (fresh["task_run_id"],))
    check = sql(live, "SELECT payload FROM run_events WHERE task_run_id=? AND type='llm.request_started'", (fresh["task_run_id"],))
    assert check and all("http_request" not in json.loads(row["payload"])["tool_names"] for row in check)
    (live["root"] / "waiting-revocation.json").write_text(json.dumps({"task_run_id": task_id,
        "new_task_id": fresh["task_run_id"], "approval": approval, "grant": revoked.json()["data"], "historical_binding": binding,
        "new_requests": check, "dispatched_count": 0}, ensure_ascii=False, indent=2) + "\n")


def test_current_grant_is_checked_in_serial_dispatch(governed):
    service, memory, versions, sessions, skills = governed
    grants = Grants(service, sessions.identities)
    service.events.authorization_checker = grants.check_dispatch
    async def check():
        created = await sessions.create_conversation(instruction="串行授权边界核查", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        tid, cid = created["task_run_id"], created["conversation"]["id"]
        await service.events.append(task_run_id=tid, conversation_id=cid, type=T.RUN_STARTED, payload={"attempt_no": 1, "attempt_id": "serial-attempt"})
        invocation = ToolInvocation("prepared-http", "http_request", {"url": "https://example.com", "method": "POST"})
        await service.events.append(task_run_id=tid, conversation_id=cid, type=T.TOOL_PREPARED, payload={"call_id": invocation.call_id,
            "tool_name": invocation.name, "input": invocation.input, "input_hash": input_hash(invocation.input), "risk_level": "medium", "side_effect_class": "outcome_unknown"})
        grant_id = service.db.read_conn.execute("SELECT id FROM grants WHERE resource_type='connector' AND grantee_id='xiaowen' AND revoked_at IS NULL").fetchone()[0]
        await grants.revoke(RequestIdentity("owner", "owner"), grant_id, {"change_id": "serial-revoke", "expected_revision": 1})
        with pytest.raises(GovernanceError, match="没有所选HTTP"):
            await service.events.append(task_run_id=tid, conversation_id=cid, type=T.TOOL_DISPATCHED, payload={"call_id": invocation.call_id})
        assert service.db.read_conn.execute("SELECT status FROM tool_calls WHERE call_id=?", (invocation.call_id,)).fetchone()[0] == "prepared"
        assert "http_request" not in [schema["name"] for schema in grants.schemas(tid, build_default_registry().schemas())[0]]
    asyncio.run(check())


def test_dispatched_bash_cancellation_preserves_effect_and_stops_children(live):
    marker = live["root"] / "workspaces/office/files/dispatched-effect.txt"
    delayed = marker.with_name("delayed-effect.txt")
    child_pid = marker.with_name("child-pid.txt")
    command = "printf 'actual-dispatched-effect' > dispatched-effect.txt; printf '%s' $$ > child-pid.txt; sleep 30; printf 'unexpected-late-effect' > delayed-effect.txt"
    run = task(live, "xiaowen", "office", "请现在调用bash执行下面这条完整命令，保持相对路径。这项操作用于核查工作区内的真实进程取消，请按审批流程提交：\n" + command)
    tid = run["task_run_id"]
    deadline = time.monotonic() + 180
    processed = set()
    approval = None
    while time.monotonic() < deadline:
        for row in sql(live, "SELECT payload FROM run_events WHERE task_run_id=? AND type='permission.requested'", (tid,)):
            card = json.loads(row["payload"])
            if card["tool_call_id"] in processed:
                continue
            call = sql(live, "SELECT input FROM tool_calls WHERE call_id=?", (card["tool_call_id"],))[0]
            observed = json.loads(call["input"]).get("command")
            assert observed in {command, "pwd && ls -la"}, call
            response = http(live, "POST", "/api/tool-approvals/" + card["tool_call_id"], {"decision": "allow_once", "input_hash": card["input_hash"]})
            assert response.status_code == 200, response.text
            processed.add(card["tool_call_id"])
            if observed == command:
                approval = card
        if approval is not None and sql(live, "SELECT call_id FROM tool_calls WHERE call_id=? AND status='dispatched'", (approval["tool_call_id"],)):
            break
        time.sleep(0.1)
    assert approval is not None and sql(live, "SELECT call_id FROM tool_calls WHERE call_id=? AND status='dispatched'", (approval["tool_call_id"],))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not child_pid.exists():
        time.sleep(0.05)
    assert marker.read_text() == "actual-dispatched-effect" and child_pid.exists()
    pid = int(child_pid.read_text())
    before = {"sha256": hashlib.sha256(marker.read_bytes()).hexdigest(), "mtime_ns": marker.stat().st_mtime_ns}
    grant = next(item for item in http(live, "GET", "/api/grants?workspace_id=office").json()["data"]["items"] if item["resource_type"] == "skill" and item["grantee_id"] == "xiaowen")
    assert http(live, "DELETE", "/api/grants/" + grant["id"], {"change_id": "revoke-dispatched-skill", "expected_revision": grant["revision"]}).status_code == 200
    terminal = until(live, "SELECT status FROM task_runs WHERE id=? AND status IN ('failed','cancelled')", (tid,))
    assert sql(live, "SELECT call_id FROM tool_calls WHERE task_run_id=? AND status='pending_verification'", (tid,))
    assert not delayed.exists()
    assert before == {"sha256": hashlib.sha256(marker.read_bytes()).hexdigest(), "mtime_ns": marker.stat().st_mtime_ns}
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    record = {"task_run_id": tid, "conversation_id": run["conversation"]["id"], "call_id": approval["tool_call_id"],
        "child_pid": pid, "child_exited": True, "terminal": terminal, "preserved_effect": before, "delayed_file_exists": False,
        "ledger": sql(live, "SELECT call_id,tool_name,status,dispatched_at FROM tool_calls WHERE task_run_id=?", (tid,))}
    (live["root"] / "dispatched-revocation.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def test_skill_revoke_filters_old_and_new_task_index(governed):
    service, memory, versions, sessions, skills = governed
    grants = Grants(service, sessions.identities)
    async def check():
        first = await sessions.create_conversation(instruction="技能授权核查", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        actor = MemoryIdentity("office", "xiaowen", "agent", "xiaowen", first["conversation"]["id"], first["task_run_id"])
        index = await skills.index(actor)
        item = next(item for item in index["items"] if item["name"] == "文档模板")
        bound = versions.task_bindings(actor)
        grant_id = service.db.read_conn.execute("SELECT id FROM grants WHERE resource_type='skill' AND resource_id=? AND revoked_at IS NULL", (item["id"],)).fetchone()[0]
        await grants.revoke(RequestIdentity("owner", "owner"), grant_id, {"change_id": "revoke-skill-index", "expected_revision": 1})
        assert "文档模板" not in [item["name"] for item in (await skills.index(actor))["items"]]
        assert (await skills.view(actor, "文档模板", call_id="revoked-body"))["error"] == "OUT_OF_SCOPE"
        assert versions.task_bindings(actor) == bound
        second = await sessions.create_conversation(instruction="新任务授权索引", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        next_actor = MemoryIdentity("office", "xiaowen", "agent", "xiaowen", second["conversation"]["id"], second["task_run_id"])
        assert item["id"] not in versions.task_bindings(next_actor)
    asyncio.run(check())


def test_repeated_old_revocation_preserves_current_regrant(governed):
    service, memory, versions, sessions, skills = governed
    grants = Grants(service, sessions.identities)
    async def check():
        old_id = service.db.read_conn.execute("SELECT id FROM grants WHERE resource_type='connector' AND grantee_id='xiaowen' AND revoked_at IS NULL").fetchone()[0]
        await grants.revoke(RequestIdentity("owner", "owner"), old_id, {"change_id": "old-revocation", "expected_revision": 1})
        new = await grants.create(RequestIdentity("owner", "owner"), {"change_id": "new-authorized-grant", "resource_type": "connector",
            "resource_id": "office-http", "grantee_type": "agent", "grantee_id": "xiaowen"})
        from agentcrew_server.bus import EventBus
        from agentcrew_server.memory.jobs import MemoryJobs
        jobs = MemoryJobs(memory, EventBus(), sessions._settings)
        jobs.identities = sessions.identities
        curate = await jobs.curator.enqueue("office", "xiaowen", "current-curate")
        assert isinstance(curate, str)
        created = await sessions.create_conversation(instruction="重新授予后的合法任务", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        await grants.revoke(RequestIdentity("owner", "owner"), old_id, {"change_id": "old-revocation-repeat", "expected_revision": 2})
        from agentcrew_server.db.projections import _row_to_event
        event = _row_to_event(service.db.read_conn.execute("SELECT * FROM run_events WHERE type='governance.grant_changed' ORDER BY global_seq DESC LIMIT 1").fetchone())
        assert event.payload["revocation_changed"] is False
        assert grants.affects(event, created["task_run_id"]) is False
        assert grants.affects_job(event, curate) is False
        assert jobs.get(curate)["status"] == "queued"
        assert grants.get(new["id"])["revoked_at"] is None
        assert "http_request" in [schema["name"] for schema in grants.schemas(created["task_run_id"], build_default_registry().schemas())[0]]
        await jobs.shutdown()
    asyncio.run(check())


def test_skill_prepare_rechecks_current_grant_in_write_transaction(governed):
    service, memory, versions, sessions, skills = governed
    grants = Grants(service, sessions.identities)
    memory.grants = grants
    async def check():
        created = await sessions.create_conversation(instruction="技能准备事务的当前授权核查", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        actor = MemoryIdentity("office", "xiaowen", "agent", "xiaowen", created["conversation"]["id"], created["task_run_id"])
        skill_id = service.db.read_conn.execute("SELECT id FROM skills WHERE workspace_id='office' AND name='文档模板'").fetchone()[0]
        before = memory._load("skill", skill_id)
        assert memory._authorize(actor, "skill", skill_id) is None
        plan = memory._plan(actor, before, before["metadata"]["entries"], before["text"], "自有准备事务参数", "2026-10-03T15:00:00+00:00",
            "prepare-after-revoke", None, "update", [], before["metadata"], None)
        grant_id = service.db.read_conn.execute("SELECT id FROM grants WHERE resource_type='skill' AND resource_id=? AND revoked_at IS NULL", (skill_id,)).fetchone()[0]
        await grants.revoke(RequestIdentity("owner", "owner"), grant_id, {"change_id": "before-prepare-revoked", "expected_revision": 1})
        with pytest.raises(GovernanceError, match="准备写入时技能"):
            await service.events.channel.execute(lambda conn: memory._prepare_tx(conn, "prepare-after-revoke", "own-domain-request-hash", plan))
        assert service.db.read_conn.execute("SELECT count(*) FROM memory_changes WHERE change_id='prepare-after-revoke'").fetchone()[0] == 0
        assert memory._load("skill", skill_id)["revision"] == before["revision"]
    asyncio.run(check())


def test_direct_hidden_http_tool_rejected_without_approval_or_dispatch(governed):
    service, memory, versions, sessions, skills = governed
    grants = Grants(service, sessions.identities)
    approvals = ApprovalService(service.db, service.events, service.data_dir / "chain-head.txt")
    approvals.grants = grants
    approvals.scheduler = ToolScheduler(build_default_registry(), gate=approvals.gate)
    async def check():
        created = await sessions.create_conversation(instruction="无HTTP员工直接调用边界", workspace_id="analytics", agent_id="xiaogang", request_identity=RequestIdentity("owner", "owner"))
        context = sessions.build_work_context(created["conversation"]["id"], created["task_run_id"])
        with pytest.raises(GovernanceError, match="没有所选HTTP"):
            await approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaogang",
                invocation=ToolInvocation("hidden-http-call", "http_request", {"url": "https://example.com"}), ctx=context)
        assert service.db.read_conn.execute("SELECT count(*) FROM tool_calls WHERE call_id='hidden-http-call'").fetchone()[0] == 0
        assert service.db.read_conn.execute("SELECT count(*) FROM run_events WHERE type='permission.requested'").fetchone()[0] == 0
    asyncio.run(check())


def test_sigkill_resume_uses_original_version_and_current_grant(live):
    current = http(live, "GET", "/api/grants?workspace_id=office").json()["data"]["items"]
    choices = [item for item in current if item["resource_type"] == "connector" and item["grantee_id"] == "xiaowen"]
    if choices:
        grant = choices[0]
    else:
        response = http(live, "POST", "/api/grants", {"change_id": "regrant-http-for-recovery", "resource_type": "connector",
            "resource_id": "office-http", "grantee_type": "agent", "grantee_id": "xiaowen"})
        assert response.status_code == 200, response.text
        grant = response.json()["data"]
    run = task(live, "xiaowen", "office", "请通过已授权http_request向https://example.com发送POST，正文为recovery-grant-check。请等待审批。")
    tid, cid = run["task_run_id"], run["conversation"]["id"]
    requested = until(live, "SELECT payload FROM run_events WHERE task_run_id=? AND type='permission.requested'", (tid,))
    old = json.loads(requested[-1]["payload"])
    binding = sql(live, "SELECT agent_spec,skill_versions FROM task_governance WHERE task_run_id=?", (tid,))[0]
    snapshot = live["root"] / "conversations" / cid / "memory-snapshot.json"
    before = {"sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(), "mtime_ns": snapshot.stat().st_mtime_ns}
    live["process"].kill()
    assert live["process"].wait(15) == -9
    log = (live["root"] / "restart.log").open("w")
    process = subprocess.Popen(live["command"], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "AGENTCREW_TOKEN": live["client"].headers["Authorization"].removeprefix("Bearer "), "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "MEMORY_REQUEST_EVIDENCE": str(live["root"] / "requests.jsonl")}, stdout=subprocess.PIPE, stderr=log, text=True)
    live["process"] = process
    ready = process.stdout.readline().strip()
    assert ready.startswith("AGENTCREW_READY "), redact((live["root"] / "restart.log").read_text())
    port = json.loads(ready.split(" ", 1)[1])["port"]
    live["client"].base_url = f"http://127.0.0.1:{port}"
    assert sql(live, "SELECT status FROM task_runs WHERE id=?", (tid,))[0]["status"] == "interrupted"
    assert http(live, "DELETE", "/api/grants/" + grant["id"], {"change_id": "revoke-recovery-http", "expected_revision": grant["revision"]}).status_code == 200
    assert http(live, "POST", "/api/tool-approvals/" + old["tool_call_id"], {"decision": "allow_once", "input_hash": old["input_hash"]}).status_code in {403, 409}
    response = http(live, "POST", "/api/task-runs/" + tid + "/resume")
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 180
    handled = set()
    terminal = []
    while time.monotonic() < deadline:
        terminal = sql(live, "SELECT status,current_attempt_no FROM task_runs WHERE id=? AND status IN ('completed','failed')", (tid,))
        if terminal:
            break
        for row in sql(live, "SELECT payload FROM run_events WHERE task_run_id=? AND attempt_no=2 AND type='permission.requested'", (tid,)):
            card = json.loads(row["payload"])
            if card["tool_call_id"] not in handled:
                declined = http(live, "POST", "/api/tool-approvals/" + card["tool_call_id"], {"decision": "reject_once", "input_hash": card["input_hash"]})
                assert declined.status_code == 200, declined.text
                handled.add(card["tool_call_id"])
        time.sleep(0.1)
    assert terminal, sql(live, "SELECT type,payload FROM run_events WHERE task_run_id=? ORDER BY global_seq", (tid,))
    assert terminal[0]["current_attempt_no"] == 2
    assert sql(live, "SELECT call_id FROM tool_calls WHERE task_run_id=? AND tool_name='http_request' AND dispatched_at IS NOT NULL", (tid,)) == []
    assert sql(live, "SELECT agent_spec,skill_versions FROM task_governance WHERE task_run_id=?", (tid,))[0] == binding
    assert before == {"sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(), "mtime_ns": snapshot.stat().st_mtime_ns}
    attempts = sql(live, "SELECT id,attempt_no,context_fingerprint FROM run_attempts WHERE task_run_id=? ORDER BY attempt_no", (tid,))
    assert len(attempts) == 2
    started = sql(live, "SELECT payload FROM run_events WHERE task_run_id=? AND type='llm.request_started' AND attempt_no=2", (tid,))
    assert started and all("http_request" not in json.loads(row["payload"])["tool_names"] for row in started)
    (live["root"] / "recovery-revocation.json").write_text(json.dumps({"task_run_id": tid, "conversation_id": cid,
        "old_approval": old, "attempts": attempts, "terminal": terminal, "historical_binding": binding,
        "snapshot_preserved": before, "resumed_requests": started, "http_dispatches": 0}, ensure_ascii=False, indent=2) + "\n")
    log.close()
