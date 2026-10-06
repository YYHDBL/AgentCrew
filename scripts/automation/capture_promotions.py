"""保存实际真人确认、模型正文、版本、账本、Grant及文件核验证据。"""

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
    if item.get("path", "").endswith("/promote-skill") and item.get("status") == 202})
assert job_ids
connection = sqlite3.connect(root / "agentcrew.db")
connection.row_factory = sqlite3.Row
result = {"job_ids": job_ids, "queries": [], "files": []}
for job_id in job_ids:
    for sql in (
        "SELECT * FROM skill_promotions WHERE job_id=?",
        "SELECT * FROM promotion_requests WHERE job_id=? ORDER BY effective_user_id,client_request_id",
        "SELECT * FROM memory_jobs WHERE id=?",
        "SELECT * FROM job_governance WHERE job_id=?",
        "SELECT r.* FROM trace_reports r JOIN skill_promotions p ON p.report_id=r.id WHERE p.job_id=?",
        "SELECT q.* FROM trace_review_requests q JOIN trace_reports r ON r.job_id=q.job_id JOIN skill_promotions p ON p.report_id=r.id WHERE p.job_id=?",
        "SELECT * FROM memory_job_calls WHERE job_id=? ORDER BY ordinal",
        "SELECT * FROM skill_reads WHERE execution_id=? ORDER BY created_at",
        "SELECT * FROM memory_changes WHERE change_id=?||'-publish'",
        "SELECT * FROM memory_ledger WHERE change_id=?||'-publish'",
        "SELECT v.* FROM skill_versions v JOIN skill_promotions p ON p.skill_id=v.skill_id WHERE p.job_id=? ORDER BY v.version_no",
        "SELECT s.* FROM skills s JOIN skill_promotions p ON p.skill_id=s.id WHERE p.job_id=?",
        "SELECT g.* FROM grants g JOIN skill_promotions p ON p.skill_id=g.resource_id WHERE p.job_id=? AND g.resource_type='skill' ORDER BY g.created_at,g.id",
        "SELECT * FROM run_events WHERE json_extract(payload,'$.job_id')=? OR json_extract(payload,'$.change_id')=?||'-publish' ORDER BY global_seq",
        "SELECT * FROM audit_log WHERE resource_type='memory_job' AND resource_id=? OR json_extract(detail,'$.change_id')=?||'-publish' ORDER BY seq",
    ):
        parameters = [job_id] * sql.count("?")
        rows = [dict(row) for row in connection.execute(sql, parameters)]
        for row in rows:
            for key in ("proposal", "result", "payload", "detail", "config_snapshot", "usage", "report", "plan", "source", "metadata", "files", "before_metadata", "after_metadata", "before_files", "after_files"):
                if key == "source" and "current_version_id" in row:
                    continue
                if key in row and row[key] is not None:
                    row[key] = json.loads(row[key])
        result["queries"].append({"sql": sql, "parameters": parameters, "rows": rows})
    skill_id = connection.execute("SELECT skill_id FROM skill_promotions WHERE job_id=?", (job_id,)).fetchone()[0]
    directory = root / "skills" / skill_id
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not any(item["path"] == str(path) for item in result["files"]):
            result["files"].append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mtime_ns": path.stat().st_mtime_ns, "size_bytes": path.stat().st_size})
result["event_watermark"] = connection.execute("SELECT max(global_seq) FROM run_events").fetchone()[0]
result["audit_seq"] = connection.execute("SELECT max(seq) FROM audit_log").fetchone()[0]
result["audit_verification"] = verify_levels(connection, root / "chain-head.txt")
assert result["audit_verification"]["ok"]
connection.close()
raw = redact(json.dumps(display_value(result), ensure_ascii=False, indent=2)) + "\n"
assert all(not key or key not in raw for key in configuration.api_keys())
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(raw)
print(json.dumps({"jobs": len(job_ids), "files": len(result["files"]), "event_watermark": result["event_watermark"], "audit_seq": result["audit_seq"],
    "audit_ok": result["audit_verification"]["ok"], "sha256": hashlib.sha256(raw.encode()).hexdigest(), "credential_matches": 0}))
