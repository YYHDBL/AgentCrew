"""M3-05：当前真实模型、文件与无人值守授权交集。"""

import json
import hashlib
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

from test_run_center_api import server, executions
from test_governance_identity import request
from test_cron_store import proposal
from test_governance_connectors import persistent_http


@pytest.fixture(scope="module")
def employee_rules(server):
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]["data_dir"]
    rules = request(server, "GET", "/api/agents/xiaowen/permission-rules?limit=200").json()["data"]["items"]
    if not any(value["tool_name"] == "write_file" and value["effect"] == "allow" and value["pattern"] == workspace for value in rules):
        result = request(server, "POST", "/api/agents/xiaowen/permission-rules", body={"change_id": uuid.uuid4().hex,
            "tool_name": "write_file", "pattern": workspace, "effect": "allow"})
        assert result.status_code == 200, result.text


def test_unattended_gate_requires_both_layers_and_rejects_interaction():
    from dataclasses import replace
    from agentcrew_core.cron.authorization import unattended_gate
    from agentcrew_core.tools import build_default_registry, PermissionRule
    from agentcrew_core.tools.metadata import ToolInvocation
    registry = build_default_registry()
    call = ToolInvocation("real-domain-input", "write_file", {"path": "/approved/output.txt", "content": "实际范围判定输入"})
    allow = PermissionRule("employee", "write_file", "/approved", "allow")
    deny = PermissionRule("employee", "write_file", "/approved", "deny")
    selected = [{"tool": "write_file", "pattern": "/approved"}]
    def verdict(rules, choices):
        return unattended_gate(registry.get(call.name).metadata, call.input, False, rules, choices, "employee", Path("/approved"))
    assert verdict([allow], selected).action == "allow"
    assert verdict([], selected).action == "deny"
    assert verdict([allow], []).action == "deny"
    assert verdict([allow, deny], selected).action == "deny"
    ask = registry.get("ask_user").metadata
    assert unattended_gate(ask, {"question": "是否继续"}, True, [], [], "employee", Path("/approved")).action == "deny"
    needs_approval_read = replace(registry.get("read_file").metadata, needs_approval=True)
    assert unattended_gate(needs_approval_read, {"path": "/approved/input.txt"}, True, [], [], "employee", Path("/approved")).action == "deny"


def wait_occurrence(server, job, identity=None):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        history = request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs', identity=identity).json()["data"]["items"]
        if history and history[0]["status"] in {"completed", "failed", "skipped", "pending_verification", "interrupted"}:
            return history[0]
        time.sleep(0.05)
    raise AssertionError("真实无人值守发生未达到终态")


def create_job(server, mode, instruction, pre_authorized, conversation=None):
    body = proposal({"kind": "every", "every_ms": 86400000, "tz": "UTC"}, "真实无人值守文件任务",
        target={"instruction": instruction, "execution_mode": mode, "conversation_id": conversation}, pre_authorized=pre_authorized)
    created = request(server, "POST", "/api/cron/jobs", body=body)
    assert created.status_code == 201, created.text
    return created.json()["data"]


def run_now(server, job):
    response = request(server, "POST", f'/api/cron/jobs/{job["id"]}/run-now',
        body={"client_request_id": uuid.uuid4().hex, "expected_revision": job["revision"]})
    assert response.status_code == 202, response.text
    return response.json()["data"]


def test_real_existing_and_new_conversation_file_runs(server, executions, employee_rules):
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]["data_dir"]
    pre_auth = [{"tool": "write_file", "pattern": workspace}]
    for mode in ("existing", "new_conversation"):
        target = Path(workspace) / ("m3-unattended-" + uuid.uuid4().hex + ".txt")
        instruction = f"直接调用write_file将内容 M3 unattended actual 写入 {target}，随后read_file核查。无需其他操作或提问。"
        job = create_job(server, mode, instruction, pre_auth, executions["conversation"] if mode == "existing" else None)
        registered = run_now(server, job)
        occurrence = wait_occurrence(server, job)
        assert occurrence["id"] == registered["id"]
        assert occurrence["status"] == "completed", occurrence
        assert target.read_text().strip() == "M3 unattended actual"
        record = request(server, "GET", f'/api/task-runs/{occurrence["task_run_id"]}').json()["data"]
        assert record["cron_job_id"] == job["id"]
        assert record["source"] == "manual"
        assert record["occurrence_id"] == occurrence["id"]
        assert (record["conversation_id"] == executions["conversation"]) == (mode == "existing")
        with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
            assert conn.execute("SELECT count(*) FROM run_events WHERE task_run_id=? AND type IN ('permission.requested','question.requested')", (record["id"],)).fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM runtime_notifications WHERE job_id=?", (job["id"],)).fetchone()[0] == 1
        current = request(server, "GET", f'/api/cron/jobs/{job["id"]}').json()["data"]
        assert current["state"]["run_count"] == 1


