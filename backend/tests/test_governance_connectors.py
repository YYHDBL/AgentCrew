"""M2-06：真实持久化HTTP、MCP SDK、凭据与边界。"""

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import socket
import subprocess
import time
import threading
import uuid
from importlib.metadata import version
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from dataclasses import replace

import pytest

from agentcrew_core.connectors import ConnectorBoundaryError, connector_address_allowed, connector_effect, validate_schema_references
from agentcrew_core.governance import RequestIdentity
from agentcrew_core.tools import ToolInvocation, ToolScheduler, build_default_registry
from agentcrew_server.governance.connectors import Connectors
from agentcrew_server.governance.grants import Grants
from agentcrew_server.governance.resources import GovernanceError
from agentcrew_server.secrets import redact
from test_governance_resources import resources
from test_governance_skills import governed
from test_governance_identity import server, request, demo


@pytest.fixture
def persistent_http(tmp_path):
    database = tmp_path / "upstream.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE operations(id INTEGER PRIMARY KEY,identity_key TEXT UNIQUE,body TEXT,auth_sha256 TEXT)")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            if self.path == "/redirect-local":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_port}/")
                self.end_headers()
                return
            if self.path == "/redirect-denied":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.2:1/denied")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write((self.headers.get("Authorization") or "真实HTTP连接可用").encode())

        def do_POST(self):
            if self.path == "/dedupe-redirect":
                self.send_response(307)
                self.send_header("Location", "/ordinary")
                self.end_headers()
                return
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            key = self.headers.get("Idempotency-Key") if self.path == "/deduplicated" else None
            authentication = self.headers.get("Authorization", "")
            with sqlite3.connect(database) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if key is None or connection.execute("SELECT id FROM operations WHERE identity_key=?", (key,)).fetchone() is None:
                    connection.execute("INSERT INTO operations(identity_key,body,auth_sha256) VALUES(?,?,?)",
                        (key, body, hashlib.sha256(authentication.encode()).hexdigest()))
                count = connection.execute("SELECT count(*) FROM operations").fetchone()[0]
            if self.path == "/slow":
                time.sleep(30)
            self.send_response(200)
            self.send_header("Content-Type", authentication)
            self.end_headers()
            self.wfile.write(json.dumps({"count": count, "authentication": authentication}).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield {"url": f"http://127.0.0.1:{server.server_port}", "port": server.server_port, "database": database}
    server.shutdown()
    server.server_close()
    thread.join()


def services(governed):
    resources, memory, versions, sessions, _skills = governed
    grants = Grants(resources, sessions.identities)
    connectors = Connectors(resources, sessions.identities, grants)
    grants.connectors = connectors
    return resources, sessions, grants, connectors


def evidence(resources, filename, actual):
    queries = ["SELECT id,workspace_id,type,status,revision FROM connectors ORDER BY id",
        "SELECT connector_id,tool_name,stable_name,connector_revision FROM connector_tools ORDER BY stable_name",
        "SELECT id,resource_type,resource_id,grantee_id,revoked_at FROM grants ORDER BY id",
        "SELECT task_run_id,effective_user_id,credential_owner_id,skill_versions FROM task_governance ORDER BY task_run_id",
        "SELECT global_seq,type,task_run_id FROM run_events ORDER BY global_seq",
        "SELECT seq,actor_id,action,resource_type,resource_id,hash FROM audit_log ORDER BY seq"]
    record = {"mcp_sdk": version("mcp"), **actual,
        "queries": [{"sql": query, "rows": [dict(row) for row in resources.db.read_conn.execute(query)]} for query in queries]}
    (resources.data_dir / filename).write_text(redact(json.dumps(record, ensure_ascii=False, indent=2)) + "\n")


async def create(resources, grants, connectors, kind, config, credential=None):
    connector = await connectors.create(RequestIdentity("owner", "owner"), {"change_id": "connector-" + uuid.uuid4().hex,
        "expected_revision": 0, "workspace_id": "office", "name": "真实连接器-" + uuid.uuid4().hex[:8], "type": kind,
        "config": config, **({"credential": credential} if credential else {})})
    grant = await grants.create(RequestIdentity("owner", "owner"), {"change_id": "connector-grant-" + uuid.uuid4().hex,
        "resource_type": "connector", "resource_id": connector["id"], "grantee_type": "agent", "grantee_id": "xiaowen"})
    return connector, grant


def test_real_http_credential_redaction_idempotency_and_target(governed, persistent_http):
    resources, sessions, grants, connectors = services(governed)
    credential = "Bearer " + uuid.uuid4().hex
    async def check():
        config = {"url": persistent_http["url"], "allowed_hosts": ["127.0.0.1"], "allowed_ports": [persistent_http["port"]],
            "allow_loopback": True, "idempotent_endpoints": [{"method": "POST", "path": path, "guarantee": "服务以唯一identity_key事务提交一次操作"} for path in ("/deduplicated", "/dedupe-redirect")]}
        connector, grant = await create(resources, grants, connectors, "http", config, credential)
        assert connector["credential_configured"] and credential not in json.dumps(connector)
        task = await sessions.create_conversation(instruction="真实HTTP连接器持久化验证", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        context = sessions.build_work_context(task["conversation"]["id"], task["task_run_id"])
        assert (await connectors.validate(RequestIdentity("owner", "owner"), connector["id"], context))["ok"]
        registry = connectors.registry(task["task_run_id"], build_default_registry())
        context.registry = registry
        scheduler = ToolScheduler(registry)
        invocation = ToolInvocation("external-same-call", "http_request", {"connector_id": connector["id"], "method": "POST", "url": persistent_http["url"] + "/deduplicated", "body": "真实一次保存"})
        first = await scheduler.run(invocation, context)
        second = await scheduler.run(invocation, context)
        assert first.ok and second.ok and credential not in first.output + second.output
        assert credential not in json.dumps(first.details) + json.dumps(second.details)
        with sqlite3.connect(persistent_http["database"]) as connection:
            assert connection.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
            assert connection.execute("SELECT body,auth_sha256 FROM operations").fetchone() == ("真实一次保存", hashlib.sha256(credential.encode()).hexdigest())
        assert connector_effect("POST", invocation.input["url"], config) == "external_idempotency"
        with pytest.raises(ConnectorBoundaryError, match="幂等的写入端点禁止重定向"):
            await scheduler.run(ToolInvocation("dedupe-redirect", "http_request", {"connector_id": connector["id"], "method": "POST", "url": persistent_http["url"] + "/dedupe-redirect", "body": "拒绝重定向"}), context)
        with sqlite3.connect(persistent_http["database"]) as connection:
            assert connection.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
        with pytest.raises(ConnectorBoundaryError):
            await scheduler.run(ToolInvocation("deny-redirect", "http_request", {"connector_id": connector["id"], "url": persistent_http["url"] + "/redirect-denied"}), context)
        with pytest.raises(ConnectorBoundaryError):
            await scheduler.run(ToolInvocation("replace-auth", "http_request", {"connector_id": connector["id"], "url": persistent_http["url"], "headers": {"Authorization": "model-auth"}}), context)
        await grants.revoke(RequestIdentity("owner", "owner"), grant["id"], {"change_id": "revoke-http-connector", "expected_revision": 1})
        with pytest.raises(GovernanceError):
            await scheduler.run(ToolInvocation("after-revoke", "http_request", {"connector_id": connector["id"], "url": persistent_http["url"]}), context)
        with sqlite3.connect(persistent_http["database"]) as connection:
            connection.row_factory = sqlite3.Row
            upstream = [dict(row) for row in connection.execute("SELECT id,identity_key,body,auth_sha256 FROM operations")]
        evidence(resources, "http-connector.json", {"task_run_id": task["task_run_id"], "connector_id": connector["id"],
            "call_id": invocation.call_id, "result": {"output": first.output, "details": first.details},
            "upstream_sql": "SELECT id,identity_key,body,auth_sha256 FROM operations", "upstream_rows": upstream,
            "credential_raw_matches": 0, "redirect_rejected": True, "grant_revoke_rejected": True})
    asyncio.run(check())


def test_real_mcp_stdio_persists_and_cleans_process(governed, persistent_http):
    resources, sessions, grants, connectors = services(governed)
    async def check():
        config = {"transport": "stdio", "command": sys.executable, "args": [str(Path(__file__).with_name("governance_mcp_server.py"))],
            "startup_files": [str(Path(__file__).with_name("governance_mcp_server.py"))],
            "allowed_hosts": [], "allowed_ports": [], "allow_loopback": False,
            "tool_policies": {"environment": {"read_only": True, "destructive": False, "needs_approval": False, "risk_level": "low"},
                "persist": {"read_only": False, "destructive": False, "needs_approval": True, "risk_level": "medium"}}}
        connector, grant = await create(resources, grants, connectors, "mcp", config)
        task = await sessions.create_conversation(instruction="真实MCP文件写入和环境验证", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        context = sessions.build_work_context(task["conversation"]["id"], task["task_run_id"])
        discovery = await connectors.validate(RequestIdentity("owner", "owner"), connector["id"], context)
        names = [tool["name"] for tool in discovery["tools"]]
        assert "mcp_" + connector["id"] + "_persist" in names
        assert all(len(name) <= 64 for name in names)
        context.registry = connectors.registry(task["task_run_id"], build_default_registry())
        scheduler = ToolScheduler(context.registry)
        long_name = connectors.stable_name(connector["id"], "employee_material_source_directory_for_governance_acceptance")
        directory = await scheduler.run(ToolInvocation("mcp-long-name", long_name, {}), context)
        assert directory.ok and str(context.cwd) in directory.output
        env = await scheduler.run(ToolInvocation("mcp-environment", "mcp_" + connector["id"] + "_environment", {}), context)
        assert env.ok, env
        actual = json.loads(env.output)["structuredContent"]
        assert set(actual["names"]) <= {"PATH", "HOME", "LANG", "TZ", "TERM", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
        with pytest.raises(ProcessLookupError):
            os.kill(actual["pid"], 0)
        saved = await scheduler.run(ToolInvocation("mcp-save", "mcp_" + connector["id"] + "_persist", {"value": "真实MCP持久化材料"}), context)
        assert saved.ok, saved
        target = Path(context.cwd) / "mcp-operations.jsonl"
        assert [json.loads(line) for line in target.read_text().splitlines()] == [{"value": "真实MCP持久化材料"}]
        protected = resources.data_dir / "protected-sample.txt"
        protected.write_text("独立无凭据保护材料")
        context = replace(context, protected=[*context.protected, protected], filesystem=None)
        read = await scheduler.run(ToolInvocation("mcp-protected-read", "mcp_" + connector["id"] + "_read_target", {"path": str(protected)}), context)
        write = await scheduler.run(ToolInvocation("mcp-protected-write", "mcp_" + connector["id"] + "_write_target", {"path": str(protected), "text": "禁止替换"}), context)
        network = await scheduler.run(ToolInvocation("mcp-forbidden-network", "mcp_" + connector["id"] + "_network_target", {"url": persistent_http["url"]}), context)
        assert not read.ok and not write.ok and not network.ok
        assert protected.read_text() == "独立无凭据保护材料"
        await grants.revoke(RequestIdentity("owner", "owner"), grant["id"], {"change_id": "revoke-mcp", "expected_revision": 1})
        with pytest.raises(GovernanceError):
            await scheduler.run(ToolInvocation("mcp-revoked", "mcp_" + connector["id"] + "_persist", {"value": "禁止再次保存"}), context)
        assert len(target.read_text().splitlines()) == 1
        evidence(resources, "stdio-connector.json", {"task_run_id": task["task_run_id"], "connector_id": connector["id"],
            "discovery": discovery, "environment": actual, "process_exited": True, "protected_read": read.ok,
            "protected_write": write.ok, "stdio_network": network.ok, "call_id": "mcp-save",
            "file": {"name": target.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "mtime_ns": target.stat().st_mtime_ns,
                "rows": [json.loads(line) for line in target.read_text().splitlines()]}})
    asyncio.run(check())


def test_real_mcp_streamable_http_persists(governed, tmp_path):
    resources, sessions, grants, connectors = services(governed)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    log = (tmp_path / "mcp-http.log").open("w")
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("governance_mcp_server.py")),
        "--transport", "streamable-http", "--port", str(port)], cwd=tmp_path, stderr=log, stdout=log)
    try:
        import httpx
        endpoint = f"http://127.0.0.1:{port}/mcp"
        deadline = time.monotonic() + 10
        ready = False
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                ready = probe.connect_ex(("127.0.0.1", port)) == 0
            if ready:
                break
            time.sleep(0.05)
        assert ready, (tmp_path / "mcp-http.log").read_text()
        async def check():
            config = {"transport": "streamable_http", "url": endpoint, "allowed_hosts": ["127.0.0.1"],
                "allowed_ports": [port], "allow_loopback": True}
            connector, grant = await create(resources, grants, connectors, "mcp", config)
            task = await sessions.create_conversation(instruction="真实网络MCP持久化", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
            context = sessions.build_work_context(task["conversation"]["id"], task["task_run_id"])
            result = await connectors.validate(RequestIdentity("owner", "owner"), connector["id"], context)
            assert result["ok"]
            registry = connectors.registry(task["task_run_id"], build_default_registry())
            context.registry = registry
            result = await ToolScheduler(registry).run(ToolInvocation("network-mcp-persist", "mcp_" + connector["id"] + "_persist", {"value": "真实StreamableHTTP材料"}), context)
            assert result.ok, result
            assert [json.loads(line) for line in (tmp_path / "mcp-operations.jsonl").read_text().splitlines()] == [{"value": "真实StreamableHTTP材料"}]
            assert registry.get("mcp_" + connector["id"] + "_persist").metadata.side_effect_class == "outcome_unknown"
            target = tmp_path / "mcp-operations.jsonl"
            evidence(resources, "network-mcp.json", {"task_run_id": task["task_run_id"], "connector_id": connector["id"],
                "call_id": "network-mcp-persist", "result": json.loads(result.output),
                "file": {"name": target.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "mtime_ns": target.stat().st_mtime_ns}})
        asyncio.run(check())
    finally:
        process.terminate()
        process.wait(15)
        log.close()


def test_old_mcp_directory_revision_cannot_execute_after_update(governed):
    resources, sessions, grants, connectors = services(governed)
    async def check():
        config = {"transport": "stdio", "command": sys.executable, "args": [str(Path(__file__).with_name("governance_mcp_server.py"))],
            "startup_files": [str(Path(__file__).with_name("governance_mcp_server.py"))],
            "allowed_hosts": [], "allowed_ports": [], "allow_loopback": False}
        connector, grant = await create(resources, grants, connectors, "mcp", config)
        task = await sessions.create_conversation(instruction="旧目录修订执行边界", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        context = sessions.build_work_context(task["conversation"]["id"], task["task_run_id"])
        await connectors.validate(RequestIdentity("owner", "owner"), connector["id"], context)
        old_registry = connectors.registry(task["task_run_id"], build_default_registry())
        await connectors.update(RequestIdentity("owner", "owner"), connector["id"], {"change_id": "change-directory-policy", "expected_revision": 1,
            "config": {**config, "tool_policies": {"persist": {"read_only": False, "destructive": True, "risk_level": "high", "needs_approval": True}}}})
        await connectors.validate(RequestIdentity("owner", "owner"), connector["id"], context)
        context.registry = old_registry
        with pytest.raises(GovernanceError, match="修订已经失效"):
            await ToolScheduler(old_registry).run(ToolInvocation("old-mcp-call", "mcp_" + connector["id"] + "_persist", {"value": "不能保存"}), context)
        assert not (Path(context.cwd) / "mcp-operations.jsonl").exists()
    asyncio.run(check())


def test_cross_origin_redirect_does_not_forward_bound_credential(governed, persistent_http):
    resources, sessions, grants, connectors = services(governed)
    async def check():
        config = {"url": persistent_http["url"], "allowed_hosts": ["127.0.0.1", "localhost"],
            "allowed_ports": [persistent_http["port"]], "allow_loopback": True}
        credential = "Bearer " + uuid.uuid4().hex
        connector, grant = await create(resources, grants, connectors, "http", config, credential)
        task = await sessions.create_conversation(instruction="真实跨来源重定向认证范围", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        context = sessions.build_work_context(task["conversation"]["id"], task["task_run_id"])
        context.registry = connectors.registry(task["task_run_id"], build_default_registry())
        result = await ToolScheduler(context.registry).run(ToolInvocation("cross-origin", "http_request", {"connector_id": connector["id"], "url": persistent_http["url"] + "/redirect-local"}), context)
        assert result.ok and result.output == "真实HTTP连接可用"
    asyncio.run(check())
def test_connector_real_management_http_contract(server, persistent_http):
    credential = "Bearer " + uuid.uuid4().hex
    from agentcrew_server.secrets import register_secret
    register_secret(credential)
    body = {"change_id": "http-connector-create", "expected_revision": 0, "workspace_id": "office", "name": "真实本地HTTP",
        "type": "http", "config": {"url": persistent_http["url"], "allowed_hosts": ["127.0.0.1"], "allowed_ports": [persistent_http["port"]], "allow_loopback": True}, "credential": credential}
    created = request(server, "POST", "/api/connectors", body=body)
    assert created.status_code == 200, created.text
    item = created.json()["data"]
    assert credential not in created.text and item["credential_configured"] is True
    assert request(server, "POST", "/api/connectors", body=body).json() == created.json()
    assert request(server, "GET", "/api/connectors/" + item["id"]).json()["data"] == item
    validated = request(server, "POST", "/api/connectors/" + item["id"] + "/validate")
    assert validated.status_code == 200 and validated.json()["data"]["status_code"] == 200
    member = demo(server, "lilei")
    assert request(server, "GET", "/api/connectors/" + item["id"], user="lilei", identity=member).status_code == 403
    revised = request(server, "PATCH", "/api/connectors/" + item["id"], body={"change_id": "disable-http-api", "expected_revision": 1, "status": "disabled"})
    assert revised.status_code == 200
    assert request(server, "PATCH", "/api/connectors/" + item["id"], body={"change_id": "stale-http-api", "expected_revision": 1, "name": "陈旧修订"}).status_code == 409
    assert request(server, "POST", "/api/connectors/" + item["id"] + "/validate").status_code == 403
    malformed = request(server, "POST", "/api/connectors", body={**body, "unknown": credential})
    assert malformed.status_code == 422 and credential not in malformed.text


@pytest.mark.parametrize("address,allowed", [("127.0.0.1", False), ("::1", False), ("169.254.169.254", False), ("10.0.0.1", False), ("8.8.8.8", True)])
def test_connector_address_domain_rule(address, allowed):
    assert connector_address_allowed(address, False) is allowed


def test_mcp_schema_rejects_network_references():
    validate_schema_references({"$defs": {"value": {"type": "string"}}, "properties": {"value": {"$ref": "#/$defs/value"}}})
    with pytest.raises(ConnectorBoundaryError):
        validate_schema_references({"properties": {"value": {"$ref": "https://example.com/external-schema"}}})
