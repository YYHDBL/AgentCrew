"""判定纯函数（harness-session §6.1 判定纪律 / ADR-008）。

全部无副作用、可参数化单测；路径判定一律先 realpath（解析符号链接）再比前缀。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

# ── bash 只读三重判定 ────────────────────────────────────────────

BASH_READONLY_WHITELIST = frozenset(
    {"ls", "cat", "head", "tail", "grep", "find", "wc", "pwd", "file", "stat", "du", "diff"}
)

# 元字符/重定向一票否决（; && || | 反引号 $( ) > < 换行）——宁可误报
BASH_METACHARS = (";", "&&", "||", "|", "`", "$(", "(", ")", ">", "<", "\n")

# find 参数否决（v1.7 重入）：出现任一即丧失只读资格
FIND_VETO_TOKENS = frozenset(
    {"-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprintf", "-fprint", "-fls"}
)


def bash_readonly(command: str) -> tuple[bool, str]:
    """只读判定 = 首词白名单 × 无元字符 × find 参数否决。返回 (是否只读, 原因)。"""
    if not command or not command.strip():
        return False, "空命令"
    for ch in BASH_METACHARS:
        if ch in command:
            return False, f"含元字符/重定向：{ch!r}"
    import shlex

    try:
        tokens = shlex.split(command)
    except ValueError as e:
        return False, f"无法解析：{e}"
    if not tokens:
        return False, "空命令"
    first = Path(tokens[0]).name
    if first not in BASH_READONLY_WHITELIST:
        return False, f"首词不在只读白名单：{first}"
    if first == "find":
        for token in tokens[1:]:
            if token in FIND_VETO_TOKENS:
                return False, f"find 参数否决：{token}"
    return True, "白名单只读命令"


# ── 路径合法范围与受保护路径 ─────────────────────────────────────

def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def path_in_scope(path: Path, scope: list[Path]) -> bool:
    """realpath 后必须落在任一 scope 根内（符号链接逃逸在此被解析掉）。"""
    real = Path(path).resolve()
    return any(_within(real, Path(root).resolve()) for root in scope)


def path_is_protected(path: Path, protected: list[Path]) -> bool:
    real = Path(path).resolve()
    return any(real == Path(p).resolve() or _within(real, Path(p).resolve())
               for p in protected)


def build_protected_paths(data_dir: Path, home: Path | None = None) -> list[Path]:
    """受保护路径清单（v1.7 + 外审回稿扩充）：平台内部数据 + 持久化提示词
    载体 + 常见凭据位置（读写双向硬禁）。"""
    home = home or Path.home()
    candidates = [
        data_dir / "agentcrew.db",
        data_dir / "config.json",
        data_dir / "chain-head.txt",
        data_dir / "instance.lock",
        data_dir / "backups",
        data_dir / "logs",
        data_dir / "USER.md",
        data_dir / "soul.md",
        home / ".ssh",
        home / ".aws",
        home / ".gnupg",
        home / ".kube",
        home / ".netrc",
        home / ".git-credentials",
        home / ".docker" / "config.json",
        home / ".config" / "gcloud",
    ]
    # MEMORY.md（当前及未来工作区）——目录可能不存在，按字面列入
    workspaces = data_dir / "workspaces"
    if workspaces.exists():
        candidates.extend(p / "MEMORY.md" for p in workspaces.iterdir() if p.is_dir())
    else:
        candidates.append(workspaces / "MEMORY.md")
    return candidates


# ── http_request 域名判定 ────────────────────────────────────────

def host_allowed(url: str, allowed_hosts: list[str]) -> tuple[bool, str]:
    """allowed_hosts 空 = 全部需审批（v1.7 fail-closed）；支持 *.example.com 通配。"""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False, "URL 无主机名"
    for pattern in allowed_hosts:
        pattern = pattern.lower()
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]) and host != pattern[1:]:
                return True, f"通配命中 {pattern}"
        elif host == pattern:
            return True, f"精确命中 {pattern}"
    return False, f"主机 {host} 不在 allowed_hosts"
