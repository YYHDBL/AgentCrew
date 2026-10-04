"""M2-07：真实规则、文件边界、活动审批及HTTP管理。"""

import asyncio
import json
import sys
import hashlib
import os
import subprocess
import time
from pathlib import Path
from dataclasses import replace

import pytest

from agentcrew_core.tools import PermissionRule, ToolInvocation, build_default_registry, evaluate_gate
from agentcrew_server.approvals import ApprovalStale
from test_approvals import Assembly, AGENT, CONV, RUN, _ctx, _wait_for_request
from test_governance_identity import server, request, demo
from test_governance_skills import governed
from test_governance_resources import resources
from agentcrew_core.events import RunEventType as T
from agentcrew_core.governance import RequestIdentity
from agentcrew_core.tools import ToolScheduler
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.governance.grants import Grants
from agentcrew_server.governance.rules import Rules
from agentcrew_server.governance.connectors import Connectors
from agentcrew_server.governance.resources import GovernanceError
from agentcrew_server.memory.jobs import MemoryJobs
from agentcrew_server.bus import EventBus
from agentcrew_core.tools.scheduler import input_hash
from test_governance_connectors import create as create_connector
from test_governance_grants import live as live_model, http as actual_http, sql as actual_sql, task as model_task, until as actual_until


def evidence(service, filename, actual):
    queries = ["SELECT id,agent_id,tool_name,pattern,effect,created_by_user_id,revoked_at FROM agent_permission_rules ORDER BY id",
        "SELECT * FROM governance_rule_owners ORDER BY rule_id", "SELECT call_id,task_run_id,tool_name,input_hash,status,dispatched_at FROM tool_calls ORDER BY prepared_at",
        "SELECT global_seq,attempt_no,type,task_run_id FROM run_events ORDER BY global_seq",
        "SELECT seq,actor_type,actor_id,action,resource_type,resource_id,hash FROM audit_log ORDER BY seq"]
    value = {**actual, "queries": [{"sql": query, "rows": [dict(row) for row in service.db.read_conn.execute(query)]} for query in queries]}
    (service.data_dir / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@pytest.fixture
def authorized(governed):
    service, memory, versions, sessions, skills = governed
    grants = Grants(service, sessions.identities)
    approvals = ApprovalService(service.db, service.events, service.data_dir / "chain-head.txt")
    approvals.grants = grants
    approvals.identities = sessions.identities
    approvals.rules = Rules(service, sessions.identities)
    approvals.scheduler = ToolScheduler(build_default_registry(), gate=approvals.gate)
    service.events.authorization_checker = grants.check_dispatch
    async def start():
        await approvals.rules.revoke(RequestIdentity("owner", "owner"), "xiaowen", "seed-office-write", {"change_id": "reset-seed-rule-for-approval", "expected_revision": 1})
        created = await sessions.create_conversation(instruction="当前规则真实执行", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.RUN_STARTED, payload={"attempt_no": 1, "attempt_id": "rules-attempt"})
        return created
    created = asyncio.run(start())
    context = sessions.build_work_context(created["conversation"]["id"], created["task_run_id"])
    return service, sessions, approvals, context, created


async def request_card(service, call_id):
    for _ in range(100):
        row = service.db.read_conn.execute("SELECT payload FROM run_events WHERE type='permission.requested' AND json_extract(payload,'$.tool_call_id')=?", (call_id,)).fetchone()
        if row:
            return json.loads(row[0])
        await asyncio.sleep(0.01)
    raise AssertionError("实际审批未产生")


@pytest.mark.parametrize("decision,effect,written", [("allow_always", "allow", True), ("reject_always", "deny", False)])
def test_permanent_decision_owns_rule_and_emits_same_transaction(authorized, decision, effect, written):
    service, sessions, approvals, context, created = authorized
    async def check():
        target = Path(context.cwd) / "new-directory/result.txt"
        task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"],
            agent_id="xiaowen", invocation=ToolInvocation("permanent-human-rule", "write_file", {"path": str(target), "content": "真实规则正文"}), ctx=context))
        card = await request_card(service, "permanent-human-rule")
        await approvals.submit(card["tool_call_id"], decision, card["input_hash"], RequestIdentity("owner", "owner"))
        assert (await task).ok == written and target.exists() == written
        row = service.db.read_conn.execute("SELECT r.id,o.revision FROM agent_permission_rules r JOIN governance_rule_owners o ON o.rule_id=r.id WHERE r.pattern=?", (str(target.parent),)).fetchone()
        assert row and row["revision"] == 1
        assert approvals.rules.get(row["id"], "xiaowen")["effect"] == effect
        event = service.db.read_conn.execute("SELECT payload FROM run_events WHERE type='governance.rule_changed' AND json_extract(payload,'$.resource_id')=?", (row["id"],)).fetchone()
        assert event and json.loads(event[0])["actor_id"] == "owner"
        assert (await approvals.submit(card["tool_call_id"], decision, card["input_hash"], RequestIdentity("owner", "owner")))["idempotent_replay"]
        evidence(service, "permanent-rule.json", {"task_run_id": created["task_run_id"], "call_id": card["tool_call_id"], "decision": decision, "written": written})
    asyncio.run(check())


