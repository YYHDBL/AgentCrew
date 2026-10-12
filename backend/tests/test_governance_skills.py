"""M2-04：真实HTTP、不可变Skill版本、任务引用与SIGKILL。"""

import asyncio
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.governance.resources import Resources
from agentcrew_server.governance.skills import SkillVersions
from agentcrew_server.memory.store import MemoryStore
from test_governance_identity import server, request, demo, conversation
from test_governance_resources import resources


@pytest.fixture
def governed(resources):
    from agentcrew_server.governance.identity import Identities
    from agentcrew_server.governance.seed import seed
    from agentcrew_server.memory.skills import MemorySkills
    from agentcrew_server.settings import SettingsService
    from agentcrew_server.config import load_config
    from agentcrew_server.sessions import SessionService
    service, memory = resources
    versions = SkillVersions(service, memory)
    async def prepare():
        await seed(service, memory)
        await versions.register_history()
    asyncio.run(prepare())
    identities = Identities(service)
    service.skill_versions = versions
    memory.skill_versions = versions
    memory.identities = identities
    service.events.task_preparer = identities.bind_task
    settings = SettingsService(load_config(service.data_dir, {}), service.data_dir, service.events.channel, service.data_dir / "chain-head.txt")
    sessions = SessionService(service.db, service.events, service.data_dir, settings)
    sessions.identities = identities
    return service, memory, versions, sessions, MemorySkills(memory)


