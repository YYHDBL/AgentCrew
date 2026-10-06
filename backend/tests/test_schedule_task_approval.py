"""M3-07：强制人工确认、不可变提案和真实计划创建。"""

from dataclasses import replace
import concurrent.futures
import json
import sqlite3
import time
import uuid
from pathlib import Path

from agentcrew_core.tools import build_default_registry, PermissionRule, evaluate_gate
from test_run_center_api import server
from test_governance_identity import request, demo
from test_cron_recovery_process import wait


def test_schedule_task_confirmation_cannot_be_waived_by_metadata_or_allow():
    tool = build_default_registry().get("schedule_task")
    assert tool is not None
    allow = PermissionRule("employee", "schedule_task", "schedule_task", "allow")
    for metadata in (tool.metadata, replace(tool.metadata, read_only=True, needs_approval=False)):
        assert evaluate_gate(metadata, {}, True, [allow], "employee").action == "ask"


def propose(server, *, interval=5000, execution_mode="new_conversation", suggested=None):
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]["data_dir"]
    directory = Path(workspace) / ("m3-approved-schedule-" + uuid.uuid4().hex)
    directory.mkdir()
    target = directory / "actual.txt"
    data = {"name": "员工真实计划-" + uuid.uuid4().hex, "schedule": {"kind": "every", "every_ms": interval, "tz": "UTC"},
        "instruction": f"必须只调用write_file将 M3 human approved actual 写入 {target}，然后只调用read_file核查该文件。write_file会自动创建合法范围内的父目录。禁止调用bash、mkdir、其他工具或处理其他路径。",
        "execution_mode": execution_mode, "pre_authorized": suggested if suggested is not None else [{"tool": "write_file", "pattern": workspace}]}
    created = request(server, "POST", "/api/conversations", body={"workspace_id": "office", "agent_id": "xiaowen",
        "client_request_id": uuid.uuid4().hex, "instruction": "必须仅调用一次schedule_task，精确参数如下：" + json.dumps(data, ensure_ascii=False) +
            "。该次只提出计划，等待真人决定。禁止自己写入文件或使用其他工具。批准后只汇报实际计划；拒绝后只汇报已拒绝，不再提出新计划，不调用ask_user。"})
    assert created.status_code == 201, created.text
    task_id = created.json()["data"]["task_run_id"]
    cards = wait(lambda: request(server, "GET", f"/api/task-runs/{task_id}/approvals?status=pending").json()["data"], lambda rows: any(item["tool"] == "schedule_task" for item in rows))
    card = next(item for item in cards if item["tool"] == "schedule_task")
    response = request(server, "GET", f'/api/cron/proposals/{card["call_id"]}')
    assert response.status_code == 200, response.text
    proposal = response.json()["data"]
    assert proposal["status"] == "pending" and proposal["job_id"] is None
    assert proposal["input_hash"] == card["input_hash"]
    assert len(proposal["candidates"]) == len({(item["tool"], item["pattern"]) for item in proposal["candidates"]})
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM cron_jobs WHERE json_extract(metadata,'$.proposal_id')=?", (proposal["id"],)).fetchone()[0] == 0
        payload = json.loads(conn.execute("SELECT payload FROM run_events WHERE task_run_id=? AND type='permission.requested' ORDER BY seq DESC LIMIT 1", (task_id,)).fetchone()[0])
        assert payload["options"] == ["allow_once", "reject_once"] and payload["proposal_id"] == proposal["id"]
    return proposal, target, task_id, directory


def body(proposal, selected=None, decision="allow_once"):
    return {"decision": decision, "input_hash": proposal["input_hash"], "expected_revision": proposal["revision"], "selected": selected if selected is not None else []}


def stop_job(server, job_id):
    job = request(server, "GET", f"/api/cron/jobs/{job_id}").json()["data"]
    assert request(server, "PATCH", f"/api/cron/jobs/{job_id}", body={"change_id": uuid.uuid4().hex, "expected_revision": job["revision"], "enabled": False}).status_code == 200


