"""真实M3 HTTP响应、事件契约、异步受理及身份边界。"""

import json
import sqlite3
import uuid
import threading
import concurrent.futures
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from httpx_sse import connect_sse
from agentcrew_core.events import RunEventType
from agentcrew_server.db.projections import _row_to_event
from agentcrew_server.runs import event_frame

from test_run_center_api import server
from test_governance_identity import request, demo
from test_cron_store import proposal
from test_cron_recovery_process import wait


ROOT = Path(__file__).resolve().parents[2]
SPEC = yaml.safe_load((ROOT / "docs/contracts/openapi.yaml").read_text())
BASE = "urn:agentcrew:openapi"
REGISTRY = Registry().with_resource(BASE, Resource.from_contents(SPEC, default_specification=DRAFT202012))


def response_contract(response, path, method="get"):
    status = str(response.status_code)
    assert status in SPEC["paths"][path][method]["responses"], (path, method, status)
    pointer = path.replace("~", "~0").replace("/", "~1")
    schema = {"$ref": f"{BASE}#/paths/{pointer}/{method}/responses/{status}/content/application~1json/schema"}
    declared = SPEC["paths"][path][method]["responses"][status]
    if "$ref" in declared:
        schema = {"$ref": BASE + declared["$ref"] + "/content/application~1json/schema"}
    Draft202012Validator(schema, registry=REGISTRY, format_checker=FormatChecker()).validate(response.json())


def test_actual_run_history_metrics_and_precise_event_contracts(server):
    response = request(server, "GET", "/api/task-runs?workspace_id=office&limit=2")
    response_contract(response, "/api/task-runs")
    task = response.json()["data"]["items"][0]["id"]
    for concrete, documented in ((f"/api/task-runs/{task}", "/api/task-runs/{id}"),
        (f"/api/task-runs/{task}/attempts", "/api/task-runs/{id}/attempts"),
        (f"/api/task-runs/{task}/events?limit=2", "/api/task-runs/{id}/events"),
        (f"/api/task-runs/{task}/audit-report", "/api/task-runs/{id}/audit-report"),
        ("/api/runs/metrics?workspace_id=office", "/api/runs/metrics")):
        response_contract(request(server, "GET", concrete), documented)
    event = request(server, "GET", f"/api/task-runs/{task}/events?limit=1").json()["data"]["items"][0]
    response_contract(request(server, "GET", f'/api/task-runs/{task}/events/{event["seq"]}'), "/api/task-runs/{id}/events/{seq}")
    assert request(server, "GET", "/api/task-runs?limit=201").status_code == 422


def test_actual_cron_crud_preview_async_occurrence_and_disabled_history(server):
    body = proposal({"kind": "every", "every_ms": 86400000, "tz": "UTC"}, "M3完整契约实际计划", target={
        "execution_mode": "new_conversation", "conversation_id": None, "instruction": "只用一句中文确认收到本条真实契约核查指令，禁止调用工具。"})
    response_contract(request(server, "POST", "/api/cron/authorization-preview", body=body), "/api/cron/authorization-preview", "post")
    created = request(server, "POST", "/api/cron/jobs", body=body)
    response_contract(created, "/api/cron/jobs", "post")
    job = created.json()["data"]["id"]
    response_contract(request(server, "GET", f"/api/cron/jobs/{job}"), "/api/cron/jobs/{id}")
    response_contract(request(server, "GET", "/api/cron/jobs?limit=2"), "/api/cron/jobs")
    run = {"client_request_id": uuid.uuid4().hex, "expected_revision": 1}
    accepted = request(server, "POST", f"/api/cron/jobs/{job}/run-now", body=run)
    response_contract(accepted, "/api/cron/jobs/{id}/run-now", "post")
    assert accepted.status_code == 202
    assert request(server, "POST", f"/api/cron/jobs/{job}/run-now", body=run).json()["data"]["id"] == accepted.json()["data"]["id"]
    task = accepted.json()["data"]["task_run_id"]
    wait(lambda: request(server, "GET", f"/api/task-runs/{task}").json()["data"], lambda value: value["status"] in {"completed", "failed"})
    edited = request(server, "PATCH", f"/api/cron/jobs/{job}", body={"change_id": uuid.uuid4().hex, "expected_revision": 1, "enabled": False})
    response_contract(edited, "/api/cron/jobs/{id}", "patch")
    response_contract(request(server, "GET", f"/api/cron/jobs/{job}/runs"), "/api/cron/jobs/{id}/runs")
    denied = request(server, "POST", f"/api/cron/jobs/{job}/run-now", body={"client_request_id": uuid.uuid4().hex, "expected_revision": 2})
    assert denied.status_code == 409
    response_contract(denied, "/api/cron/jobs/{id}/run-now", "post")
    response_contract(request(server, "DELETE", f"/api/cron/jobs/{job}", body={"change_id": uuid.uuid4().hex, "expected_revision": 2}), "/api/cron/jobs/{id}", "delete")


