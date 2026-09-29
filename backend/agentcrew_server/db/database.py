"""SQLite 连接管理：写连接（WAL）+ 只读连接（并发读不阻塞写，backend-service §2）。

- 写连接只经 WriteChannel 使用（单写 executor + busy 重试）；
- 读连接 PRAGMA query_only=ON——能用 WAL 共享内存，但 SQLite 层禁止写入。
- 建表/迁移：M0-C2 migrations.py；驱动选型：内置 sqlite3，无 ORM（M0-cards §3）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: Path, *, busy_timeout_ms: int = 5000):
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms
        # check_same_thread=False：写连接固定跑在写通道的单线程 executor，
        # 读连接可能被请求线程使用；串行化保护由写通道的全局序承担
        self._write_conn = sqlite3.connect(
            str(path), timeout=busy_timeout_ms / 1000, isolation_level=None,
            check_same_thread=False,
        )
        self._write_conn.execute("PRAGMA journal_mode=WAL")
        self._write_conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._write_conn.execute("PRAGMA foreign_keys=ON")
        self._read_conn = sqlite3.connect(
            str(path), timeout=busy_timeout_ms / 1000, isolation_level=None,
            check_same_thread=False,
        )
        self._read_conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._read_conn.execute("PRAGMA foreign_keys=ON")
        self._read_conn.execute("PRAGMA query_only=ON")
        self.closed = False

    @property
    def write_conn(self) -> sqlite3.Connection:
        return self._write_conn

    @property
    def read_conn(self) -> sqlite3.Connection:
        return self._read_conn

    @property
    def journal_mode(self) -> str:
        return self._read_conn.execute("PRAGMA journal_mode").fetchone()[0]

    def checkpoint_passive(self) -> tuple[int, int, int]:
        """PASSIVE checkpoint（§7 第 4 步）：被读事务阻塞则如实返回，不等待。"""
        return self._write_conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()

    def close(self) -> None:
        if not self.closed:
            self._read_conn.close()
            self._write_conn.close()
            self.closed = True
