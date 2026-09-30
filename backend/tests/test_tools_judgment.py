"""判定层参数化单测（M0-C5 验收五矩阵：白名单 × 元字符 × find 否决 × scope × 受保护路径）。"""

from pathlib import Path

import pytest

from agentcrew_core.tools.judgment import (
    bash_readonly,
    build_protected_paths,
    host_allowed,
    path_in_scope,
    path_is_protected,
)


@pytest.mark.parametrize("command,expected", [
    # 白名单矩阵
    ("ls -la /tmp", True),
    ("cat a.txt", True),
    ("head -n 5 b.log", True),
    ("tail -3 c.log", True),
    ("grep -r pattern dir", True),
    ("wc -l f", True),
    ("pwd", True),
    ("file x.bin", True),
    ("stat y", True),
    ("du -sh dir", True),
    ("diff a b", True),
    ("find . -name '*.md'", True),
    ("/bin/ls /tmp", True),                      # 绝对路径取 basename
    # 非白名单首词
    ("python3 script.py", False),
    ("awk '{print $1}'", False),
    ("sed -i s/a/b/ f", False),
    ("rm -rf /", False),
    ("echo hello", False),
    ("", False),
    # 元字符一票否决（即使首词在白名单）
    ("ls; rm -rf /", False),
    ("cat a && cat b", False),
    ("cat a || cat b", False),
    ("ls | grep x", False),
    ("cat `whoami`", False),
    ("cat $(whoami)", False),
    ("cat (x)", False),
    ("cat a > b", False),
    ("cat a < b", False),
    ("ls\nrm -rf /", False),
    # find 参数否决
    ("find . -name '*.tmp' -delete", False),
    ("find . -exec rm {} \\;", False),
    ("find . -execdir cmd {}", False),
    ("find . -ok cmd {} \\;", False),
    ("find . -okdir cmd {}", False),
    ("find . -fprintf /tmp/x '%p'", False),
    ("find . -fprint /tmp/x", False),
    ("find . -fls /tmp/x", False),
    # 带引号参数的解析
    ('grep "two words" file', True),
])
def test_bash_readonly_matrix(command, expected):
    allowed, reason = bash_readonly(command)
    assert allowed is expected, f"{command!r} → {allowed}（{reason}）"


def test_scope_matrix(tmp_path):
    scope = tmp_path / "ws"
    scope.mkdir()
    inside = scope / "a.txt"
    inside.write_text("x")
    assert path_in_scope(inside, [scope])
    assert path_in_scope(scope / "sub" / "b.txt", [scope])   # 目标不存在，前缀在即合法
    assert not path_in_scope(tmp_path / "outside.txt", [scope])
    assert not path_in_scope(scope, [tmp_path / "other"])


def test_scope_symlink_escape(tmp_path):
    """符号链接指向 scope 外：realpath 解析后按真实路径判定（v1.1 收紧）。"""
    scope = tmp_path / "ws"
    scope.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("s")
    link = scope / "alias.txt"
    link.symlink_to(secret)
    assert not path_in_scope(link, [scope])
    assert path_in_scope(secret, [tmp_path])  # 真实路径在范围内则合法


def test_protected_paths(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".ssh").mkdir()
    protected = build_protected_paths(data, home)
    for name in ("agentcrew.db", "config.json", "chain-head.txt", "instance.lock",
                 "USER.md", "soul.md"):
        assert path_is_protected(data / name, protected), name
    assert path_is_protected(home / ".ssh" / "id_rsa", protected)
    assert path_is_protected(home / ".aws" / "credentials", protected)
    assert not path_is_protected(tmp_path / "normal.txt", protected)


@pytest.mark.parametrize("url,allowed_hosts,expected", [
    ("https://api.example.com/v1", ["api.example.com"], True),
    ("https://api.example.com/v1", ["*.example.com"], True),
    ("https://evil.com/x", ["*.example.com"], False),
    ("https://example.com.evil.com/x", ["*.example.com"], False),  # 后缀伪装
    ("https://evil.com", [], False),  # M0 默认空 = 全部需审批
    ("not a url", ["x.com"], False),
])
def test_host_allowed_matrix(url, allowed_hosts, expected):
    ok, _ = host_allowed(url, allowed_hosts)
    assert ok is expected
