"""机密值登记与日志脱敏（安全红线：AGENTCREW_TOKEN 与 api_key 绝不进日志）。"""

from __future__ import annotations

import threading

_MIN_REDACTABLE_LEN = 8  # 过短的值不登记，避免把普通词误替换成 ***


_lock = threading.Lock()
_values: set[str] = set()


def register_secret(value: str | None) -> None:
    """登记一个机密值；此后所有日志输出中出现它都会被替换为 ***。"""
    if not value or len(value) < _MIN_REDACTABLE_LEN:
        return
    with _lock:
        _values.add(value)


def known_secrets() -> tuple[str, ...]:
    with _lock:
        return tuple(_values)


def redact(text: str) -> str:
    """把已登记机密值的任何出现替换为 ***（纯函数，供参数化单测）。"""
    for secret in known_secrets():
        if secret in text:
            text = text.replace(secret, "***")
    return text


def reset() -> None:
    """清空登记表（测试用）。"""
    with _lock:
        _values.clear()
