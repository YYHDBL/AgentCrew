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
    MIGRATIONS,
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
    assert result.applied_versions == list(range(1, MIGRATIONS[-1].version + 1))
    assert result.snapshot_path and Path(result.snapshot_path).exists()
    assert _EXPECTED_TABLES <= _tables(db)
    version = db.read_conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == MIGRATIONS[-1].version
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
    future = MIGRATIONS[-1].version + 1
    db.write_conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, '来自未来版本', 'x')", (future,)
    )
    result = run_migrations(db.write_conn, tmp_path / "backups")
    assert result.status == "conflict"
    assert isinstance(result.error, MigrationConflictError)
    assert f"v{future}" in str(result.error) and f"v{MIGRATIONS[-1].version}" in str(result.error)
    db.close()


def test_mid_failure_rolls_back_that_migration(tmp_path):
    db = _make_db(tmp_path)
    run_migrations(db.write_conn, tmp_path / "backups")
    bad = Migration(
        version=MIGRATIONS[-1].version + 1,
        name="故意非法",
        statements=("CREATE TABLE should_not_exist (id TEXT",),  # 语法错误
    )
    with pytest.raises(MigrationFailedError):
        migrations_module._apply_migration(db.write_conn, bad)
    # 该迁移事务整体撤销：既有版本及业务表保持完整。
    version = db.read_conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == MIGRATIONS[-1].version
    assert "should_not_exist" not in _tables(db)
    assert "conversations" in _tables(db)
    db.close()


def test_snapshot_keeps_last_three(tmp_path):
    db = _make_db(tmp_path)
    backups = tmp_path / "backups"
    for number in range(5):
        (backups).mkdir(exist_ok=True)
        old = sqlite3.connect(backups / f"db-v0-old{number}.sqlite")
        old.execute("CREATE TABLE saved_record(id INTEGER PRIMARY KEY)")
        old.execute("INSERT INTO saved_record VALUES(?)", (number,))
        old.commit()
        old.close()
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
    assert result.status == "applied" and result.applied_versions == list(range(2, MIGRATIONS[-1].version + 1))
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


def test_upgrade_nine_preserves_background_foreign_keys(tmp_path):
    import asyncio
    from agentcrew_server.bus import EventBus
    from agentcrew_server.config import load_config
    from agentcrew_server.db.event_store import EventStore
    from agentcrew_server.db.write_channel import WriteChannel
    from agentcrew_server.memory.jobs import MemoryJobs
    from agentcrew_server.memory.store import MemoryStore
    from agentcrew_server.sessions import SessionService
    from agentcrew_server.settings import SettingsService
    from test_session_summaries import task, summary

    db = _make_db(tmp_path)
    migrations_module._bootstrap_version_table(db.write_conn)
    for migration in migrations_module.MIGRATIONS[:9]:
        migrations_module._apply_migration(db.write_conn, migration)
    channel, bus = WriteChannel(db.write_conn), EventBus()
    events = EventStore(channel, publisher=bus.publish)
    settings = SettingsService(load_config(tmp_path, {}), tmp_path, channel, tmp_path / "chain-head.txt")
    store, sessions = MemoryStore(db, events, tmp_path, settings), SessionService(db, events, tmp_path, settings)
    jobs = MemoryJobs(store, bus, settings)
    async def prepare():
        _conversation, _task, event = await task(store, sessions)
        await summary(jobs, event, "真实旧运行库的已完成摘要")
    asyncio.run(prepare())
    tables = ("memory_jobs", "session_summaries", "memory_job_calls")
    def snapshot():
        result = {}
        for table in tables:
            cursor = db.read_conn.execute(f"SELECT * FROM {table}")
            columns = tuple(item[0] for item in cursor.description)
            result[table] = columns, [tuple(row) for row in cursor.fetchall()]
        return result
    before = snapshot()
    def preserves_background_rows():
        after = snapshot()
        for table, (columns, rows) in before.items():
            next_columns, next_rows = after[table]
            assert next_columns[:len(columns)] == columns
            assert [row[:len(columns)] for row in next_rows] == rows
        priority = after["memory_jobs"][0].index("priority")
        assert all(row[priority] == 0 for row in after["memory_jobs"][1])
    try:
        result = run_migrations(db.write_conn, tmp_path / "backups")
        assert result.applied_versions == list(range(10, MIGRATIONS[-1].version + 1))
        assert db.read_conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.write_conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        preserves_background_rows()
        bad = Migration(MIGRATIONS[-1].version + 1, "外键不完整的迁移", ("DELETE FROM memory_jobs",), rebuild_foreign_keys=True)
        with pytest.raises(MigrationFailedError, match="外键校验失败"):
            migrations_module._apply_migration(db.write_conn, bad)
        assert db.write_conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        preserves_background_rows()
    finally:
        asyncio.run(jobs.shutdown())
        channel.close()
        db.close()
