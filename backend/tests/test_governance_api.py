"""M2-11：真实HTTP管理资源、强制边界和契约操作核查。"""

import json
import uuid
import asyncio
import threading
import time
from urllib.parse import urlsplit
from pathlib import Path

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from starlette.routing import compile_path

from test_governance_identity import server, request, demo
from test_governance_connectors import persistent_http
from test_governance_rules import authorized, request_card
from test_governance_skills import governed
from test_governance_resources import resources
from agentcrew_core.governance import RequestIdentity
from agentcrew_core.tools import ToolInvocation
from agentcrew_server.runtime import RuntimeState
from agentcrew_server.api.governance_stream import visible
from agentcrew_server.db.projections import _row_to_event
from agentcrew_server.governance.management import Management
import logging
import os
import sqlite3


def change(revision, **fields):
    return {"change_id": uuid.uuid4().hex, "expected_revision": revision, **fields}


def test_workspace_and_employee_crud_current_role(server):
    member = demo(server, "lilei")
    admin = demo(server, "wangming")
    assert request(server, "POST", "/api/workspaces", identity=member, user="lilei", body=change(0, name="无权工作区")).status_code == 403
    body = change(0, name="真实治理工作区")
    created = request(server, "POST", "/api/workspaces", body=body)
    assert created.status_code == 200, created.text
    workspace = created.json()["data"]
    assert request(server, "POST", "/api/workspaces", body=body).json()["data"] == workspace
    assert Path(workspace["data_dir"]).is_dir()
    assert request(server, "GET", f'/api/workspaces/{workspace["id"]}').status_code == 200
    assert request(server, "GET", f'/api/workspaces/{workspace["id"]}', identity=admin, user="wangming").status_code == 403
    spec = {"position": "依据实际材料核查结果", "model_slot": "aux", "skill_ids": [], "connector_ids": []}
    employee_body = change(0, workspace_id=workspace["id"], name="真实治理员工", spec=spec)
    employee = request(server, "POST", "/api/agents", body=employee_body)
    assert employee.status_code == 200, employee.text
    resource = employee.json()["data"]
    assert request(server, "POST", "/api/agents", body=employee_body).json()["data"] == resource
    assert request(server, "PATCH", f'/api/agents/{resource["id"]}', body=change(1, name="更新岗位员工")).status_code == 200
    assert request(server, "PATCH", f'/api/agents/{resource["id"]}', body=change(1, name="陈旧修订")).status_code == 409
    assert request(server, "PATCH", f'/api/agents/{resource["id"]}', body=change(2, spec={**spec, "model_slot": "invalid"})).status_code == 422
    assert request(server, "GET", f'/api/agents/{resource["id"]}', identity=member, user="lilei").status_code == 403
    assert request(server, "DELETE", f'/api/agents/{resource["id"]}', body=change(2)).status_code == 200
    assert request(server, "GET", f'/api/agents/{resource["id"]}').json()["data"]["status"] == "disabled"
    assert request(server, "DELETE", f'/api/workspaces/{workspace["id"]}', body=change(1)).status_code == 200
    assert request(server, "PATCH", f'/api/workspaces/{workspace["id"]}', body=change(2, status="active")).status_code == 200
    assert request(server, "GET", "/api/workspaces/missing").status_code == 404
    assert request(server, "GET", "/api/agents/missing").status_code == 404


def test_protected_file_cannot_be_imported_as_material(server):
    protected = server["root"] / "config.json"
    response = request(server, "POST", "/api/conversations", body={"instruction": "核查材料", "workspace_id": "office",
        "agent_id": "xiaowen", "client_request_id": uuid.uuid4().hex, "import_files": [str(protected)]})
    assert response.status_code == 422
    assert "PROTECTED_PATH" in response.text


def test_local_bearer_cannot_be_delegated_to_connector(server):
    endpoint = str(server["client"].base_url).rstrip("/")
    response = request(server, "POST", "/api/connectors", body={"change_id": uuid.uuid4().hex, "expected_revision": 0,
        "workspace_id": "office", "name": "本地治理凭证委派核查", "type": "http", "credential": "Bearer " + server["token"],
        "config": {"url": endpoint, "allowed_hosts": ["127.0.0.1"], "allowed_ports": [server["client"].base_url.port], "allow_loopback": True}})
    assert response.status_code == 422
    assert "CREDENTIAL_REJECTED" in response.text


