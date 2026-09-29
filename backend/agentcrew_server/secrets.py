"""机密值登记与日志脱敏（安全红线：AGENTCREW_TOKEN 与 api_key 绝不进日志）。

外审回稿修订：取消登记的最小长度限制——红线是绝对的，登记表里只有被
显式注册为机密的值（token、api_key），不存在"误伤普通词"的来源；短密钥
（如用户手填的短 api_key）出现在异常文本里同样必须被遮蔽。
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_values: set[str] = set()


def register_secret(value: str | None) -> None:
    """登记一个机密值（任何非空长度）；此后日志中出现它都会被替换为 ***。"""
    if not value:
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
