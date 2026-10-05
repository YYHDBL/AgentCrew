"""M3-03：真实进程 SIGKILL 后计划配置及发生记录保持一致。"""

import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import uuid

import httpx

from agentcrew_server.secrets import redact, register_secret
from test_cron_store import proposal


def test_sigkill_preserves_plan_and_manual_occurrence(tmp_path):
    shutil.copy2(os.environ["AGENTCREW_TEST_CONFIG_SOURCE"], tmp_path / "config.json")
    token = secrets.token_urlsafe(32)
    register_secret(token)
    evidence = []

    def launch():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            requested_port = listener.getsockname()[1]
        log = (tmp_path / f"process-{len(evidence)}.log").open("w")
        child = subprocess.Popen([sys.executable, "-m", "agentcrew_server", "--data-dir", str(tmp_path), "--port", str(requested_port)],
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

    try:
        body = proposal({"kind": "every", "every_ms": 86400000, "tz": "Asia/Shanghai"}, "重启持久计划")
        job = call("POST", "/api/cron/jobs", body)
        trigger = {"client_request_id": uuid.uuid4().hex, "expected_revision": 1}
        occurrence = call("POST", f'/api/cron/jobs/{job["id"]}/run-now', trigger)
        with sqlite3.connect(tmp_path / "agentcrew.db") as conn:
            before = conn.execute("SELECT id,revision,schedule,target,metadata,next_run_at FROM cron_jobs WHERE id=?", (job["id"],)).fetchone()
            before_occurrence = conn.execute("SELECT id,job_id,trigger,scheduled_at,client_request_id FROM cron_job_runs WHERE id=?", (occurrence["id"],)).fetchone()
        child.send_signal(signal.SIGKILL)
        assert child.wait(10) == -signal.SIGKILL
        client.close()
        log.close()
        child, client, log = launch()
        assert call("GET", f'/api/cron/jobs/{job["id"]}')["schedule"] == job["schedule"]
        assert call("POST", f'/api/cron/jobs/{job["id"]}/run-now', trigger)["id"] == occurrence["id"]
        with sqlite3.connect(tmp_path / "agentcrew.db") as conn:
            assert conn.execute("SELECT id,revision,schedule,target,metadata,next_run_at FROM cron_jobs WHERE id=?", (job["id"],)).fetchone() == before
            assert conn.execute("SELECT id,job_id,trigger,scheduled_at,client_request_id FROM cron_job_runs WHERE id=?", (occurrence["id"],)).fetchone() == before_occurrence
            assert conn.execute("SELECT count(*) FROM cron_job_runs WHERE job_id=?", (job["id"],)).fetchone()[0] == 1
            evidence.append({"job_id": job["id"], "occurrence_id": occurrence["id"], "configuration_row": before,
                "occurrence_row": before_occurrence, "event_watermark": conn.execute("SELECT max(global_seq) FROM run_events").fetchone()[0],
                "audit_seq": conn.execute("SELECT max(seq) FROM audit_log").fetchone()[0]})
    finally:
        client.close()
        child.terminate()
        child.wait(15)
        log.close()
        (tmp_path / "persistence-evidence.json").write_text(redact(json.dumps(evidence, ensure_ascii=False, indent=2)))