def test_real_proposal_forces_human_and_approved_narrowed_plan_runs(server):
    assert request(server, "POST", "/api/agents/xiaowen/permission-rules", body={"change_id": uuid.uuid4().hex,
        "tool_name": "schedule_task", "pattern": "schedule_task", "effect": "allow"}).status_code == 200
    proposal, target, source_task, directory = propose(server)
    route = f'/api/cron/proposals/{proposal["id"]}'
    assert request(server, "POST", f'/api/tool-approvals/{proposal["call_id"]}', body={"decision": "allow_always", "input_hash": proposal["input_hash"]}).status_code == 409
    assert request(server, "POST", f'/api/tool-approvals/{proposal["call_id"]}', body={"decision": "allow_once", "input_hash": proposal["input_hash"]}).status_code == 409
    assert request(server, "POST", route, body={**body(proposal), "decision": "allow_always"}).status_code == 422
    assert request(server, "POST", route, body={**body(proposal), "input_hash": "changed-parameters"}).status_code == 409
    assert request(server, "POST", route, body={**body(proposal), "expected_revision": 99}).status_code == 409
    assert request(server, "POST", route, body=body(proposal, [{"tool": "write_file", "pattern": "/Users"}])).status_code == 403
    member = demo(server, "lilei")
    assert request(server, "GET", route, identity=member, user="lilei").status_code == 403
    assert not target.exists()
    approved_body = body(proposal, [{"tool": "write_file", "pattern": str(directory)}])
    def approve(_):
        return request(server, "POST", route, body=approved_body)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(approve, range(2)))
    assert [response.status_code for response in responses] == [200, 200], [response.text for response in responses]
    approved = responses[0].json()["data"]
    assert approved["status"] == "approved" and approved["revision"] == 2
    assert responses[1].json()["data"]["job_id"] == approved["job_id"]
    job_id = approved["job_id"]
    job = request(server, "GET", f"/api/cron/jobs/{job_id}").json()["data"]
    assert job["metadata"]["created_by"] == "agent" and job["metadata"]["created_via_task_run_id"] == source_task
    assert job["metadata"]["proposal_id"] == proposal["id"] and job["metadata"]["pre_authorized"] == approved_body["selected"]
    assert approved["proposal"] == proposal["proposal"] and approved["candidates"] == proposal["candidates"]
    wait(lambda: target.read_text() if target.is_file() else None, lambda value: value is not None)
    assert target.read_text().strip() == "M3 human approved actual"
    wait(lambda: request(server, "GET", f"/api/cron/jobs/{job_id}/runs").json()["data"]["items"], lambda rows: any(row["status"] == "completed" for row in rows))
    stop_job(server, job_id)
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM cron_jobs WHERE json_extract(metadata,'$.proposal_id')=?", (proposal["id"],)).fetchone()[0] == 1
    assert request(server, "POST", route, body=approved_body).status_code == 200
    assert request(server, "POST", route, body=body(proposal, decision="reject_once")).status_code == 409


def test_rejection_has_no_plan_and_same_decision_is_idempotent(server):
    proposal, target, task, _directory = propose(server)
    route = f'/api/cron/proposals/{proposal["id"]}'
    rejected = request(server, "POST", route, body=body(proposal, decision="reject_once"))
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["status"] == "rejected"
    assert request(server, "POST", route, body=body(proposal, decision="reject_once")).status_code == 200
    wait(lambda: request(server, "GET", f"/api/task-runs/{task}").json()["data"]["status"], lambda value: value in {"completed", "failed"})
    assert not target.exists()
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM cron_jobs WHERE json_extract(metadata,'$.proposal_id')=?", (proposal["id"],)).fetchone()[0] == 0


