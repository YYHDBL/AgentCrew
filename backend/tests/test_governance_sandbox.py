"""M2-08：实际Seatbelt文件范围、子进程与规范化边界。"""

import asyncio
import hashlib
import json
import shlex
import subprocess
import sys
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest

from agentcrew_core.tools import ToolInvocation, ToolScheduler, WorkContext, build_default_registry
from agentcrew_core.tools.judgment import filesystem_boundary
from agentcrew_core.tools.builtin.seatbelt import seatbelt_profile
from agentcrew_server.tool_outputs import ToolOutputStore
from agentcrew_server.approvals import ApprovalStale
from agentcrew_core.governance import RequestIdentity
from test_governance_rules import authorized, request_card
from test_governance_skills import governed
from test_governance_resources import resources
from test_governance_connectors import persistent_http
from test_governance_connectors import create as create_connector
from agentcrew_server.governance.connectors import Connectors
from agentcrew_server.governance.resources import GovernanceError
from agentcrew_core.events import RunEventType as T


async def approved_call(service, approvals, context, created, invocation):
    task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen", invocation=invocation, ctx=context))
    handled = False
    deadline = time.monotonic() + 10
    while not task.done() and time.monotonic() < deadline:
        row = service.db.read_conn.execute("SELECT payload FROM run_events WHERE type='permission.requested' AND json_extract(payload,'$.tool_call_id')=?", (invocation.call_id,)).fetchone()
        if row and not handled:
            card = json.loads(row[0])
            await approvals.submit(invocation.call_id, "allow_once", card["input_hash"], RequestIdentity("owner", "owner"))
            handled = True
        await asyncio.sleep(0.01)
    return await asyncio.wait_for(task, 5)


