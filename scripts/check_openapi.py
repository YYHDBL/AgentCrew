#!/usr/bin/env python3
"""契约守卫（本地/CI 均可跑）：contracts/openapi.yaml 必须是合法 YAML，且响应信封与 backend-service §4 一致。"""
import sys
from pathlib import Path

import yaml
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "contracts" / "openapi.yaml"

spec = yaml.safe_load(DOC.read_text())
validate(spec)
paths = spec.get("paths", {})
assert paths, "paths 为空"
operation_ids = set()
m2_operations = 0

errors = spec["components"]["schemas"]["Error"]
assert "error" in errors["properties"], "Error schema 必须是 {error:{code,message,detail}} 包裹形态（backend-service §4）"

# 信封描述抽查：所有 2xx 描述不得再出现裸 'data 含' 之外的旧信封残留
for path, ops in paths.items():
    for method, op in ops.items():
        if not isinstance(op, dict):
            continue
        if method in ("get", "post", "put", "patch", "delete") and op.get("operationId"):
            assert op["operationId"] not in operation_ids, f"重复操作标识：{op['operationId']}"
            operation_ids.add(op["operationId"])
        if method in ("get", "post", "put", "patch", "delete") and op.get("x-domain-card", "").startswith("M2-"):
            m2_operations += 1
            assert op.get("x-implementation-card") in {"M2-03", "M2-04", "M2-05", "M2-06", "M2-07", "M2-10", "M2-11"}, f"{path} {method} 缺少 HTTP 实施归属"
            assert op.get("x-domain-card") in {f"M2-{number:02}" for number in range(2, 11)}, f"{path} {method} 缺少领域实施归属"
            assert op.get("operationId"), f"{path} {method} 缺少操作标识"
            for status in ("401", "403", "404", "409", "422", "503"):
                assert status in op["responses"], f"{path} {method} 缺少{status}错误契约"
            if method in ("put", "patch", "delete"):
                assert op.get("requestBody", {}).get("required"), f"{path} {method} 缺少修订及幂等请求体"
        if path.startswith("/api/memory/") and method in ("get", "post", "patch", "delete"):
            assert op.get("x-implementation-card") == "M1-11", f"{path} {method} 缺少 HTTP 实施归属"
            assert op.get("x-domain-card") in {f"M1-{number:02}" for number in range(2, 11)}, f"{path} {method} 缺少领域实施归属"
            assert op.get("operationId"), f"{path} {method} 缺少操作标识"
        for code, resp in op.get("responses", {}).items():
            desc = str(resp.get("description", ""))
            assert "success" not in desc.lower() or "data" in desc.lower(), f"{path} {code} 信封描述可疑"

assert m2_operations == 44, f"M2 操作数量不完整：{m2_operations}"
print(f"openapi OK: {len(paths)} paths, {m2_operations} M2 operations, envelope consistent")
sys.exit(0)
