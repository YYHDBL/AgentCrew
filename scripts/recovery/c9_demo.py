#!/usr/bin/env python3
"""M0-C9 验收演示（真实 GLM + 真实子进程服务 + 真实 kill -9 + 真实文件）。

三段卡面验收（M0-cards C9，场景 C 为外审回稿 F1 增补）：
  A 工具调用间隙 kill -9：多步任务（写 a.txt → ask_user 挂起）在
    waiting_user（两次工具调用之间）被 kill -9 → 重启对账 interrupted →
    resume → 回答"继续" → 写 b.txt（审批 allow） → 任务完成；
    a.txt 内容与 mtime 与中断前一致、write_file 对 a.txt 只 prepared 一次
    （无重复写入痕迹——事件流为证）
  B dispatched 后 kill -9：bash sleep 10 在 dispatched 后被 kill -9 → 重启
    → 出现在 pending-verifications 清单（含证据）→ resume 被拒 409 →
    提交"确认未执行" → resume 成功 → 任务完成且账本告知模型该调用未发生
    （重放占位 + 账本文本；结构断言）
  C（F1）resume 后二连崩：A 式任务在第一个提问挂起时 kill -9 #1 → 重启对账
    → resume 成功（模型重新提问）→ 在第二次挂起时 kill -9 #2 → 重启对账必须
    按当前收敛状态补第二条 run.interrupted（旧实现见历史事件即跳过 → 任务
    永久停 waiting_user、resume 409、队列排不动）→ 再次 resume → 完成；
    write_file 仍只 prepared 一次

用法：
  cd backend && uv run python ../scripts/recovery/c9_demo.py [--data-dir /tmp/c9-demo]
（data-dir 缺省 /tmp/agentcrew-c9-demo；config.json 从 backend/data/ 复制，
内含真实 key——本脚本绝不打印 key。）
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import httpx

TOKEN = secrets.token_hex(24)
HEADERS: dict[str, str] = {}
BACKEND = Path(__file__).resolve().parents[2] / "backend"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"   [{mark}] {name}" + (f"：{detail}" if detail else ""))
    (PASS if ok else FAIL).append(name)


# ── 子进程服务管理（真 kill -9 的前提）─────────────────────────────

def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.proc: subprocess.Popen | None = None
        self.port = 0

    def start(self) -> None:
        env = dict(os.environ)
        env["AGENTCREW_TOKEN"] = TOKEN
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "agentcrew_server",
             "--data-dir", str(self.data_dir),
             "--port", str(self.port or free_port())],
            cwd=str(BACKEND), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        marker_line: list[str] = []

        def _read_until_ready() -> None:
            assert self.proc is not None and self.proc.stdout is not None
            for line in self.proc.stdout:  # 日志与标记同流：逐行找标记
                if "AGENTCREW_READY" in line:
                    marker_line.append(line)
                    return

        reader = threading.Thread(target=_read_until_ready, daemon=True)
        reader.start()
        reader.join(timeout=30)
        line = marker_line[0] if marker_line else ""
        assert "AGENTCREW_READY" in line, f"就绪标记异常：{line!r}"
        self.port = json.loads(line.split("AGENTCREW_READY", 1)[1])["port"]
        # READY 在 lifespan（含启动对账）之后打印；health 再探一次兜底
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/api/health",
                             timeout=2.0).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise AssertionError("health 未就绪")

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def kill9(self) -> None:
        assert self.proc is not None
        os.kill(self.proc.pid, signal.SIGKILL)  # 真 kill -9（进程级）
        self.proc.wait(timeout=10)
        print(f"   [kill -9] pid={self.proc.pid} 已杀死（退出码 {self.proc.returncode}）")
        self.proc = None

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc = None


# ── 驱动件：审批 / 提问 / 终态等待 / 事件轮询 ──────────────────────

def task_events(client: httpx.Client, base: str, task_id: str,
                after_seq: int = 0, limit: int = 500) -> list[dict]:
    r = client.get(
        f"{base}/api/task-runs/{task_id}/events",
        params={"after_seq": after_seq, "limit": limit}, headers=HEADERS)
    r.raise_for_status()
    return r.json()["data"]["items"]


def task_status(client: httpx.Client, base: str, conv_id: str,
                task_id: str) -> str:
    r = client.get(f"{base}/api/conversations/{conv_id}/task-runs",
                   headers=HEADERS)
    r.raise_for_status()
    for row in r.json()["data"]:
        if row["id"] == task_id:
            return row["status"]
    return "missing"


def unresolved_approvals(client: httpx.Client, base: str,
                         task_id: str) -> list[tuple[str, str]]:
    r = client.get(f"{base}/api/task-runs/{task_id}/approvals",
                   params={"status": "pending"}, headers=HEADERS)
    r.raise_for_status()
    return [(row.get("id") or row.get("call_id"), row.get("tool") or "")
            for row in r.json()["data"]]


def pending_questions(client: httpx.Client, base: str,
                      conv_id: str) -> list[dict]:
    r = client.get(f"{base}/api/conversations/{conv_id}/questions",
                   headers=HEADERS)
    r.raise_for_status()
    return r.json()["data"]


def drive_until_terminal(client: httpx.Client, base: str, conv_id: str,
                         task_id: str, answer: str, allow_tools: list[str],
                         timeout: float) -> str:
    """驱动任务到终态：待审批的工具按 allow_tools 放行（子串匹配），挂起的
    提问一律回答 answer。返回终态。"""
    deadline = time.monotonic() + timeout
    decided: set[str] = set()
    answered: set[str] = set()
    status = ""
    while time.monotonic() < deadline:
        for call_id, tool in unresolved_approvals(client, base, task_id):
            if call_id in decided:
                continue
            if any(t in tool for t in allow_tools) or not allow_tools:
                r = client.post(f"{base}/api/tool-approvals/{call_id}",
                                headers=HEADERS, json={"decision": "allow_once"})
                print(f"   审批 {tool} → allow_once（HTTP {r.status_code}）")
                decided.add(call_id)
        for q in pending_questions(client, base, conv_id):
            if q["request_id"] in answered:
                continue
            r = client.post(f"{base}/api/questions/{q['request_id']}/answer",
                            headers=HEADERS, json={"answer": answer})
            print(f"   提问 → “{answer}”（HTTP {r.status_code}）")
            answered.add(q["request_id"])
        status = task_status(client, base, conv_id, task_id)
        if status in ("completed", "failed", "cancelled", "interrupted"):
            return status
        time.sleep(0.3)
    return status or task_status(client, base, conv_id, task_id)


def write_prepared_count(client: httpx.Client, base: str, task_id: str,
                         path_fragment: str) -> int:
    """tool.prepared 里 write_file 且目标路径含 fragment 的次数（重复写入
    痕迹的判据——事件流是唯一事实）。"""
    count = 0
    after = 0
    while True:
        items = task_events(client, base, task_id, after_seq=after)
        if not items:
            break
        for ev in items:
            if ev["type"] == "tool.prepared":
                p = ev["payload"]
                if p.get("tool_name") == "write_file" \
                        and path_fragment in json.dumps(
                            p.get("input", {}), ensure_ascii=False):
                    count += 1
        after = items[-1]["seq"]
        if len(items) < 500:
            break
    return count


def all_task_events(client: httpx.Client, base: str,
                    task_id: str) -> list[dict]:
    items: list[dict] = []
    after = 0
    while True:
        batch = task_events(client, base, task_id, after_seq=after)
        if not batch:
            break
        items += batch
        after = batch[-1]["seq"]
        if len(batch) < 500:
            break
    return items


def wait_new_question(client: httpx.Client, base: str, conv_id: str,
                      task_id: str, exclude: set[str],
                      timeout: float) -> str | None:
    """等待一个不在 exclude 里的挂起提问；期间放行出现的全部工具审批。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for call_id, tool in unresolved_approvals(client, base, task_id):
            rr = client.post(f"{base}/api/tool-approvals/{call_id}",
                             headers=HEADERS, json={"decision": "allow_once"})
            print(f"   审批 {tool} → allow_once（HTTP {rr.status_code}）")
        for q in pending_questions(client, base, conv_id):
            if q["request_id"] not in exclude:
                return q["request_id"]
        time.sleep(0.3)
    return None


