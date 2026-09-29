"""启动路径子进程级测试（外审回稿补强）：退出码 / 诊断模式集成 / 信号优雅关闭。

真实 spawn 子进程跑 agentcrew_server 入口，断言 C1 卡约定的退出码：
锁冲突=2；缺 token / data-dir 不可写 / 其他启动失败=1；信号优雅关闭=0。
"""

import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import fcntl

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
TOKEN = "t" * 40  # 仅测试用；绝不打印


def _base_env() -> dict:
    env = os.environ.copy()
    env.pop("AGENTCREW_TOKEN", None)
    return env


def _run_cli(args: list[str], env_extra: dict | None = None, timeout: float = 20):
    env = _base_env()
    env.update(env_extra or {})
    return subprocess.run(
        [PYTHON, "-m", "agentcrew_server", *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=timeout,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_missing_token_refuses_to_start(tmp_path):
    proc = _run_cli(["--port", str(_free_port()), "--data-dir", str(tmp_path)])
    assert proc.returncode == 1
    assert "AGENTCREW_TOKEN 未设置" in proc.stderr


def test_unwritable_data_dir_reports_clearly(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        proc = _run_cli(
            ["--port", str(_free_port()), "--data-dir", str(ro / "nested")],
            env_extra={"AGENTCREW_TOKEN": TOKEN},
        )
        assert proc.returncode == 1
        assert "data-dir 不可写" in proc.stderr
    finally:
        ro.chmod(0o700)


def test_instance_lock_conflict_exits_2(tmp_path):
    data_dir = tmp_path / "data"
    (data_dir / "logs").mkdir(parents=True)
    lock_fd = os.open(data_dir / "instance.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 模拟另一实例持锁
        proc = _run_cli(
            ["--port", str(_free_port()), "--data-dir", str(data_dir)],
            env_extra={"AGENTCREW_TOKEN": TOKEN},
        )
        assert proc.returncode == 2
        assert "instance.lock 已被持有" in proc.stderr
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _spawn_server(args: list[str], env_extra: dict | None = None,
                  inject: str | None = None) -> subprocess.Popen:
    env = _base_env()
    env.update(env_extra or {})
    if inject:
        # 子进程内先注入再走真实 main() 启动路径（故障注入，非 mock）
        cmd = [PYTHON, "-c", inject, *args]
    else:
        cmd = [PYTHON, "-m", "agentcrew_server", *args]
    return subprocess.Popen(
        cmd, cwd=BACKEND_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )


def _wait_ready(proc: subprocess.Popen, timeout: float = 15) -> str:
    deadline = time.monotonic() + timeout
    lines: list[str] = []
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line:
            lines.append(line.strip())
            if line.startswith("AGENTCREW_READY"):
                return line.strip()
        elif proc.poll() is not None:
            break
        else:
            time.sleep(0.05)
    raise AssertionError(f"未等到就绪标记；stdout={lines} exit={proc.poll()}")


def test_sigint_graceful_exit_0(tmp_path):
    proc = _spawn_server(
        ["--port", str(_free_port()), "--data-dir", str(tmp_path)],
        env_extra={"AGENTCREW_TOKEN": TOKEN},
    )
    try:
        _wait_ready(proc)
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_bad_migration_full_startup_enters_diagnostic_mode(tmp_path):
    """真实启动集成：注入非法 SQL 迁移 → 迁移回滚 → 诊断模式起服务 → SIGTERM 优雅退出 0。"""
    inject = (
        "import sys;"
        "import agentcrew_server.db.migrations as m;"
        "from agentcrew_server.db.migrations import Migration;"
        "m.MIGRATIONS = m.MIGRATIONS + (Migration(99, 'injected-bad',"
        " ('CREATE TABLE nope (id TEXT',)),);"
        "from agentcrew_server.cli import main;"
        "sys.exit(main())"
    )
    proc = _spawn_server(
        ["--port", str(_free_port()), "--data-dir", str(tmp_path)],
        env_extra={"AGENTCREW_TOKEN": TOKEN}, inject=inject,
    )
    try:
        _wait_ready(proc)
        log_text = (tmp_path / "logs" / "sidecar.log").read_text(encoding="utf-8")
        assert "只读诊断模式" in log_text
        assert "迁移 v99" in log_text
        # 回滚核验：版本仍是 1，坏表不存在
        conn = sqlite3.connect(str(tmp_path / "agentcrew.db"))
        try:
            version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            has_bad = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='nope'"
            ).fetchone()[0]
            assert version == 1 and has_bad == 0
        finally:
            conn.close()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == 0, "诊断模式下 SIGTERM 也应优雅退出 0"
    finally:
        if proc.poll() is None:
            proc.kill()
