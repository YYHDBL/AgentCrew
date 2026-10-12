"""M2-02：实际迁移、历史保留、资源约束及种子幂等。"""

import asyncio
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agentcrew_server.db.audit import verify_internal
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import MIGRATIONS, _apply_migration, _bootstrap_version_table, run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.governance.resources import GovernanceError, Resources
from agentcrew_server.governance.seed import seed
from agentcrew_server.memory.skills import MemorySkills
from agentcrew_server.memory.store import MemoryIdentity, MemoryStore


@pytest.fixture
def resources(tmp_path):
    db = Database(tmp_path / "agentcrew.db")
    run_migrations(db.write_conn, tmp_path / "backups")
    channel = WriteChannel(db.write_conn)
    events = EventStore(channel)
    service = Resources(db, events, tmp_path)
    memory = MemoryStore(db, events, tmp_path)
    yield service, memory
    channel.close()
    db.close()


def test_seed_real_skills_and_capabilities(resources):
    service, memory = resources
    async def check():
        await seed(service, memory)
        assert service.get("organization", "demo-org")["name"] == "演示科技有限公司"
        assert service.get("agent", "xiaowen")["spec"]["connector_ids"] == ["office-http"]
        assert service.get("agent", "xiaogang")["spec"]["connector_ids"] == []
        body = await MemorySkills(memory).view(MemoryIdentity("analytics", "xiaogang"), "SQL查询")
        assert body["exists"] and "参数化SQL" in body["text"]
        assert service.db.read_conn.execute("SELECT count(*) FROM grants WHERE resource_type='connector' AND grantee_id='xiaogang'").fetchone()[0] == 0
        assert service.db.read_conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert verify_internal(service.db.read_conn).ok
    asyncio.run(check())


def test_reseed_preserves_edits_revocation_and_roles(resources):
    service, memory = resources
    async def check():
        await seed(service, memory)
        conn = service.db.write_conn
        conn.execute("UPDATE agents SET name='所有者编辑的小文',status='disabled',revision=2 WHERE id='xiaowen'")
        conn.execute("UPDATE grants SET revoked_at='2026-10-03',revision=2 WHERE resource_id='office-http'")
        conn.execute("UPDATE memberships SET role='member',revision=2 WHERE user_id='wangming'")
        conn.execute("UPDATE agent_permission_rules SET revoked_at='2026-10-03' WHERE id='seed-office-write'")
        tables = ("organizations", "workspaces", "users", "memberships", "agents", "skills", "connectors", "grants", "agent_permission_rules", "audit_log", "run_events", "memory_ledger")
        before = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in tables}
        files = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in service.data_dir.rglob("*.md")}
        await seed(service, memory)
        await seed(service, memory)
        assert before == {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in tables}
        assert files == {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in files}
    asyncio.run(check())


def test_grant_foreign_keys_and_scope_constraints(resources):
    service, memory = resources
    asyncio.run(seed(service, memory))
    conn = service.db.write_conn
    with pytest.raises(sqlite3.IntegrityError, match="CROSS_WORKSPACE_GRANT"):
        conn.execute("""INSERT INTO grants VALUES('cross','connector','office-http','agent','xiaogang',
            'xiaogang',NULL,'owner','2026-10-03',NULL,1,'cross')""")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""INSERT INTO grants VALUES('missing','connector','missing','agent','xiaowen',
            'xiaowen',NULL,'owner','2026-10-03',NULL,1,'missing')""")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""INSERT INTO grants VALUES('duplicate','connector','office-http','agent','xiaowen',
            'xiaowen',NULL,'owner','2026-10-03',NULL,1,'duplicate')""")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM agents WHERE id='xiaowen'")


