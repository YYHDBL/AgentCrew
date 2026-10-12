"""M3-06：实际HTTP副作用、SIGKILL、待核验与正常真人恢复。"""

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

from test_run_center_api import server
from test_governance_identity import request
from test_governance_connectors import persistent_http
from test_cron_store import proposal


def wait(read, accepts, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = read()
        if accepts(value):
            return value
        time.sleep(0.05)
    raise AssertionError("真实进程及副作用未达到指定验收状态")


def create_http_plan(server, upstream):
    response = request(server, "POST", "/api/connectors", body={"change_id": uuid.uuid4().hex, "expected_revision": 0,
        "workspace_id": "office", "name": "实际中断HTTP-" + uuid.uuid4().hex, "type": "http",
        "config": {"url": upstream["url"], "allowed_hosts": ["127.0.0.1"], "allowed_ports": [upstream["port"]], "allow_loopback": True}})
    assert response.status_code == 200, response.text
    connector = response.json()["data"]
    assert request(server, "POST", "/api/grants", body={"change_id": uuid.uuid4().hex, "resource_type": "connector",
        "resource_id": connector["id"], "grantee_type": "agent", "grantee_id": "xiaowen"}).status_code == 200
    assert request(server, "POST", "/api/agents/xiaowen/permission-rules", body={"change_id": uuid.uuid4().hex,
        "tool_name": "http_request", "pattern": "127.0.0.1", "effect": "allow"}).status_code == 200
    body = proposal({"kind": "every", "every_ms": 15000, "tz": "UTC"}, "真实未知副作用恢复",
        target={"instruction": f"仅调用http_request，connector_id={connector['id']}，method=POST，url={upstream['url']}/slow，body为 M3 recovery actual side effect。如果未来恢复时账本已经确认该操作完成，禁止再次发送，只汇报原操作已确认。", "execution_mode": "new_conversation", "conversation_id": None},
        pre_authorized=[{"tool": "http_request", "pattern": "127.0.0.1"}])
    result = request(server, "POST", "/api/cron/jobs", body=body)
    assert result.status_code == 201, result.text
    return result.json()["data"]


def test_unknown_effect_and_human_resume_do_not_repeat_http(server, persistent_http):
    upstream = persistent_http
    job = create_http_plan(server, upstream)
    def count():
        with sqlite3.connect(upstream["database"]) as conn:
            return conn.execute("SELECT count(*) FROM operations").fetchone()[0]
    wait(count, lambda value: value == 1)
    history = request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs').json()["data"]["items"]
    original = next(value for value in history if value["task_run_id"] is not None)
    task_id = original["task_run_id"]
    baseline = {"sha256": hashlib.sha256(Path(upstream["database"]).read_bytes()).hexdigest(),
                "mtime_ns": Path(upstream["database"]).stat().st_mtime_ns}
    server["kill_and_restart"]()
    task = request(server, "GET", f"/api/task-runs/{task_id}").json()["data"]
    assert task["status"] == "waiting_verification", task
    conversation_id = task["conversation_id"]
    pending = request(server, "GET", f"/api/conversations/{conversation_id}/pending-verifications").json()["data"]
    assert len(pending) == 1
    assert request(server, "POST", f"/api/task-runs/{task_id}/resume").status_code == 409
    def occurrences():
        return request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs').json()["data"]["items"]
    skipped = wait(occurrences, lambda rows: any(row["status"] == "skipped" for row in rows))
    current = next(value for value in skipped if value["id"] == original["id"])
    assert current["status"] == "pending_verification" and current["retry_count"] == 0
    assert count() == 1
    assert baseline == {"sha256": hashlib.sha256(Path(upstream["database"]).read_bytes()).hexdigest(),
                        "mtime_ns": Path(upstream["database"]).stat().st_mtime_ns}
    last_skip = max(value["scheduled_at"] for value in skipped if value["status"] == "skipped")
    assert last_skip > original["scheduled_at"]
    verified = request(server, "POST", f'/api/tool-calls/{pending[0]["call_id"]}/verification', body={"verdict": "confirmed_executed", "note": "已实际查询上游SQLite，唯一POST操作已保存，禁止重复发送"})
    assert verified.status_code == 200, verified.text
    assert request(server, "POST", f"/api/task-runs/{task_id}/resume").status_code == 202
    result = wait(lambda: request(server, "GET", f"/api/task-runs/{task_id}").json()["data"], lambda value: value["status"] in {"completed", "failed"})
    assert result["status"] == "completed", result
    assert request(server, "PATCH", f'/api/cron/jobs/{job["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": 1, "enabled": False}).status_code == 200
    assert count() == 1
    final = next(value for value in occurrences() if value["id"] == original["id"])
    assert final["status"] == "completed" and final["retry_count"] == 0
    assert [value["attempt_no"] for value in final["attempts"]] == [1, 2]
    with sqlite3.connect(upstream["database"]) as conn:
        rows = conn.execute("SELECT id,body,auth_sha256 FROM operations").fetchall()
    (server["root"] / "cron-unknown-recovery.json").write_text(json.dumps({"job_id": job["id"], "occurrence_id": original["id"],
        "task_run_id": task_id, "conversation_id": conversation_id, "pending": pending, "baseline": baseline,
        "upstream_sql": "SELECT id,body,auth_sha256 FROM operations", "upstream_rows": rows,
        "final_occurrence": final, "after": {"sha256": hashlib.sha256(Path(upstream["database"]).read_bytes()).hexdigest(),
            "mtime_ns": Path(upstream["database"]).stat().st_mtime_ns}}, ensure_ascii=False, indent=2))


def test_sigkill_registered_task_before_initial_attempt(server):
    result = request(server, "POST", "/api/agents", body={"change_id": uuid.uuid4().hex, "expected_revision": 0,
        "workspace_id": "office", "name": "真实登记窗口员工-" + uuid.uuid4().hex,
        "spec": {"position": "根据当前任务核查实际材料，保留原始记录，遵守权限与核验纪律。", "model_slot": "main", "skill_ids": [], "connector_ids": []}})
    assert result.status_code == 200, result.text
    employee = result.json()["data"]
    body = proposal({"kind": "every", "every_ms": 4000, "tz": "UTC"}, "真实登记未派发窗口",
        agent_id=employee["id"], target={"instruction": "只用一句中文确认收到。", "execution_mode": "new_conversation", "conversation_id": None})
    created = request(server, "POST", "/api/cron/jobs", body=body)
    assert created.status_code == 201, created.text
    job = created.json()["data"]
    def registered():
        with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT t.id,t.status,t.current_attempt_no,r.id AS occurrence_id FROM task_runs t JOIN cron_job_runs r ON r.task_run_id=t.id WHERE r.job_id=? ORDER BY r.created_at LIMIT 1", (job["id"],)).fetchone()
            generation = conn.execute("SELECT id,status FROM memory_soul_generations WHERE agent_id=? ORDER BY created_at DESC LIMIT 1", (employee["id"],)).fetchone()
            return {"task": dict(row) if row else None, "generation": dict(generation) if generation else None}
    observed = wait(registered, lambda value: value["task"] is not None and value["task"]["status"] == "queued" and value["generation"] is not None and value["generation"]["status"] == "running")
    task_id, occurrence_id = observed["task"]["id"], observed["task"]["occurrence_id"]
    server["kill_and_restart"]()
    actual = request(server, "GET", f"/api/task-runs/{task_id}").json()["data"]
    assert actual["status"] == "interrupted" and actual["current_attempt_no"] == 0
    history = wait(lambda: request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs').json()["data"]["items"],
        lambda value: any(row["status"] == "skipped" for row in value))
    original = next(value for value in history if value["id"] == occurrence_id)
    assert original["status"] == "interrupted" and original["retry_count"] == 0
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM task_runs WHERE cron_job_id=?", (job["id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM tool_calls WHERE task_run_id=?", (task_id,)).fetchone()[0] == 0
    assert request(server, "PATCH", f'/api/cron/jobs/{job["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": 1, "enabled": False}).status_code == 200
    (server["root"] / "cron-registered-recovery.json").write_text(json.dumps({"job_id": job["id"], "task_run_id": task_id,
        "occurrence_id": occurrence_id, "before": observed, "after": actual, "history": history}, ensure_ascii=False, indent=2))


def test_completed_write_before_sigkill_has_no_automatic_repetition(server):
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]["data_dir"]
    marker = Path(workspace) / ("m3-committed-effect-" + uuid.uuid4().hex + ".txt")
    assert request(server, "POST", "/api/agents/xiaowen/permission-rules", body={"change_id": uuid.uuid4().hex,
        "tool_name": "write_file", "pattern": workspace, "effect": "allow"}).status_code == 200
    assert request(server, "POST", "/api/agents/xiaowen/permission-rules", body={"change_id": uuid.uuid4().hex,
        "tool_name": "bash", "pattern": "sleep 30", "effect": "allow"}).status_code == 200
    body = proposal({"kind": "every", "every_ms": 15000, "tz": "UTC"}, "真实已完成文件副作用窗口",
        target={"instruction": f"先调用write_file将 M3 committed actual 写入 {marker}，然后bash执行精确命令 sleep 30，最后read_file核查该文件。未来恢复时已完成文件必须保留，不得重复写入；只完成尚未完成的步骤。", "execution_mode": "new_conversation", "conversation_id": None},
        pre_authorized=[{"tool": "write_file", "pattern": workspace}, {"tool": "bash", "pattern": "sleep 30"}])
    created = request(server, "POST", "/api/cron/jobs", body=body)
    assert created.status_code == 201, created.text
    job = created.json()["data"]
    def committed():
        with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT t.id,t.status,r.id AS occurrence_id FROM task_runs t JOIN cron_job_runs r ON r.task_run_id=t.id JOIN tool_calls c ON c.task_run_id=t.id WHERE r.job_id=? AND c.tool_name='write_file' AND c.status='completed' LIMIT 1", (job["id"],)).fetchone()
            return dict(row) if row is not None else None
    observed = wait(committed, lambda value: value is not None and value["status"] == "running")
    assert marker.read_text().strip() == "M3 committed actual"
    baseline = {"sha256": hashlib.sha256(marker.read_bytes()).hexdigest(), "mtime_ns": marker.stat().st_mtime_ns}
    server["kill_and_restart"]()
    original_id = observed["occurrence_id"]
    task_id = observed["id"]
    history = wait(lambda: request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs').json()["data"]["items"],
        lambda rows: any(row["status"] == "skipped" for row in rows))
    current = next(value for value in history if value["id"] == original_id)
    assert current["status"] in {"interrupted", "pending_verification"}
    assert current["retry_count"] == 0 and current["retry_at"] is None
    time.sleep(31)
    assert baseline == {"sha256": hashlib.sha256(marker.read_bytes()).hexdigest(), "mtime_ns": marker.stat().st_mtime_ns}
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        sql = "SELECT count(*) FROM tool_calls WHERE task_run_id=? AND tool_name='write_file' AND status='completed'"
        assert conn.execute(sql, (task_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM task_runs WHERE cron_job_id=?", (job["id"],)).fetchone()[0] == 1
    assert request(server, "PATCH", f'/api/cron/jobs/{job["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": 1, "enabled": False}).status_code == 200
    (server["root"] / "cron-committed-effect-recovery.json").write_text(json.dumps({"job_id": job["id"], "occurrence_id": original_id,
        "task_run_id": task_id, "registered": observed, "baseline": baseline, "after": current,
        "sql": sql, "parameters": [task_id], "completed_write_count": 1}, ensure_ascii=False, indent=2))
