#!/usr/bin/env python3
"""对打包 sidecar 的真实运行核查（任务卡 M4-02 固定命令）。

  backend/.venv/bin/python scripts/release/verify_sidecar.py \
      --binary desktop/resources/sidecar/darwin-arm64/agentcrew-sidecar \
      --data-dir desktop/.artifacts/m4-sidecar/data

受限 PATH + 独立工作目录启动真实二进制；HF_HUB_OFFLINE 强制计数资源只
能来自本地资源目录（对来源站点零访问的强制证据）。核查项：就绪与健康、
数据库初始化与迁移、FTS5、审计校验日志、计数资源读取（context.budget_checked）、
一次真实模型工具调用（写入-审批-完成）、SIGTERM 优雅退出、父进程终止
后的自行退出与无残留。报告（脱敏）写入 desktop/.artifacts/m4-sidecar/。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "desktop" / ".artifacts" / "m4-sidecar"
READY_RE = re.compile(r"^AGENTCREW_READY (\{\"port\":\s*(\d+)\})$")


class Checks:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def record(self, name: str, passed: bool, detail: str = "") -> None:
        self.items.append({"check": name, "result": "passed" if passed else "failed",
                           "detail": detail[:2000]})
        mark = "✓" if passed else "✗"
        print(f"{mark} {name}" + (f"：{detail[:200]}" if detail and not passed else ""))

    @property
    def failed(self) -> list[dict]:
        return [item for item in self.items if item["result"] == "failed"]


def fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def sidecar_env(token: str, tokenizer_dir: Path) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.environ.get("HOME", "/Users/unknown"),
        "AGENTCREW_TOKEN": token,
        "AGENTCREW_TOKENIZER_DIR": str(tokenizer_dir),
        # 强制计数资源不得访问来源站点：本地目录是唯一来源
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "LANG": "en_US.UTF-8",
    }
    for name in ("NO_PROXY", "no_proxy"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    # 模型端点代理（如开发机需要）经显式白名单透传，其余环境一律不透传
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def wait_ready(process: subprocess.Popen, stdout_lines: list[str],
               timeout: float) -> tuple[int | None, float]:
    """读 stdout 直到就绪标记；返回（端口, 用时秒）。"""
    started = time.monotonic()
    deadline = started + timeout
    buffer = ""
    while time.monotonic() < deadline:
        char = process.stdout.read(1)  # type: ignore[union-attr]
        if char:
            buffer += char
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                stdout_lines.append(line)
                match = READY_RE.match(line.strip())
                if match:
                    return int(match.group(2)), time.monotonic() - started
        elif process.poll() is not None:
            break
        else:
            time.sleep(0.01)
    return None, time.monotonic() - started


def api(port: int, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                             headers={"Authorization": f"Bearer {token}"},
                             timeout=30)


def wait_task_terminal(data_dir: Path, task_id: str, timeout: float) -> str:
    db_path = data_dir / "agentcrew.db"
    deadline = time.monotonic() + timeout
    status = "unknown"
    while True:  # 至少查询一次：timeout=0 也返回真实状态
        try:
            conn = sqlite3.connect(db_path, timeout=2)
            row = conn.execute("SELECT status FROM task_runs WHERE id=?",
                               (task_id,)).fetchone()
            conn.close()
            if row and row[0] in ("completed", "failed", "cancelled", "interrupted"):
                return row[0]
            status = row[0] if row else status
        except sqlite3.Error:
            pass
        if time.monotonic() >= deadline:
            return status
        time.sleep(min(0.5, max(deadline - time.monotonic(), 0.05)))


def pending_approvals(data_dir: Path, task_id: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(data_dir / "agentcrew.db", timeout=2)
    rows = conn.execute(
        "SELECT json_extract(payload,'$.tool_call_id'),"
        "       json_extract(payload,'$.target')"
        " FROM run_events re WHERE re.task_run_id=? AND re.type='permission.requested'"
        "   AND NOT EXISTS (SELECT 1 FROM run_events r2"
        "     WHERE r2.type='permission.resolved'"
        "       AND json_extract(r2.payload,'$.tool_call_id')"
        "         = json_extract(re.payload,'$.tool_call_id'))"
        " ORDER BY re.global_seq", (task_id,)).fetchall()
    conn.close()
    return [(row[0], row[1] or "") for row in rows]


async def real_model_task(client: httpx.AsyncClient, data_dir: Path,
                          checks: Checks) -> None:
    # soul 单发生成有字符配额，真实模型输出存在方差；按真实行为重试新会话，
    # 每次尝试与失败原因都进报告，不做任何内部状态干预
    attempts: list[dict] = []
    for attempt in range(1, 4):
        target = data_dir / "workspaces" / "default" / f"sidecar-verify-{attempt}.md"
        instruction = (
            f"请调用 write_file 工具把一行文字 “agentcrew sidecar verify {int(time.time())}” "
            f"写入文件 {target}，写完后直接回复“已写入”三个字，不要做其他事。")
        response = await client.post("/api/conversations",
                                     json={"instruction": instruction})
        response.raise_for_status()
        payload = response.json()["data"]
        task_id = payload["task_run_id"]
        deadline = time.monotonic() + 240
        decided: set[str] = set()
        status = "unknown"
        while time.monotonic() < deadline:
            for call_id, tool_target in pending_approvals(data_dir, task_id):
                if call_id in decided or tool_target != str(target):
                    continue
                decision = await client.post(f"/api/tool-approvals/{call_id}",
                                             json={"decision": "allow_once"})
                decision.raise_for_status()
                decided.add(call_id)
                print(f"  审批 allow_once：{call_id[:12]}… target={Path(tool_target).name}")
            status = wait_task_terminal(data_dir, task_id, 0)
            if status in ("completed", "failed", "cancelled", "interrupted"):
                break
            await asyncio.sleep(0.5)
        failure = ""
        if status == "failed":
            conn = sqlite3.connect(data_dir / "agentcrew.db", timeout=2)
            row = conn.execute(
                "SELECT json_extract(payload,'$.reason'), substr(payload,1,400)"
                " FROM run_events WHERE task_run_id=? AND type='run.failed'",
                (task_id,)).fetchone()
            conn.close()
            failure = (row[1] or "")[:400] if row else ""
        attempts.append({"attempt": attempt, "task_run_id": task_id,
                         "status": status, "fail_payload": failure})
        if status == "completed":
            checks.record("真实模型工具调用任务终态 completed", True,
                          f"attempts={attempt}")
            checks.record("工具调用真实写入目标文件",
                          target.is_file()
                          and "agentcrew sidecar verify" in target.read_text(),
                          f"exists={target.is_file()}")
            checks.items[-1]["attempts"] = attempts
            return
    checks.record("真实模型工具调用任务终态 completed", False,
                  f"attempts={attempts}")
    checks.record("工具调用真实写入目标文件", False, "任务未完成")


def read_logs(data_dir: Path) -> str:
    logs = data_dir / "logs"
    if not logs.is_dir():
        return ""
    chunks = []
    for path in sorted(logs.glob("*.log*")):
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def sanitizer(token: str) -> callable:  # type: ignore[valid-type]
    def clean(text: str) -> str:
        return text.replace(token, "[REDACTED]")
    return clean


def spawn_sidecar(binary: Path, port: int, data_dir: Path, cwd: Path,
                  parent_pid: int | None, env: dict[str, str]) -> subprocess.Popen:
    argv = [str(binary), "--port", str(port), "--data-dir", str(data_dir)]
    if parent_pid is not None:
        argv += ["--parent-pid", str(parent_pid)]
    return subprocess.Popen(argv, cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)


def leftovers(binary_name: str) -> list[str]:
    result = subprocess.run(["pgrep", "-fl", binary_name],
                            capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--model-config", type=Path,
                        default=ROOT / "backend" / "data" / "config.json",
                        help="真实模型配置来源（仅提取 models 段，凭据不进入报告）")
    parser.add_argument("--startup-budget", type=float, default=30.0)
    args = parser.parse_args()

    checks = Checks()
    binary = args.binary.resolve()
    data_dir = args.data_dir.resolve()
    tokenizer_dir = (ROOT / "desktop" / "resources" / "tokenizers").resolve()
    if not binary.is_file():
        raise SystemExit(f"可执行文件不存在：{binary}")
    if not tokenizer_dir.is_dir():
        raise SystemExit(f"计数资源目录不存在：{tokenizer_dir}")

    # 真实模型凭据：缺失只阻挡模型链路检查，其余照常执行
    model_entry = None
    if args.model_config.is_file():
        models = json.loads(args.model_config.read_text(encoding="utf-8")).get("models", {})
        entry = models.get("main", {})
        if entry.get("api_key") and entry.get("model"):
            model_entry = entry
    if model_entry is None:
        checks.record("真实模型凭据可用", False, "blocked-no-credentials（模型链路检查跳过）")
    else:
        checks.record("真实模型凭据可用", True)

    fresh_dir(data_dir)
    if model_entry is not None:
        (data_dir / "config.json").write_text(json.dumps({
            "models": {"main": model_entry, "aux": model_entry}}, ensure_ascii=False),
            encoding="utf-8")
    cwd = fresh_dir(REPORT_DIR / "cwd")
    token = f"verify-{random.randbytes(24).hex()}"
    env = sidecar_env(token, tokenizer_dir)

    # ── 第一次启动：就绪、健康、库/FTS5/审计、计数资源、真实模型任务 ──
    port = random.randint(20000, 40000)
    stdout_lines: list[str] = []
    process = spawn_sidecar(binary, port, data_dir, cwd, None, env)
    ready_port, startup_seconds = wait_ready(process, stdout_lines, args.startup_budget)
    checks.record(f"就绪标记（{startup_seconds:.1f}s ≤ {args.startup_budget}s 预算）",
                  ready_port is not None and startup_seconds <= args.startup_budget,
                  f"ready_port={ready_port} lines={stdout_lines[-3:]}")
    if ready_port is None:
        report(checks, token, data_dir, stdout_lines)
        return 1

    async def phase_one() -> None:
        async with api(ready_port, token) as client:  # type: ignore[arg-type]
            health = await client.get("/api/health")
            checks.record("健康响应 ok",
                          health.status_code == 200
                          and health.json().get("data", {}).get("status") == "ok",
                          f"status_code={health.status_code}")
            search = await client.get("/api/memory/search", params={
                "query": "验收材料", "workspace_id": "default", "agent_id": "default"})
            checks.record("FTS5 检索接口（MATCH 查询路径）", search.status_code == 200,
                          f"status_code={search.status_code} body={search.text[:200]}")
            if model_entry is not None:
                await real_model_task(client, data_dir, checks)

    asyncio.run(phase_one())

    db_path = data_dir / "agentcrew.db"
    checks.record("SQLite 数据库已初始化", db_path.is_file())
    conn = sqlite3.connect(data_dir / "agentcrew.db", timeout=2)
    version = conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations"
                           ).fetchone()[0]
    fts_tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%fts5%'").fetchall()]
    conn.close()
    checks.record("迁移版本已写入", version > 0, f"user_version={version}")
    checks.record("FTS5 虚拟表存在（SQLite 扩展随包可用）", len(fts_tables) >= 3,
                  f"tables={fts_tables}")
    logs = read_logs(data_dir)
    checks.record("审计链两级校验通过日志", "审计链校验通过" in logs)
    checks.record("计数资源读取（context.budget_checked）",
                  "context.budget_checked" in logs,
                  "日志缺少计数检查记录" if "context.budget_checked" not in logs else "")

    # ── SIGTERM 优雅退出 ──
    os.killpg(process.pid, signal.SIGTERM)
    try:
        code = process.wait(timeout=15)
        checks.record("SIGTERM 优雅退出 exit 0", code == 0, f"exit_code={code}")
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        checks.record("SIGTERM 优雅退出 exit 0", False, "15 秒未退出，已 SIGKILL")

    # ── 父进程终止 → sidecar 自行退出，无残留 ──
    helper = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True)
    data_dir_2 = fresh_dir(REPORT_DIR / "data-parentkill")
    if model_entry is not None:
        (data_dir_2 / "config.json").write_text(json.dumps({
            "models": {"main": model_entry, "aux": model_entry}}, ensure_ascii=False),
            encoding="utf-8")
    process2 = spawn_sidecar(binary, port + 1, data_dir_2, cwd, helper.pid, env)
    stdout2: list[str] = []
    ready2, _ = wait_ready(process2, stdout2, args.startup_budget)
    if ready2 is None:
        checks.record("第二次启动就绪（父进程看护场景）", False,
                      f"lines={stdout2[-3:]}")
    else:
        checks.record("第二次启动就绪（父进程看护场景）", True)
        os.kill(helper.pid, signal.SIGKILL)
        helper.wait()  # 收割僵尸：kill(pid,0) 对僵尸进程仍成功，看护无法触发
        try:
            code2 = process2.wait(timeout=15)
            checks.record("父进程终止后 sidecar 自行退出", True, f"exit_code={code2}")
        except subprocess.TimeoutExpired:
            os.killpg(process2.pid, signal.SIGKILL)
            process2.wait()
            checks.record("父进程终止后 sidecar 自行退出", False, "15 秒未自行退出")

    time.sleep(1.0)
    remaining = [line for line in leftovers("agentcrew-sidecar")
                 if str(data_dir) in line or str(data_dir_2) in line]
    checks.record("无残留 sidecar 进程", not remaining, f"leftover={remaining}")

    return report(checks, token, data_dir, stdout_lines)


def report(checks: Checks, token: str, data_dir: Path, stdout_lines: list[str]) -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    clean = sanitizer(token)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "checks": checks.items,
        "stdout_tail": [clean(line) for line in stdout_lines[-20:]],
        "log_tail": clean(read_logs(data_dir))[-4000:],
    }
    path = REPORT_DIR / "verify-report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告：{path}")
    if checks.failed:
        print(f"失败 {len(checks.failed)} 项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