# ── 场景 A：工具调用间隙 kill -9 → 对账 → resume → 完成且无重复写入 ──

def scenario_a(data_dir: Path) -> None:
    print("\n=== 场景 A：工具调用间隙 kill -9 → 重启对账 → resume → 完成 ===")
    marker = f"C9-A-{secrets.token_hex(4)}"
    server = Server(data_dir)
    server.start()
    client = httpx.Client(timeout=30.0)
    try:
        instruction = (
            "请严格按顺序完成四步：1) 用 write_file 在工作目录创建 a.txt，"
            f"内容为一行：{marker}；2) 用 ask_user 问我“已创建 a.txt，是否继续”；"
            "3) 我回答“继续”后，用 write_file 创建 b.txt，内容为一行：C9-B；"
            "4) 最后告诉我两个文件各自的完整内容。"
        )
        r = client.post(f"{server.base}/api/conversations", headers=HEADERS,
                        json={"instruction": instruction})
        r.raise_for_status()
        created = r.json()["data"]
        conv_id = created["conversation"]["id"]
        task_id = created["task_run_id"]
        print(f"   会话 {conv_id[:12]}… 任务 {task_id[:12]}…（a.txt 标记 {marker}）")

        # 驱动：放行 a.txt 写审批，直到 ask_user 挂起（两次工具调用之间）
        deadline = time.monotonic() + 120
        qid = None
        while time.monotonic() < deadline:
            for call_id, tool in unresolved_approvals(client, server.base,
                                                      task_id):
                rr = client.post(f"{server.base}/api/tool-approvals/{call_id}",
                                 headers=HEADERS,
                                 json={"decision": "allow_once"})
                print(f"   审批 {tool} → allow_once（HTTP {rr.status_code}）")
            qs = pending_questions(client, server.base, conv_id)
            if qs:
                qid = qs[0]["request_id"]
                break
            time.sleep(0.3)
        assert qid, "a.txt 写入 + ask_user 挂起未在期限内出现"
        # 中断前事实快照
        ws = data_dir / "workspaces" / "default"
        a_txt = ws / "a.txt"
        assert a_txt.exists(), "a.txt 应已写入（ask_user 前一步）"
        before_content = a_txt.read_text(encoding="utf-8")
        before_mtime = a_txt.stat().st_mtime_ns
        assert marker in before_content

        server.kill9()  # 真实 kill -9：任务停在 waiting_user（工具调用间隙）

        server.start()
        status = task_status(client, server.base, conv_id, task_id)
        check("A1 重启对账为 interrupted", status == "interrupted", status)
        time.sleep(2.0)  # 观察不隐式重启
        status2 = task_status(client, server.base, conv_id, task_id)
        check("A2 不隐式重启（2s 后仍 interrupted）", status2 == "interrupted",
              status2)

        r = client.post(f"{server.base}/api/task-runs/{task_id}/resume",
                        headers=HEADERS)
        check("A3 resume 202", r.status_code == 202, f"HTTP {r.status_code}")

        # resume 后：新 ask_user（旧的按“中断，未回答”留在历史）→ 答“继续”
        deadline = time.monotonic() + 120
        new_q = None
        while time.monotonic() < deadline:
            for q in pending_questions(client, server.base, conv_id):
                if q["request_id"] != qid:
                    new_q = q["request_id"]
            if new_q:
                break
            for call_id, tool in unresolved_approvals(
                    client, server.base, task_id):
                rr = client.post(
                    f"{server.base}/api/tool-approvals/{call_id}",
                    headers=HEADERS, json={"decision": "allow_once"})
                print(f"   审批 {tool} → allow_once（HTTP {rr.status_code}）")
            time.sleep(0.3)
        check("A4 resume 后模型重新提问", new_q is not None)
        if new_q:
            rr = client.post(
                f"{server.base}/api/questions/{new_q}/answer",
                headers=HEADERS, json={"answer": "继续"})
            print(f"   提问 → “继续”（HTTP {rr.status_code}）")

        final = drive_until_terminal(
            client, server.base, conv_id, task_id, "继续",
            ["write_file"], 180)
        check("A5 任务完成", final == "completed", final)

        b_txt = ws / "b.txt"
        check("A6 b.txt 内容正确",
              b_txt.exists() and "C9-B" in b_txt.read_text(encoding="utf-8"),
              b_txt.read_text(encoding="utf-8") if b_txt.exists() else "缺失")
        check("A7 a.txt 内容与中断前一致",
              a_txt.read_text(encoding="utf-8") == before_content)
        check("A8 a.txt mtime 未变（无重复写入痕迹）",
              a_txt.stat().st_mtime_ns == before_mtime)
        n = write_prepared_count(client, server.base, task_id, "a.txt")
        check("A9 write_file(a.txt) 仅 prepared 一次", n == 1, f"{n} 次")
        nb = write_prepared_count(client, server.base, task_id, "b.txt")
        check("A10 write_file(b.txt) 仅 prepared 一次", nb == 1, f"{nb} 次")

        r = client.get(f"{server.base}/api/task-runs/{task_id}/attempts",
                       headers=HEADERS)
        attempts = r.json()["data"]
        resume_att = [a for a in attempts if a["kind"] == "resume"]
        check("A11 attempts 含 kind=resume（attempt_no=2，带指纹）",
              len(resume_att) == 1 and resume_att[0]["attempt_no"] == 2
              and resume_att[0]["context_fingerprint"] is not None)
        items = task_events(client, server.base, task_id)
        check("A12 事件流可查", len(items) > 10 and any(
            e["type"] == "run.resumed" for e in items),
            f"{len(items)} 条")
        server.stop()
    finally:
        server.stop()
        client.close()


