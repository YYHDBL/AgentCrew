"""发布管理API的实际资源、版本、岗位配置和模型槽证据。"""

import argparse
import json
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.config import load_config
from agentcrew_server.db.audit import verify_levels
from agentcrew_server.secrets import register_secret, redact, known_secrets
from publish_identity_evidence import publish


def export(source, destination):
    publish(source, destination)
    for secret in load_config(source).api_keys():
        register_secret(secret)
    connection = sqlite3.connect(f"file:{source / 'agentcrew.db'}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    queries = ["SELECT id,org_id,name,status,revision,data_dir FROM workspaces ORDER BY created_at,id",
        "SELECT id,workspace_id,name,spec,status,revision,created_by_user_id FROM agents ORDER BY created_at,id",
        "SELECT id,workspace_id,name,description,status,revision,current_version_id FROM skills ORDER BY created_at,id",
        "SELECT id,skill_id,version_no,change_id,ledger_id,sha256,created_by,created_at FROM skill_versions ORDER BY skill_id,version_no",
        "SELECT task_run_id,agent_revision,agent_spec,skill_versions,effective_user_id,credential_owner_id FROM task_governance ORDER BY task_run_id",
        "SELECT id,task_run_id,attempt_no,status,context_fingerprint FROM run_attempts ORDER BY started_at,id",
        "SELECT global_seq,task_run_id,attempt_no,type,payload FROM run_events WHERE type LIKE 'governance.%' OR type='llm.request_started' OR type='run.completed' OR type='run.failed' ORDER BY global_seq",
        "SELECT seq,actor_type,actor_id,action,resource_type,resource_id,detail,hash FROM audit_log ORDER BY seq"]
    models = load_config(source).values["models"]
    evidence = {"models": {name: {"provider": slot["provider"], "model": slot["model"]} for name, slot in models.items()},
        "queries": [{"sql": query, "rows": [dict(row) for row in connection.execute(query)]} for query in queries],
        "verification": verify_levels(connection, source / "chain-head.txt")}
    connection.close()
    (destination / "resource-sql.json").write_text(redact(json.dumps(evidence, ensure_ascii=False, indent=2)) + "\n")
    for filename in ("schema-checks.json", "authentication-matrix.json", "skill-disable-execution.json"):
        if (source / filename).exists():
            (destination / filename).write_text(redact((source / filename).read_text()))
    for path in destination.glob("*.json"):
        assert not any(secret in path.read_text() for secret in known_secrets()), path.name
    print(json.dumps({"resource_queries": len(queries), "credential_matches": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    export(args.source.resolve(), args.destination.resolve())