def test_deny_committed_after_allow_prevents_serial_dispatch(authorized):
    service, sessions, approvals, context, created = authorized
    approvals.grants.approvals = approvals
    async def check():
        target = Path(context.cwd) / "serial-deny.txt"
        for _ in range(4):
            await approvals.scheduler._sem.acquire()
        try:
            task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
                invocation=ToolInvocation("serial-new-deny", "write_file", {"path": str(target), "content": "禁止派发"}), ctx=context))
            card = await request_card(service, "serial-new-deny")
            await approvals.submit(card["tool_call_id"], "allow_once", card["input_hash"], RequestIdentity("owner", "owner"))
            await approvals.rules.create(RequestIdentity("owner", "owner"), "xiaowen", {"change_id": "deny-before-dispatched", "tool_name": "write_file", "pattern": str(context.cwd), "effect": "deny"})
        finally:
            for _ in range(4):
                approvals.scheduler._sem.release()
        result = await task
        assert not result.ok and not target.exists()
        assert service.db.read_conn.execute("SELECT count(*) FROM tool_calls WHERE call_id='serial-new-deny' AND dispatched_at IS NOT NULL").fetchone()[0] == 0
    asyncio.run(check())


def test_revoked_auto_allow_cannot_supply_dispatch_approval(authorized):
    service, sessions, approvals, context, created = authorized
    approvals.grants.approvals = approvals
    async def check():
        target = Path(context.cwd) / "revoked-auto-allow.txt"
        rule = await approvals.rules.create(RequestIdentity("owner", "owner"), "xiaowen", {"change_id": "auto-allow-before-dispatch", "tool_name": "write_file", "pattern": str(context.cwd), "effect": "allow"})
        for _ in range(4):
            await approvals.scheduler._sem.acquire()
        try:
            task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
                invocation=ToolInvocation("revoked-auto-call", "write_file", {"path": str(target), "content": "禁止自动派发"}), ctx=context))
            for _ in range(100):
                if service.db.read_conn.execute("SELECT 1 FROM audit_log WHERE resource_id='revoked-auto-call' AND action='permission.rule_allowed'").fetchone():
                    break
                await asyncio.sleep(0.01)
            assert service.db.read_conn.execute("SELECT 1 FROM audit_log WHERE resource_id='revoked-auto-call' AND action='permission.rule_allowed'").fetchone()
            await approvals.rules.revoke(RequestIdentity("owner", "owner"), "xiaowen", rule["id"], {"change_id": "revoke-auto-allow", "expected_revision": 1})
        finally:
            for _ in range(4):
                approvals.scheduler._sem.release()
        assert not (await task).ok and not target.exists()
    asyncio.run(check())


