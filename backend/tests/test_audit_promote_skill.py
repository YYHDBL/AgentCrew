"""M3-09：真实报告确认、辅助模型提炼、不可变版本与独立授权。"""

import hashlib
import json
import sqlite3
import time
import uuid

import pytest

from test_run_center_api import server
from test_governance_identity import request, demo
from test_cron_recovery_process import wait


def source_report(server, *, proposal=True, excluded=()):
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM trace_reports WHERE (json_extract(report,'$.skill_proposal') IS NOT NULL)=? ORDER BY created_at DESC,id", (int(proposal),)).fetchall()
    return next(dict(row) for row in rows if row["id"] not in excluded)


def confirm(server, report, request_id=None, **overrides):
    return request(server, "POST", f'/api/audit-reports/{report["id"]}/promote-skill', body={
        "client_request_id": request_id or uuid.uuid4().hex, "confirmed": True, "expected_report_id": report["id"], **overrides})


def actual_failure_proposal(server, instruction):
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        task = conn.execute("SELECT task_run_id FROM run_events WHERE type='tool.failed' AND json_extract(payload,'$.error') LIKE '%PRE_AUTH_EXCEEDED%' ORDER BY global_seq DESC LIMIT 1").fetchone()[0]
    response = request(server, "POST", f"/api/task-runs/{task}/review", body={"client_request_id": uuid.uuid4().hex, "instruction": instruction})
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["id"]
    review = wait(lambda: request(server, "GET", f"/api/reviews/jobs/{job_id}").json()["data"], lambda value: value["status"] in {"completed", "skipped", "failed", "cancelled"}, seconds=180)
    assert review["status"] == "completed", review
    report = request(server, "GET", f"/api/task-runs/{task}/audit-report").json()["data"]["report"]
    assert report and report["job_id"] == job_id and report["report"]["skill_proposal"] is not None, review
    report["report"] = json.dumps(report["report"], ensure_ascii=False)
    return report


def new_proposal(server):
    name = "真实预授权核查-" + uuid.uuid4().hex[:8]
    return actual_failure_proposal(server, f"请分析这项实际PRE_AUTH_EXCEEDED失败的可复用机制。真人明确要求独立归档一个新技能，建议名称采用{name}、existing_skill_id为null；本次归档需要完整的适用条件与可核查清单，当前索引中相同机理可作为参考。完整机理与边界必须由真实事件支持，禁止绕过预授权或补写未执行的结果。证据不足时如实说明。")


def test_promotion_requires_bound_human_confirmation(server):
    report = source_report(server)
    assert confirm(server, report, confirmed=False).status_code == 422
    assert confirm(server, report, confirmed=1).status_code == 422
    assert confirm(server, report, expected_report_id="changed-report").status_code == 409
    member = demo(server, "lilei")
    response = request(server, "POST", f'/api/audit-reports/{report["id"]}/promote-skill', user="lilei", identity=member,
        body={"client_request_id": uuid.uuid4().hex, "confirmed": True, "expected_report_id": report["id"]})
    assert response.status_code == 403
    assert confirm(server, source_report(server, proposal=False)).status_code == 409


def test_actual_approval_order_and_empty_scope_are_independent(server):
    from agentcrew_core.reviews import automation_approval_facts
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        conn.row_factory = sqlite3.Row
        task = conn.execute("SELECT t.id,t.cron_job_id FROM task_runs t JOIN cron_proposals p ON p.job_id=t.cron_job_id WHERE p.status='approved' AND p.selected='[]' ORDER BY t.created_at DESC LIMIT 1").fetchone()
        assert task is not None
        events = [{**dict(row), "payload": json.loads(row["payload"])} for row in conn.execute("SELECT * FROM run_events WHERE task_run_id=? ORDER BY global_seq", (task["id"],))]
        linked = [{**dict(row), "payload": json.loads(row["payload"])} for row in conn.execute("SELECT * FROM run_events WHERE type='cron.proposal_resolved' AND json_extract(payload,'$.job_id')=? ORDER BY global_seq", (task["cron_job_id"],))]
    facts = automation_approval_facts(linked, events)
    assert facts["approved_before_task_queued"] is True and facts["pre_authorized_selection_empty"] is True
    assert facts["approval_global_seq"] < facts["task_queued_global_seq"] and facts["selected"] == []
    assert automation_approval_facts([], events) is None


