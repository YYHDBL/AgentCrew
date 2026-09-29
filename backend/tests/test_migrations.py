"""迁移 runner 单测：幂等 / 升级快照 / 版本冲突 / 中途失败回滚（真实 SQLite）。"""

import sqlite3
from pathlib import Path

import pytest

from agentcrew_server.db.database import Database
from agentcrew_server.db import migrations as migrations_module
from agentcrew_server.db.migrations import (
    Migration,
    MigrationConflictError,
    MigrationFailedError,
    run_migrations,
)

_EXPECTED_TABLES = {
    "schema_migrations", "conversations", "task_runs", "run_attempts",
    "run_events", "steps", "llm_calls", "tool_calls", "messages",
    "task_materials", "artifacts", "agent_permission_rules", "audit_log",
}


def _make_db(tmp_path, name="t.db", busy=200):
    return Database(tmp_path / name, busy_timeout_ms=busy)


def _tables(db):
    return {
        row[0] for row in
        db.read_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_fresh_apply_creates_all_13_tables(tmp_path):
    db = _make_db(tmp_path)
    result = run_migrations(db.write_conn, tmp_path / "backups")
    assert result.status == "applied"
    assert result.applied_versions == [1]
    assert result.snapshot_path and Path(result.snapshot_path).exists()
    assert _EXPECTED_TABLES <= _tables(db)
    version = db.read_conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == 1
    db.close()


def test_run_twice_idempotent(tmp_path):
    db = _make_db(tmp_path)
    run_migrations(db.write_conn, tmp_path / "backups")
    snapshots_before = list((tmp_path / "backups").glob("db-v*.sqlite"))
    result = run_migrations(db.write_conn, tmp_path / "backups")
    assert result.status == "up_to_date"
    assert result.applied_versions == []
    assert list((tmp_path / "backups").glob("db-v*.sqlite")) == snapshots_before
    db.close()


def test_downgrade_refused_with_both_versions(tmp_path):
    db = _make_db(tmp_path)
    run_migrations(db.write_conn, tmp_path / "backups")
    db.write_conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (9, '来自未来版本', 'x')"
    )
    result = run_migrations(db.write_conn, tmp_path / "backups")
    assert result.status == "conflict"
    assert isinstance(result.error, MigrationConflictError)
    assert "v9" in str(result.error) and "v1" in str(result.error)
    db.close()


def test_mid_failure_rolls_back_that_migration(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    run_migrations(db.write_conn, tmp_path / "backups")
    bad = Migration(
        version=2,
        name="故意非法",
        statements=("CREATE TABLE should_not_exist (id TEXT",),  # 语法错误
    )
    monkeypatch.setattr(
        migrations_module, "MIGRATIONS", migrations_module.MIGRATIONS + (bad,)
    )
    with pytest.raises(MigrationFailedError):
        run_migrations(db.write_conn, tmp_path / "backups")
    # 该迁移事务整体回滚：版本停在 1、坏表不存在、v1 表完好
    version = db.read_conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == 1
    assert "should_not_exist" not in _tables(db)
    assert "conversations" in _tables(db)
    db.close()


def test_snapshot_keeps_last_three(tmp_path):
    db = _make_db(tmp_path)
    backups = tmp_path / "backups"
    for fake in range(5):
        (backups).mkdir(exist_ok=True)
        (backups / f"db-v0-old{fake}.sqlite").write_bytes(b"fake")
    run_migrations(db.write_conn, backups)  # 触发新快照 + 清理
    remaining = sorted(p.name for p in backups.glob("db-v*.sqlite"))
    assert len(remaining) == 3
    db.close()


def test_snapshot_includes_uncheckpointed_wal_data(tmp_path):
    """外审回稿：升级前快照必须包含 WAL 中已提交未检查点的数据（v1.9 依据）。"""
    db_path = tmp_path / "old.db"
    writer = sqlite3.connect(str(db_path), isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE legacy_rows (id INTEGER PRIMARY KEY, v TEXT)")
    writer.execute("BEGIN IMMEDIATE")
    for i in range(50):
        writer.execute("INSERT INTO legacy_rows (v) VALUES (?)", (f"row-{i}",))
    writer.execute("COMMIT")  # 已提交但未 checkpoint：数据在 -wal 里
    assert (tmp_path / "old.db-wal").stat().st_size > 0

    db = Database(db_path)
    result = run_migrations(db.write_conn, tmp_path / "backups")
    assert result.status == "applied"
    snapshot = sqlite3.connect(result.snapshot_path)
    assert snapshot.execute("SELECT count(*) FROM legacy_rows").fetchone()[0] == 50
    snapshot.close()
    writer.close()
    db.close()
