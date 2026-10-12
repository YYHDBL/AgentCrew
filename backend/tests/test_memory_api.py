"""全部管理请求通过实际 uvicorn/TCP/HTTP，使用真实 SQLite 与文件。"""

import asyncio
import json
import logging
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import uvicorn
import yaml
from httpx_sse import aconnect_sse
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from starlette.routing import compile_path
from pathlib import Path as FilePath

from agentcrew_server.api.app import create_app
from agentcrew_server.memory.search import MemorySearch
from agentcrew_server.runtime import RuntimeState
from agentcrew_core.tools.metadata import ToolInvocation
from test_session_summaries import services, task


@pytest.fixture
def api(services):
    store, sessions, jobs = services
    token = uuid.uuid4().hex
    runtime = RuntimeState(log=logging.getLogger("memory-api"), data_dir=store.data_dir, db=store.db,
        write_channel=store.events.channel, event_store=store.events, bus=jobs.bus, token=token,
        memory=store, memory_jobs=jobs, memory_search=MemorySearch(store), sessions=sessions, settings=jobs.settings)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(runtime), log_config=None, lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    with httpx.Client(base_url=f"http://127.0.0.1:{listener.getsockname()[1]}", headers={"Authorization": f"Bearer {token}"}) as client:
        yield client, store, jobs
    server.should_exit = True
    thread.join(10)
    listener.close()
    assert not thread.is_alive()


SCOPE = {"workspace_id": "default", "agent_id": "default"}
CONTRACT = yaml.safe_load((FilePath(__file__).parents[2] / "docs/contracts/openapi.yaml").read_text())
CONTRACT_RESOLVER = Registry().with_resource("urn:agentcrew:openapi",
    Resource.from_contents(CONTRACT, default_specification=DRAFT202012)).resolver("urn:agentcrew:openapi")


def validate_response(response, method, path):
    matches = [(template, item) for template, item in CONTRACT["paths"].items()
        if method.lower() in item and compile_path(template)[0].fullmatch(path)]
    assert matches, f"真实 HTTP 操作缺少契约：{method} {path}"
    _template, item = sorted(matches, key=lambda pair: pair[0].count("{"))[0]
    operation = item[method.lower()]
    definition = operation["responses"][str(response.status_code)]
    if "$ref" in definition:
        definition = CONTRACT_RESOLVER.lookup(definition["$ref"]).contents
    schema = definition["content"]["application/json"]["schema"]
    Draft202012Validator({"components": CONTRACT["components"], **schema}).validate(response.json())


def request(client, method, path, body=None, scope=SCOPE):
    response = client.request(method, path, params=scope, json=body)
    validate_response(response, method, path)
    return response


def change(revision, text=None, **extra):
    return {"change_id": uuid.uuid4().hex, "expected_revision": revision, "basis": "所有者实际 HTTP 管理请求", **({"text": text} if text else {}), **extra}


