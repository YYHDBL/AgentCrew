"""M3-02：真实 HTTP、模型任务与 SQLite 的运行查询回归。"""

import hashlib
import json
import os
import secrets
import shutil
import socket
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import httpx

from test_governance_identity import request, demo
from test_governance_identity import request as governance_request
from agentcrew_server.secrets import redact, register_secret


def initialize_actual_soul(record):
    source = json.loads((Path(__file__).parent / "fixtures" / "actual-deepseek-soul.json").read_text())
    assert source["model"] == "deepseek-v4.1-flash" and source["id"] == "56bfcd73783145228af917105d23f6c4"
    assert 1215 <= source["characters"] <= 1485
    assert hashlib.sha256(source["result"].encode()).hexdigest() == source["result_sha256"]
    assert len(source["result"]) == source["characters"]
    store = governance_request(record, "GET", "/api/memory/stores/soul/xiaowen?workspace_id=office&agent_id=xiaowen")
    assert store.status_code == 200, store.text
    current = store.json()["data"]
    imported = current["revision"] == 0
    if imported:
        response = governance_request(record, "POST", "/api/memory/stores/soul/xiaowen?workspace_id=office&agent_id=xiaowen", body={
            "change_id": "real-model-soul-" + source["id"], "expected_revision": current["revision"],
            "basis": f"测试初始化导入已完成的真实 {source['model']} SoulGeneration {source['id']}，正文 SHA256 {source['result_sha256']}",
            "text": source["result"]})
        assert response.status_code == 200, response.text
    record["real_soul_source"] = {key: source[key] for key in ("id", "agent_id", "conversation_id", "task_run_id", "model", "created_at", "prompt_sha256", "characters", "result_sha256")}
    record["real_soul_imported"] = imported


@pytest.fixture(scope="module")
def server(tmp_path_factory, request):
    configured_directory = os.environ.get("AGENTCREW_RUN_CENTER_DATA_DIR")
    root = Path(configured_directory).resolve() if configured_directory else tmp_path_factory.mktemp("run-center-http")
    if configured_directory:
        assert root.is_relative_to(Path(__file__).resolve().parents[2] / "data" / "m3-intermediate")
        assert (root / "config.json").is_file()
    else:
        shutil.copy2(os.environ["AGENTCREW_TEST_CONFIG_SOURCE"], root / "config.json")
    token = secrets.token_urlsafe(32)
    register_secret(token)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log = (root / "run-center-stderr.log").open("w")
    child = subprocess.Popen([sys.executable, "-m", "agentcrew_server", "--data-dir", str(root), "--port", str(port)],
        env={**os.environ, "AGENTCREW_TOKEN": token}, stdout=subprocess.PIPE, stderr=log, text=True)
    line = child.stdout.readline().strip()
    assert line.startswith("AGENTCREW_READY "), redact((root / "run-center-stderr.log").read_text())
    client = httpx.Client(base_url=f"http://127.0.0.1:{json.loads(line.split(' ',1)[1])['port']}",
                         headers={"Authorization": f"Bearer {token}"}, timeout=60)
    record = {"root": root, "client": client, "url": str(client.base_url), "token": token, "requests": []}
    def kill_and_restart():
        nonlocal child, client, log
        previous_pid = child.pid
        child.send_signal(signal.SIGKILL)
        assert child.wait(10) == -signal.SIGKILL
        client.close()
        log.close()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            next_port = listener.getsockname()[1]
        log = (root / ("run-center-restart-" + uuid.uuid4().hex + ".log")).open("w")
        child = subprocess.Popen([sys.executable, "-m", "agentcrew_server", "--data-dir", str(root), "--port", str(next_port)],
            env={**os.environ, "AGENTCREW_TOKEN": token}, stdout=subprocess.PIPE, stderr=log, text=True)
        line = child.stdout.readline().strip()
        assert line.startswith("AGENTCREW_READY ")
        new_port = json.loads(line.split(" ", 1)[1])["port"]
        client = httpx.Client(base_url=f"http://127.0.0.1:{new_port}", headers={"Authorization": f"Bearer {token}"}, timeout=60)
        record.update(client=client, url=str(client.base_url))
        record["requests"].append({"signal": "SIGKILL/restart", "previous_pid": previous_pid, "current_pid": child.pid, "port": new_port})
    record["kill_and_restart"] = kill_and_restart
    try:
        initialize_actual_soul(record)
        if configured_directory:
            plans = governance_request(record, "GET", "/api/cron/jobs?limit=200")
            assert plans.status_code == 200, plans.text
            assert plans.json()["data"]["next_after"] is None
            disabled = []
            for plan in plans.json()["data"]["items"]:
                if not plan["state"]["enabled"]:
                    continue
                changed = governance_request(record, "PATCH", f'/api/cron/jobs/{plan["id"]}', body={"change_id": uuid.uuid4().hex,
                    "expected_revision": plan["revision"], "enabled": False})
                assert changed.status_code == 200, changed.text
                disabled.append(plan["id"])
            active = governance_request(record, "GET", "/api/task-runs?limit=200").json()["data"]["items"]
            cancelled = []
            for task in active:
                if task["cron_job_id"] in disabled and task["status"] in {"queued", "running", "waiting_user"}:
                    stopped = governance_request(record, "POST", f'/api/task-runs/{task["id"]}/cancel')
                    assert stopped.status_code == 202, stopped.text
                    cancelled.append(task["id"])
            (root / "cron-acceptance-setup.json").write_text(json.dumps({"disabled_prior_job_ids": disabled, "cancelled_active_task_ids": cancelled}, ensure_ascii=False, indent=2))
        yield record
    finally:
        client.close()
        child.terminate()
        child.wait(15)
        log.close()
        serialized = json.dumps(record["requests"], ensure_ascii=False, indent=2) + "\n"
        (root / "run-center-http-evidence.json").write_text(serialized)
        (tmp_path_factory.getbasetemp() / "http-evidence.json").write_text(serialized)
        (tmp_path_factory.getbasetemp() / (request.node.path.stem + "-http-evidence.json")).write_text(serialized)