def test_current_authorization_accepts_direct_question_dispatch(authorized):
    service, sessions, approvals, context, created = authorized
    approvals.grants.approvals = approvals
    async def check():
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.TOOL_PREPARED,
            attempt_no=1,
            payload={"call_id": "direct-ask", "tool_name": "ask_user", "input": {"question": "是否继续当前合法任务？"}, "input_hash": "own-question-input", "risk_level": "low", "side_effect_class": "verifiable"})
        event = await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.TOOL_DISPATCHED, payload={"call_id": "direct-ask"})
        assert event.type == T.TOOL_DISPATCHED
    asyncio.run(check())


def test_previous_attempt_question_cannot_dispatch(authorized):
    service, sessions, approvals, context, created = authorized
    approvals.grants.approvals = approvals
    async def check():
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.TOOL_PREPARED, attempt_no=1,
            payload={"call_id": "old-direct-ask", "tool_name": "ask_user", "input": {"question": "旧尝试提问"}, "input_hash": "own-question-input", "risk_level": "low", "side_effect_class": "verifiable"})
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.RUN_INTERRUPTED, payload={"reason": "自有任务生命周期核查"})
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.RUN_RESUMED, attempt_no=2,
            payload={"attempt_no": 2, "attempt_id": "rules-second-attempt", "resume_reason": "user_requested"})
        with pytest.raises(ApprovalStale):
            await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.TOOL_DISPATCHED, payload={"call_id": "old-direct-ask"})
    asyncio.run(check())


def test_repeat_old_allow_revocation_preserves_new_rule(authorized):
    service, sessions, approvals, context, created = authorized
    async def check():
        request = {"change_id": "original-auto-rule", "tool_name": "write_file", "pattern": str(context.cwd), "effect": "allow"}
        old = await approvals.rules.create(RequestIdentity("owner", "owner"), "xiaowen", request)
        await approvals.rules.revoke(RequestIdentity("owner", "owner"), "xiaowen", old["id"], {"change_id": "original-rule-revoke", "expected_revision": 1})
        new = await approvals.rules.create(RequestIdentity("owner", "owner"), "xiaowen", {**request, "change_id": "new-auto-rule"})
        await approvals.rules.revoke(RequestIdentity("owner", "owner"), "xiaowen", old["id"], {"change_id": "repeat-original-revoke", "expected_revision": 2})
        from agentcrew_server.db.projections import _row_to_event
        event = _row_to_event(service.db.read_conn.execute("SELECT * FROM run_events WHERE type='governance.rule_changed' ORDER BY global_seq DESC LIMIT 1").fetchone())
        assert event.payload["revocation_changed"] is False
        assert approvals.grants.affects(event, created["task_run_id"]) is False
        assert approvals.rules.get(new["id"], "xiaowen")["revoked_at"] is None
    asyncio.run(check())


