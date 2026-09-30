"""五件套真实执行测试（真实文件/真实进程/真实网络/真实沙盒，ADR-006）。"""

import asyncio
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from agentcrew_core.tools import (
    ToolInvocation,
    WorkContext,
    build_default_registry,
    build_protected_paths,
    new_call_id,
)
from agentcrew_core.tools.scheduler import ToolScheduler


@pytest.fixture
def env(tmp_path):
    """返回 (ctx, events)：events 是事件出口收到的 (type, payload) 序列。"""
    scope = tmp_path / "ws"
    (scope / "materials").mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    for name in ("agentcrew.db", "config.json", "chain-head.txt", "instance.lock"):
        (data / name).write_text("x")
    (data / "logs").mkdir()
    events: list[tuple[str, dict]] = []

    async def sink(event_type, payload):
        events.append((event_type, payload))

    ctx = WorkContext(
        scope=[scope], protected=build_protected_paths(data, home),
        artifacts_dir=tmp_path / "artifacts", task_run_id="run-t",
        emit=sink,
    )
    ctx.events = events  # 测试观察口（事件出口收到的原始序列）
    return ctx


@pytest.fixture
def scheduler():
    return ToolScheduler(build_default_registry())


def run(coro):
    return asyncio.run(coro)


async def _invoke(scheduler, name, input, ctx):
    inv = ToolInvocation(new_call_id(), name, input)
    result = await scheduler.run(inv, ctx)
    return inv, result


# ── read_file ─────────────────────────────────────────────────────

def test_read_file_real_with_paging(scheduler, env, tmp_path):
    target = tmp_path / "ws" / "doc.md"
    target.write_text("\n".join(f"第 {i} 行" for i in range(1, 11)))
    _, result = run(_invoke(scheduler, "read_file",
                            {"path": str(target), "offset": 3, "limit": 2}, env))
    assert result.ok
    assert result.output.splitlines() == ["第 3 行", "第 4 行"]
    assert result.details["total_lines"] == 10


def test_read_file_protected_and_out_of_scope(scheduler, env, tmp_path):
    db = tmp_path / "data" / "agentcrew.db"
    _, result = run(_invoke(scheduler, "read_file", {"path": str(db)}, env))
    assert not result.ok and result.error.startswith("PROTECTED_PATH")
    _, result2 = run(_invoke(scheduler, "read_file", {"path": "/etc/hosts"}, env))
    assert not result2.ok and result2.error.startswith("OUT_OF_SCOPE")


# ── write_file ────────────────────────────────────────────────────

