"""公开真实验收 JSON 前去除认证与隐藏字段，并扫描当前实际凭据。"""

import hashlib
import json
import sys
from pathlib import Path

from agentcrew_server.config import load_config
from agentcrew_server.runs import display_value
from agentcrew_server.secrets import register_secret


source, destination, config_directory = map(Path, sys.argv[1:4])
configuration = load_config(config_directory)
for key in configuration.api_keys():
    register_secret(key)


def public(value):
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items()
                if key not in {"api_key_hint", "token", "identity_token"}}
    if isinstance(value, list):
        return [public(item) for item in value]
    return value


raw = json.dumps(public(display_value(json.loads(source.read_text()))), ensure_ascii=False, indent=2) + "\n"
assert all(not key or key not in raw for key in configuration.api_keys())
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(raw)
print(json.dumps({"file": str(destination), "sha256": hashlib.sha256(raw.encode()).hexdigest(),
                  "credential_matches": 0}, ensure_ascii=False))
