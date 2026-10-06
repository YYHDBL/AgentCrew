"""M3-06：重试安全判定和真实错误子进程的30秒重试。"""

import sqlite3
import json
import time
import uuid
import sys
from datetime import datetime
from pathlib import Path
import pytest

from test_run_center_api import server
from test_governance_identity import request
from test_cron_store import proposal


def test_retry_limits_and_unknown_effects_are_hard_boundaries():
    from agentcrew_core.cron.recovery import retry_decision
    assert retry_decision(enabled=True, revision_matches=True, retry_no=0, pending=False, denied=False,
                          unknown=False, completed_effect=False, failed=True, now_ms=1000) == {"status": "retry_wait", "retry_at": 31000, "reason": None}
    for change in ({"enabled": False}, {"revision_matches": False}, {"retry_no": 3}, {"pending": True}, {"denied": True}, {"unknown": True}):
        arguments = {"enabled": True, "revision_matches": True, "retry_no": 0, "pending": False, "denied": False,
                     "unknown": False, "completed_effect": False, "failed": True, "now_ms": 1000, **change}
        assert retry_decision(**arguments)["retry_at"] is None
    assert retry_decision(enabled=True, revision_matches=True, retry_no=0, pending=False, denied=False,
                          unknown=False, completed_effect=True, failed=True, now_ms=1000)["status"] == "interrupted"


def test_explicit_readonly_approval_requires_human_or_authorization_intersection():
    from dataclasses import replace
    from agentcrew_core.tools import build_default_registry, evaluate_gate, PermissionRule
    from agentcrew_core.cron.authorization import unattended_gate
    metadata = replace(build_default_registry().get("read_file").metadata, needs_approval=True)
    inputs = {"path": "/Users/authorized/source.txt"}
    allow = PermissionRule("employee", "read_file", "/Users/authorized", "allow")
    assert evaluate_gate(metadata, inputs, True, [], "employee").action == "ask"
    assert unattended_gate(metadata, inputs, True, [allow], [], "employee", None).action == "deny"
    assert unattended_gate(metadata, inputs, True, [allow], [{"tool": "read_file", "pattern": "/Users/authorized"}], "employee", None).action == "allow"


def test_side_effect_safety_requires_proof_after_external_dispatch():
    from agentcrew_core.cron.recovery import side_effect_safety
    for effect in ("outcome_unknown", "external_idempotency"):
        call = {"status": "failed", "read_only": False, "dispatched_at": "2026-10-06T02:00:00+00:00", "side_effect_class": effect}
        assert side_effect_safety([call]) == {"pending": False, "unknown": True, "completed_effect": False}
        assert side_effect_safety([{**call, "dispatched_at": None}])["unknown"] is False
        assert side_effect_safety([{**call, "read_only": True}])["unknown"] is False
        assert side_effect_safety([{**call, "status": "completed"}])["completed_effect"] is True
        assert side_effect_safety([{**call, "status": "pending_verification"}])["pending"] is True