def test_resource_lists_use_bound_pagination(server):
    for endpoint in ("workspaces", "agents", "skills"):
        result = request(server, "GET", f"/api/{endpoint}?limit=1")
        assert result.status_code == 200, result.text
        page = result.json()["data"]
        assert len(page["items"]) == 1
        if page["next_after"]:
            next_page = request(server, "GET", f'/api/{endpoint}?limit=1&after={page["next_after"]}')
            assert next_page.status_code == 200
            assert next_page.json()["data"]["items"][0]["id"] != page["items"][0]["id"]
            assert request(server, "GET", f'/api/{endpoint}?workspace_id=analytics&after={page["next_after"]}').status_code == 422


def test_skill_resource_changes_use_immutable_version_service(server):
    create_body = change(0, workspace_id="office", agent_id="xiaowen", name="接口核查技能", description="核查实际治理接口",
        text="# 接口核查技能\n核对真实状态与材料来源。", basis="所有者提供实际技能正文", files={"references/check.md": "核查HTTP和SQLite实际结果。"})
    created = request(server, "POST", "/api/skills", body=create_body)
    assert created.status_code == 200, created.text
    resource = created.json()["data"]
    skill_id = resource["id"]
    assert request(server, "GET", f"/api/skills/{skill_id}").status_code == 200
    first = request(server, "GET", f"/api/skills/{skill_id}/versions").json()["data"]["items"][0]
    patch = change(resource["revision"], name="接口核查流程", description="核查治理状态与原始依据")
    edited = request(server, "PATCH", f"/api/skills/{skill_id}", body=patch)
    assert edited.status_code == 200, edited.text
    changed = edited.json()["data"]
    assert changed["name"] == "接口核查流程"
    assert request(server, "PATCH", f"/api/skills/{skill_id}", body=patch).json()["data"] == changed
    assert request(server, "GET", f'/api/skills/{skill_id}/versions/{first["id"]}').json()["data"] == first
    assert len(request(server, "GET", f"/api/skills/{skill_id}/versions").json()["data"]["items"]) == 2
    archived = request(server, "DELETE", f"/api/skills/{skill_id}", body=change(changed["revision"]))
    assert archived.status_code == 200 and archived.json()["data"]["status"] == "archived"
    restored = request(server, "PATCH", f"/api/skills/{skill_id}", body=change(archived.json()["data"]["revision"], status="active"))
    assert restored.status_code == 200 and restored.json()["data"]["status"] == "active"
    disabled = request(server, "PATCH", f"/api/skills/{skill_id}", body=change(restored.json()["data"]["revision"], status="disabled"))
    assert disabled.status_code == 200 and disabled.json()["data"]["status"] == "disabled"
    assert request(server, "PATCH", f"/api/skills/{skill_id}", body=change(disabled.json()["data"]["revision"], status="active")).status_code == 200
    assert request(server, "POST", "/api/skills", body=create_body).json()["data"] == resource
    assert request(server, "GET", "/api/skills/missing").status_code == 404


def test_organization_and_admin_workspace_management(server):
    admin = demo(server, "wangming")
    organization = request(server, "GET", "/api/organization").json()["data"]
    assert request(server, "PATCH", "/api/organization", identity=admin, user="wangming", body=change(organization["revision"], name="无权修改组织")).status_code == 403
    modified = request(server, "PATCH", "/api/organization", body=change(organization["revision"], name="真实治理科技有限公司"))
    assert modified.status_code == 200
    workspace = request(server, "POST", "/api/workspaces", identity=admin, user="wangming", body=change(0, name="管理员创建工作区"))
    assert workspace.status_code == 200
    resource = workspace.json()["data"]
    assert request(server, "PATCH", f'/api/workspaces/{resource["id"]}', identity=admin, user="wangming", body=change(1, status="disabled")).status_code == 200
    assert request(server, "PATCH", f'/api/workspaces/{resource["id"]}', identity=admin, user="wangming", body=change(2, status="active")).status_code == 200


