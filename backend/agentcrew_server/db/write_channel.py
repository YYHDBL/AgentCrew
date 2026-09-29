"""写通道：全部数据库变更的唯一通道（backend-service.md §2）。

- 单写 executor（单线程线程池）——写入天然全局串行，发布顺序 = 提交顺序；
- 事务内不等待任何外部系统（模型/工具/HTTP/SSE）——调用方只提交纯 SQL 闭包；
- busy/locked 的 OperationalError 退避重试 3 次；IntegrityError（UNIQUE/FK）
  是逻辑错误，不重试直接抛出。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

_BUSY_RETRIES = 3
_BACKOFF_BASE = 0.05


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


class WriteChannel:
    def __init__(self, database_write_conn: sqlite3.Connection):
        self._conn = database_write_conn
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="agentcrew-db-write"
        )
        self.closed = False

    async def execute(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """异步入口：闭包投递到单写 executor（asyncio.to_thread 语义）。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.execute_sync, fn)

    def execute_sync(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """同步入口（脚本/测试用）；服务端路径一律走 execute。"""
        if self.closed:
            raise RuntimeError("写通道已关闭")
        attempt = 0
        while True:
            try:
                return fn(self._conn)
            except sqlite3.OperationalError as e:
                if not _is_busy(e) or attempt >= _BUSY_RETRIES:
                    raise
                attempt += 1
                time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._executor.shutdown(wait=True)
