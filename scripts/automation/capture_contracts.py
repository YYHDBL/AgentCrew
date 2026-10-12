"""保存实际契约水位、模型配置、审计校验与生成文件散列。"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from agentcrew_server.config import load_config
from agentcrew_server.db.audit import verify_levels
from agentcrew_server.secrets import register_secret, redact


root, output = map(Path, sys.argv[1:3])
repository = Path(__file__).resolve().parents[2]
config = load_config(root)
for key in config.api_keys():
    register_secret(key)
with sqlite3.connect(root / "agentcrew.db") as connection:
    result = {"event_watermark": connection.execute("SELECT max(global_seq) FROM run_events").fetchone()[0],
        "audit_seq": connection.execute("SELECT max(seq) FROM audit_log").fetchone()[0],
        "event_count": connection.execute("SELECT count(*) FROM run_events").fetchone()[0],
        "audit_verification": verify_levels(connection, root / "chain-head.txt"),
        "models": {slot: {field: config.values["models"][slot][field] for field in ("provider", "model", "base_url")}
            for slot in ("main", "aux")}, "files": []}
assert result["audit_verification"]["ok"]
for name in ("docs/contracts/openapi.yaml", "docs/contracts/events.schema.json", "desktop/src/renderer/src/api/generated/http.ts",
             "desktop/src/renderer/src/api/generated/events.ts", "desktop/src/renderer/src/api/generated/events.schema.json"):
    path = repository / name
    result["files"].append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mtime_ns": path.stat().st_mtime_ns})
raw = redact(json.dumps(result, ensure_ascii=False, indent=2)) + "\n"
assert all(not key or key not in raw for key in config.api_keys())
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(raw)
print(json.dumps({"event_count": result["event_count"], "event_watermark": result["event_watermark"], "audit_seq": result["audit_seq"], "credential_matches": 0}))
