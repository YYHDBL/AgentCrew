"""写通道单测：真实 SQLite 锁竞争下的 busy 重试（WAL + busy_timeout + 3 次退避）。"""

import asyncio
import sqlite3
import threading

import pytest

from agentcrew_server.db.database import Database
from agentcrew_server.db.write_channel import WriteChannel


def _setup(tmp_path, busy_ms=100):
    db = Database(tmp_path / "t.db", busy_timeout_ms=busy_ms)
    db.write_conn.execute(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"
    )
    return db


def test_busy_lock_retries_until_release(tmp_path):
    """另一连接 BEGIN IMMEDIATE 持写锁 0.3s：写通道靠重试穿透，不丢写。"""
    db = _setup(tmp_path)
    other = sqlite3.connect(
        str(db.path), timeout=0.1, isolation_level=None, check_same_thread=False
    )
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT INTO t (v) VALUES ('other')")

    def release():
        other.execute("COMMIT")
        other.close()

    threading.Timer(0.3, release).start()

    channel = WriteChannel(db.write_conn)

    def write(conn):
        conn.execute("INSERT INTO t (v) VALUES ('mine')")
        return conn.execute("SELECT count(*) FROM t").fetchone()[0]

    count = asyncio.run(channel.execute(write))
    assert count == 2  # other + mine 都在
    channel.close()
    db.close()


def test_busy_lock_exhausts_and_raises(tmp_path):
    """锁一直被占：4 次尝试（首试+3 重试）后如实抛 OperationalError。"""
    db = _setup(tmp_path, busy_ms=50)
    other = sqlite3.connect(str(db.path), timeout=0.1, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT INTO t (v) VALUES ('holder')")

    channel = WriteChannel(db.write_conn)
    attempts = []

    def write(conn):
        attempts.append(1)
        conn.execute("INSERT INTO t (v) VALUES ('x')")
        return None

    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(channel.execute(write))
    assert len(attempts) == 4  # 1 次首试 + 3 次重试（C2 失败状态约定）

    other.execute("COMMIT")
    other.close()
    channel.close()
    db.close()


def test_integrity_error_not_retried(tmp_path):
    """UNIQUE 冲突是逻辑错误：不做 busy 重试，立即抛出。"""
    db = _setup(tmp_path)
    db.write_conn.execute("CREATE UNIQUE INDEX idx_t_v ON t (v)")
    channel = WriteChannel(db.write_conn)

    def write_dup(conn):
        conn.execute("INSERT INTO t (v) VALUES ('dup')")
        conn.execute("INSERT INTO t (v) VALUES ('dup')")  # 第二条 UNIQUE 冲突

    with pytest.raises(sqlite3.IntegrityError):
        channel.execute_sync(write_dup)
    channel.close()
    db.close()
