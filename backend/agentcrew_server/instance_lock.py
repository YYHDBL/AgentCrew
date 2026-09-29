"""data-dir 准备与 instance.lock 单实例锁（backend-service.md §1/§9）。

退出码约定（M0-cards C1）：data-dir 不可写 = 1（明确报错非崩溃）；
instance.lock 已锁 = 2（另一实例在跑）。
"""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path

_DATA_SUBDIRS = ("logs", "backups", "conversations", "artifacts")


class DataDirNotWritable(RuntimeError):
    pass


class InstanceLockError(RuntimeError):
    pass


def prepare_data_dir(data_dir: Path) -> None:
    """创建数据目录布局（§9）；不可写抛 DataDirNotWritable（明确报错非崩溃）。"""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        for name in _DATA_SUBDIRS:
            (data_dir / name).mkdir(exist_ok=True)
        probe = data_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise DataDirNotWritable(
            f"data-dir 不可写：{data_dir}（{e.strerror or e}）"
        ) from None


class InstanceLock:
    """flock 独占锁；进程退出自动释放，release 幂等。"""

    def __init__(self, data_dir: Path):
        self._path = data_dir / "instance.lock"
        self._fd: int | None = None

    def acquire(self) -> None:
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            if e.errno in (errno.EACCES, errno.EAGAIN):
                raise InstanceLockError(
                    f"instance.lock 已被持有（另一实例正在运行）：{self._path}"
                ) from None
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
