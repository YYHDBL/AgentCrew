"""保存实际计划、发生、任务、事件、审计和文件核验的完整 SQL 证据。"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from agentcrew_server.config import load_config
from agentcrew_server.runs import display_value
from agentcrew_server.secrets import register_secret, redact


root, request_file, output = map(Path, sys.argv[1:4])
configuration = load_config(root)
for key in configuration.api_keys():
    register_secret(key)
requests = json.loads(request_file.read_text())
job_ids = sorted({item["response"]["data"]["id"] for item in requests if item.get("path") == "/api/cron/jobs"
                  and item.get("method") == "POST" and item["status"] == 201})
assert job_ids
connection = sqlite3.connect(root / "agentcrew.db")
connection.row_factory = sqlite3.Row
result = {"job_ids": job_ids, "queries": [], "files": []}
for job_id in job_ids:
    for sql in (
        "SELECT * FROM cron_jobs WHERE id=?",
        "SELECT * FROM cron_job_runs WHERE job_id=? ORDER BY triggered_at,id",
        "SELECT a.* FROM cron_run_attempts a JOIN cron_job_runs r ON r.id=a.occurrence_id WHERE r.job_id=? ORDER BY a.retry_no,a.attempt_no",
        "SELECT t.* FROM task_runs t WHERE t.cron_job_id=? ORDER BY t.created_at,t.id",
        "SELECT a.* FROM run_attempts a JOIN task_runs t ON t.id=a.task_run_id WHERE t.cron_job_id=? ORDER BY a.started_at",
        "SELECT e.* FROM run_events e JOIN task_runs t ON t.id=e.task_run_id WHERE t.cron_job_id=? ORDER BY e.global_seq",
        "SELECT c.* FROM tool_calls c JOIN task_runs t ON t.id=c.task_run_id WHERE t.cron_job_id=? ORDER BY c.prepared_at",
        "SELECT * FROM runtime_notifications WHERE job_id=? ORDER BY global_seq",
        "SELECT * FROM audit_log WHERE resource_type='cron_job' AND resource_id=? ORDER BY seq"):
        rows = [dict(row) for row in connection.execute(sql, (job_id,))]
        for row in rows:
            for field in ("payload", "detail", "input", "schedule", "target", "metadata", "agent_spec_snapshot", "context_fingerprint"):
                if field in row and row[field] is not None:
                    row[field] = json.loads(row[field])
            if "input" in row and row["tool_name"] in {"read_file", "write_file"}:
                path = Path(row["input"]["path"]).expanduser()
                if not path.is_absolute():
                    path = Path(connection.execute("SELECT w.data_dir FROM workspaces w JOIN conversations c ON c.workspace_id=w.id JOIN task_runs t ON t.conversation_id=c.id WHERE t.id=?", (row["task_run_id"],)).fetchone()[0]) / path
                path = path.resolve()
                if not any(item["path"] == str(path) for item in result["files"]):
                    actual = {"path": str(path), "exists": path.is_file()}
                    if path.is_file():
                        actual.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), mtime_ns=path.stat().st_mtime_ns, size_bytes=path.stat().st_size)
                    result["files"].append(actual)
        result["queries"].append({"sql": sql, "parameters": [job_id], "rows": rows})
result["event_watermark"] = connection.execute("SELECT max(global_seq) FROM run_events").fetchone()[0]
result["audit_seq"] = connection.execute("SELECT max(seq) FROM audit_log").fetchone()[0]
connection.close()
raw = redact(json.dumps(display_value(result), ensure_ascii=False, indent=2)) + "\n"
assert all(not key or key not in raw for key in configuration.api_keys())
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(raw)
print(json.dumps({"jobs": len(job_ids), "files": len(result["files"]), "event_watermark": result["event_watermark"],
                  "audit_seq": result["audit_seq"], "sha256": hashlib.sha256(raw.encode()).hexdigest(), "credential_matches": 0}))