@pytest.fixture(scope="module")
def executions(server):
    root = server["root"]
    folder = root.parent / ("authorized-run-center-" + uuid.uuid4().hex)
    folder.mkdir()
    target = folder / "m3-run-query.txt"
    instruction = f"调用 write_file 将内容 M3 real run center 写入 {target}，随后调用 read_file 核查内容。最后调用 read_file 读取同目录不存在的 missing-m3.txt，保留实际错误并说明结果。"
    created = request(server, "POST", "/api/conversations", body={
        "workspace_id": "office", "agent_id": "xiaowen", "instruction": instruction,
        "client_request_id": uuid.uuid4().hex, "folders": [str(folder)]})
    assert created.status_code == 201, created.text
    task = created.json()["data"]["task_run_id"]
    conversation = created.json()["data"]["conversation"]["id"]
    deadline = time.monotonic() + 120
    approval_count = 0
    while time.monotonic() < deadline:
        approvals = request(server, "GET", f"/api/task-runs/{task}/approvals?status=pending").json()["data"]
        for approval in approvals:
            response = request(server, "POST", f'/api/tool-approvals/{approval["call_id"]}',
                body={"decision": "allow_once", "input_hash": approval["input_hash"]})
            assert response.status_code == 200, response.text
            approval_count += 1
        runs = request(server, "GET", f"/api/conversations/{conversation}/task-runs").json()["data"]
        if runs[0]["status"] in {"completed", "failed"}:
            break
        time.sleep(0.1)
    assert runs[0]["status"] == "completed", runs
    assert target.read_text().strip() == "M3 real run center"
    assert approval_count >= 1
    with sqlite3.connect(root / "agentcrew.db") as conn:
        assert conn.execute("SELECT count(*) FROM tool_calls WHERE task_run_id=? AND status='failed'", (task,)).fetchone()[0] >= 1
    return {"task": task, "conversation": conversation, "target": target}


def test_query_list_filters_and_current_visibility(server, executions):
    response = request(server, "GET", "/api/task-runs?workspace_id=office&limit=1")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == executions["task"]
    assert data["items"][0]["source"] == "user"
    assert data["at_global_seq"] >= 1
    filtered = request(server, "GET", f'/api/task-runs?conversation_id={executions["conversation"]}&status=failed').json()["data"]
    assert filtered["items"] == []
    member = demo(server, "lilei")
    assert request(server, "GET", f'/api/task-runs/{executions["task"]}', identity=member, user="lilei").status_code == 403
    assert request(server, "GET", "/api/task-runs?workspace_id=office", identity=member, user="lilei").json()["data"]["items"] == []