def test_store_http_auth_scope_lifecycle_pagination_and_review(api):
    client, store, _jobs = api
    path = "/api/memory/stores/user/owner"
    assert request(client, "GET", path).status_code == 200
    assert client.get(path, params=SCOPE, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert request(client, "GET", "/api/memory/stores/soul/private-agent").status_code == 403
    assert request(client, "GET", path, scope={**SCOPE, "limit": 201}).status_code == 422
    body = change(0, "偏好：使用清晰中文。")
    created = request(client, "POST", path, body)
    assert created.status_code == 200
    entry = created.json()["data"]
    repeat = request(client, "POST", path, body)
    assert repeat.json()["data"]["idempotent_replay"] is True
    assert request(client, "POST", path, {**body, "text": "不同输入"}).status_code == 409
    target = f"{path}/entries/{entry['entry_hash']}"
    edited = request(client, "PATCH", target, change(1, "新偏好：使用清晰中文。"))
    assert edited.status_code == 200
    assert edited.json()["data"]["entry_id"] == entry["entry_id"]
    assert edited.json()["data"]["entry_hash"] != entry["entry_hash"]
    assert request(client, "PATCH", f"{path}/entries/{entry['entry_hash']}", {**body, "expected_revision": 1, "text": "新偏好：使用清晰中文。"}).status_code == 409
    target = f"{path}/entries/{edited.json()['data']['entry_hash']}"
    for action, revision in (("pin", 2), ("unpin", 3), ("archive", 4), ("restore", 5)):
        assert request(client, "POST", f"{target}/{action}", change(revision)).status_code == 200
    risky = request(client, "POST", path, change(6, "财务付款金额为 200 元。"))
    assert risky.status_code == 200
    review = request(client, "POST", f"{path}/entries/{risky.json()['data']['entry_hash']}/review", change(7, review_decision="approve"))
    assert review.status_code == 200
    page = request(client, "GET", path, scope={**SCOPE, "limit": 1}).json()["data"]
    assert len(page["entries"]) == 1 and page["next_after"]
    next_page = request(client, "GET", path, scope={**SCOPE, "limit": 1, "after": page["next_after"]}).json()["data"]
    assert next_page["entries"][0]["entry_id"] != page["entries"][0]["entry_id"]
    assert request(client, "GET", path, scope={**SCOPE, "state": "archived", "after": page["next_after"]}).status_code == 422
    assert request(client, "DELETE", target, change(8)).status_code == 200
    assert request(client, "GET", path).json()["data"]["revision"] == 9
    assert store.path("user", "owner").exists()


def test_concurrent_http_edits_and_ledger_restore(api):
    client, _store, _jobs = api
    path = "/api/memory/stores/workspace/default"
    created = request(client, "POST", path, change(0, "工作区材料来源。"))
    assert created.status_code == 200
    target = f"{path}/entries/{created.json()['data']['entry_hash']}"
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda text: request(client, "PATCH", target, change(1, text)), ("更新来源甲。", "更新来源乙。")))
    assert sorted(response.status_code for response in results) == [200, 409]
    ledger = request(client, "GET", "/api/memory/ledger", scope={**SCOPE, "store_type": "workspace", "store_id": "default", "limit": 1})
    assert ledger.status_code == 200
    latest = ledger.json()["data"]["items"][0]
    assert latest["before_text"] == "工作区材料来源。"
    restored = request(client, "POST", f"/api/memory/ledger/{latest['id']}/rollback", change(2))
    assert restored.status_code == 200
    assert request(client, "GET", path).json()["data"]["text"] == "工作区材料来源。"
    assert request(client, "GET", "/api/memory/ledger", scope={**SCOPE, "store_type": "workspace", "store_id": "other"}).status_code == 403


def test_skill_http_create_edit_support_scope_and_search(api):
    client, _store, _jobs = api
    body = {**change(0, "资料核验\n先读取来源，再核查编号。"), **SCOPE, "name": "资料核验", "description": "核查材料来源", "files": {"references/source.md": "真实支撑材料"}}
    created = request(client, "POST", "/api/memory/skills", body, scope={})
    assert created.status_code == 200
    skill_id = created.json()["data"]["store_id"]
    listed = request(client, "GET", "/api/memory/skills")
    assert listed.status_code == 200 and listed.json()["data"]["items"][0]["id"] == skill_id
    file = request(client, "GET", f"/api/memory/skills/{skill_id}/file", scope={**SCOPE, "file": "references/source.md"})
    assert file.status_code == 200 and file.json()["data"]["text"] == "真实支撑材料"
    assert request(client, "GET", f"/api/memory/skills/{skill_id}/file", scope={**SCOPE, "file": "../config.json"}).status_code == 403
    value = request(client, "GET", f"/api/memory/stores/skill/{skill_id}").json()["data"]
    edited = request(client, "PATCH", f"/api/memory/stores/skill/{skill_id}/entries/{value['entries'][0]['entry_hash']}",
        change(1, "资料核验\n核查来源和完整编号。", files={"references/source.md": "更新的支撑材料"}))
    assert edited.status_code == 200
    repeated_body = change(2, "资料核验新标题\n核查真实来源。")
    current = request(client, "GET", f"/api/memory/stores/skill/{skill_id}").json()["data"]
    edit_path = f"/api/memory/stores/skill/{skill_id}/entries/{current['entries'][0]['entry_hash']}"
    assert request(client, "PATCH", edit_path, repeated_body).status_code == 200
    assert request(client, "PATCH", edit_path, repeated_body).status_code == 200
    assert request(client, "GET", f"/api/memory/stores/skill/{skill_id}", scope={**SCOPE, "agent_id": "other"}).status_code == 403
    assert request(client, "GET", "/api/memory/search", scope={**SCOPE, "query": "核查", "limit": 201}).status_code == 422