def test_background_file_deny_and_dispatch_transaction(governed):
    service, memory, versions, sessions, skills = governed
    grants = Grants(service, sessions.identities)
    rules = Rules(service, sessions.identities)
    jobs = MemoryJobs(memory, EventBus(), sessions._settings)
    jobs.identities, jobs.grants, jobs.rules = sessions.identities, grants, rules
    jobs.review.sessions.identities = sessions.identities
    async def check():
        created = await sessions.create_conversation(instruction="自有材料的后台读取规则", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        tid, cid = created["task_run_id"], created["conversation"]["id"]
        await service.events.append(task_run_id=tid, conversation_id=cid, type=T.RUN_STARTED, payload={"attempt_no": 1, "attempt_id": "background-source"})
        target = Path(sessions.build_work_context(cid, tid).cwd) / "employee-denied.txt"
        target.write_text("后台也必须遵守当前员工规则")
        done = await service.events.append(task_run_id=tid, conversation_id=cid, type=T.RUN_COMPLETED, payload={"final_text": target.read_text()})
        job_id = await jobs.review.enqueue("memory_review", "denied-background-material", done.global_seq, tid)
        await jobs._status(job_id, "running")
        await rules.create(RequestIdentity("owner", "owner"), "xiaowen", {"change_id": "deny-background-read", "tool_name": "read_file", "pattern": str(target.parent), "effect": "deny"})
        context = jobs.review.context(jobs.get(job_id))
        async def sink(kind, payload):
            await service.events.channel.execute(lambda conn: jobs._call_tx(conn, job_id, kind, payload))
        context.emit = sink
        call = ToolInvocation("background-denied-read", "read_file", {"path": str(target)})
        result = await jobs.review.execute(job_id, call, context, jobs.review.scheduler(jobs.get(job_id)))
        assert not result.ok and result.error == "PERMISSION_DENIED"
        assert service.db.read_conn.execute("SELECT count(*) FROM memory_job_calls WHERE job_id=? AND type='tool.dispatched'", (job_id,)).fetchone()[0] == 0
        await service.events.channel.execute(lambda conn: jobs._call_tx(conn, job_id, "tool.prepared", {"call_id": "background-direct", "tool_name": "read_file", "input": call.input, "input_hash": input_hash(call.input)}))
        with pytest.raises(GovernanceError):
            await service.events.channel.execute(lambda conn: jobs._call_tx(conn, job_id, "tool.dispatched", {"call_id": "background-direct"}))
        evidence(service, "background-deny.json", {"job_id": job_id, "task_run_id": tid, "call_id": call.call_id,
            "file_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "mtime_ns": target.stat().st_mtime_ns})
        await jobs.shutdown()
    asyncio.run(check())


@pytest.mark.parametrize("decision,written", [("allow_always", True), ("reject_always", False)])
def test_real_mcp_permanent_decision(authorized, decision, written):
    service, sessions, approvals, _context, _created = authorized
    connectors = Connectors(service, sessions.identities, approvals.grants)
    approvals.grants.connectors = connectors
    approvals.connectors = connectors
    async def check():
        config = {"transport": "stdio", "command": sys.executable, "args": [str(Path(__file__).with_name("governance_mcp_server.py"))],
            "startup_files": [str(Path(__file__).with_name("governance_mcp_server.py"))],
            "allowed_hosts": [], "allowed_ports": [], "allow_loopback": False}
        connector, grant = await create_connector(service, approvals.grants, connectors, "mcp", config)
        created = await sessions.create_conversation(instruction="真实MCP永久规则", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.RUN_STARTED, payload={"attempt_no": 1, "attempt_id": "mcp-rules-attempt"})
        context = sessions.build_work_context(created["conversation"]["id"], created["task_run_id"])
        await connectors.validate(RequestIdentity("owner", "owner"), connector["id"], context)
        name = connectors.stable_name(connector["id"], "persist")
        task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
            invocation=ToolInvocation("permanent-mcp", name, {"value": "真实永久规则材料"}), ctx=context))
        card = await request_card(service, "permanent-mcp")
        await approvals.submit(card["tool_call_id"], decision, card["input_hash"], RequestIdentity("owner", "owner"))
        assert (await task).ok == written
        second = await approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
            invocation=ToolInvocation("permanent-mcp-next", name, {"value": "后续真实材料"}), ctx=context)
        assert second.ok == written
        target = Path(context.cwd) / "mcp-operations.jsonl"
        assert target.exists() == written
        if written:
            assert len(target.read_text().splitlines()) == 2
        assert service.db.read_conn.execute("SELECT count(*) FROM run_events WHERE task_run_id=? AND type='permission.requested'", (created["task_run_id"],)).fetchone()[0] == 1
        evidence(service, "mcp-rule.json", {"task_run_id": created["task_run_id"], "connector_id": connector["id"], "decision": decision, "written": written,
            "actual_operation_count": len(target.read_text().splitlines()) if written else 0})
    asyncio.run(check())