def test_fixed_event_watermark_and_exact_call_location(server, executions):
    task = executions["task"]
    first = request(server, "GET", f"/api/task-runs/{task}/events?limit=2").json()["data"]
    assert first["has_more"]
    watermark = first["at_global_seq"]
    events = first["items"]
    cursor = first["next_after_seq"]
    while cursor is not None:
        page = request(server, "GET", f"/api/task-runs/{task}/events?limit=2&after_seq={cursor}&through_global_seq={watermark}").json()["data"]
        assert page["at_global_seq"] == watermark
        events.extend(page["items"])
        cursor = page["next_after_seq"]
    sequences = [event["seq"] for event in events]
    assert sequences == list(range(1, max(sequences) + 1))
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert len(events) == conn.execute("SELECT count(*) FROM run_events WHERE task_run_id=? AND global_seq<=?", (task, watermark)).fetchone()[0]
    prepared = next(event for event in events if event["type"] == "tool.prepared")
    located = request(server, "GET", f'/api/task-runs/{task}/events/{prepared["seq"]}').json()["data"]
    assert located == prepared
    assert request(server, "GET", f'/api/task-runs/{task}/events/{prepared["seq"]}?attempt_no=999').status_code == 404
    call = request(server, "GET", f'/api/task-runs/{task}/calls/{prepared["payload"]["call_id"]}').json()["data"]
    assert call["kind"] == "tool"
    assert call["seq"] == prepared["seq"]
    assert call["events"]
    serialized = json.dumps(events)
    assert "thinking_blocks" not in serialized
    assert "reasoning_content" not in serialized


def test_metrics_match_separate_sql_aggregations(server, executions):
    conversation = executions["conversation"]
    metrics = request(server, "GET", f"/api/runs/metrics?conversation_id={conversation}").json()["data"]
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        task = executions["task"]
        model = conn.execute("SELECT count(*),sum(prompt_tokens),sum(completion_tokens) FROM llm_calls l JOIN steps s ON s.id=l.step_id WHERE s.task_run_id=?", (task,)).fetchone()
        tools = conn.execute("SELECT count(*) FROM tool_calls WHERE task_run_id=?", (task,)).fetchone()[0]
        steps = conn.execute("SELECT count(*) FROM steps WHERE task_run_id=?", (task,)).fetchone()[0]
    assert metrics["task_count"] == metrics["completed_count"] == 1
    assert metrics["completion_rate"] == 1
    assert metrics["llm_call_count"] == model[0]
    assert metrics["tool_call_count"] == tools
    assert metrics["step_count"] == steps
    assert metrics["prompt_tokens"] == model[1]
    assert metrics["completion_tokens"] == model[2]
    assert metrics["usage_complete"]


def test_history_pagination_during_real_new_task(server, executions):
    task = executions["task"]
    head = request(server, "GET", f"/api/task-runs/{task}/events?limit=1").json()["data"]["at_global_seq"]
    response = request(server, "POST", f'/api/conversations/{executions["conversation"]}/instructions', body={
        "text": "请只用一句中文确认你收到新的真实查询回归指令。", "client_request_id": uuid.uuid4().hex})
    assert response.status_code == 202, response.text
    cursor, events = 0, []
    while cursor is not None:
        page = request(server, "GET", f"/api/task-runs/{task}/events?limit=1&after_seq={cursor}&through_global_seq={head}").json()["data"]
        events.extend(page["items"])
        cursor = page["next_after_seq"]
    assert len({event["seq"] for event in events}) == len(events)
    assert all(event["global_seq"] <= head for event in events)
    with sqlite3.connect(server["root"] / "agentcrew.db") as conn:
        assert len(events) == conn.execute("SELECT count(*) FROM run_events WHERE task_run_id=? AND global_seq<=?", (task, head)).fetchone()[0]


def test_invalid_cursor_and_empty_metrics(server):
    assert request(server, "GET", "/api/task-runs?after=unbound").status_code == 422
    assert request(server, "GET", "/api/task-runs?limit=201").status_code == 422
    assert request(server, "GET", "/api/task-runs?since=invalid").status_code == 422
    assert request(server, "GET", "/api/task-runs?workspace_id=unknown").status_code == 403
    assert request(server, "GET", "/api/task-runs/not-found").status_code == 404
    result = request(server, "GET", "/api/runs/metrics?conversation_id=not-found")
    assert result.status_code == 404


