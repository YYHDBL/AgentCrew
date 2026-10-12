"""M1-11：完整 CLI、实际 HTTP、JSON Schema、事件交接和脱敏证据。"""

import argparse
import asyncio
import json
import shutil
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from httpx_sse import aconnect_sse
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from starlette.routing import compile_path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.db.database import Database
from verify_review_controls import files
from verify_summaries import query, start, until

CONTRACT = yaml.safe_load((Path(__file__).resolve().parents[2] / "docs/contracts/openapi.yaml").read_text())
RESOLVER = Registry().with_resource("urn:agentcrew:openapi", Resource.from_contents(CONTRACT,
    default_specification=DRAFT202012)).resolver("urn:agentcrew:openapi")


def validation(method, path, response):
    candidates = [(template, item) for template, item in CONTRACT["paths"].items()
        if method.lower() in item and compile_path(template)[0].fullmatch(path)]
    _template, item = sorted(candidates, key=lambda pair: pair[0].count("{"))[0]
    operation = item[method.lower()]
    definition = operation["responses"][str(response.status_code)]
    if "$ref" in definition:
        definition = RESOLVER.lookup(definition["$ref"]).contents
    Draft202012Validator({"components": CONTRACT["components"],
        **definition["content"]["application/json"]["schema"]}).validate(response.json())
    return operation.get("operationId", f"{method} {path}")


