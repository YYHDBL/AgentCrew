"""使用真实HTTP证据验证全部M2操作及响应JSON Schema。"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from starlette.routing import compile_path


def check(root, output):
    document = yaml.safe_load((Path(__file__).resolve().parents[2] / "docs/contracts/openapi.yaml").read_text())
    observed = []
    for path in sorted(root.rglob("http-evidence.json")):
        observed.extend({**record, "evidence_source": str(path.relative_to(root))} for record in json.loads(path.read_text()))
    checks, coverage = [], []
    for path, item in document["paths"].items():
        route = compile_path(path)[0]
        for method, operation in item.items():
            if not isinstance(operation, dict) or not operation.get("x-domain-card", "").startswith("M2-"):
                continue
            actual = [row for row in observed if row["method"].lower() == method and route.fullmatch(urlsplit(row["path"]).path)]
            success = [row for row in actual if 200 <= row["status"] < 300]
            if not success:
                coverage.append({"operation_id": operation["operationId"], "method": method, "path": path, "passed": False})
                continue
            for row in actual:
                response = operation["responses"].get(str(row["status"]))
                if response is None:
                    raise AssertionError(f'实际状态未声明：{operation["operationId"]} {row["status"]}')
                if "$ref" in response:
                    response = document["components"]["responses"][response["$ref"].rsplit("/", 1)[1]]
                schema = response.get("content", {}).get("application/json", {}).get("schema")
                if schema is not None:
                    Draft202012Validator({**schema, "components": document["components"]}, format_checker=FormatChecker()).validate(row["response"])
                checks.append({"operation_id": operation["operationId"], "method": method, "actual_path": row["path"], "status": row["status"],
                    "evidence_source": row["evidence_source"], "schema_checked": schema is not None})
            coverage.append({"operation_id": operation["operationId"], "method": method, "path": path, "passed": True,
                "statuses": sorted({row["status"] for row in actual}), "actual_requests": len(actual)})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"coverage": coverage, "schema_checks": checks}, ensure_ascii=False, indent=2) + "\n")
    missing = [row["operation_id"] for row in coverage if not row["passed"]]
    print(json.dumps({"operations": len(coverage), "passed": len(coverage) - len(missing), "missing": missing, "actual_requests": len(checks)}))
    assert not missing, f"真实HTTP成功证据缺少操作：{missing}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    check(args.root.resolve(), args.output.resolve())
