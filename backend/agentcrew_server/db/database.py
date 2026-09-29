"""SQLite 连接管理：写连接（WAL）+ 每线程只读连接（backend-service §2）。

- 写连接只经 WriteChannel 使用（单写 executor + busy 重试）；
- 读连接 **每线程一条**（thread-local）：asyncio.to_thread 的多个 worker
  会并发读——单条连接被并发使用会 InterfaceError（外审回稿实测发现）；
  WAL 支持多读者并发，各线程各持一条 query_only 连接即安全；
- 建表/迁移：M0-C2 migrations.py；驱动选型：内置 sqlite3，无 ORM（M0-cards §3）。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class Database:
    def __init__(self, path: Path, *, busy_timeout_ms: int = 5000):
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms
        # check_same_thread=False：写连接固定跑在写通道的单线程 executor，
        # 读连接按线程各自持有；串行化保护由写通道的全局序承担
        self._write_conn = sqlite3.connect(
            str(path), timeout=busy_timeout_ms / 1000, isolation_level=None,
            check_same_thread=False,
        )
        self._write_conn.execute("PRAGMA journal_mode=WAL")
        self._write_conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._write_conn.execute("PRAGMA foreign_keys=ON")
        self._read_local = threading.local()
        self._read_conns: dict[int, sqlite3.Connection] = {}
        self._read_registry_lock = threading.Lock()
        self.closed = False

    @property
    def write_conn(self) -> sqlite3.Connection:
        return self._write_conn

    @property
    def read_conn(self) -> sqlite3.Connection:
        """当前线程的只读连接（懒创建，query_only=ON，能用 WAL 共享内存）。"""
        conn = getattr(self._read_local, "conn", None)
        if conn is None:
            if self.closed:
                raise RuntimeError("数据库已关闭")
            conn = sqlite3.connect(
                str(self.path), timeout=self.busy_timeout_ms / 1000,
                isolation_level=None, check_same_thread=False,
            )
            conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA query_only=ON")
            self._read_local.conn = conn
            with self._read_registry_lock:
                self._read_conns[threading.get_ident()] = conn
        return conn

    @property
    def journal_mode(self) -> str:
        return self.read_conn.execute("PRAGMA journal_mode").fetchone()[0]

    def checkpoint_passive(self) -> tuple[int, int, int]:
        """PASSIVE checkpoint（§7 第 4 步）：被读事务阻塞则如实返回，不等待。"""
        return self._write_conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()

    def close(self) -> None:
        if not self.closed:
            with self._read_registry_lock:
                for conn in self._read_conns.values():
                    try:
                        conn.close()
                    except sqlite3.Error:
                        pass
                self._read_conns.clear()
            self._write_conn.close()
            self.closed = True