def test_governance_stream_uses_current_role_and_closes_after_revocation(server):
    identity = demo(server, "wangming")
    ready = threading.Event()
    ended = threading.Event()
    observed = []
    def consume():
        with httpx.Client(base_url=server["url"], headers={"Authorization": "Bearer " + server["token"], "X-AgentCrew-Identity": identity}, timeout=30) as client:
            with client.stream("GET", "/api/governance/stream?workspace_id=office&from=0") as response:
                assert response.status_code == 200
                ready.set()
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        observed.append(json.loads(line[5:].strip()))
        ended.set()
    reader = threading.Thread(target=consume)
    reader.start()
    assert ready.wait(10)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not observed:
        time.sleep(0.01)
    assert observed
    assert all(row["payload"]["scope"]["workspace_id"] == "office" for row in observed)
    response = request(server, "PUT", "/api/workspaces/office/members/wangming", body=change(1, enabled=False))
    assert response.status_code == 200
    assert ended.wait(5)
    reader.join(5)
    assert request(server, "GET", "/api/agents/xiaowen", identity=identity, user="wangming").status_code == 403
    assert request(server, "PUT", "/api/workspaces/office/members/wangming", body=change(2, enabled=True)).status_code == 200
    server["requests"].append({"method": "GET", "path": "/api/governance/stream?workspace_id=office&from=0", "effective_user": "wangming", "status": 200,
        "response": {"actual_frames": observed, "closed_after_revocation": ended.is_set()}})


def test_actual_responses_match_m2_openapi_schema(server):
    document = yaml.safe_load((Path(__file__).resolve().parents[2] / "docs" / "contracts" / "openapi.yaml").read_text())
    checks = []
    for path, item in document["paths"].items():
        route = compile_path(path)[0]
        for method, operation in item.items():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            for actual in server["requests"]:
                if actual["method"].lower() != method or route.fullmatch(urlsplit(actual["path"]).path) is None:
                    continue
                response = operation["responses"].get(str(actual["status"]))
                if response is None or actual["response"] is None:
                    continue
                if "$ref" in response:
                    response = document["components"]["responses"][response["$ref"].rsplit("/", 1)[1]]
                schema = response.get("content", {}).get("application/json", {}).get("schema")
                if schema is None:
                    continue
                Draft202012Validator({**schema, "components": document["components"]}, format_checker=FormatChecker()).validate(actual["response"])
                checks.append({"operation_id": operation["operationId"], "method": method, "actual_path": actual["path"], "status": actual["status"]})
    assert {"createWorkspace", "editWorkspace", "disableWorkspace", "createAgent", "editAgent", "disableAgent", "createGovernanceSkill",
        "editGovernanceSkill", "archiveGovernanceSkill", "listWorkspaces", "listAgents", "listGovernanceSkills"}.issubset({row["operation_id"] for row in checks})
    (server["root"] / "schema-checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n")