@pytest.fixture(scope="module")
def promoted(server):
    report = new_proposal(server)
    request_id = uuid.uuid4().hex
    response = confirm(server, report, request_id)
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["id"]
    result = wait(lambda: request(server, "GET", f"/api/reviews/jobs/{job_id}").json()["data"],
        lambda value: value["status"] in {"completed", "failed", "cancelled", "interrupted"}, seconds=180)
    assert result["status"] == "completed", result
    assert result["skill_id"] and result["version_id"] and result["report_id"] == report["id"]
    assert confirm(server, report, request_id).json()["data"]["id"] == job_id
    alias = uuid.uuid4().hex
    assert confirm(server, report, alias).json()["data"]["id"] == job_id
    assert confirm(server, source_report(server, excluded=(report["id"],)), alias).status_code == 409
    return {"report": report, "job": result}


def test_actual_promotion_publishes_model_body_version_and_ledger(server, promoted):
    job = promoted["job"]
    skill_id = job["skill_id"]
    version = request(server, "GET", f'/api/skills/{skill_id}/versions/{job["version_id"]}').json()["data"]
    resource = request(server, "GET", f"/api/skills/{skill_id}").json()["data"]
    assert resource["source"] == "agent" and not job["granted"]
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("SELECT count(*) FROM grants WHERE resource_type='skill' AND resource_id=? AND grantee_id='xiaowen' AND revoked_at IS NULL", (skill_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM skill_versions WHERE change_id=?", (job["id"] + "-publish",)).fetchone()[0] == 1
        ledger = dict(conn.execute("SELECT * FROM memory_ledger WHERE change_id=?", (job["id"] + "-publish",)).fetchone())
        assert json.loads(ledger["source"])["job_id"] == job["id"]
        assert json.loads(ledger["basis"])["report_id"] == promoted["report"]["id"]
        assert conn.execute("SELECT count(*) FROM skill_reads WHERE execution_id=?", (job["id"],)).fetchone()[0] == 1
    path = server["root"] / "skills" / skill_id / "SKILL.md"
    assert path.read_text() == version["content"] and version["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "机制" in version["content"] and len(version["content"]) > 100
    assert "无人值守" in version["content"] and "通知" in version["content"]
    assert version["content"].count("## 机制说明") == 1
    (server["root"] / "promotion-actual.json").write_text(json.dumps({**promoted, "version": version, "resource": resource,
        "file": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mtime_ns": path.stat().st_mtime_ns}}, ensure_ascii=False, indent=2))


def test_independent_grant_then_actual_next_task_reads_skill(server, promoted):
    job = promoted["job"]
    skill = request(server, "GET", f'/api/skills/{job["skill_id"]}').json()["data"]
    granted = request(server, "POST", "/api/grants", body={"change_id": uuid.uuid4().hex,
        "resource_type": "skill", "resource_id": skill["id"], "grantee_type": "agent", "grantee_id": "xiaowen"})
    assert granted.status_code == 200, granted.text
    employee = request(server, "GET", "/api/agents/xiaowen").json()["data"]
    spec = employee["spec"]
    spec["skill_ids"] = list(dict.fromkeys([*spec["skill_ids"], skill["id"]]))
    changed = request(server, "PATCH", "/api/agents/xiaowen", body={"change_id": uuid.uuid4().hex,
        "expected_revision": employee["revision"], "spec": spec})
    assert changed.status_code == 200, changed.text
    created = request(server, "POST", "/api/conversations", body={"workspace_id": "office", "agent_id": "xiaowen", "client_request_id": uuid.uuid4().hex,
        "instruction": f'必须实际调用skill_view读取技能“{skill["name"]}”正文。随后依据正文说明可复用的机制与执行边界，禁止其他工具。'})
    assert created.status_code == 201, created.text
    task = created.json()["data"]["task_run_id"]
    final = wait(lambda: request(server, "GET", f"/api/task-runs/{task}").json()["data"], lambda value: value["status"] in {"completed", "failed"})
    assert final["status"] == "completed", final
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        binding = json.loads(conn.execute("SELECT skill_versions FROM task_governance WHERE task_run_id=?", (task,)).fetchone()[0])
        assert binding[skill["id"]] == job["version_id"]
        call = conn.execute("SELECT call_id,status FROM tool_calls WHERE task_run_id=? AND tool_name='skill_view' AND json_extract(input,'$.name')=?", (task, skill["name"])).fetchone()
        assert call is not None and call[1] == "completed"
    (server["root"] / "promotion-reuse.json").write_text(json.dumps({"job_id": job["id"], "report_id": job["report_id"], "grant": granted.json()["data"],
        "task_run_id": task, "binding": binding, "call_id": call[0], "task": final}, ensure_ascii=False, indent=2))


def test_cancel_actual_promotion_stream_preserves_unpublished_state(server, promoted):
    report = new_proposal(server)
    response = confirm(server, report)
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["id"]
    def started():
        with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
            return conn.execute("SELECT count(*) FROM memory_job_calls WHERE job_id=? AND type='llm.request_started'", (job_id,)).fetchone()[0]
    wait(started, lambda count: count > 0)
    before = time.monotonic()
    cancelled = request(server, "POST", f"/api/reviews/jobs/{job_id}/cancel")
    elapsed = time.monotonic() - before
    assert cancelled.status_code in {200, 202} and elapsed < 2
    result = cancelled.json()["data"]
    assert result["status"] == "cancelled" and result["version_id"] is None and result["usage"] is None
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM skill_versions WHERE change_id=?", (job_id + "-publish",)).fetchone()[0] == 0
    assert confirm(server, report).json()["data"]["id"] == job_id
    (server["root"] / "promotion-cancel.json").write_text(json.dumps({"job": result, "elapsed_seconds": elapsed}, ensure_ascii=False, indent=2))


def test_existing_proposal_name_updates_one_version_without_changing_grants(server, promoted):
    skill_id = promoted["job"]["skill_id"]
    resource = request(server, "GET", f"/api/skills/{skill_id}").json()["data"]
    report = actual_failure_proposal(server, f"请针对这项真实预授权拒绝分析完善现有技能{resource['name']}的机制与执行边界。现有技能id为{skill_id}，真人希望形成更新建议，skill_proposal.name使用现有名称且existing_skill_id使用该实际id。请提供由真实事件支持的改进机理；证据不足时如实说明。")
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        conn.row_factory = sqlite3.Row
        skill = conn.execute("SELECT id,current_version_id FROM skills WHERE id=?", (skill_id,)).fetchone()
        grants_before = conn.execute("SELECT * FROM grants WHERE resource_type='skill' AND resource_id=? ORDER BY id", (skill[0],)).fetchall()
    old = request(server, "GET", f"/api/skills/{skill[0]}/versions/{skill[1]}").json()["data"]
    response = confirm(server, report)
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["id"]
    result = wait(lambda: request(server, "GET", f"/api/reviews/jobs/{job_id}").json()["data"], lambda value: value["status"] in {"completed", "failed", "cancelled"}, seconds=180)
    assert result["status"] == "completed" and result["skill_id"] == skill[0], result
    new = request(server, "GET", f'/api/skills/{skill[0]}/versions/{result["version_id"]}').json()["data"]
    assert new["version_no"] == old["version_no"] + 1
    assert new["content"].count("## 机制说明") == 1
    assert request(server, "GET", f"/api/skills/{skill[0]}/versions/{skill[1]}").json()["data"] == old
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        conn.row_factory = sqlite3.Row
        assert [tuple(row) for row in conn.execute("SELECT * FROM grants WHERE resource_type='skill' AND resource_id=? ORDER BY id", (skill[0],))] == [tuple(row) for row in grants_before]
    (server["root"] / "promotion-update.json").write_text(json.dumps({"report_id": report["id"], "job": result, "old": old, "new": new, "grants_unchanged": True}, ensure_ascii=False, indent=2))


def test_actual_skill_deny_stops_promotion_before_model_request(server, promoted):
    report = new_proposal(server)
    name = json.loads(report["report"])["skill_proposal"]["name"]
    created = request(server, "POST", "/api/agents/xiaowen/permission-rules", body={"change_id": uuid.uuid4().hex,
        "tool_name": "skill_patch", "pattern": name, "effect": "deny"})
    assert created.status_code == 200, created.text
    rule = created.json()["data"]
    response = confirm(server, report)
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["id"]
    result = wait(lambda: request(server, "GET", f"/api/reviews/jobs/{job_id}").json()["data"], lambda value: value["status"] in {"failed", "cancelled"})
    assert result["version_id"] is None
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM memory_job_calls WHERE job_id=? AND type='llm.request_started'", (job_id,)).fetchone()[0] == 0
    assert request(server, "DELETE", f'/api/agents/xiaowen/permission-rules/{rule["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": rule["revision"]}).status_code == 200
    (server["root"] / "promotion-deny.json").write_text(json.dumps({"rule": rule, "job": result, "actual_model_calls": 0}, ensure_ascii=False, indent=2))


def test_sigkill_before_publication_interrupts_actual_promotion(server, promoted):
    report = new_proposal(server)
    response = confirm(server, report)
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["id"]
    def started():
        with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
            return conn.execute("SELECT count(*) FROM memory_job_calls WHERE job_id=? AND type='llm.request_started'", (job_id,)).fetchone()[0]
    wait(started, lambda count: count > 0)
    server["kill_and_restart"]()
    result = request(server, "GET", f"/api/reviews/jobs/{job_id}").json()["data"]
    assert result["status"] == "interrupted" and result["version_id"] is None and result["usage"] is None
    assert confirm(server, report).json()["data"]["id"] == job_id
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM skill_versions WHERE change_id=?", (job_id + "-publish",)).fetchone()[0] == 0
    (server["root"] / "promotion-sigkill.json").write_text(json.dumps({"job": result, "signal": "SIGKILL", "published_versions": 0}, ensure_ascii=False, indent=2))


def test_revoking_current_skill_grant_cancels_actual_review_stream(server, promoted):
    task = promoted["report"]["task_run_id"]
    response = request(server, "POST", f"/api/task-runs/{task}/review", body={"client_request_id": uuid.uuid4().hex,
        "instruction": "核查当前已授权技能对这项实际失败的适用边界，仅根据真实事件分析。"})
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["id"]
    def started():
        with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
            return conn.execute("SELECT count(*) FROM memory_job_calls WHERE job_id=? AND type='llm.request_started'", (job_id,)).fetchone()[0]
    wait(started, lambda count: count > 0)
    grants = request(server, "GET", "/api/grants?workspace_id=office&grantee_id=xiaowen&limit=200").json()["data"]["items"]
    grant = next(item for item in grants if item["resource_type"] == "skill" and item["resource_id"] == promoted["job"]["skill_id"] and not item["revoked_at"])
    assert request(server, "DELETE", f'/api/grants/{grant["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": grant["revision"]}).status_code == 200
    result = wait(lambda: request(server, "GET", f"/api/reviews/jobs/{job_id}").json()["data"], lambda value: value["status"] == "cancelled")
    assert "AUTHORIZATION_REVOKED" in result["reason"] and result["usage"] is None
    assert request(server, "POST", "/api/grants", body={"change_id": uuid.uuid4().hex, "resource_type": "skill", "resource_id": grant["resource_id"], "grantee_type": "agent", "grantee_id": "xiaowen"}).status_code == 200
    (server["root"] / "promotion-grant-revocation.json").write_text(json.dumps({"revoked_grant": grant, "review": result}, ensure_ascii=False, indent=2))


def test_actual_current_granted_skill_is_readable_under_content_protection(server):
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        row = conn.execute("SELECT s.id,s.name,s.current_version_id FROM skills s JOIN skill_promotions p ON p.skill_id=s.id AND p.version_id=s.current_version_id JOIN grants g ON g.resource_id=s.id AND g.resource_type='skill' AND g.grantee_type='agent' AND g.grantee_id='xiaowen' AND g.revoked_at IS NULL WHERE s.status='active' ORDER BY p.confirmed_at DESC LIMIT 1").fetchone()
    assert row is not None
    skill_id, name, version_id = row
    created = request(server, "POST", "/api/conversations", body={"workspace_id": "office", "agent_id": "xiaowen", "client_request_id": uuid.uuid4().hex,
        "instruction": f"必须实际调用skill_view读取技能“{name}”的正文。随后根据实际正文说明无人值守失败处理与授权边界，禁止执行该技能中的任何操作，禁止其他工具。"})
    assert created.status_code == 201, created.text
    task = created.json()["data"]["task_run_id"]
    final = wait(lambda: request(server, "GET", f"/api/task-runs/{task}").json()["data"], lambda value: value["status"] in {"completed", "failed"})
    assert final["status"] == "completed", final
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        binding = json.loads(conn.execute("SELECT skill_versions FROM task_governance WHERE task_run_id=?", (task,)).fetchone()[0])
        assert binding[skill_id] == version_id
        call = conn.execute("SELECT call_id,status FROM tool_calls WHERE task_run_id=? AND tool_name='skill_view' AND json_extract(input,'$.name')=?", (task, name)).fetchone()
        assert call is not None and call[1] == "completed"
    (server["root"] / "promotion-protected-read.json").write_text(json.dumps({"skill_id": skill_id, "version_id": version_id, "task_run_id": task, "binding": binding,
        "call_id": call[0], "task": final}, ensure_ascii=False, indent=2))
