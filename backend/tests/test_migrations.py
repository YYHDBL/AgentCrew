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
    assert result.applied_versions == [1, 2, 3, 4, 5, 6]
    assert result.snapshot_path and Path(result.snapshot_path).exists()
    assert _EXPECTED_TABLES <= _tables(db)
    version = db.read_conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == 6
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
    assert "v9" in str(result.error) and "v6" in str(result.error)
    db.close()


def test_mid_failure_rolls_back_that_migration(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    run_migrations(db.write_conn, tmp_path / "backups")
    bad = Migration(
        version=7,
        name="故意非法",
        statements=("CREATE TABLE should_not_exist (id TEXT",),  # 语法错误
    )
    monkeypatch.setattr(
        migrations_module, "MIGRATIONS", migrations_module.MIGRATIONS + (bad,)
    )
    with pytest.raises(MigrationFailedError):
        run_migrations(db.write_conn, tmp_path / "backups")
    # 该迁移事务整体撤销：既有版本及业务表保持完整。
    version = db.read_conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == 6
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


def test_upgrade_preserves_events_and_global_seq(tmp_path):
    """v1 库升级：run_events 表重建（任务与会话身份允许为空）后
    global_seq 保持原值——SSE 游标/at_global_seq 不漂移；旧事件可继续追加。"""
    import json as _json

    db = _make_db(tmp_path)
    conn = db.write_conn
    # 先停在 v1
    import agentcrew_server.db.migrations as m
    v1_only = tuple(x for x in m.MIGRATIONS if x.version == 1)
    saved = m.MIGRATIONS
    try:
        m.MIGRATIONS = v1_only
        m.run_migrations(conn, tmp_path / "backups")
    finally:
        m.MIGRATIONS = saved
    conn.execute(
        "INSERT INTO conversations (id, workspace_id, agent_id, created_at,"
        " updated_at) VALUES ('c1', 'ws', 'a', 't', 't')")
    conn.execute(
        "INSERT INTO task_runs (id, conversation_id, instruction, status,"
        " created_at, updated_at) VALUES ('r1', 'c1', 'i', 'queued', 't', 't')")
    for i in range(1, 4):
        conn.execute(
            "INSERT INTO run_events (id, task_run_id, seq, conversation_id,"
            " type, payload, created_at) VALUES (?, 'r1', ?, 'c1',"
            " 'step.started', '{}', 't')", (f"e{i}", i))
    before = conn.execute(
        "SELECT global_seq, id FROM run_events ORDER BY global_seq").fetchall()
    assert [r[0] for r in before] == [1, 2, 3]

    result = m.run_migrations(conn, tmp_path / "backups")
    assert result.status == "applied" and result.applied_versions == [2, 3, 4, 5, 6]
    after = conn.execute(
        "SELECT global_seq, id, task_run_id FROM run_events"
        " ORDER BY global_seq").fetchall()
    assert [tuple(r) for r in after] == [(1, "e1", "r1"), (2, "e2", "r1"), (3, "e3", "r1")]
    # 升级后新事件继续追加且 AUTOINCREMENT 不回退
    conn.execute(
        "INSERT INTO run_events (id, task_run_id, seq, conversation_id,"
        " type, payload, created_at) VALUES ('e4', NULL, NULL, 'c1',"
        " 'queue.paused', '{}', 't')")
    row = conn.execute(
        "SELECT global_seq FROM run_events WHERE id = 'e4'").fetchone()
    assert row[0] == 4
    # 会话域事件（task NULL）与任务事件在同一查询中共存
    rows = conn.execute(
        "SELECT id, task_run_id FROM run_events ORDER BY global_seq").fetchall()
    assert rows[3] == ("e4", None)
    db.close()