def test_attempt_and_historical_configuration_remain_visible(server, executions):
    attempts = request(server, "GET", f'/api/task-runs/{executions["task"]}/attempts').json()["data"]
    assert attempts[0]["kind"] == "initial"
    record = request(server, "GET", f'/api/task-runs/{executions["task"]}').json()["data"]
    assert record["agent_spec_snapshot"]
    assert "skill_versions" in record


def test_actual_model_failure_reports_unknown_usage(server, executions):
    original = request(server, "GET", "/api/settings").json()["data"]["models"]["main"]["max_tokens"]
    updated = request(server, "PATCH", "/api/settings", body={"models": {"main": {"max_tokens": 1}}})
    assert updated.status_code == 200, updated.text
    created = request(server, "POST", "/api/conversations", body={
        "workspace_id": "office", "agent_id": "xiaowen",
        "instruction": "请详细说明一次真实文件核查的完整步骤。", "client_request_id": uuid.uuid4().hex}).json()["data"]
    task, conversation = created["task_run_id"], created["conversation"]["id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = request(server, "GET", f"/api/task-runs/{task}").json()["data"]
        if state["status"] == "failed":
            break
        time.sleep(0.1)
    assert state["status"] == "failed", state
    metrics = request(server, "GET", f"/api/runs/metrics?conversation_id={conversation}").json()["data"]
    assert metrics["llm_call_count"] >= 1
    assert metrics["missing_usage_calls"] >= 1
    assert metrics["prompt_tokens"] is None
    assert metrics["completion_tokens"] is None
    assert not metrics["usage_complete"]
    assert metrics["latency_ms"] is None
    assert request(server, "PATCH", "/api/settings", body={"models": {"main": {"max_tokens": original}}}).status_code == 200


def test_run_list_snapshot_survives_status_changes(server, executions):
    tasks = []
    for _ in range(2):
        created = request(server, "POST", "/api/conversations", body={
            "workspace_id": "office", "agent_id": "xiaowen", "client_request_id": uuid.uuid4().hex,
            "instruction": "必须调用 ask_user 提问是否继续，等待真人回答；回答之后用一句文字确认完成。"}).json()["data"]
        task, conversation = created["task_run_id"], created["conversation"]["id"]
        deadline = time.monotonic() + 60
        questions = []
        while time.monotonic() < deadline:
            state = request(server, "GET", f"/api/task-runs/{task}").json()["data"]
            assert state["status"] != "failed", state
            for approval in request(server, "GET", f"/api/task-runs/{task}/approvals?status=pending").json()["data"]:
                response = request(server, "POST", f'/api/tool-approvals/{approval["call_id"]}',
                    body={"decision": "allow_once", "input_hash": approval["input_hash"]})
                assert response.status_code == 200, response.text
            questions = request(server, "GET", f"/api/conversations/{conversation}/questions").json()["data"]
            if questions:
                break
            time.sleep(0.1)
        assert questions
        tasks.append((task, questions[0]["request_id"]))
    first = request(server, "GET", "/api/task-runs?workspace_id=office&status=waiting_user&limit=1").json()["data"]
    assert first["next_after"] is not None
    for task, question in tasks:
        assert request(server, "POST", f"/api/questions/{question}/answer", body={"answer": "继续并完成。"}).status_code == 200
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            for approval in request(server, "GET", f"/api/task-runs/{task}/approvals?status=pending").json()["data"]:
                response = request(server, "POST", f'/api/tool-approvals/{approval["call_id"]}',
                    body={"decision": "allow_once", "input_hash": approval["input_hash"]})
                assert response.status_code == 200, response.text
            state = request(server, "GET", f"/api/task-runs/{task}").json()["data"]
            if state["status"] == "completed":
                break
            time.sleep(0.1)
        assert state["status"] == "completed"
    response = request(server, "GET", f'/api/task-runs?workspace_id=office&status=waiting_user&limit=1&after={first["next_after"]}')
    assert response.status_code == 200, response.text
    second = response.json()["data"]
    assert len(second["items"]) == 1
    assert second["items"][0]["id"] != first["items"][0]["id"]
    assert second["items"][0]["status"] == "waiting_user"
    assert second["at_global_seq"] == first["at_global_seq"]
