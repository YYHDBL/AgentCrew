"""M3-08：只读审查白名单、实际轨迹及辅助作业。"""

from agentcrew_core.tools import build_default_registry
import json
import sqlite3
import uuid
import time

from test_run_center_api import server
from test_governance_identity import request, demo
from test_cron_recovery_process import wait
from test_cron_store import proposal
from pathlib import Path


def test_trace_tool_is_readonly_and_registered():
    tool = build_default_registry().get("trace_read")
    assert tool is not None and tool.metadata.read_only and not tool.metadata.needs_approval


def test_review_registry_cannot_execute_write_tools():
    from agentcrew_server.reviews.trace_audit import TraceAudit
    registry = TraceAudit.registry()
    assert set(registry.names()) == {"trace_read", "session_search"}
    assert all(registry.get(name).metadata.read_only for name in registry.names())


def test_frozen_attempt_does_not_include_a_later_terminal_event():
    from agentcrew_core.events import Event, RunEventType as T
    from agentcrew_core.events.run_projection import project_attempts
    started = Event(1, "started", "task", 1, "conversation", T.RUN_STARTED,
        {"attempt_no": 1, "attempt_id": "actual-attempt"}, 1, ts="2026-10-06T00:00:00+00:00")
    ended = Event(2, "ended", "task", 2, "conversation", T.RUN_COMPLETED, {}, 1, ts="2026-10-06T00:01:00+00:00")
    frozen = project_attempts([started])[0]
    assert frozen["status"] == "running" and frozen["outcome"] is None and frozen["ended_at"] is None
    assert project_attempts([started, ended])[0]["ended_at"] == ended.ts
    failed = Event(3, "failed", "task", 3, "conversation", T.RUN_FAILED, {"reason": "actual-error"}, 1, ts=ended.ts)
    assert project_attempts([started, failed])[0]["outcome"] == "failed:actual-error"


def test_actual_verification_batch_target_keeps_frozen_attempt(server):
    from agentcrew_server.reviews.trace_audit import TraceAudit
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        conn.row_factory = sqlite3.Row
        event = conn.execute("SELECT * FROM run_events WHERE type='tool.verification_submitted' AND attempt_no IS NULL ORDER BY global_seq DESC LIMIT 1").fetchone()
        assert event is not None
        started = conn.execute("SELECT payload FROM run_events WHERE task_run_id=? AND type IN ('run.started','run.resumed') AND global_seq<=? ORDER BY global_seq DESC LIMIT 1", (event["task_run_id"], event["global_seq"])).fetchone()
        assert started is not None
        expected = json.loads(started[0])["attempt_no"]
        target = TraceAudit.target(conn, event["task_run_id"], event["global_seq"], event["attempt_no"])
        assert target["attempt_no"] == expected and target["source_global_seq"] == event["global_seq"]


def test_actual_main_trace_read_returns_real_source_watermark(server):
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        target = conn.execute("SELECT t.id FROM task_runs t JOIN conversations c ON c.id=t.conversation_id WHERE c.agent_id='xiaowen' AND t.status='failed' ORDER BY t.created_at DESC LIMIT 1").fetchone()[0]
    created = request(server, "POST", "/api/conversations", body={"workspace_id": "office", "agent_id": "xiaowen", "client_request_id": uuid.uuid4().hex,
        "instruction": f"必须只调用trace_read读取真实任务 {target}，limit=20，after_seq=0。随后只简短汇报实际状态和水位，禁止其他工具。"})
    assert created.status_code == 201, created.text
    task = created.json()["data"]["task_run_id"]
    result = wait(lambda: request(server, "GET", f"/api/task-runs/{task}").json()["data"], lambda value: value["status"] in {"completed", "failed"})
    assert result["status"] == "completed", result
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        call = conn.execute("SELECT call_id,status FROM tool_calls WHERE task_run_id=? AND tool_name='trace_read'", (task,)).fetchone()
        assert call is not None and call[1] == "completed"
        payload = json.loads(conn.execute("SELECT payload FROM run_events WHERE task_run_id=? AND type='tool.completed' AND json_extract(payload,'$.call_id')=?", (task, call[0])).fetchone()[0])
        assert payload["details"]["source_global_seq"] > 0
    (server["root"] / "trace-main-read.json").write_text(json.dumps({"source_task_run_id": target, "reader_task_run_id": task, "call_id": call[0], "result": payload}, ensure_ascii=False, indent=2))


