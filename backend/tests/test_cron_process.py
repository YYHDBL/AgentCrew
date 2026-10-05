"""M3-04：真实暂停、恢复、终止重启及错过不补跑。"""

import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid

import httpx

from agentcrew_server.secrets import redact, register_secret
from test_cron_store import proposal


def test_real_stop_continue_and_kill_missed_windows(tmp_path):
    shutil.copy2(os.environ["AGENTCREW_TEST_CONFIG_SOURCE"], tmp_path / "config.json")
    token = secrets.token_urlsafe(32)
    register_secret(token)
    evidence = []

    def launch():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        log = (tmp_path / f"process-{len(evidence)}.log").open("w")
        child = subprocess.Popen([sys.executable, "-m", "agentcrew_server", "--data-dir", str(tmp_path), "--port", str(port)],
            env={**os.environ, "AGENTCREW_TOKEN": token}, stdout=subprocess.PIPE, stderr=log, text=True)
        line = child.stdout.readline().strip()
        assert line.startswith("AGENTCREW_READY ")
        port = json.loads(line.split(" ", 1)[1])["port"]
        evidence.append({"pid": child.pid, "port": port})
        return child, httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=30), log

    child, client, log = launch()

    def call(method, path, body=None):
        response = client.request(method, path, json=body)
        value = response.json()
        evidence.append({"method": method, "path": path, "request": body, "status": response.status_code, "response": value})
        assert response.status_code < 400, response.text
        return value["data"]

    def history(job_id):
        return call("GET", f"/api/cron/jobs/{job_id}/runs?limit=200")["items"]

    def wait(read, accepts, seconds=10):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            value = read()
            if accepts(value):
                return value
            time.sleep(0.02)
        raise AssertionError("实际调度没有达到指定状态")

    try:
        every = call("POST", "/api/cron/jobs", proposal({"kind": "every", "every_ms": 1000, "tz": "UTC"}, "真实暂停计划"))
        wait(lambda: history(every["id"]), lambda value: any(row["status"] == "fired" for row in value))
        before = call("GET", f'/api/cron/jobs/{every["id"]}')
        stopped_at = time.time_ns() // 1_000_000
        child.send_signal(signal.SIGSTOP)
        time.sleep(4.2)
        child.send_signal(signal.SIGCONT)
        resumed_at = time.time_ns() // 1_000_000
        observed = wait(lambda: history(every["id"]), lambda value: any(row["status"] == "missed" for row in value))
        missed = next(row for row in observed if row["status"] == "missed")
        assert missed["missed_count"] >= 3
        assert missed["scheduled_at"] == before["state"]["next_run_at"]
        assert missed["missed_through"] <= resumed_at
        assert sum(row["status"] == "fired" for row in observed) == 1
        assert len({row["scheduled_at"] for row in observed}) == len(observed)
        wait(lambda: history(every["id"]), lambda value: any(row["status"] == "skipped" for row in value))
        current = call("GET", f'/api/cron/jobs/{every["id"]}')
        call("PATCH", f'/api/cron/jobs/{every["id"]}', {"change_id": uuid.uuid4().hex, "expected_revision": current["revision"], "enabled": False})
        evidence.append({"signal": "SIGSTOP/SIGCONT", "stopped_at": stopped_at, "resumed_at": resumed_at, "missed": missed})
        crossing = call("POST", "/api/cron/jobs", proposal({"kind": "at", "at_ms": time.time_ns() // 1_000_000 + 3000, "tz": "UTC"}, "跨越到期但迟延小于宽限"))
        child.send_signal(signal.SIGSTOP)
        time.sleep(4)
        child.send_signal(signal.SIGCONT)
        crossing_history = wait(lambda: history(crossing["id"]), bool)
        assert crossing_history[0]["triggered_at"] - crossing_history[0]["scheduled_at"] < 2000
        assert crossing_history[0]["status"] == "missed"
        evidence.append({"signal": "SIGSTOP/SIGCONT", "crossing_at_job_id": crossing["id"], "occurrences": crossing_history})
        at = call("POST", "/api/cron/jobs", proposal({"kind": "at", "at_ms": time.time_ns() // 1_000_000 + 2000, "tz": "Asia/Shanghai"}, "终止后过期at"))
        child.send_signal(signal.SIGKILL)
        assert child.wait(10) == -signal.SIGKILL
        client.close()
        log.close()
        time.sleep(3.5)
        child, client, log = launch()
        ended = wait(lambda: history(at["id"]), bool)
        assert len(ended) == 1
        assert ended[0]["status"] == "missed"
        assert ended[0]["task_run_id"] is None
        status = call("GET", f'/api/cron/jobs/{at["id"]}')
        assert status["state"]["next_run_at"] is None
        assert not status["state"]["enabled"]
        evidence.append({"signal": "SIGKILL/restart", "at_job_id": at["id"], "ended": ended, "state": status["state"]})
    finally:
        child.send_signal(signal.SIGCONT)
        client.close()
        child.terminate()
        child.wait(15)
        log.close()
        (tmp_path / "clock-process-evidence.json").write_text(redact(json.dumps(evidence, ensure_ascii=False, indent=2)))