def test_real_task_sandbox_boundary_matrix(authorized, persistent_http):
    service, sessions, approvals, original, created = authorized
    outside_dir = service.data_dir / "ordinary-external"
    readonly_dir = service.data_dir / "read-authorized"
    protected_dir = Path(original.cwd) / "protected-copy"
    for directory in (outside_dir, readonly_dir, protected_dir):
        directory.mkdir()
        (directory / "material.txt").write_text("独立安全验收材料")
    (Path(original.cwd) / "inside.txt").write_text("实际合法材料")
    context = replace(original, scope=[*original.scope, readonly_dir], readonly_scope=[readonly_dir], protected=[*original.protected, protected_dir], filesystem=None)
    alias = Path(context.cwd) / "outside-alias"
    alias.symlink_to(outside_dir, target_is_directory=True)
    before = {str(directory): {"sha256": hashlib.sha256((directory / "material.txt").read_bytes()).hexdigest(), "mtime_ns": (directory / "material.txt").stat().st_mtime_ns} for directory in (outside_dir, readonly_dir, protected_dir)}
    cases = [("inside-read", "cat inside.txt", True), ("inside-write", "printf '实际合法写入' > inside-written.txt", True),
        ("outside-read", "cat " + shlex.quote(str(outside_dir / "material.txt")), False),
        ("outside-write", "printf '禁止外部写入' > " + shlex.quote(str(outside_dir / "material.txt")), False),
        ("protected-read", "cat protected-copy/material.txt", False),
        ("protected-write", "printf '禁止保护写入' > protected-copy/material.txt", False),
        ("readonly-read", "cat " + shlex.quote(str(readonly_dir / "material.txt")), True),
        ("readonly-write", "printf '禁止读取目录写入' > " + shlex.quote(str(readonly_dir / "material.txt")), False),
        ("alias-read", "cat outside-alias/material.txt", False),
        ("network-denied", "/usr/bin/curl --silent --show-error --max-time 2 --data '真实禁止网络材料' " + shlex.quote(persistent_http["url"] + "/ordinary"), False)]
    async def check():
        results = []
        for call_id, command, ok in cases:
            result = await approved_call(service, approvals, context, created, ToolInvocation("sandbox-" + call_id, "bash", {"command": command}))
            assert result.ok == ok, (call_id, result)
            results.append({"call_id": "sandbox-" + call_id, "command": command, "ok": result.ok, "output": result.output, "details": result.details})
        assert (Path(context.cwd) / "inside-written.txt").read_text() == "实际合法写入"
        after = {str(directory): {"sha256": hashlib.sha256((directory / "material.txt").read_bytes()).hexdigest(), "mtime_ns": (directory / "material.txt").stat().st_mtime_ns} for directory in (outside_dir, readonly_dir, protected_dir)}
        assert before == after
        with sqlite3.connect(persistent_http["database"]) as connection:
            assert connection.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
        queries = ["SELECT call_id,tool_name,status,input_hash,dispatched_at,completed_at FROM tool_calls ORDER BY prepared_at",
            "SELECT id,attempt_no,status FROM run_attempts ORDER BY started_at", "SELECT id,status FROM memory_jobs ORDER BY created_at",
            "SELECT global_seq,attempt_no,type,task_run_id FROM run_events ORDER BY global_seq", "SELECT seq,actor_id,action,resource_id,hash FROM audit_log ORDER BY seq"]
        record = {"task_run_id": created["task_run_id"], "conversation_id": created["conversation"]["id"], "calls": results, "files_before": before, "files_after": after,
            "actual_http_operations": 0, "upstream_sql": "SELECT count(*) FROM operations",
            "queries": [{"sql": query, "rows": [dict(row) for row in service.db.read_conn.execute(query)]} for query in queries]}
        (service.data_dir / "sandbox-matrix.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    asyncio.run(check())


@pytest.fixture
def sandbox(tmp_path):
    inside = tmp_path / "workspace"
    outside = tmp_path / "outside"
    inside.mkdir()
    (inside / "scope-readable.txt").write_text("合法范围实际读取")
    outside.mkdir()
    original = outside / "ordinary.txt"
    original.write_text("独立普通用户材料")
    context = WorkContext(scope=[inside], protected=[], cwd=inside, artifacts_dir=tmp_path / "artifacts", output_store=ToolOutputStore())
    return context, original


def test_seatbelt_denies_ordinary_scope_outside_read(sandbox):
    context, outside = sandbox
    async def check():
        positive = await ToolScheduler(build_default_registry()).run(ToolInvocation("inside-read-control", "bash", {"command": "/bin/cat scope-readable.txt"}), context)
        assert positive.ok and positive.output == "合法范围实际读取"
        result = await ToolScheduler(build_default_registry()).run(ToolInvocation("outside-read", "bash", {"command": "/bin/cat " + shlex.quote(str(outside))}), context)
        assert not result.ok and "独立普通用户材料" not in result.output
        assert "Operation not permitted" in result.details["stderr"]
    asyncio.run(check())


def test_seatbelt_denies_readonly_directory_write(sandbox):
    context, outside = sandbox
    context.scope.append(outside.parent)
    context.write_scope = [context.cwd]
    before = {"sha256": hashlib.sha256(outside.read_bytes()).hexdigest(), "mtime_ns": outside.stat().st_mtime_ns}
    async def check():
        result = await ToolScheduler(build_default_registry()).run(ToolInvocation("readonly-write", "bash", {"command": "printf '禁止写入' > " + shlex.quote(str(outside))}), context)
        assert not result.ok
        assert before == {"sha256": hashlib.sha256(outside.read_bytes()).hexdigest(), "mtime_ns": outside.stat().st_mtime_ns}
    asyncio.run(check())


def test_child_process_inherits_ordinary_read_boundary(sandbox):
    context, outside = sandbox
    program = "import subprocess; result=subprocess.run(['/bin/cat'," + repr(str(outside)) + "],capture_output=True); print('actual_child_status=' + str(result.returncode)); print(result.stdout.decode()); raise SystemExit(result.returncode)"
    command = shlex.quote(sys.executable) + " -c " + shlex.quote(program)
    async def check():
        result = await ToolScheduler(build_default_registry()).run(ToolInvocation("child-outside-read", "bash", {"command": command}), context)
        assert not result.ok and "独立普通用户材料" not in result.output
        assert "actual_child_status=1" in result.output
    asyncio.run(check())


def test_scope_root_replaced_by_symlink_does_not_expand_authorization(authorized):
    service, sessions, approvals, context, created = authorized
    scope = service.data_dir / "authorized-folder"
    outside = service.data_dir / "outside-folder"
    scope.mkdir()
    outside.mkdir()
    target = outside / "material.txt"
    target.write_text("替换链接后仍然保持原正文")
    before = {"sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "mtime_ns": target.stat().st_mtime_ns}
    context = replace(context, scope=[*context.scope, scope], write_scope=[*context.write_scope, scope], filesystem=None)
    async def check():
        task = asyncio.create_task(approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
            invocation=ToolInvocation("scope-link-swap", "write_file", {"path": str(scope / "material.txt"), "content": "禁止扩大范围"}), ctx=context))
        try:
            card = await request_card(service, "scope-link-swap")
            scope.rename(scope.with_name("original-folder"))
            scope.symlink_to(outside, target_is_directory=True)
            with pytest.raises(ApprovalStale, match="OUT_OF_SCOPE"):
                await approvals.submit(card["tool_call_id"], "allow_once", card["input_hash"])
            assert before == {"sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "mtime_ns": target.stat().st_mtime_ns}
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
    asyncio.run(check())


def test_actual_profile_load_failure_prevents_execution(sandbox):
    context, _outside = sandbox
    target = Path(context.cwd) / "must-not-execute.txt"
    result = subprocess.run(["/usr/bin/sandbox-exec", "-p", "(version invalid)", "/bin/sh", "-c", "printf '禁止执行' > " + shlex.quote(str(target))], capture_output=True, text=True)
    assert result.returncode != 0 and not target.exists()


def test_bash_requires_explicit_authorized_working_directory(sandbox):
    context, _outside = sandbox
    async def check():
        with pytest.raises(ValueError, match="工作目录"):
            await ToolScheduler(build_default_registry()).run(ToolInvocation("missing-cwd", "bash", {"command": "pwd"}), replace(context, cwd=None))
    asyncio.run(check())


def test_rebuilt_context_keeps_saved_scope_after_link_replacement(authorized):
    service, sessions, approvals, _context, _created = authorized
    scope = service.data_dir / "saved-folder"
    outside = service.data_dir / "saved-outside"
    scope.mkdir()
    outside.mkdir()
    (outside / "material.txt").write_text("新上下文不能读取未授权材料")
    async def check():
        created = await sessions.create_conversation(instruction="保存授权目录后重建范围", workspace_id="office", agent_id="xiaowen", folders=[str(scope)], request_identity=RequestIdentity("owner", "owner"))
        scope.rename(scope.with_name("saved-original"))
        scope.symlink_to(outside, target_is_directory=True)
        context = sessions.build_work_context(created["conversation"]["id"], created["task_run_id"])
        result = await ToolScheduler(build_default_registry()).run(ToolInvocation("rebuilt-scope-read", "read_file", {"path": str(scope / "material.txt")}), context)
        assert not result.ok and "新上下文不能读取未授权材料" not in result.output
    asyncio.run(check())


def test_missing_protected_case_variant_denied_by_kernel(sandbox):
    context, _outside = sandbox
    protected = Path(context.cwd) / "not-yet-config.json"
    context = replace(context, protected=[protected], filesystem=None)
    target = protected.with_name("NOT-YET-CONFIG.JSON")
    async def check():
        result = await ToolScheduler(build_default_registry()).run(ToolInvocation("case-protected-write", "bash", {"command": "printf '禁止创建' > " + shlex.quote(str(target))}), context)
        assert not result.ok and not target.exists()
    asyncio.run(check())


def test_frozen_kernel_profile_rejects_replaced_protected_link(sandbox):
    context, _outside = sandbox
    first, second = Path(context.cwd) / "first", Path(context.cwd) / "second"
    first.mkdir()
    second.mkdir()
    (second / "material.txt").write_text("保护链接替换后的材料")
    alias = Path(context.cwd) / "protected-alias"
    alias.symlink_to(first, target_is_directory=True)
    context = replace(context, protected=[alias], filesystem=None)
    boundary = filesystem_boundary(context)
    profile = seatbelt_profile([str(path) for path in boundary.write_roots], [str(path) for path in boundary.protected], read_realpaths=[str(path) for path in boundary.read_roots])
    alias.unlink()
    alias.symlink_to(second, target_is_directory=True)
    result = subprocess.run(["/usr/bin/sandbox-exec", "-p", profile, "/bin/cat", str(alias / "material.txt")], capture_output=True, text=True)
    assert result.returncode != 0 and "保护链接替换后的材料" not in result.stdout


def test_application_rejects_replaced_protected_link(sandbox):
    context, _outside = sandbox
    first, second = Path(context.cwd) / "app-first", Path(context.cwd) / "app-second"
    first.mkdir()
    second.mkdir()
    (second / "material.txt").write_text("应用保护链接材料")
    alias = Path(context.cwd) / "app-protected-alias"
    alias.symlink_to(first, target_is_directory=True)
    context = replace(context, protected=[alias], filesystem=None)
    filesystem_boundary(context)
    alias.unlink()
    alias.symlink_to(second, target_is_directory=True)
    async def check():
        result = await ToolScheduler(build_default_registry()).run(ToolInvocation("protected-app-link", "read_file", {"path": str(alias / "material.txt")}), context)
        assert not result.ok and result.error.startswith("PROTECTED_PATH")
    asyncio.run(check())


@pytest.mark.parametrize("tool_name", ["write_file", "bash"])
def test_nested_readonly_scope_precedes_writable_parent(sandbox, tool_name):
    context, _outside = sandbox
    readonly = Path(context.cwd) / "nested-readonly"
    readonly.mkdir()
    child = readonly / "writable-child"
    child.mkdir()
    target = child / "material.txt"
    target.write_text("嵌套只读目录保持原正文")
    context = replace(context, scope=[*context.scope, readonly, child], write_scope=[context.cwd, child], filesystem=None)
    inputs = {"path": str(target), "content": "禁止父目录扩权"} if tool_name == "write_file" else {"command": "printf '禁止父目录扩权' > " + shlex.quote(str(target))}
    async def check():
        result = await ToolScheduler(build_default_registry()).run(ToolInvocation("nested-readonly", tool_name, inputs), context)
        assert not result.ok and target.read_text() == "嵌套只读目录保持原正文"
    asyncio.run(check())


def test_stdio_data_argument_requires_scope_authorization(authorized):
    service, sessions, approvals, _context, _created = authorized
    connectors = Connectors(service, sessions.identities, approvals.grants)
    approvals.grants.connectors = connectors
    approvals.connectors = connectors
    dataset = service.data_dir / "outside-dataset.csv"
    dataset.write_text("真实范围外CSV数据材料")
    script = Path(__file__).with_name("governance_mcp_server.py").resolve()
    async def check():
        config = {"transport": "stdio", "command": str(script), "args": [str(dataset)], "allowed_hosts": [], "allowed_ports": [], "allow_loopback": False}
        connector, grant = await create_connector(service, approvals.grants, connectors, "mcp", config)
        created = await sessions.create_conversation(instruction="数据参数仍需普通文件授权", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.RUN_STARTED, payload={"attempt_no": 1, "attempt_id": "dataset-scope-attempt"})
        context = sessions.build_work_context(created["conversation"]["id"], created["task_run_id"])
        await connectors.validate(RequestIdentity("owner", "owner"), connector["id"], context)
        name = connectors.stable_name(connector["id"], "dataset_material")
        result = await approved_call(service, approvals, context, created, ToolInvocation("mcp-data-argument", name, {}))
        assert not result.ok and "真实范围外CSV数据材料" not in result.output
        permitted = await sessions.create_conversation(instruction="明确授予实际数据目录范围", workspace_id="office", agent_id="xiaowen", folders=[str(dataset.parent)], request_identity=RequestIdentity("owner", "owner"))
        await service.events.append(task_run_id=permitted["task_run_id"], conversation_id=permitted["conversation"]["id"], type=T.RUN_STARTED, payload={"attempt_no": 1, "attempt_id": "dataset-authorized-attempt"})
        allowed_context = sessions.build_work_context(permitted["conversation"]["id"], permitted["task_run_id"])
        allowed = await approved_call(service, approvals, allowed_context, permitted, ToolInvocation("mcp-data-authorized", name, {}))
        assert allowed.ok and "真实范围外CSV数据材料" in allowed.output
        record = {"task_run_id": created["task_run_id"], "authorized_task_run_id": permitted["task_run_id"], "connector_id": connector["id"],
            "denied_call_id": "mcp-data-argument", "authorized_call_id": "mcp-data-authorized", "denied": not result.ok, "authorized": allowed.ok,
            "file_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(), "mtime_ns": dataset.stat().st_mtime_ns,
            "tool_sql": "SELECT call_id,status,input_hash FROM tool_calls ORDER BY prepared_at",
            "tool_rows": [dict(row) for row in service.db.read_conn.execute("SELECT call_id,status,input_hash FROM tool_calls ORDER BY prepared_at")]}
        (service.data_dir / "stdio-dataset-scope.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    asyncio.run(check())


def test_directory_prefix_is_not_an_authorized_scope(sandbox):
    context, _outside = sandbox
    prefix = Path(context.cwd).with_name("workspace-suffix")
    prefix.mkdir()
    target = prefix / "material.txt"
    target.write_text("目录前缀不能提供文件授权")
    async def check():
        result = await ToolScheduler(build_default_registry()).run(ToolInvocation("prefix-denied", "bash", {"command": "cat " + shlex.quote(str(target))}), context)
        assert not result.ok and "目录前缀不能提供文件授权" not in result.output
    asyncio.run(check())


def test_nested_scope_cannot_override_protected_parent(sandbox):
    context, _outside = sandbox
    protected = Path(context.cwd) / "protected-parent"
    child = protected / "declared-child"
    child.mkdir(parents=True)
    target = child / "material.txt"
    target.write_text("保护父目录不能被子目录范围覆盖")
    context = replace(context, scope=[*context.scope, child], protected=[protected], filesystem=None)
    async def check():
        result = await ToolScheduler(build_default_registry()).run(ToolInvocation("protected-parent", "bash", {"command": "cat " + shlex.quote(str(target))}), context)
        assert not result.ok and "保护父目录不能被子目录范围覆盖" not in result.output
    asyncio.run(check())


def test_registered_startup_target_cannot_change_without_revision(authorized):
    service, sessions, approvals, _context, _created = authorized
    connectors = Connectors(service, sessions.identities, approvals.grants)
    approvals.grants.connectors = connectors
    approvals.connectors = connectors
    code = service.data_dir / "declared-helper.py"
    code.write_text("value = '实际代码资源'\n")
    dataset = service.data_dir / "changed-dataset.csv"
    dataset.write_text("真实新目标数据材料")
    script = Path(__file__).with_name("governance_mcp_server.py").resolve()
    async def check():
        config = {"transport": "stdio", "command": str(script), "args": [str(dataset)], "startup_files": [str(code)],
            "allowed_hosts": [], "allowed_ports": [], "allow_loopback": False}
        connector, grant = await create_connector(service, approvals.grants, connectors, "mcp", config)
        created = await sessions.create_conversation(instruction="启动资源必须保持绑定的目标", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.RUN_STARTED, payload={"attempt_no": 1, "attempt_id": "startup-binding-attempt"})
        context = sessions.build_work_context(created["conversation"]["id"], created["task_run_id"])
        await connectors.validate(RequestIdentity("owner", "owner"), connector["id"], context)
        code.unlink()
        code.symlink_to(dataset)
        with pytest.raises(GovernanceError, match="启动"):
            await approved_call(service, approvals, context, created, ToolInvocation("changed-startup", connectors.stable_name(connector["id"], "dataset_material"), {}))
        assert service.db.read_conn.execute("SELECT count(*) FROM tool_calls WHERE call_id='changed-startup' AND dispatched_at IS NOT NULL").fetchone()[0] == 0
    asyncio.run(check())


def test_registered_startup_code_is_readonly_to_employee(authorized):
    service, sessions, approvals, original, _created = authorized
    connectors = Connectors(service, sessions.identities, approvals.grants)
    code = Path(original.cwd) / "declared-readonly.py"
    code.write_text("value = '真实已登记启动代码'\n")
    script = Path(__file__).with_name("governance_mcp_server.py").resolve()
    async def check():
        config = {"transport": "stdio", "command": str(script), "args": [], "startup_files": [str(code)],
            "allowed_hosts": [], "allowed_ports": [], "allow_loopback": False}
        await create_connector(service, approvals.grants, connectors, "mcp", config)
        created = await sessions.create_conversation(instruction="登记代码通过配置服务管理", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        await service.events.append(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], type=T.RUN_STARTED, payload={"attempt_no": 1, "attempt_id": "readonly-code-attempt"})
        context = sessions.build_work_context(created["conversation"]["id"], created["task_run_id"])
        result = await approvals.run_tool(task_run_id=created["task_run_id"], conversation_id=created["conversation"]["id"], agent_id="xiaowen",
            invocation=ToolInvocation("rewrite-startup-code", "write_file", {"path": str(code), "content": "禁止直接替换"}), ctx=context)
        assert not result.ok and code.read_text() == "value = '真实已登记启动代码'\n"
        assert service.db.read_conn.execute("SELECT count(*) FROM run_events WHERE type='permission.requested' AND task_run_id=?", (created["task_run_id"],)).fetchone()[0] == 0
    asyncio.run(check())


def test_existing_task_uses_current_startup_write_protection(authorized):
    service, sessions, approvals, context, created = authorized
    connectors = Connectors(service, sessions.identities, approvals.grants)
    code = Path(context.cwd) / "newly-managed-code.py"
    code.write_text("value = '当前代码保护'\n")
    script = Path(__file__).with_name("governance_mcp_server.py").resolve()
    async def check():
        await create_connector(service, approvals.grants, connectors, "mcp", {"transport": "stdio", "command": str(script), "args": [],
            "startup_files": [str(code)], "allowed_hosts": [], "allowed_ports": [], "allow_loopback": False})
        result = await approved_call(service, approvals, context, created, ToolInvocation("old-context-code-write", "write_file", {"path": str(code), "content": "禁止旧上下文写入"}))
        assert not result.ok and code.read_text() == "value = '当前代码保护'\n"
    asyncio.run(check())