def test_real_aux_analyzes_existing_actual_failure(server):
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        row = conn.execute("SELECT t.id FROM task_runs t JOIN run_events e ON e.task_run_id=t.id WHERE t.status='failed' AND e.type='tool.failed' AND json_extract(e.payload,'$.error') LIKE '%PRE_AUTH_EXCEEDED%' ORDER BY e.global_seq DESC LIMIT 1").fetchone()
    assert row is not None
    task = row[0]
    response = request(server, "POST", f"/api/task-runs/{task}/review", body={"client_request_id": uuid.uuid4().hex})
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["id"]
    result = wait(lambda: request(server, "GET", f"/api/reviews/jobs/{job_id}").json()["data"],
        lambda value: value["status"] in {"completed", "skipped", "failed", "cancelled", "interrupted"}, seconds=150)
    assert result["status"] in {"completed", "skipped"}, result
    report_result = request(server, "GET", f"/api/task-runs/{task}/audit-report").json()["data"]
    report = report_result["report"]
    if result["status"] == "completed":
        assert report is not None and report["job_id"] == job_id and report["task_run_id"] == task
        reference = report["report"]["root_cause_event"]
        if reference is not None:
            assert reference["task_run_id"] == task and reference["global_seq"] <= report["source_global_seq"]
            located = request(server, "GET", f'/api/task-runs/{task}/events/{reference["seq"]}').json()["data"]
            assert located["global_seq"] == reference["global_seq"] and located["attempt_no"] == reference["attempt_no"]
    else:
        assert result["reason"] and report is None
    member = demo(server, "lilei")
    assert request(server, "GET", f"/api/reviews/jobs/{job_id}", identity=member, user="lilei").status_code == 403
    assert request(server, "GET", f"/api/task-runs/{task}/audit-report", identity=member, user="lilei").status_code == 403
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        calls = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM memory_job_calls WHERE job_id=? AND type='tool.prepared'", (job_id,))]
        assert all(call["tool_name"] in {"trace_read", "session_search"} for call in calls)
        assert conn.execute("SELECT count(*) FROM memory_job_approvals WHERE job_id=?", (job_id,)).fetchone()[0] == 0
    (server["root"] / "trace-review-actual.json").write_text(json.dumps({"task_run_id": task, "job": result, "report": report, "calls": calls}, ensure_ascii=False, indent=2))