def test_execution_reads_bound_body_and_new_task_reads_new_version(governed):
    from agentcrew_core.governance import RequestIdentity
    from agentcrew_server.memory.store import MemoryIdentity
    service, memory, versions, sessions, skills = governed
    async def check():
        initial = await sessions.create_conversation(instruction="阅读已授权文档流程", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        first_id = initial["task_run_id"]
        actor = MemoryIdentity("office", "xiaowen", "agent", "xiaowen", initial["conversation"]["id"], first_id)
        original = await skills.view(actor, "文档模板", call_id="bound-original")
        assert original["revision"] == 1
        changed = await skills.change(MemoryIdentity("office", "xiaowen"), "文档模板", action="edit", change_id="bound-edit",
            expected_revision=1, basis="所有者更新材料核查流程", text="# 文档模板\n新正文包含完整核验步骤。")
        assert changed["version_no"] == 2
        assert (await skills.view(actor, "文档模板", call_id="bound-after-edit"))["text"] == original["text"]
        next_task = await sessions.create_conversation(instruction="使用当前文档流程", workspace_id="office", agent_id="xiaowen", request_identity=RequestIdentity("owner", "owner"))
        next_actor = MemoryIdentity("office", "xiaowen", "agent", "xiaowen", next_task["conversation"]["id"], next_task["task_run_id"])
        current = await skills.view(next_actor, "文档模板", call_id="bound-current")
        assert current["revision"] == 2 and "完整核验步骤" in current["text"]
        assert original["version_id"] != current["version_id"]
        assert versions.version(original["version_id"])["content"] == original["text"]
    asyncio.run(check())


def test_concurrent_publish_rejects_stale_revision(governed):
    from agentcrew_server.memory.store import MemoryIdentity
    service, memory, versions, sessions, skills = governed
    async def check():
        async def publish(change_id, text):
            return await skills.change(MemoryIdentity("office", "xiaowen"), "文档模板", action="edit", change_id=change_id,
                expected_revision=1, basis="所有者并发更新相同修订", text=text)
        results = await asyncio.gather(publish("concurrent-first", "第一个合法正文"), publish("concurrent-second", "第二个合法正文"))
        assert len([result for result in results if result.get("version_no") == 2]) == 1
        assert len([result for result in results if result.get("error") == "REVISION_CONFLICT"]) == 1
        assert service.db.read_conn.execute("SELECT count(*) FROM skill_versions WHERE change_id IN ('concurrent-first','concurrent-second')").fetchone()[0] == 1
    asyncio.run(check())


def test_workspace_name_conflict_creates_no_intent_or_files(governed):
    from agentcrew_core.governance import RequestIdentity
    from agentcrew_server.governance.identity import Identities
    from agentcrew_server.memory.store import MemoryIdentity
    service, memory, versions, sessions, skills = governed
    async def check():
        identities = Identities(service)
        def create(conn):
            value = service.create_agent(conn, "office-second", "office", "材料助理", {"position": "核查材料", "model_slot": "main", "skill_ids": [], "connector_ids": []})
            return value, service.scope("agent", "office-second", conn), {"status": "active"}
        await service.mutate(change_id="second-office-agent", actor_id="owner", request={}, action="governance.resource_changed",
            kind="agent", resource_id="office-second", operation=create,
            authorize=lambda conn: identities.require(RequestIdentity("owner", "owner"), "manage", "office", conn))
        before = {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in (service.data_dir / "skills").rglob("*") if path.is_file()}
        result = await skills.change(MemoryIdentity("office", "office-second"), "文档模板", action="create", change_id="duplicate-workspace-name",
            expected_revision=0, basis="核查工作区级名称唯一性", description="核查材料", text="检查材料完整性。")
        assert result["error"] == "REVISION_CONFLICT"
        assert service.db.read_conn.execute("SELECT count(*) FROM memory_changes WHERE change_id='duplicate-workspace-name'").fetchone()[0] == 0
        assert before == {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in (service.data_dir / "skills").rglob("*") if path.is_file()}
        assert await memory.recover() == []
    asyncio.run(check())


def test_two_audit_entries_crossing_100_updates_anchor(governed):
    from agentcrew_core.governance import RequestIdentity
    from agentcrew_server.db.audit import read_chain_head, snapshot_chain_head
    from agentcrew_server.governance.identity import Identities
    from agentcrew_server.memory.store import MemoryIdentity
    service, memory, versions, sessions, skills = governed
    async def check():
        identities = Identities(service)
        await service.events.channel.execute(lambda conn: snapshot_chain_head(conn, service.data_dir / "chain-head.txt"))
        while service.db.read_conn.execute("SELECT max(seq) FROM audit_log").fetchone()[0] < 99:
            revision = service.db.read_conn.execute("SELECT revision FROM memberships WHERE user_id='wangming'").fetchone()[0]
            await identities.role(RequestIdentity("owner", "owner"), "membership-wangming",
                {"role": "admin", "expected_revision": revision, "change_id": "audit-boundary-role-" + str(revision)})
        result = await skills.change(MemoryIdentity("office", "xiaowen"), "文档模板", action="edit", change_id="audit-boundary-version",
            expected_revision=1, basis="核查双审计事务的周期锚点", text="核查原始来源与完整输出。")
        assert result["version_no"] == 2
        assert service.db.read_conn.execute("SELECT max(seq) FROM audit_log").fetchone()[0] == 101
        assert read_chain_head(service.data_dir / "chain-head.txt")[0] == 101
        assert verify_with_anchor(service.db.read_conn, service.data_dir / "chain-head.txt").ok
    asyncio.run(check())


def create_skill(server):
    response = request(server, "POST", "/api/memory/skills", body={"workspace_id": "office", "agent_id": "xiaowen", "change_id": "version-create-" + uuid.uuid4().hex,
        "expected_revision": 0, "name": "版本核验-" + uuid.uuid4().hex[:8], "description": "核查来源并记录结果", "text": "# 原始流程\n核查原始材料。",
        "basis": "所有者提供的实际材料流程", "files": {"references/source.md": "原始来源", "templates/checklist.md": "原始清单"}})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_publish_immutable_versions_restore_and_real_files(server):
    created = create_skill(server)
    skill_id = created["store_id"]
    first = request(server, "GET", f"/api/skills/{skill_id}/versions/{created['version_id']}").json()["data"]
    body = {"change_id": "version-edit-" + uuid.uuid4().hex, "expected_revision": 1, "basis": "所有者更新完整材料",
        "text": "# 更新流程\n核查来源和输出。", "files": {"references/source.md": "更新来源", "templates/checklist.md": None, "assets/new.md": "新的材料"}}
    updated = request(server, "POST", f"/api/skills/{skill_id}/versions", body=body)
    assert updated.status_code == 200, updated.text
    second = updated.json()["data"]
    assert second["version_no"] == 2
    assert request(server, "POST", f"/api/skills/{skill_id}/versions", body=body).json() == updated.json()
    assert request(server, "POST", f"/api/skills/{skill_id}/versions", body={**body, "text": "不同内容"}).status_code == 409
    assert request(server, "POST", f"/api/skills/{skill_id}/versions", body={**body, "change_id": "stale-version"}).status_code == 409
    restored = request(server, "POST", f"/api/skills/{skill_id}/versions", body={"change_id": "version-restore-" + uuid.uuid4().hex,
        "expected_revision": 2, "basis": "所有者恢复原始材料流程", "restore_version_id": first["id"]})
    assert restored.status_code == 200, restored.text
    third = restored.json()["data"]
    assert third["version_no"] == 3 and third["content"] == first["content"]
    root = server["root"] / "skills" / skill_id
    assert (root / "SKILL.md").read_text() == third["content"]
    assert (root / "references/source.md").read_text() == "原始来源"
    assert (root / "templates/checklist.md").read_text() == "原始清单"
    assert not (root / "assets/new.md").exists()
    connection = sqlite3.connect(server["root"] / "agentcrew.db")
    with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_SKILL_VERSION"):
        connection.execute("UPDATE skill_versions SET content='改写历史' WHERE id=?", (first["id"],))
    with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_SKILL_VERSION"):
        connection.execute("DELETE FROM skill_versions WHERE id=?", (first["id"],))
    connection.close()
    assert request(server, "GET", f"/api/skills/{skill_id}/versions/{first['id']}").json()["data"] == first
    page = request(server, "GET", f"/api/skills/{skill_id}/versions?limit=2").json()["data"]
    assert [item["version_no"] for item in page["items"]] == [3, 2] and page["next_after"]
    next_page = request(server, "GET", f"/api/skills/{skill_id}/versions?limit=2&after={page['next_after']}").json()["data"]
    assert [item["version_no"] for item in next_page["items"]] == [1]
    record = {"skill_id": skill_id, "versions": [first, second, third], "history_unchanged": True,
        "files": [{"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mtime_ns": path.stat().st_mtime_ns} for path in sorted(root.rglob("*")) if path.is_file()]}
    (server["root"] / "version-publish-restore.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def test_raw_http_idempotency_survives_later_body_and_file_changes(server):
    created = create_skill(server)
    skill_id = created["store_id"]
    first = request(server, "GET", f"/api/skills/{skill_id}/versions/{created['version_id']}").json()["data"]
    partial = {"change_id": "partial-version-" + uuid.uuid4().hex, "expected_revision": 1, "basis": "只更新支撑材料", "files": {"assets/partial.md": "真实追加材料"}}
    second = request(server, "POST", f"/api/skills/{skill_id}/versions", body=partial)
    assert second.status_code == 200, second.text
    third = request(server, "POST", f"/api/skills/{skill_id}/versions", body={"change_id": "later-body-" + uuid.uuid4().hex,
        "expected_revision": 2, "basis": "随后更新正文", "text": "随后更新的完整正文。"})
    assert third.status_code == 200
    assert request(server, "POST", f"/api/skills/{skill_id}/versions", body=partial).json() == second.json()
    restore = {"change_id": "idempotent-restore-" + uuid.uuid4().hex, "expected_revision": 3, "basis": "恢复原始完整材料", "restore_version_id": first["id"]}
    fourth = request(server, "POST", f"/api/skills/{skill_id}/versions", body=restore)
    assert fourth.status_code == 200, fourth.text
    fifth = request(server, "POST", f"/api/skills/{skill_id}/versions", body={"change_id": "later-files-" + uuid.uuid4().hex,
        "expected_revision": 4, "basis": "随后再次修改文件清单", "files": {"assets/later.md": "随后追加的材料"}})
    assert fifth.status_code == 200
    assert request(server, "POST", f"/api/skills/{skill_id}/versions", body=restore).json() == fourth.json()


def test_task_version_binding_and_current_grant(server):
    listed = request(server, "GET", "/api/memory/skills?workspace_id=office&agent_id=xiaowen").json()["data"]["items"]
    skill = next(item for item in listed if item["name"] == "文档模板")
    versions = request(server, "GET", f"/api/skills/{skill['id']}/versions").json()["data"]["items"]
    first_version = versions[0]
    initial = conversation(server)
    connection = sqlite3.connect(server["root"] / "agentcrew.db")
    first_binding = json.loads(connection.execute("SELECT skill_versions FROM task_governance WHERE task_run_id=?", (initial["task_run_id"],)).fetchone()[0])
    assert first_binding[skill["id"]] == first_version["id"]
    changed = request(server, "POST", f"/api/skills/{skill['id']}/versions", body={"change_id": "template-update-" + uuid.uuid4().hex,
        "expected_revision": first_version["version_no"], "basis": "所有者增加核验步骤", "text": "# 文档模板\n核对来源并检查完整输出。"})
    assert changed.status_code == 200, changed.text
    latest_version = changed.json()["data"]
    fresh = conversation(server)
    next_binding = json.loads(connection.execute("SELECT skill_versions FROM task_governance WHERE task_run_id=?", (fresh["task_run_id"],)).fetchone()[0])
    assert next_binding[skill["id"]] == latest_version["id"]
    assert json.loads(connection.execute("SELECT skill_versions FROM task_governance WHERE task_run_id=?", (initial["task_run_id"],)).fetchone()[0]) == first_binding
    connection.close()
    member = demo(server, "lilei")
    assert request(server, "POST", f"/api/skills/{skill['id']}/versions", user="lilei", identity=member,
        body={"change_id": "member-version", "expected_revision": 2, "basis": "越权发布", "text": "正文"}).status_code == 403
    record = {"original_task_id": initial["task_run_id"], "new_task_id": fresh["task_run_id"], "original_binding": first_binding,
        "new_binding": next_binding, "original_binding_preserved": True}
    (server["root"] / "task-version-bindings.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")


@pytest.mark.parametrize("boundary", ["prepared", "first_file", "files_replaced", "committed"])
def test_sigkill_commits_one_skill_version(tmp_path, boundary):
    child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("governance_skill_crash_process.py")), str(tmp_path), boundary],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        observed = child.stdout.readline().strip()
        assert observed == boundary, child.stderr.read() if child.poll() is not None else observed
        assert os.WIFSTOPPED(os.waitpid(child.pid, os.WUNTRACED)[1])
        child.kill()
        assert child.wait(10) == -signal.SIGKILL
        db = Database(tmp_path / "agentcrew.db")
        channel = WriteChannel(db.write_conn)
        events = EventStore(channel)
        memory = MemoryStore(db, events, tmp_path)
        versions = SkillVersions(Resources(db, events, tmp_path), memory)
        memory.skill_versions = versions
        async def check():
            await memory.recover()
            await memory.recover()
            row = db.read_conn.execute("SELECT id,skill_id FROM skill_versions WHERE change_id='skill-version-sigkill'").fetchone()
            assert row is not None
            version = versions.version(row[0])
            assert version["version_no"] == 1 and "完整恢复流程" in version["content"]
            assert db.read_conn.execute("SELECT count(*) FROM skill_versions WHERE change_id='skill-version-sigkill'").fetchone()[0] == 1
            assert db.read_conn.execute("SELECT count(*) FROM memory_ledger WHERE change_id='skill-version-sigkill'").fetchone()[0] == 1
            assert db.read_conn.execute("SELECT count(*) FROM run_events WHERE type='governance.skill_version_published' AND json_extract(payload,'$.change_id')='skill-version-sigkill'").fetchone()[0] == 1
            assert (memory.path("skill", row[1]).parent / "references/procedure.md").read_text() == "核对原始来源"
            assert verify_with_anchor(db.read_conn, tmp_path / "chain-head.txt").ok
            record = {"boundary": boundary, "process_exit": child.returncode, "recoveries": 2, "version": version,
                "ledger_count": 1, "version_event_count": 1, "two_level_audit": True}
            (tmp_path / "version-sigkill.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        try:
            asyncio.run(check())
        finally:
            channel.close()
            db.close()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(10)