def test_employee_model_slot_is_used_in_actual_request(server):
    response = request(server, "POST", "/api/agents", body=change(0, workspace_id="office", name="真实辅助模型员工",
        spec={"position": "按照实际指令简短回复，遵守当前权限", "model_slot": "aux", "skill_ids": [], "connector_ids": []}))
    assert response.status_code == 200
    employee = response.json()["data"]
    soul = request(server, "POST", f'/api/memory/stores/soul/{employee["id"]}?workspace_id=office&agent_id={employee["id"]}',
        body={"change_id": uuid.uuid4().hex, "expected_revision": 0, "basis": "所有者明确配置辅助槽员工岗位",
            "text": "我是负责核查当前指令的数字员工，使用已经配置的模型槽，简短报告真实处理结果。"})
    assert soul.status_code == 200
    task = request(server, "POST", "/api/conversations", body={"workspace_id": "office", "agent_id": employee["id"],
        "instruction": "仅用简短文字确认收到本条指令。", "client_request_id": uuid.uuid4().hex})
    assert task.status_code == 201
    task_id = task.json()["data"]["task_run_id"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        observed = request(server, "GET", f"/api/task-runs/{task_id}/events?after_seq=0&limit=500").json()["data"]["items"]
        if any(row["type"] in {"run.completed", "run.failed"} for row in observed):
            break
        time.sleep(0.05)
    started = [row for row in observed if row["type"] == "llm.request_started"]
    assert started and all(row["payload"]["model_slot"] == "aux" for row in started)
    assert any(row["type"] == "run.completed" for row in observed)


def test_remaining_management_operations_real_http(server, persistent_http):
    employee = request(server, "POST", "/api/agents", body=change(0, workspace_id="office", name="完整接口核查员工",
        spec={"position": "核查实际管理接口", "model_slot": "main", "skill_ids": [], "connector_ids": []})).json()["data"]
    connector_body = change(0, workspace_id="office", name="真实持久化接口", type="http",
        config={"url": persistent_http["url"], "allowed_hosts": ["127.0.0.1"], "allowed_ports": [persistent_http["port"]], "allow_loopback": True})
    connector_response = request(server, "POST", "/api/connectors", body=connector_body)
    assert connector_response.status_code == 200
    connector = connector_response.json()["data"]
    connector_id = connector["id"]
    assert request(server, "GET", "/api/connectors").status_code == 200
    assert request(server, "GET", f"/api/connectors/{connector_id}").status_code == 200
    assert request(server, "POST", f"/api/connectors/{connector_id}/validate").status_code == 200
    assert request(server, "PATCH", f"/api/connectors/{connector_id}", body=change(1, name="更新持久化接口")).status_code == 200
    grant_body = {"change_id": uuid.uuid4().hex, "resource_type": "connector", "resource_id": connector_id,
        "grantee_type": "agent", "grantee_id": employee["id"]}
    grant_response = request(server, "POST", "/api/grants", body=grant_body)
    assert grant_response.status_code == 200
    grant = grant_response.json()["data"]
    assert request(server, "GET", "/api/grants").status_code == 200
    assert request(server, "DELETE", f'/api/grants/{grant["id"]}', body=change(grant["revision"])).status_code == 200
    assert request(server, "DELETE", f"/api/connectors/{connector_id}", body=change(2)).status_code == 200
    rule_response = request(server, "POST", f'/api/agents/{employee["id"]}/permission-rules', body={"change_id": uuid.uuid4().hex,
        "tool_name": "bash", "pattern": "printf actual-contract", "effect": "deny"})
    assert rule_response.status_code == 200
    rule = rule_response.json()["data"]
    assert request(server, "GET", f'/api/agents/{employee["id"]}/permission-rules').status_code == 200
    assert request(server, "DELETE", f'/api/agents/{employee["id"]}/permission-rules/{rule["id"]}', body=change(rule["revision"])).status_code == 200
    memberships = request(server, "GET", "/api/memberships")
    assert memberships.status_code == 200
    membership = next(row for row in memberships.json()["data"]["items"] if row["user_id"] == "lilei")
    assert request(server, "PATCH", f'/api/memberships/{membership["id"]}/role', body=change(membership["revision"], role="member")).status_code == 200
    skill = request(server, "POST", "/api/skills", body=change(0, workspace_id="office", agent_id="xiaowen", name="契约版本材料",
        description="核查版本发布与正文", text="# 契约版本材料\n核查当前有效版本。", basis="所有者提供真实版本材料")).json()["data"]
    versions = request(server, "GET", f'/api/skills/{skill["id"]}/versions').json()["data"]["items"]
    version = request(server, "POST", f'/api/skills/{skill["id"]}/versions', body={"change_id": uuid.uuid4().hex, "expected_revision": 1,
        "basis": "所有者更新版本材料", "text": "# 契约版本材料\n按实际HTTP与SQLite核查当前有效版本。"})
    assert version.status_code == 200
    assert request(server, "GET", f'/api/skills/{skill["id"]}/versions/{versions[0]["id"]}').status_code == 200
    for endpoint in ("/api/audit", "/api/audit/export", "/api/identity"):
        assert request(server, "GET", endpoint).status_code == 200
    assert request(server, "POST", "/api/audit/verify").status_code == 200


def test_governance_stream_private_authorization_is_hidden(authorized):
    service, sessions, approvals, context, created = authorized
    runtime = RuntimeState(log=logging.getLogger("governance-test"), data_dir=service.data_dir,
        db=service.db, identities=sessions.identities)
    async def check():
        task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
            invocation=ToolInvocation("private-authorization-event", "write_file", {"path": "private-auth.txt", "content": "本人任务内容"}), ctx=context))
        card = await request_card(service, "private-authorization-event")
        await approvals.submit("private-authorization-event", "allow_once", card["input_hash"], RequestIdentity("owner", "owner"))
        assert (await task).ok
        assert (await approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
            invocation=ToolInvocation("private-read-authorization", "read_file", {"path": "private-auth.txt"}), ctx=context)).ok
        rows = service.db.read_conn.execute("SELECT * FROM run_events WHERE type='governance.authorization_checked' AND task_run_id=?", (created["task_run_id"],)).fetchall()
        assert rows
        assert all(not visible(runtime, _row_to_event(row), RequestIdentity("owner", "lilei", True), "office") for row in rows)
    asyncio.run(check())