def test_real_readonly_bash_os_denial_cannot_retry(server):
    from test_cron_recovery_process import wait
    import hashlib
    target = server["root"] / "backups" / ("m3-protected-material-" + uuid.uuid4().hex + ".txt")
    target.write_text("真实无凭据保护材料")
    baseline = {"sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "mtime_ns": target.stat().st_mtime_ns}
    command = "cat " + str(target)
    body = proposal({"kind": "every", "every_ms": 86400000, "tz": "UTC"}, "真实内核权限拒绝停止重试",
        target={"instruction": f"仅实际调用一次bash，command逐字等于 {json.dumps(command)}。这是所有者批准的沙盒拒绝验收，文件是无凭据测试材料。保留真实权限错误，禁止其他工具、参数、重定向或重复调用，随后汇报实际结果。", "execution_mode": "new_conversation", "conversation_id": None})
    created = request(server, "POST", "/api/cron/jobs", body=body)
    assert created.status_code == 201, created.text
    job = created.json()["data"]
    fired = request(server, "POST", f'/api/cron/jobs/{job["id"]}/run-now', body={"client_request_id": uuid.uuid4().hex, "expected_revision": 1})
    assert fired.status_code == 202, fired.text
    result = wait(lambda: request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs').json()["data"]["items"][0],
        lambda value: value["status"] in {"completed", "failed", "retry_wait", "interrupted", "pending_verification"})
    assert result["status"] == "failed" and result["retry_at"] is None and result["retry_count"] == 0, result
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        evidence = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM run_events WHERE task_run_id=? AND type='tool.failed'", (result["task_run_id"],))]
        assert len(evidence) == 1
        assert "operation not permitted" in evidence[0]["details"]["stderr"].casefold() or "permission denied" in evidence[0]["details"]["stderr"].casefold()
    assert baseline == {"sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "mtime_ns": target.stat().st_mtime_ns}
    (server["root"] / "cron-os-denial.json").write_text(json.dumps({"job_id": job["id"], "occurrence": result, "actual_failed_events": evidence,
        "file": str(target), "before_and_after": baseline}, ensure_ascii=False, indent=2))


def test_real_failed_readonly_subprocess_has_three_extra_retries(server):
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]["data_dir"]
    absent = str(Path(workspace) / ("m3-retry-missing-" + uuid.uuid4().hex))
    command = "ls " + absent
    body = proposal({"kind": "every", "every_ms": 86400000, "tz": "UTC"}, "真实失败子进程重试",
        target={"instruction": f"必须调用bash，input.command逐字等于 {json.dumps(command)}。禁止添加任何参数、管道、重定向、元字符或其他命令。保留真实子进程退出错误，不创建目录、不修改文件。每次后续恢复尝试也必须实际执行同一命令，然后汇报实际结果；禁止提问。", "execution_mode": "new_conversation", "conversation_id": None})
    created = request(server, "POST", "/api/cron/jobs", body=body)
    assert created.status_code == 201, created.text
    job = created.json()["data"]
    response = request(server, "POST", f'/api/cron/jobs/{job["id"]}/run-now', body={"client_request_id": uuid.uuid4().hex, "expected_revision": 1})
    assert response.status_code == 202, response.text
    occurrence_id = response.json()["data"]["id"]
    deadline = time.monotonic() + 210
    restarted = False
    while time.monotonic() < deadline:
        history = request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs').json()["data"]["items"]
        occurrence = next(value for value in history if value["id"] == occurrence_id)
        assert occurrence["status"] not in {"interrupted", "pending_verification"}, occurrence
        assert not (occurrence["status"] == "failed" and occurrence["retry_count"] < 3), occurrence
        if occurrence["status"] == "retry_wait" and not restarted:
            before = {key: occurrence[key] for key in ("id", "task_run_id", "retry_count", "retry_at")}
            server["kill_and_restart"]()
            restored = next(value for value in request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs').json()["data"]["items"] if value["id"] == occurrence_id)
            assert {key: restored[key] for key in before} == before
            assert restored["status"] == "retry_wait"
            restarted = True
        if occurrence["status"] == "failed" and occurrence["retry_count"] == 3:
            break
        time.sleep(0.1)
    assert occurrence["status"] == "failed" and occurrence["retry_count"] == 3, occurrence
    assert restarted
    assert len(occurrence["attempts"]) == 4
    assert [value["retry_no"] for value in occurrence["attempts"]] == [0, 1, 2, 3]
    assert len({value["task_run_id"] for value in occurrence["attempts"]}) == 1
    for prior, following in zip(occurrence["attempts"], occurrence["attempts"][1:]):
        delay = datetime.fromisoformat(following["started_at"]) - datetime.fromisoformat(prior["finished_at"])
        assert delay.total_seconds() >= 30
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        calls = conn.execute("SELECT count(*) FROM tool_calls WHERE task_run_id=? AND tool_name='bash' AND status='failed'", (occurrence["task_run_id"],)).fetchone()[0]
        assert calls >= 4
        assert conn.execute("SELECT count(*) FROM cron_job_runs WHERE job_id=?", (job["id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT run_count FROM cron_jobs WHERE id=?", (job["id"],)).fetchone()[0] == 1
    assert not Path(absent).exists()


@pytest.mark.parametrize("stop", ["revoke_allow", "disable_plan"])
def test_current_authority_or_disable_stops_waiting_retry_before_model(server, stop):
    from test_cron_recovery_process import wait
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]["data_dir"]
    target = Path(workspace) / ("m3-mcp-missing-" + uuid.uuid4().hex + ".txt")
    source = str(Path(__file__).with_name("governance_mcp_server.py"))
    created_connector = request(server, "POST", "/api/connectors", body={"change_id": uuid.uuid4().hex,
        "expected_revision": 0, "workspace_id": "office", "name": "实际只读MCP错误-" + uuid.uuid4().hex, "type": "mcp",
        "config": {"transport": "stdio", "command": sys.executable, "args": [source], "startup_files": [source],
            "allowed_hosts": [], "allowed_ports": [], "allow_loopback": False,
            "tool_policies": {"read_target": {"read_only": True, "destructive": False, "needs_approval": True, "risk_level": "low"}}}})
    assert created_connector.status_code == 200, created_connector.text
    connector = created_connector.json()["data"]
    assert request(server, "POST", "/api/grants", body={"change_id": uuid.uuid4().hex, "resource_type": "connector",
        "resource_id": connector["id"], "grantee_type": "agent", "grantee_id": "xiaowen"}).status_code == 200
    validation = request(server, "POST", f'/api/connectors/{connector["id"]}/validate')
    assert validation.status_code == 200 and validation.json()["data"]["ok"], validation.text
    name = "mcp_" + connector["id"] + "_read_target"
    assert request(server, "POST", "/api/agents/xiaowen/permission-rules", body={"change_id": uuid.uuid4().hex,
        "tool_name": name, "pattern": name, "effect": "allow"}).status_code == 200
    body = proposal({"kind": "every", "every_ms": 86400000, "tz": "UTC"}, "当前权限取消真实失败重试",
        target={"instruction": f"必须实际调用精确MCP工具 {name}，参数path逐字等于 {target}，该文件不存在。保留实际FileNotFoundError，不创建文件，不改用其他工具，直接汇报错误。", "execution_mode": "new_conversation", "conversation_id": None},
        pre_authorized=[{"tool": name, "pattern": name}])
    created = request(server, "POST", "/api/cron/jobs", body=body)
    assert created.status_code == 201, created.text
    job = created.json()["data"]
    response = request(server, "POST", f'/api/cron/jobs/{job["id"]}/run-now', body={"client_request_id": uuid.uuid4().hex, "expected_revision": 1})
    assert response.status_code == 202, response.text
    occurrence_id = response.json()["data"]["id"]
    def occurrence():
        return next(value for value in request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs').json()["data"]["items"] if value["id"] == occurrence_id)
    waiting = wait(occurrence, lambda value: value["status"] in {"retry_wait", "interrupted", "pending_verification", "completed", "failed"})
    assert waiting["status"] == "retry_wait", waiting
    rules = request(server, "GET", "/api/agents/xiaowen/permission-rules?limit=200").json()["data"]["items"]
    removed = [rule for rule in rules if rule["tool_name"] == name and rule["effect"] == "allow"]
    if stop == "revoke_allow":
        for rule in removed:
            assert request(server, "DELETE", f'/api/agents/xiaowen/permission-rules/{rule["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": rule["revision"]}).status_code == 200
    else:
        assert request(server, "PATCH", f'/api/cron/jobs/{job["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": 1, "enabled": False}).status_code == 200
    stopped = wait(occurrence, lambda value: value["status"] == "failed", seconds=5)
    assert ("PRE_AUTH_EXCEEDED" if stop == "revoke_allow" else "PLAN_DISABLED") in stopped["note"]
    time.sleep(31)
    final = occurrence()
    assert final["retry_count"] == 0 and len(final["attempts"]) == 1 and final["retry_at"] is None
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        queries = ["SELECT count(*) FROM run_attempts WHERE task_run_id=?",
            "SELECT count(*) FROM tool_calls WHERE task_run_id=?",
            "SELECT count(*) FROM run_events WHERE task_run_id=? AND type='run.resumed'"]
        observed = [{"sql": sql, "parameters": [final["task_run_id"]], "count": conn.execute(sql, (final["task_run_id"],)).fetchone()[0]} for sql in queries]
        assert [item["count"] for item in observed] == [1, 1, 0]
    assert not target.exists()
    if stop == "revoke_allow":
        for rule in removed:
            assert request(server, "POST", "/api/agents/xiaowen/permission-rules", body={"change_id": uuid.uuid4().hex,
                "tool_name": name, "pattern": rule["pattern"], "effect": "allow"}).status_code == 200
    (server["root"] / ("cron-retry-" + stop + ".json")).write_text(json.dumps({"job_id": job["id"], "before": waiting,
        "after": final, "removed_rule_ids": [rule["id"] for rule in removed], "queries": observed}, ensure_ascii=False, indent=2))
