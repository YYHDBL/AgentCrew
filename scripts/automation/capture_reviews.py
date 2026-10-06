"""保存实际轨迹审查、冻结目标、模型调用与触发计数的SQL证据。"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from agentcrew_server.config import load_config
from agentcrew_server.db.audit import verify_levels
from agentcrew_server.runs import display_value
from agentcrew_server.secrets import register_secret, redact


root, request_file, output = map(Path, sys.argv[1:4])
configuration = load_config(root)
for key in configuration.api_keys():
    register_secret(key)
requests = json.loads(request_file.read_text())
job_ids = sorted({item["response"]["data"]["id"] for item in requests
    if item.get("status") in {200, 202} and "/review" in item.get("path", "")
    and isinstance(item.get("response", {}).get("data"), dict)
    and "id" in item["response"]["data"]})
assert job_ids
connection = sqlite3.connect(root / "agentcrew.db")
connection.row_factory = sqlite3.Row
result = {"job_ids": job_ids, "queries": []}
for job_id in job_ids:
    for sql in (
        "SELECT * FROM memory_jobs WHERE id=?",
        "SELECT * FROM job_governance WHERE job_id=?",
        "SELECT * FROM trace_targets WHERE job_id=? ORDER BY source_global_seq",
        "SELECT * FROM trace_reports WHERE job_id=? ORDER BY created_at,id",
        "SELECT * FROM memory_job_calls WHERE job_id=? ORDER BY ordinal",
        "SELECT * FROM memory_job_approvals WHERE job_id=?",
        "SELECT t.* FROM task_runs t JOIN trace_targets x ON x.task_run_id=t.id WHERE x.job_id=? ORDER BY t.id",
        "SELECT e.* FROM run_events e JOIN trace_targets x ON x.task_run_id=e.task_run_id WHERE x.job_id=? ORDER BY e.global_seq",
        "SELECT * FROM run_events WHERE json_extract(payload,'$.job_id')=? ORDER BY global_seq",
        "SELECT * FROM audit_log WHERE resource_type='memory_job' AND resource_id=? ORDER BY seq",
    ):
        rows = [dict(row) for row in connection.execute(sql, (job_id,))]
        for row in rows:
            for key in ("payload", "detail", "config_snapshot", "usage", "report"):
                if key in row and row[key] is not None:
                    row[key] = json.loads(row[key])
        result["queries"].append({"sql": sql, "parameters": [job_id], "rows": rows})
for sql in ("SELECT * FROM trace_review_state", "SELECT * FROM trace_trigger_state ORDER BY bucket",
            "SELECT * FROM trace_counted_events ORDER BY global_seq"):
    result["queries"].append({"sql": sql, "parameters": [], "rows": [dict(row) for row in connection.execute(sql)]})
result["event_watermark"] = connection.execute("SELECT max(global_seq) FROM run_events").fetchone()[0]
result["audit_seq"] = connection.execute("SELECT max(seq) FROM audit_log").fetchone()[0]
result["audit_verification"] = verify_levels(connection, root / "chain-head.txt")
assert result["audit_verification"]["ok"]
connection.close()
raw = redact(json.dumps(display_value(result), ensure_ascii=False, indent=2)) + "\n"
assert all(not key or key not in raw for key in configuration.api_keys())
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(raw)
print(json.dumps({"jobs": len(job_ids), "event_watermark": result["event_watermark"], "audit_seq": result["audit_seq"],
    "audit_ok": result["audit_verification"]["ok"], "sha256": hashlib.sha256(raw.encode()).hexdigest(), "credential_matches": 0}))