def test_upgrade_real_history_snapshot_and_identity_mapping(tmp_path):
    db = Database(tmp_path / "agentcrew.db")
    _bootstrap_version_table(db.write_conn)
    for migration in MIGRATIONS[:10]:
        _apply_migration(db.write_conn, migration)
    channel = WriteChannel(db.write_conn)
    events = EventStore(channel)
    memory = MemoryStore(db, events, tmp_path)
    async def check():
        db.write_conn.execute("INSERT INTO conversations(id,workspace_id,agent_id,created_at,updated_at) VALUES('legacy','default','default','2026-10-01','2026-10-01')")
        changed = await memory.change(MemoryIdentity("default", "default"), "workspace", "default", change_id="legacy-memory",
            expected_revision=0, basis="真实旧库工作区材料", operations=[{"action": "add", "text": "原始资料位于工作区。"}])
        await MemorySkills(memory).change(MemoryIdentity("default", "default"), "历史技能", action="create", change_id="legacy-skill",
            expected_revision=0, description="检查实际旧材料", text="核对原始文件并记录结果。", basis="旧用户登记的技能")
        tables = ("conversations", "memory_stores", "memory_ledger", "run_events", "audit_log", "memory_skills")
        old = {table: [tuple(row) for row in db.read_conn.execute(f"SELECT * FROM {table}")] for table in tables}
        files = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*.md")}
        result = run_migrations(db.write_conn, tmp_path / "backups")
        assert result.applied_versions == list(range(11, MIGRATIONS[-1].version + 1))
        snapshot = sqlite3.connect(result.snapshot_path)
        assert snapshot.execute("SELECT count(*) FROM memory_ledger").fetchone()[0] == 2
        snapshot.close()
        service = Resources(db, events, tmp_path)
        await seed(service, memory)
        assert run_migrations(db.write_conn, tmp_path / "backups").status == "up_to_date"
        await seed(service, memory)
        for table, rows in old.items():
            assert [tuple(row) for row in db.read_conn.execute(f"SELECT * FROM {table} LIMIT ?", (len(rows),))] == rows
        assert files == {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in files}
        assert db.read_conn.execute("SELECT effective_user_id FROM governance_conversations WHERE conversation_id='legacy'").fetchone()[0] == "owner"
        assert db.read_conn.execute("SELECT count(*) FROM grants WHERE resource_type='connector' AND grantee_id='default'").fetchone()[0] == 0
        assert db.read_conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert verify_internal(db.read_conn).ok
        evidence = {"migration_versions": result.applied_versions, "legacy_change_id": changed["change_id"],
            "queries": [{"sql": f"SELECT count(*) FROM {table}", "before": len(rows), "after": db.read_conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]} for table, rows in old.items()],
            "file_sha_mtime_preserved": True, "audit_verified": True,
            "mapping": [dict(row) for row in db.read_conn.execute("SELECT * FROM governance_conversations")]}
        (tmp_path / "legacy-upgrade.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    try:
        asyncio.run(check())
    finally:
        channel.close()
        db.close()


def test_unknown_legacy_identity_fails_without_partial_seed(resources):
    service, memory = resources
    service.db.write_conn.execute("INSERT INTO agent_permission_rules VALUES('unknown','unregistered','bash','pwd','allow','unknown-user','2026-10-01',NULL)")
    with pytest.raises(GovernanceError, match="未能验证"):
        asyncio.run(seed(service, memory))
    assert service.db.read_conn.execute("SELECT count(*) FROM organizations").fetchone()[0] == 0
    assert service.db.read_conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0


def test_sigkill_migration_is_atomic(tmp_path):
    child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("governance_migration_process.py")), str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "migration-boundary"
        assert os.WIFSTOPPED(os.waitpid(child.pid, os.WUNTRACED)[1])
        child.kill()
        assert child.wait(10) == -signal.SIGKILL
        db = Database(tmp_path / "agentcrew.db")
        assert db.read_conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 10
        assert db.read_conn.execute("SELECT count(*) FROM sqlite_master WHERE name='organizations'").fetchone()[0] == 0
        assert run_migrations(db.write_conn, tmp_path / "backups").applied_versions == list(range(11, MIGRATIONS[-1].version + 1))
        assert run_migrations(db.write_conn, tmp_path / "backups").status == "up_to_date"
        assert db.read_conn.execute("PRAGMA foreign_key_check").fetchall() == []
        evidence = {"process_exit": child.returncode, "boundary": "CREATE TABLE demo_identities",
            "version_after_sigkill": 10, "partial_organizations_after_sigkill": 0,
            "queries": [{"sql": "SELECT version,name FROM schema_migrations ORDER BY version",
                "rows": [dict(row) for row in db.read_conn.execute("SELECT version,name FROM schema_migrations ORDER BY version")]}],
            "second_migration_status": "up_to_date", "foreign_key_violations": []}
        (tmp_path / "migration-sigkill.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
        db.close()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(10)
