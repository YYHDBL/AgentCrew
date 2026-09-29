"""机密脱敏与日志格式化单测（纯函数；安全红线：token/api_key 不进日志）。"""

import logging
import sys

from agentcrew_server.logging_setup import RedactingFormatter
from agentcrew_server.secrets import redact, register_secret, reset


def setup_function(_):
    reset()


def test_redact_masks_registered_token():
    register_secret("tok_abcdef1234567890")
    assert redact("Authorization Bearer tok_abcdef1234567890") == "Authorization Bearer ***"


def test_redact_multiple_occurrences():
    register_secret("SECRETVALUE99")
    assert redact("a SECRETVALUE99 b SECRETVALUE99") == "a *** b ***"


def test_short_secret_is_still_redacted():
    # 外审回稿：红线绝对——短 api_key（如用户手填的 6 位 key）同样必须遮蔽
    register_secret("k3y!")
    register_secret("ab12")
    assert redact("api_key=k3y!") == "api_key=***"
    assert redact("token ab12 in error") == "token *** in error"


def test_empty_value_ignored():
    register_secret("")
    register_secret(None)
    assert redact("anything") == "anything"


def test_redacting_formatter_covers_exception_text():
    register_secret("tok_1234567890abcdef")
    formatter = RedactingFormatter("%(levelname)s %(message)s")
    try:
        raise ValueError("bad token tok_1234567890abcdef")
    except ValueError:
        record = logging.getLogger("t").makeRecord(
            "t", logging.ERROR, "p", 1, "捕获异常", None, sys.exc_info()
        )
    assert "tok_1234567890abcdef" not in formatter.format(record)
    assert "***" in formatter.format(record)