def test_http_idempotency_binds_action_entry_and_ledger_resource(api):
    client, _store, _jobs = api
    path = "/api/memory/stores/user/owner"
    first = request(client, "POST", path, change(0, "第一条偏好。"))
    second = request(client, "POST", path, change(1, "第二条偏好。"))
    pin = change(2)
    one = f"{path}/entries/{first.json()['data']['entry_hash']}/pin"
    two = f"{path}/entries/{second.json()['data']['entry_hash']}/pin"
    assert request(client, "POST", one, pin).status_code == 200
    assert request(client, "POST", two, pin).status_code == 409


def test_external_binary_modification_returns_explicit_http_conflict(api):
    client, store, _jobs = api
    path = "/api/memory/stores/user/owner"
    assert request(client, "POST", path, change(0, "原始偏好。" )).status_code == 200
    store.path("user", "owner").write_bytes(b"\xff")
    result = request(client, "GET", path)
    assert result.status_code == 409 and result.json()["error"]["code"] == "EXTERNAL_MODIFICATION"


def test_actual_sse_handoff_reconnect_and_private_scope_filter(api):
    client, store, jobs = api
    other = asyncio.run(jobs.review.sessions.create_conversation(instruction="登记另一个员工的实际范围", agent_id="other"))
    assert other["conversation"]["id"]
    private = request(client, "POST", "/api/memory/stores/soul/other", change(0, "其他员工的私有知识。"), scope={**SCOPE, "agent_id": "other"})
    assert private.status_code == 200
    first = request(client, "POST", "/api/memory/stores/user/owner", change(0, "共享用户偏好。"))
    assert first.status_code == 200
    async def check():
        seen = []
        async with httpx.AsyncClient(base_url=str(client.base_url), headers=dict(client.headers), timeout=10) as async_client:
            async def produce():
                for revision in range(1, 16):
                    response = await async_client.post("/api/memory/stores/user/owner", params=SCOPE, json=change(revision, f"并发补播偏好{revision}。"))
                    assert response.status_code == 200
            producing = asyncio.create_task(produce())
            async with aconnect_sse(async_client, "GET", "/api/memory/stream", params={**SCOPE, "from": 0}) as source:
                assert source.response.status_code == 200
                async for event in source.aiter_sse():
                    if not event.data:
                        continue
                    row = json.loads(event.data)
                    assert row["type"] == "memory.updated" and row["payload"]["store_type"] == "user"
                    assert "task_run_id" not in row and "seq" not in row
                    seen.append(row["global_seq"])
                    if len(seen) == 16:
                        break
            await producing
            assert seen == sorted(set(seen))
            expected = [row[0] for row in store.db.read_conn.execute("SELECT global_seq FROM run_events WHERE type='memory.updated' AND json_extract(payload,'$.store_type')='user' ORDER BY global_seq")]
            assert seen == expected
            response = await async_client.post("/api/memory/stores/workspace/default", params={**SCOPE, "agent_id": "other"}, json=change(0, "同工作区共享事实。"))
            assert response.status_code == 200
            await async_client.patch(f"/api/memory/stores/soul/other/entries/{private.json()['data']['entry_hash']}",
                params={**SCOPE, "agent_id": "other"}, json=change(1, "更新其他员工的私有知识。"))
            async with aconnect_sse(async_client, "GET", "/api/memory/stream", params={**SCOPE, "from": seen[-1]}) as source:
                async for event in source.aiter_sse():
                    if event.data:
                        row = json.loads(event.data)
                        assert row["payload"]["store_type"] == "workspace" and row["global_seq"] > seen[-1]
                        break
    asyncio.run(check())