def test_five_real_completed_tasks_register_one_frozen_batch(server):
    agent_id = "xiaowen"
    bucket = json.dumps({"workspace_id": "office", "agent_id": agent_id, "effective_user_id": "owner", "credential_owner_id": "owner"}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        row = conn.execute("SELECT completed_count FROM trace_trigger_state WHERE bucket=?", (bucket,)).fetchone()
    remainder = (5 - row[0] % 5) % 5 if row is not None else 0
    preparations = []
    for _ in range(remainder):
        created = request(server, "POST", "/api/conversations", body={"workspace_id": "office", "agent_id": agent_id,
            "client_request_id": uuid.uuid4().hex, "instruction": "只用一句中文确认收到本条指令，禁止调用工具。"})
        assert created.status_code == 201, created.text
        task = created.json()["data"]["task_run_id"]
        result = wait(lambda: request(server, "GET", f"/api/task-runs/{task}").json()["data"], lambda value: value["status"] in {"completed", "failed"})
        assert result["status"] == "completed", result
        preparations.append(task)
    def settled():
        with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
            return conn.execute("SELECT completed_count,completed_watermark FROM trace_trigger_state WHERE bucket=?", (bucket,)).fetchone()
    state = wait(settled, lambda value: value is not None and value[0] == value[1] and value[0] % 5 == 0)
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        start = conn.execute("SELECT coalesce(max(global_seq),0) FROM run_events").fetchone()[0]
    completed = []
    for _ in range(5):
        created = request(server, "POST", "/api/conversations", body={"workspace_id": "office", "agent_id": agent_id,
            "client_request_id": uuid.uuid4().hex, "instruction": "只用一句中文确认本次真实批量审查材料已接收，禁止调用工具。"})
        assert created.status_code == 201, created.text
        task = created.json()["data"]["task_run_id"]
        result = wait(lambda: request(server, "GET", f"/api/task-runs/{task}").json()["data"], lambda value: value["status"] in {"completed", "failed"})
        assert result["status"] == "completed", result
        completed.append(task)
    def batches():
        with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
            return conn.execute("SELECT id,status FROM memory_jobs WHERE kind='trace_audit' AND agent_id=? AND trigger_key LIKE 'batch:%' AND trigger_global_seq>?", (agent_id, start)).fetchall()
    rows = wait(batches, lambda values: len(values) == 1)
    batch_id = rows[0][0]
    result = wait(lambda: request(server, "GET", f"/api/reviews/jobs/{batch_id}").json()["data"],
        lambda value: value["status"] in {"completed", "skipped", "failed", "cancelled", "interrupted"}, seconds=180)
    assert result["status"] in {"completed", "skipped"}, result
    assert {item["task_run_id"] for item in result["targets"]} == set(completed)
    assert all(item["attempt_no"] == 1 and item["result"] is not None for item in result["targets"])
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM trace_counted_events e JOIN conversations c ON c.id=(SELECT conversation_id FROM task_runs WHERE id=e.task_run_id) WHERE e.global_seq>? AND e.kind='completed' AND c.agent_id=?", (start, agent_id)).fetchone()[0] == 5
    (server["root"] / "trace-batch-actual.json").write_text(json.dumps({"source_watermark": start, "agent_id": agent_id, "preparation_task_ids": preparations,
        "actual_counter_before": list(state), "task_ids": completed, "job": result}, ensure_ascii=False, indent=2))


def test_real_failure_immediately_registers_high_priority_review(server):
    workspace = request(server, "GET", "/api/workspaces/office").json()["data"]["data_dir"]
    missing = Path(workspace) / ("m3-trace-missing-" + uuid.uuid4().hex)
    create = request(server, "POST", "/api/cron/jobs", body=proposal({"kind": "every", "every_ms": 86400000, "tz": "UTC"},
        "真实失败高优先审查", target={"execution_mode": "new_conversation", "conversation_id": None,
            "instruction": f"必须只实际调用一次read_file读取 {missing}。保留真实NOT_FOUND错误，禁止其他工具或创建文件，随后汇报实际结果。"}))
    assert create.status_code == 201, create.text
    plan_id = create.json()["data"]["id"]
    fire = request(server, "POST", f"/api/cron/jobs/{plan_id}/run-now", body={"client_request_id": uuid.uuid4().hex, "expected_revision": 1})
    assert fire.status_code == 202, fire.text
    def registered():
        with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT j.id,j.priority,j.trigger_global_seq,j.task_run_id FROM memory_jobs j JOIN task_runs t ON t.id=j.task_run_id WHERE t.cron_job_id=? AND j.kind='trace_audit' AND j.trigger_key LIKE 'failure:%'", (plan_id,)).fetchone()
            return dict(row) if row else None
    review = wait(registered, lambda value: value is not None)
    assert review["priority"] == 100
    assert request(server, "PATCH", f"/api/cron/jobs/{plan_id}", body={"change_id": uuid.uuid4().hex, "expected_revision": 1, "enabled": False}).status_code == 200
    result = wait(lambda: request(server, "GET", f'/api/reviews/jobs/{review["id"]}').json()["data"],
        lambda value: value["status"] in {"completed", "skipped", "failed", "cancelled", "interrupted"}, seconds=150)
    assert result["status"] in {"completed", "skipped"}, result
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM memory_jobs WHERE kind='trace_audit' AND trigger_key=?", ("failure:" + str(review["trigger_global_seq"]),)).fetchone()[0] == 1
    assert not missing.exists()
    (server["root"] / "trace-failure-auto.json").write_text(json.dumps({"plan_id": plan_id, "occurrence": fire.json()["data"], "job": result,
        "registered": review, "file_exists": missing.exists()}, ensure_ascii=False, indent=2))


def test_foreground_cancels_actual_review_stream_within_two_seconds(server):
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        source = conn.execute("SELECT task_run_id FROM trace_reports ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    response = request(server, "POST", f"/api/task-runs/{source}/review", body={"client_request_id": uuid.uuid4().hex})
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["id"]
    def started():
        with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
            return conn.execute("SELECT count(*) FROM memory_job_calls WHERE job_id=? AND type='llm.request_started'", (job_id,)).fetchone()[0]
    wait(started, lambda count: count > 0)
    before = time.monotonic()
    foreground = request(server, "POST", "/api/conversations", body={"workspace_id": "office", "agent_id": "xiaowen",
        "client_request_id": uuid.uuid4().hex, "instruction": "只用一句中文确认前台已获得执行优先权，禁止调用工具。"})
    elapsed = time.monotonic() - before
    assert foreground.status_code == 201 and elapsed < 2, (foreground.text, elapsed)
    result = request(server, "GET", f"/api/reviews/jobs/{job_id}").json()["data"]
    assert result["status"] == "cancelled", result
    assert result["usage"] is None
    task = foreground.json()["data"]["task_run_id"]
    wait(lambda: request(server, "GET", f"/api/task-runs/{task}").json()["data"]["status"], lambda value: value in {"completed", "failed"})
    (server["root"] / "trace-cancel-actual.json").write_text(json.dumps({"job_id": job_id, "foreground_task_run_id": task,
        "elapsed_seconds": elapsed, "job": result, "actual_model_request_started": True}, ensure_ascii=False, indent=2))


def test_sigkill_interrupts_actual_review_without_duplicate_trigger(server):
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        source = conn.execute("SELECT task_run_id FROM trace_reports ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    response = request(server, "POST", f"/api/task-runs/{source}/review", body={"client_request_id": uuid.uuid4().hex})
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["id"]
    def started():
        with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
            return conn.execute("SELECT count(*) FROM memory_job_calls WHERE job_id=? AND type='llm.request_started'", (job_id,)).fetchone()[0]
    wait(started, lambda count: count > 0)
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        trigger = conn.execute("SELECT trigger_key FROM memory_jobs WHERE id=?", (job_id,)).fetchone()[0]
    server["kill_and_restart"]()
    after = request(server, "GET", f"/api/reviews/jobs/{job_id}").json()["data"]
    assert after["status"] == "interrupted" and after["usage"] is None
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM memory_jobs WHERE kind='trace_audit' AND trigger_key=?", (trigger,)).fetchone()[0] == 1
    assert request(server, "POST", f"/api/reviews/jobs/{job_id}/cancel").status_code == 200
    (server["root"] / "trace-sigkill-actual.json").write_text(json.dumps({"job_id": job_id, "task_run_id": source,
        "trigger_key": trigger, "after": after, "signal": "SIGKILL"}, ensure_ascii=False, indent=2))
