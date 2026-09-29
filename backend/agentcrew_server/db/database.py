"""SQLite 连接管理（M0-C1 最小实现：WAL + busy_timeout=5s）。

建表、版本化迁移、升级前快照与投影同事务写入在 M0-C2 交付；
本模块给 C2 留出连接管理骨架。驱动选型：内置 sqlite3（M0-cards §3，
无 ORM、同步封装进 executor——C2 引入写通道时落地）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: Path):
        self.path = path
        # check_same_thread=False：连接可能被 lifespan 线程与主线程先后访问；
        # 串行化保护由 C2 的写通道（单写 executor + 全局写锁）统一承担
        self._conn = sqlite3.connect(
            str(path), timeout=5.0, isolation_level=None, check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.closed = False

    @property
    def journal_mode(self) -> str:
        return self._conn.execute("PRAGMA journal_mode").fetchone()[0]

    def checkpoint_passive(self) -> tuple[int, int, int]:
        """PASSIVE checkpoint（§7 第 4 步）：被读事务阻塞则结果如实返回，不等待。"""
        return self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()

    def close(self) -> None:
        if not self.closed:
            self._conn.close()
            self.closed = True