# ── 场景 B：bash dispatched 后 kill -9 → 待核验 → 409 → 提交 → resume ──

def scenario_b(data_dir: Path) -> None:
    print("\n=== 场景 B：bash dispatched 后 kill -9 → 待核验清单 → 409 → 提交 → resume ===")
    server = Server(data_dir)
    server.start()
    client = httpx.Client(timeout=30.0)
    try:
        r = client.post(f"{server.base}/api/conversations", headers=HEADERS,
                        json={"instruction": "用 bash 执行命令 sleep 10；"
                                             "完成后告诉我 sleep 的退出码。"})
        r.raise_for_status()
        created = r.json()["data"]
        conv_id = created["conversation"]["id"]
        task_id = created["task_run_id"]

        # 放行 bash 审批，轮询到 tool.dispatched（sleep 10 窗口内）即杀
        deadline = time.monotonic() + 120
        dispatched = False
        while time.monotonic() < deadline:
            for call_id, tool in unresolved_approvals(client, server.base,
                                                      task_id):
                rr = client.post(f"{server.base}/api/tool-approvals/{call_id}",
                                 headers=HEADERS,
                                 json={"decision": "allow_once"})
                print(f"   审批 {tool} → allow_once（HTTP {rr.status_code}）")
            for ev in task_events(client, server.base, task_id):
                if ev["type"] == "tool.dispatched":
                    dispatched = True
                    break
            if dispatched:
                break
            time.sleep(0.1)
        assert dispatched, "bash 未在期限内派发"

        server.kill9()  # sleep 10 仍在执行中：账本停在 dispatched 无终态

        server.start()
        r = client.get(f"{server.base}/api/conversations/{conv_id}"
                       f"/pending-verifications", headers=HEADERS)
        listing = r.json()["data"]
        bash_calls = [item for item in listing if item["tool"] == "bash"]
        check("B1 出现在待核验清单（含参数与证据）",
              len(bash_calls) == 1
              and "sleep 10" in json.dumps(bash_calls[0]["input"])
              and bash_calls[0]["evidence"],
              f"{len(bash_calls)} 条")
        call_id = bash_calls[0]["call_id"]
        status = task_status(client, server.base, conv_id, task_id)
        check("B2 任务转 waiting_verification",
              status == "waiting_verification", status)

        r = client.post(f"{server.base}/api/task-runs/{task_id}/resume",
                        headers=HEADERS)
        body = {}
        try:
            body = r.json()
        except ValueError:
            pass
        pending_in_detail = body.get("error", {}).get("detail", {}) \
            .get("pending_verifications", [])
        check("B3 resume 被拒 409 带清单",
              r.status_code == 409
              and body.get("error", {}).get("code") == "PENDING_VERIFICATION"
              and any(p.get("call_id") == call_id for p in pending_in_detail),
              f"HTTP {r.status_code}")

        r = client.post(f"{server.base}/api/tool-calls/{call_id}/verification",
                        headers=HEADERS,
                        json={"verdict": "confirmed_not_executed",
                              "note": "确认未执行（sleep 进程随服务被杀）"})
        check("B4 提交“确认未执行”",
              r.status_code == 200
              and r.json()["data"]["status"] == "not_executed",
              f"HTTP {r.status_code}")

        r = client.post(f"{server.base}/api/task-runs/{task_id}/resume",
                        headers=HEADERS)
        check("B5 核验后 resume 成功", r.status_code == 202,
              f"HTTP {r.status_code}")

        # 账本/占位告知模型“未发生”：模型可重做 sleep（新 call_id 新决定）
        # 或直接汇报——两条路都算任务继续；不断言模型措辞（ADR-006）
        final = drive_until_terminal(
            client, server.base, conv_id, task_id, "继续", ["bash", "sleep"],
            180)
        check("B6 核验后任务完成", final == "completed", final)

        items: list[dict] = []
        after = 0
        while True:
            batch = task_events(client, server.base, task_id, after_seq=after)
            if not batch:
                break
            items += batch
            after = batch[-1]["seq"]
            if len(batch) < 500:
                break
        check("B7 verification 事件入流且可查",
              any(e["type"] == "tool.verification_submitted"
                  and e["payload"]["call_id"] == call_id for e in items))
        atts = client.get(f"{server.base}/api/task-runs/{task_id}/attempts",
                          headers=HEADERS).json()["data"]
        check("B8 attempts 含 kind=resume",
              any(a["kind"] == "resume" for a in atts))
        server.stop()
    finally:
        server.stop()
        client.close()