async def verify(seed, root, output):
    shutil.copytree(seed, root, ignore=shutil.ignore_patterns("requests.jsonl", "streams.jsonl", "main-streams.jsonl", "service.log", "instance.lock"))
    scope = {"workspace_id": "default", "agent_id": "skill-validator"}
    token = uuid.uuid4().hex
    process, log, port = await start(root, token)
    records, error, sse = [], None, None
    before_calls = query(root, "SELECT COUNT(*) AS count FROM llm_calls")[0]["count"]
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30,
                headers={"Authorization": f"Bearer {token}"}) as client:
            async def request(method, path, body=None, *, params=None, expected=200, headers=None):
                response = await client.request(method, path, params=scope if params is None else params, json=body, headers=headers)
                assert response.status_code == expected, (method, path, response.status_code, response.text[:600])
                operation = validation(method, path, response)
                result = response.json()
                if path == "/api/settings" and "data" in result:
                    view = result["data"].get("settings", result["data"])
                    for slot in view["models"].values():
                        slot.pop("api_key_hint", None)
                records.append({"method": method, "path": path, "params": scope if params is None else params,
                    "request": body, "status": response.status_code, "response": result, "operation_id": operation,
                    "schema_validated": True, "observed_at": datetime.now(timezone.utc).isoformat()})
                return result.get("data", result)
            def body(revision, text=None, **extra):
                return {"change_id": uuid.uuid4().hex, "expected_revision": revision, "basis": "所有者真实 HTTP 验收操作",
                    **({"text": text} if text is not None else {}), **extra}
            await request("GET", "/api/settings", params={})
            await request("PATCH", "/api/settings", {"memory": {"write_approval": False}}, params={})
            path = "/api/memory/stores/user/owner"
            initial = await request("GET", path)
            await request("GET", path, headers={"Authorization": "Bearer invalid"}, expected=401)
            await request("GET", "/api/memory/stores/soul/private-agent", expected=403)
            await request("GET", path, params={**scope, "limit": 201}, expected=422)
            revision = initial["revision"]
            add_body = body(revision, "管理验收偏好：保留清晰中文来源。")
            created = await request("POST", path, add_body)
            repeated = await request("POST", path, add_body)
            assert repeated["idempotent_replay"]
            await request("POST", path, {**add_body, "text": "同标识的其他正文。"}, expected=409)
            target = f"{path}/entries/{created['entry_hash']}"
            async def edit(text):
                return await client.patch(target, params=scope, json=body(revision + 1, text))
            changes = await asyncio.gather(edit("管理验收偏好：并发编辑甲。"), edit("管理验收偏好：并发编辑乙。"))
            assert sorted(response.status_code for response in changes) == [200, 409]
            for response in changes:
                validation("PATCH", target, response)
                records.append({"method": "PATCH", "path": target, "status": response.status_code,
                    "response": response.json(), "operation_id": "editMemoryEntry", "schema_validated": True})
            current = next(response.json()["data"] for response in changes if response.status_code == 200)
            target = f"{path}/entries/{current['entry_hash']}"
            revision = current["revision"]
            for action in ("pin", "unpin", "archive", "restore"):
                changed = await request("POST", f"{target}/{action}", body(revision))
                revision = changed["revision"]
            deleted = await request("DELETE", target, body(revision))
            revision = deleted["revision"]
            risky = await request("POST", path, body(revision, "管理财务核验：付款金额为 300 元。"))
            risk_path = f"{path}/entries/{risky['entry_hash']}/review"
            rejected = await request("POST", risk_path, body(risky["revision"], review_decision="reject"))
            approved = await request("POST", risk_path, body(rejected["revision"], review_decision="approve"))
            page = await request("GET", path, params={**scope, "limit": 1})
            assert page["next_after"]
            await request("GET", path, params={**scope, "limit": 1, "after": page["next_after"]})
            await request("GET", path, params={**scope, "state": "archived", "after": page["next_after"]}, expected=422)
            ledger_params = {**scope, "store_type": "user", "store_id": "owner", "limit": 1}
            ledger = await request("GET", "/api/memory/ledger", params=ledger_params)
            await request("GET", "/api/memory/ledger", params={**ledger_params, "after": ledger["next_after"]})
            await request("POST", f"/api/memory/ledger/{ledger['items'][0]['id']}/rollback", body(approved["revision"]))
            skill_body = {**body(0, "管理核验流程\n读取来源，核查编号，保存完整材料。"), **scope,
                "name": "api-verification-" + uuid.uuid4().hex[:8], "description": "核查真实材料来源", "files": {"references/source.md": "真实完整的支撑材料"}}
            skill = await request("POST", "/api/memory/skills", skill_body, params={})
            await request("POST", "/api/memory/skills", skill_body, params={})
            skill_id = skill["store_id"]
            await request("GET", "/api/memory/skills", params={**scope, "limit": 1})
            skill_path = f"/api/memory/stores/skill/{skill_id}"
            skill_store = await request("GET", skill_path)
            edit_body = body(1, "管理核验流程更新\n核查来源、编号和完整支撑材料。", files={"references/source.md": "更新后完整支撑材料"})
            edit_path = f"{skill_path}/entries/{skill_store['entries'][0]['entry_hash']}"
            await request("PATCH", edit_path, edit_body)
            await request("PATCH", edit_path, edit_body)
            await request("GET", f"/api/memory/skills/{skill_id}/file", params={**scope, "file": "references/source.md"})
            await request("GET", f"/api/memory/skills/{skill_id}/file", params={**scope, "file": "../config.json"}, expected=403)
            for word in ("核验", "管理验收"):
                await request("GET", "/api/memory/search", params={**scope, "query": word, "limit": 1})
            jobs = await request("GET", "/api/memory/jobs", params={**scope, "limit": 1})
            job_id = jobs["items"][0]["id"]
            await request("GET", f"/api/memory/jobs/{job_id}")
            await request("POST", f"/api/memory/jobs/{job_id}/cancel")
            old_approval = query(root, "SELECT job_id,id,input_hash FROM memory_job_approvals WHERE status='expired' LIMIT 1")[0]
            await request("POST", f"/api/memory/jobs/{old_approval['job_id']}/approvals/{old_approval['id']}", {
                "decision": "allow_once", "input_hash": old_approval["input_hash"]}, expected=409)
            await request("POST", "/api/memory/curate/run", {**scope, "client_request_id": uuid.uuid4().hex}, params={}, expected=202)
            await until(root, "SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM memory_jobs WHERE kind='curate' AND status IN ('queued','running'))")
            head = query(root, "SELECT MAX(global_seq) AS value FROM run_events")[0]["value"]
            seen = []
            async with aconnect_sse(client, "GET", "/api/memory/stream", params={**scope, "from": head}) as stream:
                assert stream.response.status_code == 200
                current = await request("GET", "/api/memory/stores/workspace/default")
                changed = await request("POST", "/api/memory/stores/workspace/default", body(current["revision"], "实时事件交接：实际来源更新。"))
                async for item in stream.aiter_sse():
                    if item.data:
                        frame = json.loads(item.data)
                        seen.append(frame)
                        assert frame["global_seq"] == changed["global_seq"] and "seq" not in frame
                        break
            async with aconnect_sse(client, "GET", "/api/memory/stream", params={**scope, "from": head}) as stream:
                async for item in stream.aiter_sse():
                    if item.data:
                        assert json.loads(item.data) == seen[0]
                        break
            sse = {"operation_id": "streamMemoryEvents", "from": head, "frames": seen, "reconnect_equal": True}
            operations = {row["operation_id"] for row in records} | {"streamMemoryEvents"}
            expected = {operation["operationId"] for item in CONTRACT["paths"].values() for operation in item.values()
                if isinstance(operation, dict) and operation.get("x-implementation-card") == "M1-11"}
            assert expected <= operations, sorted(expected - operations)
            assert query(root, "SELECT COUNT(*) AS count FROM llm_calls")[0]["count"] == before_calls
    finally:
        exc = sys.exception()
        if exc is not None:
            error = f"{type(exc).__name__}: {exc}"
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
        exit_code = await asyncio.wait_for(process.wait(), 15)
        log.close()
        db = Database(root / "agentcrew.db")
        audit = verify_with_anchor(db.read_conn, root / "chain-head.txt")
        db.close()
        statements = ["SELECT COUNT(*) AS count FROM llm_calls", "SELECT * FROM memory_ledger ORDER BY id DESC LIMIT 25",
            "SELECT global_seq,type,payload FROM run_events WHERE type IN ('memory.updated','memory.archived','skill.patched','memory.curated') ORDER BY global_seq DESC LIMIT 40"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"observed_at": datetime.now(timezone.utc).isoformat(), "scope": scope,
            "http": records, "sse": sse, "llm_before": before_calls, "llm_after": query(root, statements[0])[0]["count"],
            "queries": [{"sql": sql, "rows": query(root, sql)} for sql in statements], "files": files(root),
            "audit": {"ok": audit.ok, "checked_count": audit.checked_count}, "error": error, "service_exit": exit_code},
            ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-data-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.seed_data_dir.absolute(), args.data_dir.absolute(), args.output.absolute()))