def test_actual_review_list_and_cancel_match_declared_status(server):
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        job = conn.execute("SELECT id FROM memory_jobs WHERE kind='trace_audit' ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    response_contract(request(server, "GET", f"/api/reviews/jobs/{job}"), "/api/reviews/jobs/{id}")
    response_contract(request(server, "POST", f"/api/reviews/jobs/{job}/cancel"), "/api/reviews/jobs/{id}/cancel", "post")
    response_contract(request(server, "GET", "/api/reviews/jobs?limit=1"), "/api/reviews/jobs")


def test_actual_cron_sse_has_committed_frames_and_rejects_out_of_scope(server):
    schema = json.loads((ROOT / "docs/contracts/events.schema.json").read_text())
    with connect_sse(server["client"], "GET", "/api/cron/stream?workspace_id=office&from=0") as source:
        assert source.response.status_code == 200
        frame = next(event for event in source.iter_sse() if event.data and event.event == "message")
        value = json.loads(frame.data)
        Draft202012Validator(schema).validate(value)
        assert value["type"].startswith("cron.")
        with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
            assert conn.execute("SELECT type FROM run_events WHERE global_seq=?", (value["global_seq"],)).fetchone()[0] == value["type"]
        server["requests"].append({"path": "/api/cron/stream", "status": 200, "actual_frame": value})
    assert request(server, "GET", "/api/cron/stream?workspace_id=missing").status_code == 403


def test_actual_exit_impact_is_sql_snapshot_and_member_cannot_manage(server):
    response = request(server, "GET", "/api/runtime/exit-impact")
    response_contract(response, "/api/runtime/exit-impact")
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert response.json()["data"]["at_global_seq"] <= conn.execute("SELECT max(global_seq) FROM run_events").fetchone()[0]
    member = demo(server, "lilei")
    assert request(server, "GET", "/api/runtime/exit-impact", identity=member, user="lilei").status_code == 403
    assert request(server, "POST", "/api/cron/jobs", identity=member, user="lilei", body={}).status_code == 403


def test_all_actual_display_events_conform_to_registered_schema(server):
    schema = json.loads((ROOT / "docs/contracts/events.schema.json").read_text())
    assert set(schema["definitions"]["EventType"]["enum"]) == {kind.value for kind in RunEventType}
    validator = Draft202012Validator(schema)
    counts = {}
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT * FROM run_events ORDER BY global_seq"):
            frame = event_frame(_row_to_event(row))
            validator.validate(frame)
            counts[frame["type"]] = counts.get(frame["type"], 0) + 1
    (server["root"] / "m3-event-contract-counts.json").write_text(json.dumps(counts, ensure_ascii=False, indent=2))


def test_actual_report_proposal_calls_and_promotion_contracts(server):
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        task, call = conn.execute("SELECT task_run_id,call_id FROM tool_calls ORDER BY prepared_at DESC LIMIT 1").fetchone()
        proposal_id = conn.execute("SELECT id FROM cron_proposals ORDER BY created_at DESC LIMIT 1").fetchone()[0]
        report_id, job_id = conn.execute("SELECT report_id,job_id FROM skill_promotions WHERE version_id IS NOT NULL ORDER BY confirmed_at DESC LIMIT 1").fetchone()
    response_contract(request(server, "GET", f"/api/task-runs/{task}/calls/{call}"), "/api/task-runs/{id}/calls/{call_id}")
    response_contract(request(server, "GET", f"/api/cron/proposals/{proposal_id}"), "/api/cron/proposals/{id}")
    accepted = request(server, "POST", f"/api/audit-reports/{report_id}/promote-skill", body={"client_request_id": uuid.uuid4().hex, "confirmed": True, "expected_report_id": report_id})
    response_contract(accepted, "/api/audit-reports/{id}/promote-skill", "post")
    assert accepted.json()["data"]["id"] == job_id


def test_actual_cron_subscription_closes_when_current_role_is_disabled(server):
    actor = demo(server, "wangming")
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        revision, role = conn.execute("SELECT revision,role FROM memberships WHERE id='membership-wangming'").fetchone()
        head = conn.execute("SELECT max(global_seq) FROM run_events").fetchone()[0]
    connected = threading.Event()
    def consume():
        with connect_sse(server["client"], "GET", f"/api/cron/stream?workspace_id=office&from={head}", headers={"X-AgentCrew-Identity": actor}) as source:
            assert source.response.status_code == 200
            connected.set()
            return [event.data for event in source.iter_sse() if event.data]
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(consume)
        assert connected.wait(5)
        denied = request(server, "PATCH", "/api/memberships/membership-wangming/role", body={"change_id": uuid.uuid4().hex,
            "expected_revision": revision, "role": role, "status": "disabled"})
        assert denied.status_code == 200
        assert future.result(timeout=5) == []
    assert request(server, "GET", "/api/cron/jobs", identity=actor, user="wangming").status_code in {401, 403}
    restored = request(server, "PATCH", "/api/memberships/membership-wangming/role", body={"change_id": uuid.uuid4().hex,
        "expected_revision": revision + 1, "role": role, "status": "active"})
    assert restored.status_code == 200
    server["requests"].append({"path": "/api/cron/stream", "actual_role_revocation_closed_stream": True, "from_global_seq": head})
