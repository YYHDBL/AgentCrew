"""发布真实审计诊断与受控恢复的脱敏SQL、文件及请求证据。"""

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.config import load_config
from agentcrew_server.db.audit import verify_levels
from agentcrew_server.secrets import redact, register_secret
from publish_identity_evidence import publish as publish_requests


def publish(source, destination):
    publish_requests(source, destination)
    secrets = load_config(source).api_keys()
    for secret in secrets:
        register_secret(secret)
    connection = sqlite3.connect(f"file:{source / 'agentcrew.db'}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    queries = [
        "SELECT seq,actor_type,actor_id,action,resource_type,resource_id,detail,prev_hash,hash FROM audit_log ORDER BY seq",
        "SELECT global_seq,type,task_run_id,attempt_no,payload FROM run_events ORDER BY global_seq",
        "SELECT id,conversation_id,status,current_attempt_no,finished_at FROM task_runs ORDER BY created_at",
        "SELECT id,task_run_id,attempt_no,status,context_fingerprint FROM run_attempts ORDER BY started_at",
        "SELECT call_id,task_run_id,tool_name,status,input_hash,dispatched_at,completed_at FROM tool_calls ORDER BY prepared_at",
        "SELECT * FROM audit_anchor_state", "SELECT * FROM audit_anchor_intents",
    ]
    models = load_config(source).values["models"]
    record = {"models": {name: {"provider": slot["provider"], "model": slot["model"]} for name, slot in models.items()},
        "queries": [{"sql": query, "rows": [dict(row) for row in connection.execute(query)]} for query in queries],
        "verification": verify_levels(connection, source / "chain-head.txt"), "preserved": []}
    for result_path in sorted((source / "backups" / "governance").glob("preserved-*/restore-result.json")):
        result = json.loads(result_path.read_text())
        original = sqlite3.connect(f"file:{result_path.parent / 'agentcrew.db'}?mode=ro", uri=True)
        original.row_factory = sqlite3.Row
        rows = [dict(row) for row in original.execute("SELECT seq,action,detail,hash FROM audit_log ORDER BY seq")]
        original.close()
        record["preserved"].append({"result": result, "original_database_sha256": hashlib.sha256((result_path.parent / "agentcrew.db").read_bytes()).hexdigest(), "original_audit_rows": rows})
    connection.close()
    (destination / "audit-sql.json").write_text(redact(json.dumps(record, ensure_ascii=False, indent=2)) + "\n")
    for filename in ("audit-recovery.json", "diagnostic-active-cancel.json"):
        if (source / filename).exists():
            (destination / filename).write_text(redact((source / filename).read_text()))
    for path in destination.glob("*.json"):
        assert not any(secret and secret in path.read_text() for secret in secrets), path.name
    print(json.dumps({"queries": len(queries), "preserved_databases": len(record["preserved"]), "credential_matches": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    publish(args.source.resolve(), args.destination.resolve())