def test_actual_job_http_listing_scope_approval_cancel_and_governance(api):
    client, store, jobs = api
    async def prepare():
        conversation, task_id, event = await task(store, jobs.review.sessions)
        job_id = await jobs.review.enqueue("memory_review", "api-review-record", event.global_seq, task_id)
        await jobs._status(job_id, "running")
        approval_id = await jobs.review.request_approval(job_id, ToolInvocation("api-review-save", "memory_write",
            {"target": "user", "action": "add", "text": "人类批准后保存的偏好。", "expected_revision": 0, "basis": "实际审批输入"}))
        return conversation, job_id, approval_id
    conversation, job_id, approval_id = asyncio.run(prepare())
    path = f"/api/memory/jobs/{job_id}"
    get = request(client, "GET", path)
    assert get.status_code == 200 and get.json()["data"]["status"] == "waiting_approval"
    assert "config_snapshot" not in get.json()["data"] and "calls" in get.json()["data"]
    assert request(client, "GET", path, scope={**SCOPE, "agent_id": "other"}).status_code == 403
    assert request(client, "GET", path, scope={"agent_id": "default"}).status_code == 422
    listing = request(client, "GET", "/api/memory/jobs", scope={**SCOPE, "conversation_id": conversation, "limit": 1})
    assert listing.status_code == 200 and listing.json()["data"]["items"][0]["id"] == job_id
    approval = get.json()["data"]["approvals"][0]
    body = {"decision": "reject_once", "input_hash": approval["input_hash"]}
    assert request(client, "POST", f"{path}/approvals/{approval_id}", body).status_code == 200
    assert request(client, "POST", f"{path}/approvals/{approval_id}", body).status_code == 200
    assert request(client, "POST", f"{path}/cancel").json()["data"]["status"] == "cancelled"
    assert request(client, "POST", f"{path}/approvals/{approval_id}", body).status_code == 409
    current = {**SCOPE, "client_request_id": uuid.uuid4().hex}
    govern = request(client, "POST", "/api/memory/curate/run", current, scope={})
    assert govern.status_code == 202
    assert request(client, "POST", "/api/memory/curate/run", current, scope={}).json()["data"]["id"] == govern.json()["data"]["id"]


def test_http_schema_rejects_undeclared_fields_and_boolean_revision(api):
    client, _store, _jobs = api
    path = "/api/memory/stores/user/owner"
    assert request(client, "POST", path, {**change(0, "正常偏好。"), "actor_type": "agent"}).status_code == 422
    assert request(client, "POST", path, {**change(0, "正常偏好。"), "expected_revision": False}).status_code == 422
    skill = {**change(0, "正常技能正文。"), **SCOPE, "name": "正常技能", "description": "合法描述", "review_decision": "approve"}
    assert request(client, "POST", "/api/memory/skills", skill, scope={}).status_code == 422


@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_metadata_external_path_replacement_explicit_conflict(api, replacement):
    client, store, _jobs = api
    path = "/api/memory/stores/user/owner"
    assert request(client, "POST", path, change(0, "外部替换核验偏好。" )).status_code == 200
    metadata = store.path("user", "owner").with_suffix(".meta.json")
    metadata.unlink()
    if replacement == "symlink":
        metadata.symlink_to(store.path("user", "owner"))
    else:
        metadata.mkdir()
    response = request(client, "GET", path)
    assert response.status_code == 409 and response.json()["error"]["code"] == "EXTERNAL_MODIFICATION"
