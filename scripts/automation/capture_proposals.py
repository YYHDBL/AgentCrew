"""捕获实际提案、真人决定、源任务及批准计划的SQL证据。"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

from agentcrew_server.config import load_config
from agentcrew_server.runs import display_value
from agentcrew_server.secrets import register_secret, redact

root, request_file, output = map(Path, sys.argv[1:4])
configuration = load_config(root)
for key in configuration.api_keys():
    register_secret(key)
requests = json.loads(request_file.read_text())
proposal_ids = sorted({urlsplit(item["path"]).path.removeprefix("/api/cron/proposals/") for item in requests
    if item.get("path", "").startswith("/api/cron/proposals/")})
assert proposal_ids
connection = sqlite3.connect(root / "agentcrew.db")
connection.row_factory = sqlite3.Row
result = {"proposal_ids": proposal_ids, "queries": []}
for proposal_id in proposal_ids:
    statements = [
        ("SELECT * FROM cron_proposals WHERE id=?", [proposal_id]),
        ("SELECT * FROM cron_jobs WHERE id=(SELECT job_id FROM cron_proposals WHERE id=?)", [proposal_id]),
        ("SELECT * FROM task_runs WHERE id=(SELECT task_run_id FROM cron_proposals WHERE id=?)", [proposal_id]),
        ("SELECT a.* FROM run_attempts a JOIN cron_proposals p ON p.task_run_id=a.task_run_id WHERE p.id=? ORDER BY a.attempt_no", [proposal_id]),
        ("SELECT l.* FROM llm_calls l JOIN steps s ON s.id=l.step_id JOIN cron_proposals p ON p.task_run_id=s.task_run_id WHERE p.id=? ORDER BY s.ordinal", [proposal_id]),
        ("SELECT c.* FROM tool_calls c JOIN cron_proposals p ON p.task_run_id=c.task_run_id WHERE p.id=? ORDER BY c.prepared_at", [proposal_id]),
        ("SELECT e.* FROM run_events e WHERE e.task_run_id=(SELECT task_run_id FROM cron_proposals WHERE id=?) OR (e.type IN ('cron.proposal_requested','cron.proposal_resolved','cron.job_changed') AND json_extract(e.payload,'$.proposal_id')=?) ORDER BY e.global_seq", [proposal_id, proposal_id]),
        ("SELECT * FROM audit_log WHERE resource_type='cron_proposal' AND resource_id=? OR resource_type='tool_call' AND resource_id=? ORDER BY seq", [proposal_id, proposal_id]),
    ]
    for sql, parameters in statements:
        rows = [dict(row) for row in connection.execute(sql, parameters)]
        for row in rows:
            for key in ("proposal", "candidates", "selected", "target", "schedule", "metadata", "payload", "input", "detail", "context_fingerprint"):
                if key in row and row[key] is not None:
                    row[key] = json.loads(row[key])
        result["queries"].append({"sql": sql, "parameters": parameters, "rows": rows})
result["event_watermark"] = connection.execute("SELECT max(global_seq) FROM run_events").fetchone()[0]
result["audit_seq"] = connection.execute("SELECT max(seq) FROM audit_log").fetchone()[0]
connection.close()
raw = redact(json.dumps(display_value(result), ensure_ascii=False, indent=2)) + "\n"
assert all(not key or key not in raw for key in configuration.api_keys())
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(raw)
print(json.dumps({"proposals": len(proposal_ids), "event_watermark": result["event_watermark"], "audit_seq": result["audit_seq"],
    "sha256": hashlib.sha256(raw.encode()).hexdigest(), "credential_matches": 0}))