def test_skill_disable_event_affects_bound_execution(authorized, governed):
    service, sessions, approvals, context, created = authorized
    _, memory, _, _, _ = governed
    runtime = RuntimeState(log=logging.getLogger("governance-test"), data_dir=service.data_dir, db=service.db,
        governance=service, identities=sessions.identities, memory=memory)
    skill = service.db.read_conn.execute("SELECT id,revision FROM skills WHERE workspace_id='office' ORDER BY name LIMIT 1").fetchone()
    async def check():
        response = await Management(runtime).skill_update(RequestIdentity("owner", "owner"), skill[0], change(skill[1], status="disabled"))
        assert response["status"] == "disabled"
        row = service.db.read_conn.execute("SELECT * FROM run_events WHERE type='governance.skill_version_published' AND json_extract(payload,'$.resource_id')=? ORDER BY global_seq DESC LIMIT 1", (skill[0],)).fetchone()
        assert approvals.grants.affects(_row_to_event(row), created["task_run_id"])
    asyncio.run(check())


def test_skill_disable_cancels_actual_dispatched_subprocess(server):
    soul_path = "/api/memory/stores/soul/xiaowen?workspace_id=office&agent_id=xiaowen"
    before = request(server, "GET", soul_path).json()["data"]
    assert request(server, "POST", soul_path, body={"change_id": uuid.uuid4().hex, "expected_revision": before["revision"],
        "basis": "所有者配置实际资源禁用核查岗位", "text": "根据用户明确的合法工具指令执行操作，在人工审批之前等待。核查真实效果，遵守当前授权和资源状态。"}).status_code == 200
    command = "printf '%s' \"$$\" > skill-disable-pid.txt; printf 'actual' > skill-disable-effect.txt; sleep 120; printf 'forbidden' > skill-disable-delayed.txt"
    created = request(server, "POST", "/api/conversations", body={"workspace_id": "office", "agent_id": "xiaowen",
        "instruction": f"调用bash执行这条完整命令，要求审批时等待决定：{command}", "client_request_id": uuid.uuid4().hex})
    assert created.status_code == 201
    task_id = created.json()["data"]["task_run_id"]
    marker = server["root"] / "workspaces" / "office" / "files" / "skill-disable-effect.txt"
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and not marker.exists():
        for card in request(server, "GET", f"/api/task-runs/{task_id}/approvals").json()["data"]:
            if not card["stale"]:
                assert request(server, "POST", f'/api/tool-approvals/{card["call_id"]}', body={"decision": "allow_once", "input_hash": card["input_hash"]}).status_code == 200
        time.sleep(0.05)
    assert marker.read_text() == "actual"
    pid = int((marker.parent / "skill-disable-pid.txt").read_text())
    resources = request(server, "GET", "/api/skills?workspace_id=office&limit=200").json()["data"]["items"]
    skill = next(row for row in resources if row["name"] == "文档模板")
    disabled = request(server, "PATCH", f'/api/skills/{skill["id"]}', body=change(skill["revision"], status="disabled"))
    assert disabled.status_code == 200
    connection = sqlite3.connect(server["root"] / "agentcrew.db")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if connection.execute("SELECT status FROM task_runs WHERE id=?", (task_id,)).fetchone()[0] == "waiting_verification":
            break
        time.sleep(0.01)
    assert connection.execute("SELECT status FROM task_runs WHERE id=?", (task_id,)).fetchone()[0] == "waiting_verification"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not (marker.parent / "skill-disable-delayed.txt").exists()
    call = connection.execute("SELECT call_id FROM tool_calls WHERE task_run_id=? AND status='pending_verification'", (task_id,)).fetchone()[0]
    assert request(server, "POST", f"/api/tool-calls/{call}/verification", body={"verdict": "confirmed_executed", "note": "实际原文件存在，子进程已退出，延迟写入未产生"}).status_code == 200
    assert request(server, "PATCH", f'/api/skills/{skill["id"]}', body=change(disabled.json()["data"]["revision"], status="active")).status_code == 200
    connection.close()
    (server["root"] / "skill-disable-execution.json").write_text(json.dumps({"task_run_id": task_id, "skill_id": skill["id"],
        "call_id": call, "pid": pid, "actual_effect": marker.read_text(), "process_gone": True, "delayed_write": False}, ensure_ascii=False, indent=2) + "\n")


