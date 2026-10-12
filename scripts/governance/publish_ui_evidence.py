"""发布真实Electron、HAR、SQL和逐张检查后的治理界面证据。"""

import argparse
import base64
import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.config import load_config
from agentcrew_server.db.audit import verify_levels
from agentcrew_server.secrets import register_secret, redact, known_secrets


def publish(source, destination):
    root = source / "data"
    for secret in load_config(root).api_keys():
        register_secret(secret)
    har = json.loads((source / "network.har").read_text())
    traffic = []
    for item in har["log"]["entries"]:
        request, response = item["request"], item["response"]
        for header in request["headers"]:
            if header["name"].lower() in {"authorization", "x-agentcrew-identity", "cookie"}:
                register_secret(header["value"])
                if header["value"].startswith("Bearer "):
                    register_secret(header["value"][7:])
        content = response["content"]
        body = content.get("text")
        if body is not None and content.get("encoding") == "base64":
            body = base64.b64decode(body).decode()
        value = json.loads(body) if body and "application/json" in content.get("mimeType", "") else body
        data = value.get("data") if isinstance(value, dict) else None
        if isinstance(data, dict) and data.get("identity_token"):
            register_secret(data["identity_token"])
        post = request.get("postData")
        submitted = json.loads(post["text"]) if post and post.get("text") and "application/json" in post["mimeType"] else post
        if isinstance(submitted, dict) and submitted.get("credential"):
            register_secret(submitted["credential"])
        traffic.append({"started_at": item["startedDateTime"], "method": request["method"], "url": request["url"],
            "request": submitted, "status": response["status"], "response": value, "response_mime_type": content.get("mimeType"),
            "response_body_recorded": body is not None})
    connection = sqlite3.connect(f"file:{root / 'agentcrew.db'}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    for row in connection.execute("SELECT result FROM governance_changes"):
        value = json.loads(row[0])
        if value.get("identity_token"):
            register_secret(value["identity_token"])
    queries = ["SELECT id,user_id,role,status,revision FROM memberships ORDER BY id",
        "SELECT workspace_id,user_id,enabled,revision FROM workspace_members ORDER BY workspace_id,user_id",
        "SELECT id,workspace_id,name,spec,status,revision FROM agents ORDER BY created_at,id",
        "SELECT id,workspace_id,name,status,revision,current_version_id FROM skills ORDER BY created_at,id",
        "SELECT id,skill_id,version_no,change_id,ledger_id,sha256,created_by FROM skill_versions ORDER BY skill_id,version_no",
        "SELECT id,resource_type,resource_id,grantee_type,grantee_id,revision,revoked_at FROM grants ORDER BY created_at,id",
        "SELECT id,conversation_id,status,current_attempt_no FROM task_runs ORDER BY created_at,id",
        "SELECT id,task_run_id,attempt_no,status,context_fingerprint FROM run_attempts ORDER BY started_at,id",
        "SELECT call_id,task_run_id,tool_name,status,input_hash,dispatched_at,completed_at FROM tool_calls ORDER BY prepared_at,call_id",
        "SELECT global_seq,type,task_run_id,attempt_no,payload FROM run_events ORDER BY global_seq",
        "SELECT seq,actor_type,actor_id,action,resource_type,resource_id,hash FROM audit_log ORDER BY seq"]
    actual = {"queries": [{"sql": query, "rows": [dict(row) for row in connection.execute(query)]} for query in queries],
        "verification": verify_levels(connection, root / "chain-head.txt")}
    connection.close()
    destination.mkdir(parents=True, exist_ok=True)
    result = json.loads((source / "result.json").read_text())
    result["screenshots"] = [Path(path).name for path in result["screenshots"]]
    models = load_config(root).values["models"]
    result["models"] = {name: {"provider": slot["provider"], "model": slot["model"]} for name, slot in models.items()}
    for name, value in (("electron.json", result), ("http.json", traffic), ("sql-audit.json", actual)):
        (destination / name).write_text(redact(json.dumps(value, ensure_ascii=False, indent=2)) + "\n")
    for path in result["screenshots"]:
        shutil.copy2(source / path, destination / path)
    if (source / "audit-export.json").exists():
        (destination / "audit-export.json").write_text(redact((source / "audit-export.json").read_text()))
    for path in destination.glob("*.json"):
        assert not any(secret in path.read_text() for secret in known_secrets()), path.name
    print(json.dumps({"cases": len(result["cases"]), "http_requests": len(traffic), "event_watermark": result["event_watermark"],
        "audit_sequence": result["audit_sequence"], "credential_matches": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    publish(args.source.resolve(), args.destination.resolve())