# ── 场景 C（F1）：resume 后二连崩 → 对账再补中断 → 再次 resume → 完成 ──

def scenario_c(data_dir: Path) -> None:
    print("\n=== 场景 C（F1）：resume 成功后再 kill -9 → 二连崩对账 → 再次 resume → 完成 ===")
    marker = f"C9-F1-{secrets.token_hex(4)}"
    server = Server(data_dir)
    server.start()
    client = httpx.Client(timeout=30.0)
    try:
        instruction = (
            "请严格按顺序完成三步：1) 用 write_file 在工作目录创建 f1.txt，"
            f"内容为一行：{marker}；2) 用 ask_user 问我“F1 是否继续”；"
            "3) 我回答“继续”后，告诉我 f1.txt 的完整内容。"
            "已完成的写入不要重复执行。"
        )
        r = client.post(f"{server.base}/api/conversations", headers=HEADERS,
                        json={"instruction": instruction})
        r.raise_for_status()
        created = r.json()["data"]
        conv_id = created["conversation"]["id"]
        task_id = created["task_run_id"]
        print(f"   会话 {conv_id[:12]}… 任务 {task_id[:12]}…（f1.txt 标记 {marker}）")

        # 第一次中断：放行写审批 → 第一个提问挂起（工具调用间隙）即杀
        qid1 = wait_new_question(client, server.base, conv_id, task_id,
                                 set(), 120)
        assert qid1, "第一次提问未在期限内出现（f1.txt 应已写完）"
        ws = data_dir / "workspaces" / "default"
        f1_txt = ws / "f1.txt"
        assert f1_txt.exists() and marker in f1_txt.read_text(encoding="utf-8")
        server.kill9()  # kill -9 #1：waiting_user

        server.start()
        status = task_status(client, server.base, conv_id, task_id)
        check("C1 一崩重启对账为 interrupted", status == "interrupted", status)
        evs = all_task_events(client, server.base, task_id)
        n_int = sum(1 for e in evs if e["type"] == "run.interrupted")
        check("C2 run.interrupted 恰 1 条", n_int == 1, f"{n_int} 条")

        r = client.post(f"{server.base}/api/task-runs/{task_id}/resume",
                        headers=HEADERS)
        body = r.json() if r.headers.get("content-type", "").startswith(
            "application/json") else {}
        warnings = body.get("data", {}).get("warnings")
        check("C3 resume #1 202（warnings 清单随响应返回）",
              r.status_code == 202 and isinstance(warnings, list),
              f"HTTP {r.status_code} warnings={warnings}")

        # resume #1 真实开跑：等模型重新提问（不回答）→ kill -9 #2
        qid2 = wait_new_question(client, server.base, conv_id, task_id,
                                 {qid1}, 120)
        check("C4 resume 后模型重新提问（resume 尝试真实开跑）", qid2 is not None)
        assert qid2, "第二次提问未在期限内出现"
        server.kill9()  # kill -9 #2：resume attempt 的 waiting_user

        server.start()
        status = task_status(client, server.base, conv_id, task_id)
        check("C5 二崩重启对账为 interrupted（F1：不再停 waiting_user）",
              status == "interrupted", status)
        evs = all_task_events(client, server.base, task_id)
        n_int = sum(1 for e in evs if e["type"] == "run.interrupted")
        check("C6 第二条 run.interrupted 已补（F1）", n_int == 2, f"{n_int} 条")

        r = client.post(f"{server.base}/api/task-runs/{task_id}/resume",
                        headers=HEADERS)
        check("C7 resume #2 成功（旧实现此处 409，队列永久卡死）",
              r.status_code == 202, f"HTTP {r.status_code}")

        final = drive_until_terminal(
            client, server.base, conv_id, task_id, "继续", ["write_file"], 180)
        check("C8 二连崩后任务完成", final == "completed", final)

        check("C9 f1.txt 内容正确",
              f1_txt.exists()
              and marker in f1_txt.read_text(encoding="utf-8"))
        n = write_prepared_count(client, server.base, task_id, "f1.txt")
        check("C10 write_file(f1.txt) 仍仅 prepared 一次", n == 1, f"{n} 次")

        atts = client.get(f"{server.base}/api/task-runs/{task_id}/attempts",
                          headers=HEADERS).json()["data"]
        resume_atts = [a for a in atts if a["kind"] == "resume"]
        check("C11 attempts 含 2 次 resume（attempt_no 2→3）",
              [a["attempt_no"] for a in resume_atts] == [2, 3],
              f"{[a['attempt_no'] for a in resume_atts]}")
        evs = all_task_events(client, server.base, task_id)
        check("C12 run.resumed ×2 / run.interrupted ×2 事件可查",
              sum(1 for e in evs if e["type"] == "run.resumed") == 2
              and sum(1 for e in evs if e["type"] == "run.interrupted") == 2)
        server.stop()
    finally:
        server.stop()
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="/tmp/agentcrew-c9-demo")
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)

    src = BACKEND / "data" / "config.json"
    assert src.exists(), f"缺真实配置：{src}（应含 GLM key，git 忽略）"
    shutil.copy(src, data_dir / "config.json")

    HEADERS["Authorization"] = f"Bearer {TOKEN}"

    scenario_a(data_dir)
    scenario_b(data_dir)
    scenario_c(data_dir)

    print(f"\n==== C9 验收：{len(PASS)} 通过 / {len(FAIL)} 失败 ====")
    if FAIL:
        for name in FAIL:
            print(f"   FAIL：{name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