def test_real_missing_plan_allow_and_ask_user_have_no_waiting(server, executions, employee_rules):
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]["data_dir"]
    target = Path(workspace) / ("m3-denied-" + uuid.uuid4().hex + ".txt")
    job = create_job(server, "existing", f"只调用write_file将 M3 forbidden 写入 {target}。保留实际拒绝结果，禁止改用bash或其他工具。", [], executions["conversation"])
    run_now(server, job)
    outcome = wait_occurrence(server, job)
    assert outcome["status"] == "failed", outcome
    assert not target.exists()
    interaction = create_job(server, "existing", "必须调用ask_user提问是否继续，保留实际工具结果，随后结束。", [], executions["conversation"])
    run_now(server, interaction)
    outcome = wait_occurrence(server, interaction)
    assert outcome["status"] == "failed", outcome
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM run_events WHERE task_run_id=? AND type IN ('permission.requested','question.requested')", (outcome["task_run_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT status FROM task_runs WHERE id=?", (outcome["task_run_id"],)).fetchone()[0] != "waiting_user"


def test_current_deny_and_scope_stop_real_file_calls(server, executions, employee_rules):
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]["data_dir"]
    target = Path(workspace) / ("m3-protected-result-" + uuid.uuid4().hex + ".txt")
    target.write_text("M3 unchanged evidence")
    baseline = (hashlib.sha256(target.read_bytes()).hexdigest(), target.stat().st_mtime_ns)
    job = create_job(server, "existing", f"仅调用write_file将 M3 disallowed change 写入 {target}，保留拒绝原因。禁止改用其他工具。",
        [{"tool": "write_file", "pattern": workspace}], executions["conversation"])
    deny = request(server, "POST", "/api/agents/xiaowen/permission-rules", body={"change_id": uuid.uuid4().hex,
        "tool_name": "write_file", "pattern": str(target), "effect": "deny"})
    assert deny.status_code == 200, deny.text
    run_now(server, job)
    occurrence = wait_occurrence(server, job)
    assert occurrence["status"] == "failed", occurrence
    assert baseline == (hashlib.sha256(target.read_bytes()).hexdigest(), target.stat().st_mtime_ns)
    deny = deny.json()["data"]
    assert request(server, "DELETE", f'/api/agents/xiaowen/permission-rules/{deny["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": deny["revision"]}).status_code == 200
    outside = server["root"].parent / ("scope-outside-" + uuid.uuid4().hex + ".txt")
    protected = server["root"] / "config.json"
    sha = hashlib.sha256(protected.read_bytes()).hexdigest()
    boundary = create_job(server, "existing", f"仅调用write_file写入 {outside}，然后read_file读取 {protected}，保留实际拒绝结果，禁止改用其他工具。",
        [{"tool": "write_file", "pattern": workspace}], executions["conversation"])
    run_now(server, boundary)
    result = wait_occurrence(server, boundary)
    assert result["status"] == "failed", result
    assert not outside.exists()
    assert hashlib.sha256(protected.read_bytes()).hexdigest() == sha
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM run_events WHERE task_run_id=? AND type='permission.requested'", (result["task_run_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM tool_calls WHERE task_run_id=? AND status='dispatched'", (result["task_run_id"],)).fetchone()[0] == 0


def test_employee_snapshot_and_current_allow_revocation(server, executions, employee_rules):
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]["data_dir"]
    target = Path(workspace) / ("m3-current-allow-" + uuid.uuid4().hex + ".txt")
    job = create_job(server, "existing", f"仅调用write_file将 M3 revoked allow 写入 {target}，保留实际结果。禁止改用其他工具。",
        [{"tool": "write_file", "pattern": workspace}], executions["conversation"])
    employee = request(server, "GET", "/api/agents/xiaowen").json()["data"]
    new_position = "更新后的真实岗位-" + uuid.uuid4().hex
    changed = request(server, "PATCH", "/api/agents/xiaowen", body={"change_id": uuid.uuid4().hex,
        "expected_revision": employee["revision"], "spec": {**employee["spec"], "position": new_position}})
    assert changed.status_code == 200, changed.text
    rules = request(server, "GET", "/api/agents/xiaowen/permission-rules?limit=200").json()["data"]["items"]
    rule = next(value for value in rules if value["tool_name"] == "write_file" and value["effect"] == "allow" and value["pattern"] == workspace)
    assert request(server, "DELETE", f'/api/agents/xiaowen/permission-rules/{rule["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": rule["revision"]}).status_code == 200
    run_now(server, job)
    occurrence = wait_occurrence(server, job)
    assert occurrence["status"] == "failed", occurrence
    assert not target.exists()
    record = request(server, "GET", f'/api/task-runs/{occurrence["task_run_id"]}').json()["data"]
    assert record["agent_spec_snapshot"] == job["metadata"]["agent_spec_snapshot"]
    assert record["agent_spec_snapshot"]["position"] != new_position
    assert request(server, "POST", "/api/agents/xiaowen/permission-rules", body={"change_id": uuid.uuid4().hex,
        "tool_name": "write_file", "pattern": workspace, "effect": "allow"}).status_code == 200


def test_disabled_employee_cannot_dispatch_stored_plan(server):
    job = create_job(server, "new_conversation", "只用一句中文确认收到。", [])
    employee = request(server, "GET", "/api/agents/xiaowen").json()["data"]
    changed = request(server, "PATCH", "/api/agents/xiaowen", body={"change_id": uuid.uuid4().hex, "expected_revision": employee["revision"], "status": "disabled"})
    assert changed.status_code == 200, changed.text
    outcome = run_now(server, job)
    assert outcome["status"] == "failed", outcome
    assert outcome["task_run_id"] is None
    assert "AUTHORIZATION_REVOKED" in outcome["note"]
    assert request(server, "GET", f'/api/cron/jobs/{job["id"]}').json()["data"]["state"]["last_status"] == "failed"
    updated = changed.json()["data"]
    assert request(server, "PATCH", "/api/agents/xiaowen", body={"change_id": uuid.uuid4().hex, "expected_revision": updated["revision"], "status": "active"}).status_code == 200


def test_paused_queue_and_busy_conversation_stay_unchanged(server, executions):
    conversation = executions["conversation"]
    created = request(server, "POST", f"/api/conversations/{conversation}/instructions", body={
        "text": "必须调用ask_user提问是否继续，等待真人回答。", "client_request_id": uuid.uuid4().hex})
    assert created.status_code == 202, created.text
    task = created.json()["data"]["task_run_id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        for approval in request(server, "GET", f"/api/task-runs/{task}/approvals?status=pending").json()["data"]:
            assert request(server, "POST", f'/api/tool-approvals/{approval["call_id"]}', body={"decision": "allow_once", "input_hash": approval["input_hash"]}).status_code == 200
        questions = request(server, "GET", f"/api/conversations/{conversation}/questions").json()["data"]
        if questions:
            break
        time.sleep(0.05)
    assert questions
    job = create_job(server, "existing", "只回复收到。", [], conversation)
    busy = run_now(server, job)
    assert busy["status"] == "skipped" and busy["task_run_id"] is None
    queued = request(server, "POST", f"/api/conversations/{conversation}/instructions", body={"text": "等待所有者继续处理的实际排队指令。", "client_request_id": uuid.uuid4().hex})
    assert queued.status_code == 202, queued.text
    assert request(server, "POST", f"/api/task-runs/{task}/cancel").status_code == 202
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = request(server, "GET", f"/api/conversations/{conversation}/state").json()["data"]
        if state["queue_paused"]:
            break
        time.sleep(0.05)
    assert state["queue_paused"]
    paused = run_now(server, job)
    assert paused["status"] == "skipped" and paused["task_run_id"] is None
    final = request(server, "GET", f"/api/conversations/{conversation}/state").json()["data"]
    assert final["queue_paused"] and final["queue"] == state["queue"]
    assert request(server, "POST", f"/api/conversations/{conversation}/queue/cancel", body={"all": True}).status_code == 200


def test_real_http_connector_grant_is_rechecked(server, persistent_http):
    connector = request(server, "POST", "/api/connectors", body={"change_id": uuid.uuid4().hex, "expected_revision": 0,
        "workspace_id": "office", "name": "无人值守实际HTTP-" + uuid.uuid4().hex, "type": "http",
        "config": {"url": persistent_http["url"], "allowed_hosts": ["127.0.0.1"], "allowed_ports": [persistent_http["port"]], "allow_loopback": True}})
    assert connector.status_code == 200, connector.text
    connector = connector.json()["data"]
    grant = request(server, "POST", "/api/grants", body={"change_id": uuid.uuid4().hex, "resource_type": "connector",
        "resource_id": connector["id"], "grantee_type": "agent", "grantee_id": "xiaowen"})
    assert grant.status_code == 200, grant.text
    grant = grant.json()["data"]
    rule = request(server, "POST", "/api/agents/xiaowen/permission-rules", body={"change_id": uuid.uuid4().hex,
        "tool_name": "http_request", "pattern": "127.0.0.1", "effect": "allow"})
    assert rule.status_code == 200, rule.text
    instruction = f"仅调用http_request，指定connector_id={connector['id']}，method=POST，url={persistent_http['url']}/ordinary，body为 M3 actual HTTP。保留真实结果。禁止使用bash或其他工具。"
    job = create_job(server, "new_conversation", instruction, [{"tool": "http_request", "pattern": "127.0.0.1"}])
    run_now(server, job)
    result = wait_occurrence(server, job)
    assert result["status"] == "completed", result
    with sqlite3.connect(persistent_http["database"]) as conn:
        actual = conn.execute("SELECT id,body,auth_sha256 FROM operations").fetchall()
        assert len(actual) == 1, actual
    assert request(server, "DELETE", f'/api/grants/{grant["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": grant["revision"]}).status_code == 200
    run_now(server, job)
    denied = wait_occurrence(server, job)
    with sqlite3.connect(persistent_http["database"]) as conn:
        assert conn.execute("SELECT id,body,auth_sha256 FROM operations").fetchall() == actual
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        declarations = conn.execute("SELECT json_extract(payload,'$.tool_declarations') FROM run_events WHERE task_run_id=? AND type='llm.request_started'", (denied["task_run_id"],)).fetchall()
        assert declarations
        assert all(connector["id"] not in row[0] for row in declarations)
        assert conn.execute("SELECT count(*) FROM run_events WHERE task_run_id=? AND type='permission.requested'", (denied["task_run_id"],)).fetchone()[0] == 0
    (server["root"] / "cron-http-grant-evidence.json").write_text(json.dumps({"job_id": job["id"], "first_occurrence": result,
        "revoked_occurrence": denied, "connector_id": connector["id"], "grant_id": grant["id"],
        "upstream_sql": "SELECT id,body,auth_sha256 FROM operations", "upstream_rows": actual}, ensure_ascii=False, indent=2))


def test_at_every_cron_produce_real_scheduled_files(server, employee_rules):
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]["data_dir"]
    for kind in ("at", "every", "cron"):
        stamp = time.time_ns() // 1_000_000
        schedule = {"kind": "at", "at_ms": stamp + 1500, "tz": "UTC"} if kind == "at" else {
            "kind": "every", "every_ms": 15000, "tz": "Asia/Shanghai"} if kind == "every" else {
            "kind": "cron", "expr": "* * * * * */15", "tz": "UTC"}
        target = Path(workspace) / ("m3-scheduled-" + kind + "-" + uuid.uuid4().hex + ".txt")
        body = proposal(schedule, "真实定时文件-" + kind,
            target={"instruction": f"仅调用write_file将内容 M3 scheduled {kind} 写入 {target}，然后read_file核查。无需提问或其他操作。", "execution_mode": "new_conversation", "conversation_id": None},
            pre_authorized=[{"tool": "write_file", "pattern": workspace}])
        response = request(server, "POST", "/api/cron/jobs", body=body)
        assert response.status_code == 201, response.text
        job = response.json()["data"]
        outcome = wait_occurrence(server, job)
        assert outcome["status"] == "completed", outcome
        assert outcome["trigger"] == "scheduled"
        assert target.read_text().strip() == "M3 scheduled " + kind
        history = request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs?limit=200').json()["data"]["items"]
        assert len({row["scheduled_at"] for row in history}) == len(history)
        assert request(server, "PATCH", f'/api/cron/jobs/{job["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": 1, "enabled": False}).status_code == 200
