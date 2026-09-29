#!/usr/bin/env python3
"""CI 守卫：contracts/openapi.yaml 必须是合法 YAML，且响应信封与 backend-service §4 一致。"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "contracts" / "openapi.yaml"

spec = yaml.safe_load(DOC.read_text())
paths = spec.get("paths", {})
assert paths, "paths 为空"

errors = spec["components"]["schemas"]["Error"]
assert "error" in errors["properties"], "Error schema 必须是 {error:{code,message,detail}} 包裹形态（backend-service §4）"

# 信封描述抽查：所有 2xx 描述不得再出现裸 'data 含' 之外的旧信封残留
for path, ops in paths.items():
    for method, op in ops.items():
        if not isinstance(op, dict):
            continue
        for code, resp in op.get("responses", {}).items():
            desc = str(resp.get("description", ""))
            assert "success" not in desc.lower() or "data" in desc.lower(), f"{path} {code} 信封描述可疑"

print(f"openapi OK: {len(paths)} paths, envelope consistent")
sys.exit(0)
