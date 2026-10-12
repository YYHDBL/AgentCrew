"""发布真实HTTP身份验收的脱敏请求与身份、事件、审计SQL证据。"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.config import load_config
from agentcrew_server.secrets import redact, register_secret


def publish(source, destination):
    requests = json.loads((source / "http-evidence.json").read_text())
    secrets = load_config(source).api_keys()
    connection = sqlite3.connect(f"file:{source / 'agentcrew.db'}?mode=ro", uri=True)
    for row in connection.execute("SELECT result FROM governance_changes"):
        value = json.loads(row[0])
        if "identity_token" in value:
            secrets.append(value["identity_token"])
    secrets.extend(row[0] for row in connection.execute("SELECT credential FROM connectors WHERE credential IS NOT NULL"))
    connection.close()
    for item in requests:
        request = item.get("request")
        if isinstance(request, dict) and request.get("credential"):
            secrets.append(request["credential"])
    for secret in secrets:
        register_secret(secret)
    destination.mkdir(parents=True, exist_ok=True)
    public_requests = destination / "http-identity.json"
    public_requests.write_text(redact(json.dumps(requests, ensure_ascii=False, indent=2)) + "\n")
    connection = sqlite3.connect(f"file:{source / 'agentcrew.db'}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    queries = ["SELECT id,user_id,role,status,revision FROM memberships ORDER BY id",
        "SELECT conversation_id,workspace_id,agent_id,credential_owner_id,effective_user_id FROM governance_conversations ORDER BY conversation_id",
        "SELECT task_run_id,effective_user_id,credential_owner_id,agent_revision FROM task_governance ORDER BY task_run_id",
        "SELECT job_id,effective_user_id,credential_owner_id FROM job_governance ORDER BY job_id",
        "SELECT id,resource_type,resource_id,grantee_type,grantee_id,revision,revoked_at FROM grants ORDER BY created_at,id",
        "SELECT call_id,task_run_id,tool_name,status,input_hash,dispatched_at,completed_at FROM tool_calls ORDER BY prepared_at,call_id",
        "SELECT id,task_run_id,attempt_no,status FROM run_attempts ORDER BY started_at,id",
        "SELECT global_seq,type,task_run_id FROM run_events ORDER BY global_seq",
        "SELECT seq,actor_type,actor_id,action,resource_type,resource_id,prev_hash,hash FROM audit_log ORDER BY seq"]
    record = {"queries": [{"sql": query, "rows": [dict(row) for row in connection.execute(query)]} for query in queries],
        "event_watermark": connection.execute("SELECT coalesce(max(global_seq),0) FROM run_events").fetchone()[0],
        "audit_sequence": connection.execute("SELECT coalesce(max(seq),0) FROM audit_log").fetchone()[0]}
    connection.close()
    (destination / "identity-sql-audit.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    for path in destination.iterdir():
        if path.is_file():
            text = path.read_text()
            assert not any(secret and secret in text for secret in secrets), f"公开文件含凭据：{path.name}"
    print(json.dumps({"requests": len(requests), "event_watermark": record["event_watermark"], "audit_sequence": record["audit_sequence"], "credential_matches": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    publish(args.source.resolve(), args.destination.resolve())
