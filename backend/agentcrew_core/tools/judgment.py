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

# 元字符/重定向一票否决（; && || | & 反引号 $( ) > < 换行）——宁可误报。
# 单个 & 必须否决（外审回稿 F06）：`cat x & touch y` 中 touch 会在后台执行，
# 实测产生写入——若判只读则 Seatbelt scope 内写入绕过人工审批
BASH_METACHARS = (";", "&&", "||", "|", "&", "`", "$(", "(", ")", ">", "<", "\n")

# find 参数否决（v1.7 重入）：出现任一即丧失只读资格
FIND_VETO_TOKENS = frozenset(
    {"-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprintf", "-fprint", "-fls"}
)


def _first_word_identity(command_word: str, cwd: Path | None) -> tuple[bool, str]:
    """首词必须是可信白名单程序本体（外审回稿 F06）：
    ① 不含路径分隔符（./cat、子目录脚本一律不算）；
    ② 经 PATH 解析得到绝对路径（找不到的不算）；
    ③ 解析结果不得落在 bash 的当前工作目录内（工作区内同名脚本影子化
       白名单命令的实测绕过路径）。cwd 由 WorkContext 提供（S09）：任务
       工作目录就是子进程的落点，检查须以它为基准。"""
    import shutil

    if "/" in command_word:
        return False, f"首词含路径成分：{command_word}"
    resolved = shutil.which(command_word)
    if resolved is None:
        return False, f"PATH 中找不到命令：{command_word}"
    real = Path(resolved).resolve()
    base = Path(cwd).resolve() if cwd is not None else Path.cwd().resolve()
    try:
        real.relative_to(base)
        return False, f"命令解析到工作目录内（影子脚本嫌疑）：{resolved}"
    except ValueError:
        return True, ""


def bash_readonly(command: str, cwd: Path | None = None) -> tuple[bool, str]:
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
    first = tokens[0]
    if Path(first).name not in BASH_READONLY_WHITELIST:
        return False, f"首词不在只读白名单：{first}"
    ok, reason = _first_word_identity(first, cwd)
    if not ok:
        return False, reason
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
    real = Path(str(Path(path).resolve()).casefold())
    for protected_path in protected:
        root = Path(str(Path(protected_path).resolve()).casefold())
        if "*" in root.parts:
            if real.match(str(root)):
                return True
        elif real == root or _within(real, root):
            return True
    return False


def build_protected_paths(data_dir: Path, home: Path | None = None) -> list[Path]:
    """受保护路径清单（v1.7 + 外审回稿扩充）：平台内部数据 + 持久化提示词
    载体 + 常见凭据位置（读写双向硬禁）。-wal/-shm 是库的活跃内容（WAL 中
    是已提交页，敏感标记可从 sidecar 文件读出——外审回稿 F07）。"""
    home = home or Path.home()
    db = data_dir / "agentcrew.db"
    candidates = [
        db,
        data_dir / "agentcrew.db-wal",
        data_dir / "agentcrew.db-shm",
        data_dir / "config.json",
        data_dir / "chain-head.txt",
        data_dir / "instance.lock",
        data_dir / "backups",
        data_dir / "logs",
        data_dir / "USER.md",
        data_dir / "USER.meta.json",
        data_dir / "soul.md",
        data_dir / "soul.meta.json",
        data_dir / "agents",
        data_dir / "archive",
        data_dir / "skills",
        data_dir / "conversations" / "*" / "memory-snapshot.json",
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
    candidates.extend([workspaces / "MEMORY.md", workspaces / "MEMORY.meta.json",
                       workspaces / "*" / "MEMORY.md", workspaces / "*" / "MEMORY.meta.json"])
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