def test_skill_management_remains_available_after_original_employee_disabled(server):
    original = request(server, "POST", "/api/agents", body=change(0, workspace_id="office", name="技能原始创建员工",
        spec={"position": "核查原始材料", "model_slot": "main", "skill_ids": [], "connector_ids": []})).json()["data"]
    skill_response = request(server, "POST", "/api/skills", body=change(0, workspace_id="office", agent_id=original["id"], name="独立工作区技能",
        description="验证工作区级技能管理", text="核查当前工作区的真实材料。", basis="所有者创建独立治理资源"))
    assert skill_response.status_code == 200
    skill = skill_response.json()["data"]
    assert request(server, "POST", "/api/grants", body={"change_id": uuid.uuid4().hex, "resource_type": "skill", "resource_id": skill["id"],
        "grantee_type": "agent", "grantee_id": "xiaowen"}).status_code == 200
    assert request(server, "DELETE", f'/api/agents/{original["id"]}', body=change(original["revision"])).status_code == 200
    edited = request(server, "PATCH", f'/api/skills/{skill["id"]}', body=change(skill["revision"], description="原员工禁用后仍可合法管理"))
    assert edited.status_code == 200, edited.text
    assert request(server, "POST", f'/api/skills/{skill["id"]}/versions', body={"change_id": uuid.uuid4().hex,
        "expected_revision": 2, "basis": "工作区所有者更新独立技能版本", "text": "原员工禁用后继续核查工作区真实材料。"}).status_code == 200


def test_all_m2_operations_require_bearer(server):
    document = yaml.safe_load((Path(__file__).resolve().parents[2] / "docs/contracts/openapi.yaml").read_text())
    checks = []
    with httpx.Client(base_url=server["url"]) as anonymous:
        for path, item in document["paths"].items():
            names = compile_path(path)[2]
            concrete = path.format(**{name: "office" for name in names})
            for method, operation in item.items():
                if not isinstance(operation, dict) or not operation.get("x-domain-card", "").startswith("M2-"):
                    continue
                response = anonymous.request(method, concrete, json={} if method != "get" else None)
                assert response.status_code == 401
                checks.append({"operation_id": operation["operationId"], "status": response.status_code})
                server["requests"].append({"method": method.upper(), "path": concrete, "request": None, "status": response.status_code,
                    "effective_user": "unauthenticated", "response": response.json()})
    assert len(checks) == 44
    (server["root"] / "authentication-matrix.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n")
