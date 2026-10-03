"""M2-03：真实HTTP服务、认证、并发身份、会话隔离及SSE撤权。"""

import concurrent.futures
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest

from agentcrew_server.secrets import redact, register_secret
from test_governance_resources import resources


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("identity-http")
    shutil.copy2(Path(os.environ["AGENTCREW_TEST_CONFIG_SOURCE"]), root / "config.json")
    token = secrets.token_urlsafe(32)
    register_secret(token)
    log = (root / "stderr.log").open("w")
    child = subprocess.Popen([sys.executable, "-m", "agentcrew_server", "--data-dir", str(root), "--port", "8833"],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, "AGENTCREW_TOKEN": token}, stdout=subprocess.PIPE,
        stderr=log, text=True)
    try:
        line = child.stdout.readline().strip()
        assert line.startswith("AGENTCREW_READY "), redact((root / "stderr.log").read_text())
        port = json.loads(line.split(" ", 1)[1])["port"]
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=60)
        record = {"root": root, "client": client, "url": str(client.base_url), "token": token, "requests": []}
        yield record
        (root / "http-evidence.json").write_text(json.dumps(record["requests"], ensure_ascii=False, indent=2) + "\n")
        client.close()
    finally:
        child.terminate()
        child.wait(15)
        log.close()


def request(server, method, path, *, user="owner", identity=None, body=None):
    headers = {"X-AgentCrew-Identity": identity} if identity is not None else {}
    response = server["client"].request(method, path, headers=headers, json=body)
    value = response.json() if response.content else None
    if isinstance(value, dict) and "data" in value and "identity_token" in value["data"]:
        register_secret(value["data"]["identity_token"])
    server["requests"].append({"method": method, "path": path, "effective_user": user,
        "request": body, "status": response.status_code, "response": json.loads(redact(json.dumps(value, ensure_ascii=False)))})
    return response


