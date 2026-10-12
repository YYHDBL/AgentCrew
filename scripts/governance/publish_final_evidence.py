"""发布经过核查的M2贯穿回归、实际SQL、文件校验与脱敏材料。"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.config import load_config
from agentcrew_server.db.audit import verify_levels
from agentcrew_server.secrets import known_secrets, redact, register_secret


def register(root):
    for secret in load_config(root).api_keys():
        register_secret(secret)
    path = root / "agentcrew.db"
    if path.exists():
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "connectors" in tables:
            for row in connection.execute("SELECT credential FROM connectors WHERE credential IS NOT NULL"):
                register_secret(row[0])
        if "governance_changes" in tables:
            for row in connection.execute("SELECT result FROM governance_changes"):
                result = json.loads(row[0])
                if result.get("identity_token"):
                    register_secret(result["identity_token"])
        connection.close()


def sanitize(value):
    if isinstance(value, dict):
        if isinstance(value.get("name"), str) and value["name"].lower() in {"authorization", "x-agentcrew-identity", "cookie", "set-cookie"}:
            register_secret(value["value"])
            return {"name": value["name"], "value": "***"}
        return {key: sanitize(item) for key, item in value.items()
                if key.lower() not in {"api_key", "api_key_hint", "authorization", "token", "identity_token", "cookie", "x-agentcrew-identity"}}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def publish(args):
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    snapshots = [item.split("=", 1) for item in args.snapshot]
    for _, root in snapshots:
        register(Path(root))
    register(Path(__file__).resolve().parents[2] / "data/m1-final-memory-03")
    fixtures = sorted(args.fixture_root.glob("*/*.json"))
    fixtures = [path for path in fixtures if not path.parent.is_symlink() and path.name not in {"config.json", "USER.meta.json"} and not path.name.endswith(".meta.json")]
    for root in {path.parent for path in fixtures if (path.parent / "config.json").exists()}:
        register(root)

    def save(name, value):
        (destination / name).write_text(redact(json.dumps(sanitize(value), ensure_ascii=False, indent=2)) + "\n")

    for item in args.artifact:
        name, source = item.split("=", 1)
        source = Path(source)
        if source.suffix not in {".json", ".txt"} or Path(name).suffix not in {".json", ".txt"}:
            raise ValueError("公开材料只接受脱敏JSON或测试文本输出；HAR必须经过专用发布服务处理")
        if source.suffix == ".json":
            value = json.loads(source.read_text())
            if "all_checks_passed" in value:
                assert value["all_checks_passed"], source
            save(name, value)
        else:
            (destination / name).write_text(redact(source.read_text()).rstrip() + "\n")
    save("backend-boundaries.json", [{"source": str(path.relative_to(args.fixture_root)), "evidence": json.loads(path.read_text())} for path in fixtures])
    queries = [
        "SELECT id,conversation_id,status,current_attempt_no FROM task_runs ORDER BY created_at,id",
        "SELECT id,task_run_id,attempt_no,status,context_fingerprint FROM run_attempts ORDER BY started_at,id",
        "SELECT task_run_id,agent_spec,skill_versions FROM task_governance ORDER BY task_run_id",
        "SELECT call_id,task_run_id,tool_name,status,input_hash,dispatched_at,completed_at FROM tool_calls ORDER BY prepared_at,call_id",
        "SELECT global_seq,task_run_id,attempt_no,type,json_extract(payload,'$.call_id') AS call_id,json_extract(payload,'$.job_id') AS job_id,json_extract(payload,'$.error') AS error FROM run_events ORDER BY global_seq",
        "SELECT id,kind,task_run_id,status,trigger_global_seq FROM memory_jobs ORDER BY created_at,id",
        "SELECT id,change_id,action,store_type,store_id,source FROM memory_ledger ORDER BY id",
        "SELECT id,task_run_id,snapshot_id,sha256,event_global_seq FROM context_checkpoints ORDER BY event_global_seq",
        "SELECT seq,actor_type,actor_id,action,resource_type,resource_id,hash FROM audit_log ORDER BY seq",
        "SELECT type,count(*) AS count FROM run_events GROUP BY type ORDER BY type",
        "PRAGMA foreign_key_check",
    ]
    for name, location in snapshots:
        root = Path(location)
        connection = sqlite3.connect(f"file:{root / 'agentcrew.db'}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        verification = verify_levels(connection, root / "chain-head.txt")
        assert verification["ok"], name
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == [], name
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok", name
        files = [{"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mtime_ns": path.stat().st_mtime_ns}
            for path in sorted(set(root.rglob("*.md")) | set(root.rglob("*.meta.json")) | set(root.rglob("memory-snapshot.json")) | set(root.rglob("*.txt")))
            if not any(part in {"backups", "diagnostic-materials"} for part in path.relative_to(root).parts)]
        save(f"{name}-sql-audit.json", {"source": str(root), "verification": verification, "files": files,
            "queries": [{"sql": sql, "rows": [dict(row) for row in connection.execute(sql)]} for sql in queries]})
        connection.close()
    for item in args.image:
        name, source = item.split("=", 1)
        shutil.copy2(source, destination / name)
    checks = []
    for path in sorted(destination.rglob("*")):
        if not path.is_file() or path.name == "publication-check.json":
            continue
        content = path.read_bytes()
        matches = sum(secret.encode() in content for secret in known_secrets())
        assert matches == 0, path
        checks.append({"path": str(path.relative_to(destination)), "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "credential_matches": matches})
    save("publication-check.json", {"credential_matches": 0, "files": checks})
    print(json.dumps({"files": len(checks), "backend_evidence": len(fixtures), "credential_matches": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--snapshot", action="append", default=[])
    parser.add_argument("--image", action="append", default=[])
    publish(parser.parse_args())