def test_authorized_external_scope_symlink_and_scope_denial(authorized):
    service, sessions, approvals, context, created = authorized
    async def check():
        external = service.data_dir / "approved-external"
        external.mkdir()
        outside = service.data_dir / "unauthorized-external"
        outside.mkdir()
        outside_file = outside / "untouched.txt"
        outside_file.write_text("范围外原正文")
        before = {"sha256": hashlib.sha256(outside_file.read_bytes()).hexdigest(), "mtime_ns": outside_file.stat().st_mtime_ns}
        expanded = replace(context, scope=[*context.scope, external], write_scope=[*context.write_scope, external], filesystem=None)
        target = external / "approved.txt"
        task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
            invocation=ToolInvocation("external-approved", "write_file", {"path": str(target), "content": "实际外部目录审批"}), ctx=expanded))
        card = await request_card(service, "external-approved")
        assert card["target"] == str(target)
        await approvals.submit(card["tool_call_id"], "allow_once", card["input_hash"], RequestIdentity("owner", "owner"))
        assert (await task).ok and target.read_text() == "实际外部目录审批"
        alias = Path(context.cwd) / "escape-alias"
        alias.symlink_to(outside, target_is_directory=True)
        for call_id, path in (("scope-denied", outside_file), ("symlink-denied", alias / "untouched.txt")):
            result = await approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
                invocation=ToolInvocation(call_id, "write_file", {"path": str(path), "content": "禁止范围扩大"}), ctx=expanded)
            assert not result.ok
        assert before == {"sha256": hashlib.sha256(outside_file.read_bytes()).hexdigest(), "mtime_ns": outside_file.stat().st_mtime_ns}
        assert service.db.read_conn.execute("SELECT count(*) FROM run_events WHERE task_run_id=? AND type='permission.requested'", (created["task_run_id"],)).fetchone()[0] == 1
        evidence(service, "external-scope.json", {"task_run_id": created["task_run_id"], "call_id": card["tool_call_id"], "approval": card, "outside_preserved": before,
            "external_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "external_mtime_ns": target.stat().st_mtime_ns})
    asyncio.run(check())


def test_actual_sigkill_old_rule_rejected_new_approval_accepted(live_model):
    listed = actual_http(live_model, "GET", "/api/agents/xiaowen/permission-rules").json()["data"]["items"]
    for rule in listed:
        if rule["effect"] == "allow" and rule["tool_name"] == "write_file":
            assert actual_http(live_model, "DELETE", "/api/agents/xiaowen/permission-rules/" + rule["id"], {"change_id": "kill-revoke-" + rule["id"], "expected_revision": rule["revision"]}).status_code == 200
    target = live_model["root"] / "workspaces/office/files/rules-kill.txt"
    run = model_task(live_model, "xiaowen", "office", "请直接执行write_file调用，参数为" + json.dumps({"path": str(target), "content": "真实恢复审批正文"}, ensure_ascii=False) + "。需要审批时等待决定。")
    tid, cid = run["task_run_id"], run["conversation"]["id"]
    old = json.loads(actual_until(live_model, "SELECT payload FROM run_events WHERE task_run_id=? AND type='permission.requested'", (tid,))[-1]["payload"])
    live_model["process"].kill()
    assert live_model["process"].wait(15) == -9
    log = (live_model["root"] / "rules-restart.log").open("w")
    child = subprocess.Popen(live_model["command"], cwd=Path(__file__).resolve().parents[1], env={**os.environ,
        "AGENTCREW_TOKEN": live_model["client"].headers["Authorization"].removeprefix("Bearer "), "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "MEMORY_REQUEST_EVIDENCE": str(live_model["root"] / "requests.jsonl")}, stdout=subprocess.PIPE, stderr=log, text=True)
    live_model["process"] = child
    ready = child.stdout.readline().strip()
    assert ready.startswith("AGENTCREW_READY ")
    live_model["client"].base_url = "http://127.0.0.1:" + str(json.loads(ready.split(" ", 1)[1])["port"])
    assert actual_http(live_model, "POST", "/api/tool-approvals/" + old["tool_call_id"], {"decision": "allow_always", "input_hash": old["input_hash"]}).status_code == 409
    cards = actual_http(live_model, "GET", "/api/task-runs/" + tid + "/approvals").json()["data"]
    assert next(card for card in cards if card["call_id"] == old["tool_call_id"])["stale"]
    assert actual_sql(live_model, "SELECT id FROM agent_permission_rules WHERE pattern=? AND revoked_at IS NULL", (str(target.parent),)) == []
    assert actual_http(live_model, "POST", "/api/task-runs/" + tid + "/resume").status_code == 202
    resumed = actual_until(live_model, "SELECT global_seq FROM run_events WHERE task_run_id=? AND type='run.resumed'", (tid,))[-1]["global_seq"]
    handled, approved = set(), []
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        for row in actual_sql(live_model, "SELECT payload FROM run_events WHERE task_run_id=? AND global_seq>? AND type='permission.requested'", (tid, resumed)):
            card = json.loads(row["payload"])
            if card["tool_call_id"] in handled:
                continue
            call = actual_sql(live_model, "SELECT tool_name,input FROM tool_calls WHERE call_id=?", (card["tool_call_id"],))[0]
            inputs = json.loads(call["input"])
            allowed = call["tool_name"] == "write_file" and Path(inputs["path"]).resolve() == target.resolve()
            decision = "allow_once" if allowed else "reject_once"
            assert actual_http(live_model, "POST", "/api/tool-approvals/" + card["tool_call_id"], {"decision": decision, "input_hash": card["input_hash"]}).status_code == 200
            handled.add(card["tool_call_id"])
            if allowed:
                approved.append(card["tool_call_id"])
        for question in actual_http(live_model, "GET", "/api/conversations/" + cid + "/questions").json()["data"]:
            assert actual_http(live_model, "POST", "/api/questions/" + question["request_id"] + "/answer", {"answer": "请在当前范围内直接使用write_file写入指定文件，内容为真实恢复审批正文，等待新审批。"}).status_code == 200
        terminal = actual_sql(live_model, "SELECT status FROM task_runs WHERE id=? AND status IN ('completed','failed')", (tid,))
        if terminal:
            break
        time.sleep(0.1)
    assert approved and terminal and target.read_text() == "真实恢复审批正文"
    attempts = actual_sql(live_model, "SELECT id,attempt_no,status FROM run_attempts WHERE task_run_id=? ORDER BY attempt_no", (tid,))
    assert len(attempts) == 2
    (live_model["root"] / "rules-sigkill.json").write_text(json.dumps({"task_run_id": tid, "conversation_id": cid, "old_approval": old,
        "new_approved_calls": approved, "attempts": attempts, "terminal": terminal, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "mtime_ns": target.stat().st_mtime_ns}, ensure_ascii=False, indent=2) + "\n")
    log.close()


def test_new_deny_invalidates_waiting_approval(authorized):
    service, sessions, approvals, context, created = authorized
    async def check():
        target = Path(context.cwd) / "new-directory/rejected.txt"
        task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
            invocation=ToolInvocation("new-deny", "write_file", {"path": str(target), "content": "不能写入"}), ctx=context))
        try:
            card = await request_card(service, "new-deny")
            await approvals.rules.create(RequestIdentity("owner", "owner"), "xiaowen", {"change_id": "deny-during-approval", "tool_name": "write_file", "pattern": str(target.parent), "effect": "deny"})
            with pytest.raises(ApprovalStale, match="deny"):
                await approvals.submit(card["tool_call_id"], "allow_once", card["input_hash"], RequestIdentity("owner", "owner"))
            assert not target.exists()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(check())


@pytest.mark.parametrize("name,inputs,pattern,readonly", [
    ("read_file", {"path": "/private/var/agentcrew-materials/a.txt"}, "/private/var/agentcrew-materials", True),
    ("bash", {"command": "pwd"}, "pwd", True),
])
def test_deny_precedes_readonly_metadata(name, inputs, pattern, readonly):
    metadata = build_default_registry().get(name).metadata
    rules = [PermissionRule("xiaowen", name, pattern, effect) for effect in ("allow", "deny")]
    assert evaluate_gate(metadata, inputs, readonly, rules, "xiaowen").action == "deny"


@pytest.mark.parametrize("protected", [False, True])
def test_hard_file_boundary_precedes_approval(tmp_path, protected):
    async def check():
        assembly = Assembly(tmp_path)
        context = _ctx(assembly, tmp_path)
        target = tmp_path / ("ws/protected.txt" if protected else "outside.txt")
        target.write_text("受控原正文")
        if protected:
            context.protected.append(target)
        task = asyncio.create_task(assembly.approvals.run_tool(task_run_id=RUN, conversation_id=CONV,
            agent_id=AGENT, invocation=ToolInvocation("hard-boundary", "write_file", {"path": str(target), "content": "禁止替换"}), ctx=context))
        try:
            await asyncio.sleep(0.1)
            assert assembly.db.read_conn.execute("SELECT count(*) FROM run_events WHERE type='permission.requested'").fetchone()[0] == 0
            result = await asyncio.wait_for(task, 2)
            assert not result.ok and target.read_text() == "受控原正文"
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assembly.close()
    asyncio.run(check())


def test_orphan_approval_cannot_create_rule(tmp_path):
    async def check():
        assembly = Assembly(tmp_path)
        task = asyncio.create_task(assembly.approvals.run_tool(task_run_id=RUN, conversation_id=CONV,
            agent_id=AGENT, invocation=ToolInvocation("orphan-rule", "write_file", {"path": str(tmp_path / "ws/result.txt"), "content": "禁止孤儿写入"}), ctx=_ctx(assembly, tmp_path)))
        call_id = await _wait_for_request(assembly)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ApprovalStale, match="执行方|过期|停止"):
            await assembly.approvals.submit(call_id, "allow_always")
        assert assembly.db.read_conn.execute("SELECT count(*) FROM agent_permission_rules").fetchone()[0] == 0
        assert assembly.db.read_conn.execute("SELECT count(*) FROM run_events WHERE type='permission.resolved'").fetchone()[0] == 0
        assembly.close()
    asyncio.run(check())


def test_rules_actual_http_management_and_member_read(server):
    endpoint = "/api/agents/xiaowen/permission-rules"
    listed = request(server, "GET", endpoint)
    assert listed.status_code == 200, listed.text
    target = server["root"] / "workspaces/office/files/human-managed"
    body = {"change_id": "human-allow-directory", "tool_name": "write_file", "pattern": str(target), "effect": "allow"}
    created = request(server, "POST", endpoint, body=body)
    assert created.status_code == 200, created.text
    rule = created.json()["data"]
    assert rule["pattern"] == str(target.resolve()) and rule["revision"] == 1
    assert request(server, "POST", endpoint, body=body).json() == created.json()
    assert request(server, "POST", endpoint, body={**body, "effect": "deny"}).status_code == 409
    member = demo(server, "lilei")
    assert request(server, "GET", endpoint, user="lilei", identity=member).status_code == 200
    assert request(server, "POST", endpoint, user="lilei", identity=member, body={**body, "change_id": "member-rules"}).status_code == 403
    grants = request(server, "GET", "/api/grants?grantee_id=lilei").json()["data"]["items"]
    access = next(item for item in grants if item["resource_type"] == "agent" and item["resource_id"] == "xiaogang")
    assert request(server, "DELETE", "/api/grants/" + access["id"], body={"change_id": "remove-member-analytics", "expected_revision": access["revision"]}).status_code == 200
    assert request(server, "GET", "/api/agents/xiaogang/permission-rules", user="lilei", identity=member).status_code == 403
    revoke = {"change_id": "revoke-human-rule", "expected_revision": 1}
    revoked = request(server, "DELETE", endpoint + "/" + rule["id"], body=revoke)
    assert revoked.status_code == 200 and revoked.json()["data"]["revoked_at"]
    assert request(server, "DELETE", endpoint + "/" + rule["id"], body=revoke).json() == revoked.json()
    assert request(server, "DELETE", endpoint + "/" + rule["id"], body={**revoke, "change_id": "stale-rule"}).status_code == 409
    first = request(server, "GET", endpoint + "?revoked=true&limit=1").json()["data"]
    assert first["next_after"]
    second = request(server, "GET", endpoint + "?revoked=true&limit=1&after=" + first["next_after"]).json()["data"]
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert request(server, "GET", endpoint + "?limit=1&after=" + first["next_after"]).status_code == 422