def demo(server, user):
    response = request(server, "POST", "/api/identity/demo", body={"user_id": user, "change_id": "identity-" + uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["identity"]["effective_user_id"] == user
    return response.json()["data"]["identity_token"]


def conversation(server, identity=None, user="owner"):
    response = request(server, "POST", "/api/conversations", user=user, identity=identity, body={"workspace_id": "office", "agent_id": "xiaowen",
        "instruction": "只用一句简短文字确认收到这条测试指令。", "client_request_id": uuid.uuid4().hex})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_bearer_and_identity_authentication(server):
    with httpx.Client(base_url=server["url"]) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/identity").status_code == 401
        assert client.get("/api/identity", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert request(server, "GET", "/api/identity", identity="unissued-identity").status_code == 401
    assert request(server, "GET", "/api/identity").json()["data"]["credential_owner_id"] == "owner"


def test_member_management_and_impersonation_rejected(server):
    member = demo(server, "lilei")
    for method, path in (("PATCH", "/api/settings"), ("POST", "/api/memory/curate/run"), ("PUT", "/api/workspaces/office/members/lilei")):
        assert request(server, method, path, user="lilei", identity=member, body={}).status_code == 403
    assert request(server, "PATCH", "/api/memberships/membership-wangming/role", user="lilei", identity=member,
        body={"role": "owner", "change_id": "illegal-role", "expected_revision": 1}).status_code == 403
    assert request(server, "POST", "/api/conversations", user="lilei", identity=member,
        body={"workspace_id": "office", "agent_id": "xiaowen", "instruction": "测试", "actor": "owner", "role": "owner"}).status_code == 422


def test_last_owner_and_revision_idempotency(server):
    body = {"role": "member", "change_id": "last-owner", "expected_revision": 1}
    assert request(server, "PATCH", "/api/memberships/membership-owner/role", body=body).status_code == 403
    body = {"role": "admin", "change_id": "admin-role", "expected_revision": 1}
    response = request(server, "PATCH", "/api/memberships/membership-wangming/role", body=body)
    assert response.status_code == 200
    assert request(server, "PATCH", "/api/memberships/membership-wangming/role", body=body).json() == response.json()
    assert request(server, "PATCH", "/api/memberships/membership-wangming/role", body={**body, "role": "member"}).status_code == 409
    assert request(server, "PATCH", "/api/memberships/membership-wangming/role", body={**body, "change_id": "stale-role"}).status_code == 409


def test_concurrent_window_identity_and_membership_pagination(server):
    member, admin = demo(server, "lilei"), demo(server, "wangming")
    def read(token, user):
        response = server["client"].get("/api/identity", headers={"X-AgentCrew-Identity": token})
        assert response.json()["data"]["effective_user_id"] == user
        return response.json()["data"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(read, token, user) for token, user in [(member, "lilei"), (admin, "wangming")] * 10]
        assert len([future.result() for future in futures]) == 20
    assert request(server, "GET", "/api/identity").json()["data"]["effective_user_id"] == "owner"
    first = request(server, "GET", "/api/memberships?limit=1").json()["data"]
    cursor = first["next_after"]
    assert cursor
    second = request(server, "GET", "/api/memberships?limit=1&after=" + cursor).json()["data"]
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert request(server, "GET", "/api/memberships?limit=1&after=" + cursor, user="lilei", identity=member).status_code == 422
    assert len(request(server, "GET", "/api/memberships", user="lilei", identity=member).json()["data"]["items"]) == 1


def test_conversation_scope_and_identity_records(server):
    member = demo(server, "lilei")
    own, other = conversation(server, member, "lilei"), conversation(server)
    cid, tid = other["conversation"]["id"], other["task_run_id"]
    for path in (f"/api/conversations/{cid}/messages", f"/api/conversations/{cid}/scope", f"/api/conversations/{cid}/state",
            f"/api/conversations/{cid}/artifacts", f"/api/conversations/{cid}/stream", f"/api/task-runs/{tid}/attempts", f"/api/task-runs/{tid}/events?limit=2"):
        assert request(server, "GET", path, user="lilei", identity=member).status_code == 403
    for path in (f"/api/task-runs/{tid}/cancel", f"/api/task-runs/{tid}/resume", f"/api/conversations/{cid}/queue/continue"):
        assert request(server, "POST", path, user="lilei", identity=member).status_code == 403
    assert request(server, "POST", "/api/conversations", user="lilei", identity=member,
        body={"workspace_id": "default", "agent_id": "default", "instruction": "范围检查"}).status_code == 403
    own_id = own["conversation"]["id"]
    rows = request(server, "GET", "/api/conversations", user="lilei", identity=member).json()["data"]
    assert own_id in {row["id"] for row in rows} and cid not in {row["id"] for row in rows}
    search = request(server, "GET", "/api/memory/search?workspace_id=office&agent_id=xiaowen&query=测试指令", user="lilei", identity=member)
    assert search.status_code == 200, search.text
    history = [item for item in search.json()["data"]["items"] if item["kind"] in {"message", "summary"}]
    assert history and {item["source"]["conversation_id"] for item in history} == {own_id}
    database = sqlite3.connect(server["root"] / "agentcrew.db")
    assert database.execute("SELECT effective_user_id,credential_owner_id FROM task_governance WHERE task_run_id=?", (own["task_run_id"],)).fetchone() == ("lilei", "owner")
    database.close()


def test_role_revocation_closes_actual_sse(server):
    member = demo(server, "lilei")
    created = conversation(server, member, "lilei")
    cid = created["conversation"]["id"]
    connected = threading.Event()
    def consume():
        with httpx.Client(base_url=server["url"], timeout=10) as client:
            with client.stream("GET", f"/api/conversations/{cid}/stream", headers={"Authorization": f"Bearer {server['token']}", "X-AgentCrew-Identity": member}) as response:
                assert response.status_code == 200
                connected.set()
                return list(response.iter_lines())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        stream = executor.submit(consume)
        assert connected.wait(5)
        response = request(server, "PATCH", "/api/memberships/membership-lilei/role",
            body={"role": "member", "status": "disabled", "expected_revision": 1, "change_id": "disable-member"})
        assert response.status_code == 200
        assert isinstance(stream.result(timeout=5), list)
    assert request(server, "GET", "/api/identity", user="lilei", identity=member).status_code == 401
    assert request(server, "GET", "/api/identity").status_code == 200
    assert request(server, "POST", "/api/identity/demo", body={"user_id": "lilei", "change_id": "disabled-member-demo"}).status_code == 401


def test_credential_owner_revocation_closes_demo_sse(server):
    response = request(server, "PATCH", "/api/memberships/membership-wangming/role",
        body={"role": "owner", "expected_revision": 2, "change_id": "second-owner"})
    assert response.status_code == 200
    admin = demo(server, "wangming")
    created = conversation(server, admin, "wangming")
    cid = created["conversation"]["id"]
    connected = threading.Event()
    def consume():
        with httpx.Client(base_url=server["url"], timeout=10) as client:
            with client.stream("GET", f"/api/conversations/{cid}/stream", headers={"Authorization": f"Bearer {server['token']}", "X-AgentCrew-Identity": admin}) as response:
                assert response.status_code == 200
                connected.set()
                return list(response.iter_lines())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        stream = executor.submit(consume)
        assert connected.wait(5)
        assert request(server, "PATCH", "/api/memberships/membership-owner/role",
            body={"role": "member", "expected_revision": 1, "change_id": "credential-owner-demoted"}).status_code == 200
        assert isinstance(stream.result(timeout=5), list)
    assert request(server, "GET", "/api/identity", user="wangming", identity=admin).status_code == 401


def test_revoked_queued_job_reaches_terminal_and_releases_queue(resources):
    import asyncio
    from agentcrew_core.governance import RequestIdentity
    from agentcrew_server.bus import EventBus
    from agentcrew_server.config import load_config
    from agentcrew_server.governance.identity import Identities
    from agentcrew_server.governance.seed import seed
    from agentcrew_server.memory.jobs import MemoryJobs
    from agentcrew_server.settings import SettingsService
    service, memory = resources
    settings = SettingsService(load_config(service.data_dir, {}), service.data_dir, service.events.channel, service.data_dir / "chain-head.txt")
    jobs = MemoryJobs(memory, EventBus(), settings)
    async def check():
        await seed(service, memory)
        identities = Identities(service)
        jobs.identities = identities
        memory.identities = identities
        rejected = await jobs.curator.enqueue("office", "xiaowen", "queued-admin", identity=RequestIdentity("owner", "wangming", True))
        following = await jobs.curator.enqueue("office", "xiaowen", "following-owner", startup=True, identity=RequestIdentity("owner", "owner"))
        assert isinstance(rejected, str) and isinstance(following, str)
        await identities.role(RequestIdentity("owner", "owner"), "membership-wangming",
            {"change_id": "revoke-queued-admin", "expected_revision": 1, "role": "admin", "status": "disabled"})
        await jobs.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and jobs.get(following)["status"] not in {"completed", "failed"}:
            await asyncio.sleep(0.05)
        assert jobs.get(rejected)["status"] == "failed"
        assert "资格已失效" in jobs.get(rejected)["error"]
        assert jobs.get(following)["status"] == "completed"
        assert service.db.read_conn.execute("SELECT count(*) FROM memory_job_calls WHERE job_id=? AND type='llm.request_started'", (rejected,)).fetchone()[0] == 0
        queries = ["SELECT id,kind,status,error FROM memory_jobs ORDER BY created_at,id",
            "SELECT job_id,effective_user_id,credential_owner_id FROM job_governance ORDER BY job_id",
            "SELECT global_seq,type FROM run_events ORDER BY global_seq",
            "SELECT seq,actor_type,actor_id,action,hash FROM audit_log ORDER BY seq"]
        evidence = {"rejected_job_id": rejected, "following_job_id": following, "rejected_status": "failed", "following_status": "completed",
            "rejected_model_requests": 0, "queries": [{"sql": query, "rows": [dict(row) for row in service.db.read_conn.execute(query)]} for query in queries]}
        (service.data_dir / "queued-revocation.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    async def run_check():
        try:
            await check()
        finally:
            await jobs.shutdown()
    asyncio.run(run_check())
