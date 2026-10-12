"""M3-02：真实 SIGKILL、同任务恢复及历史查询无副作用验收。"""

import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

from agentcrew_server.secrets import redact, register_secret
from test_run_center_api import initialize_actual_soul


def test_sigkill_history_and_resume_with_real_model(tmp_path):
    directory = tmp_path / "runtime"
    directory.mkdir()
    shutil.copy2(os.environ["AGENTCREW_TEST_CONFIG_SOURCE"], directory / "config.json")
    token = secrets.token_urlsafe(32)
    register_secret(token)
    records = []

    def launch():
        log = (directory / f"process-{len(records)}.log").open("w")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            requested_port = listener.getsockname()[1]
        child = subprocess.Popen([sys.executable, "-m", "agentcrew_server", "--data-dir", str(directory), "--port", str(requested_port)],
            env={**os.environ, "AGENTCREW_TOKEN": token}, stdout=subprocess.PIPE, stderr=log, text=True)
        line = child.stdout.readline().strip()
        assert line.startswith("AGENTCREW_READY "), redact((directory / f"process-{len(records)}.log").read_text())
        port = json.loads(line.split(" ", 1)[1])["port"]
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=60)
        records.append({"pid": child.pid, "port": port})
        return child, client, log

    child, client, log = launch()

    def request(method, path, body=None):
        response = client.request(method, path, json=body)
        value = response.json() if response.content else None
        records.append({"method": method, "path": path, "request": body, "status": response.status_code,
                        "response": json.loads(redact(json.dumps(value, ensure_ascii=False)))})
        assert response.status_code < 400, response.text
        return value["data"] if value else None

    def wait(read, accepts, seconds=120):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = read()
            if accepts(value):
                return value
            time.sleep(0.1)
        raise AssertionError("真实模型及运行状态未达到指定验收条件")

    try:
        initialized = {"client": client, "requests": records}
        initialize_actual_soul(initialized)
        records.append({"real_soul_source": initialized["real_soul_source"], "real_soul_imported": initialized["real_soul_imported"]})
        workspace = request("GET", "/api/workspaces/office")["data_dir"]
        first = os.path.join(workspace, "m3-before-kill.txt")
        second = os.path.join(workspace, "m3-after-resume.txt")
        created = request("POST", "/api/conversations", {"workspace_id": "office", "agent_id": "xiaowen",
            "client_request_id": uuid.uuid4().hex,
            "instruction": f"先调用 write_file 将 M3 before kill 写入 {first}，然后必须调用 ask_user 提问是否继续，并等待真人回答。获得回答后调用 write_file 将 M3 after resume 写入 {second}。恢复时保留已完成文件，不重复写入。"})
        task, conversation = created["task_run_id"], created["conversation"]["id"]
        def initial_question():
            state = request("GET", f"/api/task-runs/{task}")
            assert state["status"] != "failed", state
            for item in request("GET", f"/api/task-runs/{task}/approvals?status=pending"):
                request("POST", f'/api/tool-approvals/{item["call_id"]}', {"decision": "allow_once", "input_hash": item["input_hash"]})
            return request("GET", f"/api/conversations/{conversation}/questions")

        question = wait(initial_question, bool)[0]
        first_file, second_file = Path(first), Path(second)
        assert first_file.read_text().strip() == "M3 before kill"
        before = {"sha256": hashlib.sha256(first_file.read_bytes()).hexdigest(), "mtime_ns": first_file.stat().st_mtime_ns}
        child.send_signal(signal.SIGKILL)
        assert child.wait(10) == -signal.SIGKILL
        client.close()
        log.close()
        child, client, log = launch()
        interrupted = request("GET", f"/api/task-runs/{task}")
        assert interrupted["status"] == "interrupted"
        first_page = request("GET", f"/api/task-runs/{task}/events?limit=1")
        assert first_page["has_more"]
        assert not second_file.exists()
        request("POST", f"/api/task-runs/{task}/resume")

        def settle():
            for item in request("GET", f"/api/conversations/{conversation}/questions"):
                request("POST", f'/api/questions/{item["request_id"]}/answer', {"answer": "继续，完成后续文件。"})
            for item in request("GET", f"/api/task-runs/{task}/approvals?status=pending"):
                request("POST", f'/api/tool-approvals/{item["call_id"]}', {"decision": "allow_once", "input_hash": item["input_hash"]})
            return request("GET", f"/api/task-runs/{task}")

        completed = wait(settle, lambda value: value["status"] in {"completed", "failed"})
        assert completed["status"] == "completed", completed
        assert second_file.read_text().strip() == "M3 after resume"
        assert before == {"sha256": hashlib.sha256(first_file.read_bytes()).hexdigest(), "mtime_ns": first_file.stat().st_mtime_ns}
        attempts = request("GET", f"/api/task-runs/{task}/attempts")
        assert [item["kind"] for item in attempts] == ["initial", "resume"]
        events = request("GET", f"/api/task-runs/{task}/events?limit=500")["items"]
        interrupted_event = next(item for item in events if item["type"] == "run.interrupted")
        resumed_event = next(item for item in events if item["type"] == "run.resumed")
        assert interrupted_event["seq"] < resumed_event["seq"]
        assert request("GET", f'/api/task-runs/{task}/events/{resumed_event["seq"]}?attempt_no=2') == resumed_event
        with sqlite3.connect(directory / "agentcrew.db") as conn:
            sql = "SELECT (SELECT count(*) FROM llm_calls),(SELECT count(*) FROM tool_calls),(SELECT max(global_seq) FROM run_events)"
            counts = conn.execute(sql).fetchone()
        for _ in range(3):
            request("GET", f"/api/task-runs/{task}/events?limit=500")
            request("GET", f"/api/runs/metrics?conversation_id={conversation}")
        with sqlite3.connect(directory / "agentcrew.db") as conn:
            assert conn.execute(sql).fetchone()[:2] == counts[:2]
        records.append({"task_run_id": task, "conversation_id": conversation, "before": before,
            "old_question_id": question["request_id"], "sql": sql, "counts": counts,
            "attempts": attempts, "events": events, "passed": True})
    finally:
        client.close()
        child.terminate()
        child.wait(15)
        log.close()
        (tmp_path / "recovery-evidence.json").write_text(redact(json.dumps(records, ensure_ascii=False, indent=2)))