def test_write_file_atomic_with_sha256_and_artifact_events(scheduler, env, tmp_path):
    target = tmp_path / "ws" / "out" / "summary.md"
    content = "# 总结\n正文内容" * 100
    inv, result = run(_invoke(scheduler, "write_file",
                              {"path": str(target), "content": content}, env))
    assert result.ok
    assert target.read_text(encoding="utf-8") == content
    assert result.details["sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert not list(target.parent.glob(".*tmp*")), "原子写不留 tmp 残留"
    arts = [(t, p) for t, p in env.events
            if t.startswith("artifact.") and p["artifact_id"] == inv.call_id]
    assert [t for t, _ in arts] == ["artifact.created", "artifact.ready"]
    assert arts[1][1]["size_bytes"] == len(content.encode("utf-8"))


def test_write_file_rejects_protected_and_out_of_scope(scheduler, env, tmp_path):
    _, result = run(_invoke(scheduler, "write_file",
                            {"path": str(tmp_path / "data" / "config.json"),
                             "content": "hack"}, env))
    assert not result.ok and result.error.startswith("PROTECTED_PATH")
    _, result2 = run(_invoke(scheduler, "write_file",
                             {"path": "/tmp/c5-outside.txt", "content": "x"}, env))
    assert not result2.ok and result2.error.startswith("OUT_OF_SCOPE")
    assert not Path("/tmp/c5-outside.txt").exists()


# ── bash ──────────────────────────────────────────────────────────

def test_bash_whitelisted_command_runs(scheduler, env, tmp_path):
    target = tmp_path / "ws" / "hello.txt"
    target.write_text("world")
    inv, result = run(_invoke(
        scheduler, "bash", {"command": f"cat {target}"}, env))
    assert result.ok and result.output.strip() == "world"
    # 只读判定进入事件元数据（C6 审批闸门依据）
    prepared = next(p for t, p in env.events
                    if t == "tool.prepared" and p["call_id"] == inv.call_id)
    assert prepared["read_only_verdict"] is True


def test_bash_env_whitelist_token_absent(scheduler, env):
    """安全红线：子进程环境只含白名单变量（+sh 自置的 PWD/SHLVL/_），
    AGENTCREW_TOKEN 绝不传入。"""
    os.environ["AGENTCREW_TOKEN"] = "tok-secret-abcdef123456"
    try:
        _, result = run(_invoke(
            scheduler, "bash",
            {"command": "echo TOKEN=[$AGENTCREW_TOKEN] "
                        "NAMES=$(env | cut -d= -f1 | sort | tr '\\n' ' ')"},
            env,
        ))
        assert result.ok
        assert "TOKEN=[]" in result.output, f"token 泄漏进子进程：{result.output!r}"
        names = set(result.output.split("NAMES=")[1].split()) - {"NAMES"}
        shell_set = {"PWD", "SHLVL", "_", "OLDPWD"}
        unexpected = names - shell_set - {"PATH", "HOME", "LANG", "TZ", "TERM"}
        assert not unexpected, f"白名单外变量进入子进程：{sorted(unexpected)}"
    finally:
        del os.environ["AGENTCREW_TOKEN"]


def test_bash_seatbelt_blocks_out_of_scope_write(scheduler, env):
    outside = Path("/tmp/c5-seatbelt-outside.txt")
    outside.unlink(missing_ok=True)
    _, result = run(_invoke(scheduler, "bash",
                            {"command": f"echo x > {outside}"}, env))
    assert not result.ok, "scope 外写入应被 Seatbelt 内核级拒绝"
    assert "Operation not permitted" in result.details.get("stderr", "")
    assert not outside.exists()


def test_bash_seatbelt_blocks_network(scheduler, env):
    _, result = run(_invoke(
        scheduler, "bash",
        {"command": "curl -s -m 5 -o /dev/null -w %{http_code} https://example.com",
         "timeout_ms": 15_000}, env,
    ))
    assert not result.ok, "沙盒内 curl 应失败（网络全禁，http_request 是唯一网络入口）"
    assert result.details["exit_code"] != 0


def test_bash_seatbelt_blocks_credential_read(scheduler, env, tmp_path):
    key = tmp_path / "home" / ".ssh" / "id_rsa"
    key.write_text("PRIVATE-KEY-MATERIAL")
    _, result = run(_invoke(scheduler, "bash",
                            {"command": f"cat {key}"}, env))
    assert not result.ok, "凭据路径读取应被 Seatbelt 内核级拒绝"
    assert "PRIVATE" not in result.output + result.details.get("stderr", "")


def test_bash_write_inside_scope_allowed(scheduler, env, tmp_path):
    target = tmp_path / "ws" / "from_bash.txt"
    _, result = run(_invoke(scheduler, "bash",
                            {"command": f"echo made-in-sandbox > {target}"}, env))
    assert result.ok, result.details
    assert target.read_text().strip() == "made-in-sandbox"


def test_bash_timeout_kills_process_group(scheduler, env):
    _, result = run(_invoke(scheduler, "bash",
                            {"command": "sleep 30", "timeout_ms": 800}, env))
    assert not result.ok and result.error == "TIMEOUT"
    leftovers = subprocess.run(
        ["ps", "-eo", "command"], capture_output=True, text=True,
    ).stdout
    sleep_lines = [l for l in leftovers.splitlines()
                   if l.strip() == "sleep 30"]
    assert not sleep_lines, "进程组收割后不应残留 sleep 30"


def test_bash_big_output_externalized(scheduler, env):
    inv, result = run(_invoke(scheduler, "bash", {"command": "seq 1 20000"}, env))
    assert result.ok
    assert result.artifact_path and Path(result.artifact_path).exists()
    assert "已外部化" in result.output and result.artifact_path in result.output
    content = Path(result.artifact_path).read_text()
    assert content.startswith("1\n2\n") and len(content.encode()) > 32 * 1024
    # 外部化也计入 artifacts 投影（ADR-008 产物口径）
    arts = [(t, p) for t, p in env.events
            if t.startswith("artifact.") and p["artifact_id"] == inv.call_id]
    assert [t for t, _ in arts] == ["artifact.created", "artifact.ready"]


# ── http_request ──────────────────────────────────────────────────

def test_http_request_real(scheduler, env):
    env.allowed_hosts = ["example.com"]
    _, result = run(_invoke(scheduler, "http_request",
                            {"url": "https://example.com"}, env))
    assert result.ok and result.details["status_code"] == 200
    assert "Example Domain" in result.output


def test_http_request_host_not_allowed(scheduler, env):
    env.allowed_hosts = ["only.trusted.io"]
    _, result = run(_invoke(scheduler, "http_request",
                            {"url": "https://example.com"}, env))
    assert not result.ok and result.error.startswith("HOST_NOT_ALLOWED")


# ── ask_user ──────────────────────────────────────────────────────

def test_ask_user_full_roundtrip(scheduler, env):
    async def resolver(request_id):
        return "用 CSV 格式"

    env.ask_resolver = resolver
    inv, result = run(_invoke(scheduler, "ask_user",
                              {"question": "清单要什么格式？",
                               "options": ["CSV", "Excel"]}, env))
    assert result.ok and result.output == "用 CSV 格式"
    qs = [(t, p) for t, p in env.events
          if t.startswith("question.") and p["request_id"] == inv.call_id]
    assert [t for t, _ in qs] == ["question.requested", "question.answered"]
    assert qs[0][1]["question"] == "清单要什么格式？"
    assert qs[1][1]["answer"] == "用 CSV 格式"


def test_ask_user_cancel_means_no_answer(scheduler, env):
    async def resolver(request_id):
        return None

    env.ask_resolver = resolver
    _, result = run(_invoke(scheduler, "ask_user", {"question": "在吗"}, env))
    assert result.ok and result.details["cancelled"] is True
    assert "取消" in result.output