def test_suggested_scope_cannot_become_an_unapproved_capability(server):
    suggestion = [{"tool": "write_file", "pattern": "/Users"}]
    proposal, target, task, _directory = propose(server, interval=60000, execution_mode="existing", suggested=suggestion)
    assert proposal["proposal"]["pre_authorized"] == suggestion
    assert {"tool": "write_file", "pattern": "/Users"} not in proposal["candidates"]
    route = f'/api/cron/proposals/{proposal["id"]}'
    assert request(server, "POST", route, body=body(proposal, suggestion)).status_code == 403
    approved = request(server, "POST", route, body=body(proposal))
    assert approved.status_code == 200, approved.text
    job_id = approved.json()["data"]["job_id"]
    wait(lambda: request(server, "GET", f"/api/task-runs/{task}").json()["data"]["status"], lambda value: value in {"completed", "failed"})
    job = request(server, "GET", f"/api/cron/jobs/{job_id}").json()["data"]
    assert job["metadata"]["pre_authorized"] == [] and job["target"]["execution_mode"] == "existing"
    fired = request(server, "POST", f"/api/cron/jobs/{job_id}/run-now", body={"client_request_id": uuid.uuid4().hex, "expected_revision": 1})
    assert fired.status_code == 202, fired.text
    occurrence = wait(lambda: request(server, "GET", f"/api/cron/jobs/{job_id}/runs").json()["data"]["items"][0],
        lambda value: value["status"] in {"completed", "failed", "interrupted", "pending_verification"})
    assert occurrence["status"] == "failed" and "PRE_AUTH_EXCEEDED" in occurrence["note"]
    assert not target.exists()
    stop_job(server, job_id)


def test_cancelled_source_expires_proposal_without_creation(server):
    proposal, target, task, _directory = propose(server)
    assert request(server, "POST", f"/api/task-runs/{task}/cancel").status_code == 202
    route = f'/api/cron/proposals/{proposal["id"]}'
    expired = wait(lambda: request(server, "GET", route).json()["data"], lambda value: value["status"] == "expired")
    assert expired["job_id"] is None and expired["revision"] == 2
    assert request(server, "POST", route, body=body(proposal)).status_code == 409
    assert not target.exists()


def test_sigkill_pending_proposal_cannot_use_old_approval(server):
    proposal, target, task, _directory = propose(server)
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        before = conn.execute("SELECT t.status,c.status FROM task_runs t JOIN tool_calls c ON c.task_run_id=t.id WHERE c.call_id=?", (proposal["call_id"],)).fetchone()
    assert before == ("running", "prepared")
    server["kill_and_restart"]()
    route = f'/api/cron/proposals/{proposal["id"]}'
    expired = request(server, "GET", route).json()["data"]
    assert expired["status"] == "expired" and expired["job_id"] is None
    assert request(server, "POST", route, body=body(proposal)).status_code == 409
    assert request(server, "POST", f'/api/tool-approvals/{proposal["call_id"]}', body={"decision": "allow_once", "input_hash": proposal["input_hash"]}).status_code == 409
    assert request(server, "GET", f"/api/task-runs/{task}").json()["data"]["status"] == "interrupted"
    assert not target.exists()
    (server["root"] / "cron-pending-proposal-window.json").write_text(json.dumps({"proposal": proposal, "task_before": before,
        "proposal_after": expired, "signal": "SIGKILL", "file_exists": target.exists()}, ensure_ascii=False, indent=2))


def test_sigkill_after_approval_recovers_one_real_plan(server):
    proposal, _target, task, directory = propose(server, interval=60000)
    route = f'/api/cron/proposals/{proposal["id"]}'
    approved_body = body(proposal, [{"tool": "write_file", "pattern": str(directory)}])
    result = request(server, "POST", route, body=approved_body)
    assert result.status_code == 200, result.text
    job_id = result.json()["data"]["job_id"]
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        before = conn.execute("SELECT t.status,c.status FROM task_runs t JOIN tool_calls c ON c.task_run_id=t.id WHERE c.call_id=?", (proposal["call_id"],)).fetchone()
    assert before[0] == "running"
    server["kill_and_restart"]()
    assert request(server, "POST", route, body=approved_body).json()["data"]["job_id"] == job_id
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM cron_jobs WHERE json_extract(metadata,'$.proposal_id')=?", (proposal["id"],)).fetchone()[0] == 1
        state = conn.execute("SELECT status FROM tool_calls WHERE call_id=?", (proposal["call_id"],)).fetchone()[0]
        assert state == "completed"
        assert conn.execute("SELECT count(*) FROM tool_calls WHERE task_run_id=? AND status='pending_verification'", (task,)).fetchone()[0] == 0
    stop_job(server, job_id)
    (server["root"] / "cron-approved-proposal-window.json").write_text(json.dumps({"proposal": result.json()["data"], "task_before": before,
        "tool_after": state, "job_id": job_id, "signal": "SIGKILL"}, ensure_ascii=False, indent=2))
