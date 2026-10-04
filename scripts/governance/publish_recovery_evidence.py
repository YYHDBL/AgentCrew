"""发布真实 Electron 恢复验收的脱敏请求、SQL 和文件校验记录。"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.config import load_config
from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.secrets import redact, register_secret


def publish(source, destination):
    root = source / "data"
    secrets = load_config(root).api_keys()
    connection = sqlite3.connect(f"file:{root / 'agentcrew.db'}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    secrets.extend(row[0] for row in connection.execute("SELECT credential FROM connectors WHERE credential IS NOT NULL"))
    for row in connection.execute("SELECT result FROM governance_changes"):
        result = json.loads(row[0])
        if result.get("identity_token"):
            secrets.append(result["identity_token"])
    for secret in secrets:
        register_secret(secret)
    destination.mkdir(parents=True, exist_ok=True)
    result = json.loads((source / "result.json").read_text())
    result["screenshots"] = ["recovered.png"]
    (destination / "electron-recovery.json").write_text(redact(json.dumps(result, ensure_ascii=False, indent=2)) + "\n")
    queries = [
        "SELECT id,conversation_id,status,current_attempt_no,finished_at FROM task_runs ORDER BY created_at",
        "SELECT id,task_run_id,attempt_no,status,context_fingerprint FROM run_attempts ORDER BY started_at",
        "SELECT task_run_id,agent_spec,skill_versions FROM task_governance ORDER BY task_run_id",
        "SELECT call_id,task_run_id,tool_name,status,side_effect_class,input_hash,dispatched_at,completed_at FROM tool_calls ORDER BY prepared_at",
        "SELECT global_seq,task_run_id,attempt_no,type,payload FROM run_events ORDER BY global_seq",
        "SELECT id,kind,task_run_id,status,trigger_global_seq FROM memory_jobs ORDER BY created_at",
        "SELECT id,task_run_id,snapshot_id,sha256,event_global_seq FROM context_checkpoints ORDER BY event_global_seq",
        "SELECT seq,actor_type,actor_id,action,resource_type,resource_id,prev_hash,hash FROM audit_log ORDER BY seq",
    ]
    audit = verify_with_anchor(connection, root / "chain-head.txt")
    assert audit.ok
    files = []
    for path in sorted((root / "workspaces" / "office" / "files" / "m2-09").glob("*.txt")):
        files.append({"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mtime_ns": path.stat().st_mtime_ns})
    evidence = {"queries": [{"sql": query, "rows": [dict(row) for row in connection.execute(query)]} for query in queries],
        "files": files, "audit": vars(audit)}
    (destination / "sql-audit.json").write_text(redact(json.dumps(evidence, ensure_ascii=False, indent=2)) + "\n")
    connection.close()
    for path in destination.glob("*.json"):
        assert not any(secret and secret in path.read_text() for secret in secrets)
    shutil.copy2(source / "recovered.png", destination / "recovered.png")
    print(json.dumps({"requests": len(result["requests"]), "task_run_id": result["task_run_id"],
        "actual_operations": len(result["upstream"]["operations"]), "credential_matches": 0, "audit_checked": audit.checked_count}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    publish(args.source.resolve(), args.destination.resolve())
