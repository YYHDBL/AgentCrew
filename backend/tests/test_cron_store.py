"""M3-03：真实HTTP计划配置、发生身份、修订、权限和技能引用。"""

import concurrent.futures
import json
import sqlite3
import time
import uuid

from test_governance_identity import server, request, demo


def proposal(schedule, name="真实定时配置", **changes):
    return {"change_id": uuid.uuid4().hex, "workspace_id": "office", "agent_id": "xiaowen",
        "name": name, "schedule": schedule,
        "target": {"instruction": "核查当前实际工作目录并记录结果。", "execution_mode": "new_conversation", "conversation_id": None},
        "pre_authorized": [], **changes}


def test_three_schedules_crud_revision_and_idempotency(server):
    now = time.time_ns() // 1_000_000
    bodies = [proposal({"kind": "at", "at_ms": now + 60000, "tz": "Asia/Shanghai"}, "真实at"),
              proposal({"kind": "every", "every_ms": 5000, "tz": "UTC"}, "真实every"),
              proposal({"kind": "cron", "expr": "*/5 * * * *", "tz": "Europe/Berlin"}, "真实cron")]
    for body in bodies:
        response = request(server, "POST", "/api/cron/jobs", body=body)
        assert response.status_code == 201, response.text
        job = response.json()["data"]
        assert job["revision"] == 1
        assert job["schedule"] == body["schedule"]
        assert job["metadata"]["owner_id"] == "owner"
        assert job["metadata"]["created_by"] == "user"
        assert job["metadata"]["skill_versions"]
        assert request(server, "POST", "/api/cron/jobs", body=body).json()["data"] == job
        assert request(server, "POST", "/api/cron/jobs", body={**body, "name": "不同内容"}).status_code == 409
        assert request(server, "GET", f'/api/cron/jobs/{job["id"]}').json()["data"] == job
        changed = request(server, "PATCH", f'/api/cron/jobs/{job["id"]}', body={
            "change_id": uuid.uuid4().hex, "expected_revision": 1, "enabled": False, "name": body["name"] + "已停用"})
        assert changed.status_code == 200, changed.text
        assert changed.json()["data"]["state"]["enabled"] is False
        assert changed.json()["data"]["revision"] == 2
        assert request(server, "PATCH", f'/api/cron/jobs/{job["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": 1, "enabled": True}).status_code == 409
        deleted = request(server, "DELETE", f'/api/cron/jobs/{job["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": 2})
        assert deleted.status_code == 200
        assert deleted.json()["data"]["deleted_at"]
        assert request(server, "GET", f'/api/cron/jobs/{job["id"]}').status_code == 200


def test_creation_rejects_invalid_schedule_identity_and_scope(server):
    for schedule in ({"kind": "every", "every_ms": 0, "tz": "UTC"},
                     {"kind": "every", "every_ms": 9223372036854775807, "tz": "UTC"},
                     {"kind": "at", "at_ms": True, "tz": "UTC"},
                     {"kind": "cron", "expr": "", "tz": "UTC"},
                     {"kind": "cron", "expr": "0 0 31 2 *", "tz": "UTC"},
                     {"kind": "cron", "expr": "@daily", "tz": "UTC"},
                     {"kind": "cron", "expr": "0 0 * * *", "tz": "invalid-zone"}):
        assert request(server, "POST", "/api/cron/jobs", body=proposal(schedule)).status_code == 422
    member = demo(server, "lilei")
    body = proposal({"kind": "every", "every_ms": 5000, "tz": "UTC"})
    assert request(server, "POST", "/api/cron/jobs", identity=member, user="lilei", body=body).status_code == 403
    assert request(server, "POST", "/api/cron/jobs", body={**body, "created_by": "agent"}).status_code == 422
    assert request(server, "POST", "/api/cron/jobs", body={**body, "agent_id": "xiaogang"}).status_code == 403
    assert request(server, "POST", "/api/cron/jobs", body={**body, "pre_authorized": [{"tool": "write_file", "pattern": "/Users"}]}).status_code == 403
    assert request(server, "POST", "/api/cron/jobs", body={**body, "target": {**body["target"], "execution_mode": "existing"}}).status_code == 422


def test_manual_occurrence_has_independent_identity_and_history(server):
    body = proposal({"kind": "every", "every_ms": 5000, "tz": "UTC"}, "独立手动发生")
    job = request(server, "POST", "/api/cron/jobs", body=body).json()["data"]
    trigger = {"client_request_id": uuid.uuid4().hex, "expected_revision": 1}
    fired = request(server, "POST", f'/api/cron/jobs/{job["id"]}/run-now', body=trigger)
    assert fired.status_code == 202, fired.text
    occurrence = fired.json()["data"]
    assert occurrence["trigger"] == "manual"
    assert occurrence["scheduled_at"] is None
    assert occurrence["triggered_at"] > 0
    assert occurrence["retry_count"] == 0
    assert request(server, "POST", f'/api/cron/jobs/{job["id"]}/run-now', body=trigger).json()["data"]["id"] == occurrence["id"]
    other = request(server, "POST", f'/api/cron/jobs/{job["id"]}/run-now', body={"client_request_id": uuid.uuid4().hex, "expected_revision": 1}).json()["data"]
    assert other["id"] != occurrence["id"]
    history = request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs?limit=1').json()["data"]
    assert len(history["items"]) == 1
    assert history["next_after"]
    following = request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs?limit=1&after={history["next_after"]}').json()["data"]
    assert following["items"][0]["id"] != history["items"][0]["id"]
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM cron_job_runs WHERE job_id=?", (job["id"],)).fetchone()[0] == 2
    assert request(server, "DELETE", f'/api/cron/jobs/{job["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": 1}).status_code == 200
    assert len(request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs?limit=200').json()["data"]["items"]) == 2


def test_concurrent_edit_serializes_revision(server):
    job = request(server, "POST", "/api/cron/jobs", body=proposal({"kind": "every", "every_ms": 5000, "tz": "UTC"}, "并发修订")).json()["data"]
    def edit(index):
        return server["client"].patch(f'/api/cron/jobs/{job["id"]}', json={"change_id": uuid.uuid4().hex, "expected_revision": 1, "name": f"并发编辑{index}"}).status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(edit, range(2))) == [200, 409]


def test_authorization_preview_and_enabled_skill_references(server):
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]
    body = proposal({"kind": "every", "every_ms": 5000, "tz": "UTC"}, "真实技能引用",
                    pre_authorized=[{"tool": "write_file", "pattern": workspace["data_dir"]}])
    preview = request(server, "POST", "/api/cron/authorization-preview", body=body)
    assert preview.status_code == 200, preview.text
    assert body["pre_authorized"][0] in preview.json()["data"]["candidates"]
    job = request(server, "POST", "/api/cron/jobs", body=body).json()["data"]
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM skill_references WHERE resource_type='cron' AND resource_id=? AND active=1", (job["id"],)).fetchone()[0] == len(job["metadata"]["skill_versions"])
    assert request(server, "PATCH", f'/api/cron/jobs/{job["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": 1, "enabled": False}).status_code == 200
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM skill_references WHERE resource_type='cron' AND resource_id=? AND active=1", (job["id"],)).fetchone()[0] == 0


def test_revoked_agent_does_not_hide_other_member_plans(server):
    admin = demo(server, "wangming")
    agent = request(server, "POST", "/api/agents", body={"change_id": uuid.uuid4().hex, "expected_revision": 0,
        "workspace_id": "office", "name": "计划可见性员工", "spec": {"position": "核查实际材料", "model_slot": "main", "skill_ids": [], "connector_ids": []}}).json()["data"]
    existing = request(server, "GET", "/api/grants?workspace_id=office&grantee_id=wangming&limit=200").json()["data"]["items"]
    grants = {}
    for employee in ("xiaowen", agent["id"]):
        grant = next((value for value in existing if value["resource_type"] == "agent" and value["resource_id"] == employee), None)
        if grant is None:
            response = request(server, "POST", "/api/grants", body={"change_id": uuid.uuid4().hex,
                "resource_type": "agent", "resource_id": employee, "grantee_type": "user", "grantee_id": "wangming"})
            assert response.status_code == 200, response.text
            grant = response.json()["data"]
        grants[employee] = grant
    jobs = []
    for employee in ("xiaowen", agent["id"]):
        response = request(server, "POST", "/api/cron/jobs", identity=admin, user="wangming",
            body=proposal({"kind": "every", "every_ms": 86400000, "tz": "UTC"}, "成员当前计划", agent_id=employee))
        assert response.status_code == 201, response.text
        jobs.append(response.json()["data"])
    assert request(server, "PATCH", "/api/memberships/membership-wangming/role", body={"change_id": uuid.uuid4().hex, "expected_revision": 1, "role": "member"}).status_code == 200
    revoked = grants[agent["id"]]
    assert request(server, "DELETE", f'/api/grants/{revoked["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": revoked["revision"]}).status_code == 200
    response = request(server, "GET", "/api/cron/jobs?workspace_id=office", identity=admin, user="wangming")
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["data"]["items"]] == [jobs[0]["id"]]
    assert request(server, "GET", f'/api/cron/jobs/{jobs[1]["id"]}', identity=admin, user="wangming").status_code == 403


def test_preview_retains_file_candidate_after_connector_revocation(server):
    created = request(server, "POST", "/api/agents/xiaowen/permission-rules", body={
        "change_id": uuid.uuid4().hex, "tool_name": "http_request", "pattern": "example.com", "effect": "allow"})
    assert created.status_code == 200, created.text
    grants = request(server, "GET", "/api/grants?workspace_id=office&grantee_id=xiaowen&limit=200").json()["data"]["items"]
    grant = next(value for value in grants if value["resource_type"] == "connector" and value["resource_id"] == "office-http")
    assert request(server, "DELETE", f'/api/grants/{grant["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": grant["revision"]}).status_code == 200
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]
    body = proposal({"kind": "every", "every_ms": 86400000, "tz": "UTC"}, "当前文件候选",
                    pre_authorized=[{"tool": "write_file", "pattern": workspace["data_dir"]}])
    preview = request(server, "POST", "/api/cron/authorization-preview", body=body)
    assert preview.status_code == 200, preview.text
    assert all(value["tool"] != "http_request" for value in preview.json()["data"]["candidates"])
    assert request(server, "POST", "/api/cron/jobs", body=body).status_code == 201
