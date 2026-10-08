"""保存真实 Electron 运行中心的审查、计划、审计与文件来源证明。"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from agentcrew_server.config import load_config
from agentcrew_server.db.audit import verify_levels
from agentcrew_server.runs import display_value
from agentcrew_server.secrets import redact, register_secret


source, output = map(Path, sys.argv[1:3])
root = source / "data"
config = load_config(root)
for secret in config.api_keys():
    register_secret(secret)
connection = sqlite3.connect(root / "agentcrew.db")
connection.row_factory = sqlite3.Row
queries = ["SELECT * FROM cron_jobs ORDER BY created_at,id",
    "SELECT * FROM cron_job_runs ORDER BY triggered_at,id",
    "SELECT * FROM cron_run_attempts ORDER BY occurrence_id,attempt_no",
    "SELECT * FROM memory_jobs ORDER BY created_at,id",
    "SELECT * FROM trace_reports ORDER BY created_at,id",
    "SELECT * FROM trace_targets ORDER BY job_id,task_run_id",
    "SELECT * FROM memory_job_calls ORDER BY job_id,ordinal",
    "SELECT * FROM memory_ledger ORDER BY id",
    "SELECT * FROM task_governance ORDER BY task_run_id"]
result = {"queries": [{"sql": query, "rows": [dict(row) for row in connection.execute(query)]} for query in queries],
    "event_watermark": connection.execute("SELECT max(global_seq) FROM run_events").fetchone()[0],
    "audit_seq": connection.execute("SELECT max(seq) FROM audit_log").fetchone()[0],
    "verification": verify_levels(connection, root / "chain-head.txt"),
    "models": {slot: {field: config.values["models"][slot][field] for field in ("provider", "model", "base_url")} for slot in ("main", "aux")},
    "files": []}
assert result["verification"]["ok"]
evidence = json.loads((source / "result.json").read_text())
file = Path(evidence["file_replay"]["path"])
result["files"].append({"path": str(file), "sha256": hashlib.sha256(file.read_bytes()).hexdigest(), "mtime_ns": file.stat().st_mtime_ns})
for name in ("desktop/src/renderer/src/run-center/RunCenter.tsx", "desktop/src/renderer/src/run-center/Replay.tsx", "desktop/src/renderer/src/api/generated/event-validator.js"):
    file = Path(__file__).resolve().parents[2] / name
    result["files"].append({"path": name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest(), "mtime_ns": file.stat().st_mtime_ns})
connection.close()
raw = redact(json.dumps(display_value(result), ensure_ascii=False, indent=2)) + "\n"
credential_matches = sum(secret in raw for secret in config.api_keys() if secret)
assert credential_matches == 0
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(raw)
print(json.dumps({"event_watermark": result["event_watermark"], "audit_seq": result["audit_seq"], "audit_ok": result["verification"]["ok"], "credential_matches": credential_matches}))
