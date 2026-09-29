"""日志装配：data/logs/sidecar.log 轮转 5MB×3 + stderr 双输出，机密值脱敏。

token / api_key 永不落日志（红线）：登记进 secrets 后由 RedactingFormatter
对最终格式化文本兜底替换（含 exc_info 堆栈文本）。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .secrets import redact

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class RedactingFormatter(logging.Formatter):
    """格式化后做机密替换——任何来源（含异常堆栈）的机密文本都被覆盖。"""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(log_dir: Path, level: str) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in list(root.handlers):  # 幂等：重复装配先摘掉旧 handler
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level.upper())
    formatter = RedactingFormatter(_FORMAT)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            log_dir / "sidecar.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stderr),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    return logging.getLogger("agentcrew")


def shutdown_logging() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.flush()
        handler.close()
        root.removeHandler(handler)
