#!/usr/bin/env python3
"""M0-C8 验收演示（真实 GLM + 真实服务 + 真实审批 + 真实文件 + 真实 SSE）。

场景（对应 M0-cards C8 验收清单）：
  A 单文件总结：真实 GLM 任务读材料并总结 → SSE 全程 STEP/LLM/TOOL 事件、
    messages 投影含最终回复
  B 多步读写 + 审批四决定：五个 write_file（三个目录），allow_once /
    allow_always（后同目录放行）/ reject_once（模型继续）/ reject_always
    （规则拒），artifacts ready 落库
  C ask_user 全链 + S07：提问挂起（waiting_user）→ 等 65s 不被 60s 工具
    超时截断 → 回答 → 写文件（审批）→ 完成；期间 PATCH settings 证明
    运行中任务 context_fingerprint 保持旧版（E：下一新任务用新版）
  D answer=null：用户取消回答按"拒绝回答"告知模型，任务照常完成
  F 排队完整链：运行中入队两条 → 停止（cancelled → queue_paused 不自动
    接续）→ 继续（队首执行）→ 取消剩余 → 无遗漏
  G 守门真触发：gates.max_steps=2 → RUN_FAILED(max_steps)；
    gates.token_budget=300 → RUN_FAILED(token_budget)
  H bash 执行中取消 → 终态前结清（外审回稿·致命②）：dispatched 无结果
    转 tool.pending_verification（先于 run.cancelled），继续队列 409 阻断

用法：
  cd backend && uv run python ../scripts/loop/c8_demo.py [--data-dir /tmp/c8-demo]
（data-dir 缺省 /tmp/agentcrew-c8-demo；config.json 从 backend/data/ 复制，
内含真实 key——本脚本绝不打印 key。）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import httpx
import uvicorn

from agentcrew_core.tools import ToolScheduler, build_default_registry
from agentcrew_server.api.app import create_app
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.bus import EventBus
from agentcrew_server.config import load_config
from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.providers import build_provider
from agentcrew_server.questions import QuestionService
from agentcrew_server.run_manager import RunManager
from agentcrew_server.sessions import SessionService
from agentcrew_server.settings import SettingsService

TOKEN = secrets.token_hex(24)
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
REPO = Path(__file__).resolve().parents[2]

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"   [{mark}] {name}" + (f"：{detail}" if detail else ""))
    (PASS if ok else FAIL).append(name)


def build(data_dir: Path):
    db = Database(data_dir / "agentcrew.db")
    run_migrations(db.write_conn, data_dir / "backups")
    channel = WriteChannel(db.write_conn)
    bus = EventBus()
    store = EventStore(channel, publisher=bus.publish)
    config = load_config(data_dir, {})
    settings = SettingsService(config, data_dir, channel,
                               data_dir / "chain-head.txt", env={})
    sessions = SessionService(db, store, data_dir, settings)
    approvals = ApprovalService(db, store, data_dir / "chain-head.txt")
    approvals.scheduler = ToolScheduler(build_default_registry(),
                                        gate=approvals.gate)
    questions = QuestionService(db, store)
    run_manager = RunManager(
        db=db, event_store=store, bus=bus, settings=settings,
        sessions=sessions, approvals=approvals, scheduler=approvals.scheduler,
        questions=questions)
    provider = build_provider(config.values.get("models", {}))  # 健康检查用
    runtime = create_runtime(data_dir, db, channel, bus, store, provider,
                             approvals, approvals.scheduler, settings,
                             sessions, questions, run_manager)
    return db, channel, store, settings, sessions, approvals, questions, \
        run_manager, create_app(runtime)


def create_runtime(data_dir, db, channel, bus, store, provider, approvals,
                   scheduler, settings, sessions, questions, run_manager):
    from agentcrew_server.runtime import RuntimeState
    return RuntimeState(
        log=logging.getLogger("c8"), data_dir=data_dir, db=db,
        write_channel=channel, token=TOKEN, bus=bus, event_store=store,
        provider=provider, approvals=approvals, scheduler=scheduler,
        settings=settings, sessions=sessions, questions=questions,
        run_manager=run_manager)


def one(db, sql: str, params=()):
    return db.read_conn.execute(sql, params).fetchone()


def task_status(db, task_id: str) -> str:
    row = one(db, "SELECT status FROM task_runs WHERE id=?", (task_id,))
    return row[0] if row else "missing"


def fail_reason(db, task_id: str) -> str:
    row = one(db, "SELECT json_extract(payload,'$.reason') FROM run_events"
                  " WHERE task_run_id=? AND type='run.failed'", (task_id,))
    return row[0] if row else ""


async def wait_terminal(db, task_id: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = task_status(db, task_id)
        if status in ("completed", "failed", "cancelled", "interrupted"):
            return status
        await asyncio.sleep(0.3)
    return task_status(db, task_id)


def unresolved_approvals(db, task_id: str):
    return db.read_conn.execute(
        "SELECT json_extract(payload,'$.tool_call_id'),"
        "       json_extract(payload,'$.target')"
        " FROM run_events re WHERE re.task_run_id=? AND re.type='permission.requested'"
        "   AND NOT EXISTS (SELECT 1 FROM run_events r2"
        "     WHERE r2.type='permission.resolved'"
        "       AND json_extract(r2.payload,'$.tool_call_id')"
        "         = json_extract(re.payload,'$.tool_call_id'))"
        " ORDER BY re.global_seq", (task_id,)).fetchall()


async def decide_approvals(client, base, db, task_id: str,
                           mapping: list[tuple[str, str]],
                           timeout: float) -> list[str]:
    """真实审批驱动：按 target 子串匹配提交决定，直到任务终态。"""
    decided: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for call_id, target in unresolved_approvals(db, task_id):
            if call_id in decided:
                continue
            for substr, decision in mapping:
                if substr in (target or ""):
                    r = await client.post(
                        f"{base}/api/tool-approvals/{call_id}",
                        headers=HEADERS, json={"decision": decision})
                    print(f"   审批 {target} → {decision}（HTTP {r.status_code}）")
                    decided.append(call_id)
                    break
        if task_status(db, task_id) in ("completed", "failed", "cancelled"):
            return decided
        await asyncio.sleep(0.3)
    return decided


async def latest_question(db, conversation_id: str):
    row = one(db, "SELECT json_extract(payload,'$.request_id')"
                  " FROM run_events WHERE conversation_id=?"
                  "   AND type='question.requested' ORDER BY global_seq DESC"
                  " LIMIT 1", (conversation_id,))
    return row[0] if row else None


async def sse_collect(client, base, conversation_id: str, timeout: float):
    """订阅会话 SSE（from=0 含历史补播），收集到 run.completed/failed 为止。"""
    frames: list[dict] = []
    deadline = time.monotonic() + timeout
    try:
        async with client.stream(
            "GET",
            f"{base}/api/conversations/{conversation_id}/stream?from=0",
            headers=HEADERS,
        ) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    try:
                        frames.append(json.loads(line[6:]))
                    except json.JSONDecodeError:
                        continue
                    if frames[-1].get("type") in ("run.completed",
                                                  "run.failed"):
                        return frames
                if time.monotonic() > deadline:
                    return frames
    except httpx.HTTPError:
        pass
    return frames


async def scenario_a(client, base, db, seed: Path):
    print("\n── A 单文件总结（真实 GLM + SSE 全程事件）──")
    r = await client.post(f"{base}/api/conversations", headers=HEADERS, json={
        "instruction": "请读取资料目录（见环境信息）中的 c8-素材.txt，"
                       "然后用一两句话总结它的内容直接回复。不要做其他事。",
        "import_files": [str(seed / "c8-素材.txt")],
    })
    data = r.json()["data"]
    conv_id, task_id = data["conversation"]["id"], data["task_run_id"]
    print(f"① 会话已建 conversation={conv_id[:12]}… task={task_id[:12]}…")
    sse_task = asyncio.create_task(sse_collect(client, base, conv_id, 180))
    status = await wait_terminal(db, task_id, 180)
    frames = await asyncio.wait_for(sse_task, timeout=30)
    check("任务完成", status == "completed", f"status={status}"
          + (f" reason={fail_reason(db, task_id)}" if status == "failed" else ""))
    types = {f.get("type") for f in frames}
    need = {"run.queued", "run.started", "step.started", "llm.request_started",
            "llm.request_done", "tool.prepared", "tool.dispatched",
            "tool.completed", "step.completed", "run.completed"}
    check("SSE 全程 STEP/LLM/TOOL 事件", need.issubset(types),
          f"缺 {sorted(need - types)}" if not need.issubset(types)
          else f"共 {len(frames)} 帧")
    tool_round = one(db, "SELECT payload FROM run_events WHERE task_run_id=?"
                        " AND type='llm.request_done' ORDER BY global_seq",
                     (task_id,))[0]
    final_round = one(db, "SELECT payload FROM run_events WHERE task_run_id=?"
                          " AND type='llm.request_done'"
                          " ORDER BY global_seq DESC LIMIT 1", (task_id,))[0]
    tool_uses = json.loads(tool_round).get("tool_uses") or []
    text = json.loads(final_round).get("text", "")
    reply = one(db, "SELECT content FROM messages WHERE task_run_id=?"
                    " AND role='assistant'", (task_id,))
    check("工具轮 llm.request_done 含全部 tool_use 块（C9 重建依据）",
          len(tool_uses) == 1 and tool_uses[0]["name"] == "read_file",
          f"tool_uses={[t.get('name') for t in tool_uses]}")
    check("最终轮 llm.request_done 带回复全文", len(text) > 10)
    check("messages 投影含最终回复",
          reply is not None and len(reply[0]) > 5)
    return conv_id


async def scenario_b(client, base, db, data_dir: Path):
    print("\n── B 多步读写 + 审批四决定（三个目录五次写入）──")
    ws = data_dir / "workspaces" / "default"
    for d in ("批1", "批2", "批3"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    r = await client.post(f"{base}/api/conversations", headers=HEADERS, json={
        "instruction": (
            f"请按顺序执行五次 write_file（每次一个文件，写完一个再写下一个）：\n"
            f"1) {ws / '批1' / 'a.md'} 内容 AAA\n"
            f"2) {ws / '批1' / 'b.md'} 内容 BBB\n"
            f"3) {ws / '批1' / 'e.md'} 内容 EEE\n"
            f"4) {ws / '批2' / 'c.md'} 内容 CCC\n"
            f"5) {ws / '批3' / 'd.md'} 内容 DDD\n"
            "注意：写入被拒绝时不要重试该文件，直接继续下一个；全部处理完后"
            "逐一汇报五个文件的状态。"),
    })
    data = r.json()["data"]
    task_id = data["task_run_id"]
    mapping = [
        ("批1/a.md", "allow_once"),
        ("批1/b.md", "allow_always"),
        ("批2/c.md", "reject_once"),
        ("批3/d.md", "reject_always"),
    ]
    decided = await decide_approvals(client, base, db, task_id, mapping, 300)
    status = await wait_terminal(db, task_id, 60)
    check("任务完成（reject 后模型继续）", status == "completed",
          f"status={status}")
    check("四张审批卡各真实决定一次",
          len(decided) == 4, f"decided={len(decided)}")
    check("allow_once/allow_always 的文件真实写入",
          (ws / "批1" / "a.md").read_text() == "AAA"
          and (ws / "批1" / "b.md").read_text() == "BBB")
    check("allow_always 后同目录放行（e.md 无审批卡）",
          (ws / "批1" / "e.md").read_text() == "EEE")
    e_card = one(db, "SELECT count(*) FROM run_events"
                     " WHERE type='permission.requested'"
                     "   AND json_extract(payload,'$.target') LIKE '%批1/e.md'")
    check("e.md 未弹卡", e_card[0] == 0)
    check("reject_once 的文件未写入", not (ws / "批2" / "c.md").exists())
    check("reject_always 的文件未写入", not (ws / "批3" / "d.md").exists())
    denied = one(db, "SELECT count(*) FROM tool_calls WHERE task_run_id=?"
                     " AND error='PERMISSION_DENIED'", (task_id,))[0]
    check("两次 PERMISSION_DENIED 入账本", denied == 2, f"denied={denied}")
    artifacts_ready = one(db, "SELECT count(*) FROM artifacts"
                              " WHERE task_run_id=? AND status='ready'",
                          (task_id,))[0]
    check("artifacts 出现 ready 记录", artifacts_ready == 3,
          f"ready={artifacts_ready}")
    return task_id


async def scenario_c_and_e(client, base, db, data_dir: Path):
    print("\n── C ask_user 全链 + S07（65s 等待）+ 配置版本绑定 ──")
    ws = data_dir / "workspaces" / "default"
    r = await client.post(f"{base}/api/conversations", headers=HEADERS, json={
        "instruction": (
            "请先用 ask_user 工具向我提问：'演示确认：请回复一句话'。"
            "等收到我的回答后，把我的回答原样写入工作目录根下的 回答.md"
            "（write_file），然后直接汇报完成。除此之外不要做任何事。"),
    })
    conv_id = r.json()["data"]["conversation"]["id"]
    task_id = r.json()["data"]["task_run_id"]
    # 等提问挂起
    qid = None
    for _ in range(200):
        qid = await latest_question(db, conv_id)
        if qid:
            break
        await asyncio.sleep(0.3)
    check("真实任务里模型提问（question.requested）", qid is not None)
    state = (await client.get(f"{base}/api/conversations/{conv_id}/state",
                              headers=HEADERS)).json()["data"]
    check("挂起（waiting_questions=1）",
          state["waiting_questions"] == 1 and state["state"] == "running")
    check("task_runs 派生态 waiting_user",
          task_status(db, task_id) == "waiting_user")
    pending = (await client.get(
        f"{base}/api/conversations/{conv_id}/questions",
        headers=HEADERS)).json()["data"]
    check("GET questions 恢复提问卡",
          len(pending) == 1 and pending[0]["request_id"] == qid)
    # S07：等 65s 证明不被 60s 工具超时截断
    print("   …等待 65s（S07：ask_user 不得被工具 60s 超时截断）")
    await asyncio.sleep(65)
    still = task_status(db, task_id)
    timeout_cut = one(db, "SELECT count(*) FROM run_events"
                          " WHERE task_run_id=? AND type='tool.failed'"
                          "   AND json_extract(payload,'$.error')='TIMEOUT'",
                      (task_id,))[0]
    check("65s 后仍挂起未被截断",
          still == "waiting_user" and timeout_cut == 0,
          f"status={still} timeout_cut={timeout_cut}")
    # 配置版本绑定：运行中 PATCH → 本 attempt 保持旧版
    r = await client.patch(f"{base}/api/settings", headers=HEADERS, json={
        "models": {"main": {"max_tokens": 2048}}})
    new_version = r.json()["data"]["settings"]["config_version"]
    attempts = (await client.get(f"{base}/api/task-runs/{task_id}/attempts",
                                 headers=HEADERS)).json()["data"]
    bound = attempts[0]["context_fingerprint"]["model_config"]["max_tokens"]
    check("运行中 PATCH：本任务 fingerprint 保持旧版 4096", bound == 4096,
          f"bound={bound}（settings 已到 v{new_version}）")
    # 回答 → 写文件（弹审批，allow_once）→ 完成
    r = await client.post(f"{base}/api/questions/{qid}/answer", headers=HEADERS,
                          json={"answer": "今天天气很好，适合演示 AgentCrew。"})
    check("POST answer 200", r.status_code == 200)
    await decide_approvals(client, base, db, task_id,
                           [("回答.md", "allow_once")], 120)
    status = await wait_terminal(db, task_id, 120)
    check("回答后任务完成", status == "completed", f"status={status}")
    answered = one(db, "SELECT json_extract(payload,'$.answer') FROM run_events"
                       " WHERE type='question.answered'")
    check("question.answered 落库（含回答原文）",
          answered and "适合演示" in answered[0])
    content = (ws / "回答.md").read_text()
    check("回答内容真实写入 回答.md", "适合演示" in content)
    # E：下一新任务用新版（fingerprint=2048）
    r = await client.post(f"{base}/api/conversations/{conv_id}/instructions",
                          headers=HEADERS,
                          json={"text": "读取工作目录里的 回答.md，"
                                        "用一句话概括后直接回复。"})
    e_task = r.json()["data"]["task_run_id"]
    status = await wait_terminal(db, e_task, 120)
    attempts = (await client.get(f"{base}/api/task-runs/{e_task}/attempts",
                                 headers=HEADERS)).json()["data"]
    bound = attempts[0]["context_fingerprint"]["model_config"]["max_tokens"]
    check("下一新任务 fingerprint 用新版 2048", bound == 2048,
          f"bound={bound}")
    check("E 任务完成（同会话上下文连续）", status == "completed",
          f"status={status}")


async def scenario_d(client, base, db, data_dir: Path):
    print("\n── D answer=null（拒绝回答告知模型）──")
    ws = data_dir / "workspaces" / "default"
    r = await client.post(f"{base}/api/conversations", headers=HEADERS, json={
        "instruction": (
            "请用 ask_user 工具问我：'演示取消：需要执行额外步骤吗？'。"
            "如果用户取消回答（工具返回用户取消/未回答），就把'用户未回答'"
            "写入工作目录根下的 cancel.md，然后汇报结束。"),
    })
    conv_id = r.json()["data"]["conversation"]["id"]
    task_id = r.json()["data"]["task_run_id"]
    qid = None
    for _ in range(200):
        qid = await latest_question(db, conv_id)
        if qid:
            break
        await asyncio.sleep(0.3)
    check("提问挂起", qid is not None)
    r = await client.post(f"{base}/api/questions/{qid}/answer",
                          headers=HEADERS, json={"answer": None})
    check("answer=null 提交 200", r.status_code == 200)
    # 拒绝回答后模型仍会落文件 → 真实审批一次
    await decide_approvals(client, base, db, task_id,
                           [("cancel.md", "allow_once")], 120)
    status = await wait_terminal(db, task_id, 120)
    check("拒绝回答后任务照常完成", status == "completed", f"status={status}")
    check("模型按'拒绝回答'落文件 cancel.md",
          (ws / "cancel.md").exists()
          and "未回答" in (ws / "cancel.md").read_text())
    payload = one(db, "SELECT payload FROM run_events"
                      " WHERE type='question.answered' ORDER BY global_seq"
                      " DESC LIMIT 1")[0]
    check("question.answered 载荷 answer=null",
          json.loads(payload)["answer"] is None)


async def scenario_f(client, base, db):
    print("\n── F 排队完整链（停止→暂停→继续→取消剩余）──")
    r = await client.post(f"{base}/api/conversations", headers=HEADERS, json={
        "instruction": (
            "请先用 ask_user 工具问我：'排队演示：确认继续吗？'，"
            "得到回答后直接回复'收到'即可，不要调用其他工具。"),
    })
    conv_id = r.json()["data"]["conversation"]["id"]
    t1 = r.json()["data"]["task_run_id"]
    qid = None
    for _ in range(200):
        qid = await latest_question(db, conv_id)
        if qid:
            break
        await asyncio.sleep(0.3)
    check("t1 提问挂起（等待用户）", qid is not None
          and task_status(db, t1) == "waiting_user")
    r2 = await client.post(f"{base}/api/conversations/{conv_id}/instructions",
                           headers=HEADERS,
                           json={"text": "直接回复'第二个任务执行完毕'这八个字，"
                                         "不要调用任何工具。"})
    check("运行中发第二条指令入队", r2.json()["data"]["mode"] == "queued")
    r3 = await client.post(f"{base}/api/conversations/{conv_id}/instructions",
                           headers=HEADERS,
                           json={"text": "直接回复'第三条'三个字，"
                                         "不要调用任何工具。"})
    state = (await client.get(f"{base}/api/conversations/{conv_id}/state",
                              headers=HEADERS)).json()["data"]
    check("队列两条", len(state["queue"]) == 2)
    q3_id = state["queue"][1]["id"]
    # 停止运行中任务 → cancelled + 队列暂停不自动接续
    r = await client.post(f"{base}/api/task-runs/{t1}/cancel", headers=HEADERS)
    check("POST cancel 202", r.status_code == 202)
    status = await wait_terminal(db, t1, 30)
    check("t1 已取消", status == "cancelled")
    await asyncio.sleep(2)  # 留出"本应自动接续"的观察窗口
    state = (await client.get(f"{base}/api/conversations/{conv_id}/state",
                              headers=HEADERS)).json()["data"]
    check("队列暂停、不自动跑队首",
          state["state"] == "idle" and state["queue_paused"] is True
          and state["can_continue_queue"] is True)
    t_count = one(db, "SELECT count(*) FROM task_runs"
                      " WHERE conversation_id=?", (conv_id,))[0]
    check("暂停期未偷跑新任务", t_count == 1, f"tasks={t_count}")
    unanswered = one(db, "SELECT count(*) FROM run_events"
                         " WHERE conversation_id=?"
                         "   AND type='question.requested'"
                         "   AND NOT EXISTS (SELECT 1 FROM run_events r2"
                         "     WHERE r2.type='question.answered'"
                         "       AND json_extract(r2.payload,'$.request_id')="
                         "           json_extract(run_events.payload,"
                         "               '$.request_id'))", (conv_id,))[0]
    check("S07 取消干净释放：留下未回答状态", unanswered == 1)
    # 继续 → 队首执行；趁 t2 运行中取消剩余（完成后会被自动接续出队）
    r = await client.post(f"{base}/api/conversations/{conv_id}/queue/continue",
                          headers=HEADERS)
    check("POST queue/continue 202", r.status_code == 202)
    r = await client.post(f"{base}/api/conversations/{conv_id}/queue/cancel",
                          headers=HEADERS, json={"item_ids": [q3_id]})
    check("queue/cancel 取消剩余（t2 运行中）",
          r.json()["data"]["cancelled_ids"] == [q3_id])
    t2 = one(db, "SELECT id FROM task_runs WHERE conversation_id=?"
                 " ORDER BY created_at DESC LIMIT 1", (conv_id,))[0]
    status = await wait_terminal(db, t2, 120)
    check("队首出队并执行完成", status == "completed"
          and t2 != t1, f"status={status}")
    await asyncio.sleep(2)  # t2 完成后自动接续应发现队列空
    t_count = one(db, "SELECT count(*) FROM task_runs"
                      " WHERE conversation_id=?", (conv_id,))[0]
    # messages：3 条用户指令（t1 + 两条排队）+ t2 的最终回复
    msgs = one(db, "SELECT count(*) FROM messages WHERE conversation_id=?",
               (conv_id,))[0]
    state = (await client.get(f"{base}/api/conversations/{conv_id}/state",
                              headers=HEADERS)).json()["data"]
    check("无遗漏：两条排队指令恰建一任务、发送记录保留",
          t_count == 2 and msgs == 4 and state["state"] == "idle",
          f"tasks={t_count} messages={msgs} state={state['state']}")


async def scenario_g(client, base, db, seed: Path):
    print("\n── G 守门真触发（回合上限=2 / token 预算）──")
    r = await client.patch(f"{base}/api/settings", headers=HEADERS, json={
        "gates": {"max_steps": 2}})
    check("PATCH gates.max_steps=2", r.status_code == 200)
    r = await client.post(f"{base}/api/conversations", headers=HEADERS, json={
        "instruction": (
            "请依次读取资料目录（见环境信息）中的 g1.txt、g2.txt、g3.txt——"
            "必须分三次调用 read_file，一次只读一个文件，前一个读完才能读"
            "下一个，禁止并行；三个都读完后用一句话总结。"),
        "import_files": [str(seed / "g1.txt"), str(seed / "g2.txt"),
                         str(seed / "g3.txt")],
    })
    task_id = r.json()["data"]["task_run_id"]
    status = await wait_terminal(db, task_id, 180)
    check("回合上限真触发 → RUN_FAILED(max_steps)",
          status == "failed" and fail_reason(db, task_id) == "max_steps",
          f"status={status} reason={fail_reason(db, task_id)}")
    r = await client.patch(f"{base}/api/settings", headers=HEADERS, json={
        "gates": {"max_steps": 40, "token_budget": 300}})
    check("PATCH token_budget=300", r.status_code == 200)
    r = await client.post(f"{base}/api/conversations", headers=HEADERS, json={
        "instruction": "请读取资料目录（见环境信息）中的 g1.txt，"
                       "然后用一句话总结直接回复。",
        "import_files": [str(seed / "g1.txt")],
    })
    task_id = r.json()["data"]["task_run_id"]
    status = await wait_terminal(db, task_id, 180)
    check("token 预算硬上限真触发 → RUN_FAILED(token_budget)",
          status == "failed" and fail_reason(db, task_id) == "token_budget",
          f"status={status} reason={fail_reason(db, task_id)}")
    r = await client.patch(f"{base}/api/settings", headers=HEADERS, json={
        "gates": {"token_budget": 2_000_000}})
    check("PATCH 恢复 token_budget", r.status_code == 200)


async def scenario_h(client, base, db):
    """外审回稿·致命②：bash 执行中取消 → 终态前 dispatched 无结果调用
    转 tool.pending_verification（先于 run.cancelled），继续队列被 409 阻断。"""
    print("\n── H bash 执行中取消 → 终态前结清（外审致命②）──")
    r = await client.post(f"{base}/api/conversations", headers=HEADERS, json={
        "instruction": "请用 bash 工具执行命令 sleep 30，等它执行完毕后"
                       "直接回复'睡完了'。不要做其他事。",
    })
    conv_id = r.json()["data"]["conversation"]["id"]
    task_id = r.json()["data"]["task_run_id"]
    # 直接等卡并提交（不走 decide_approvals——它阻塞到任务终态，
    # 而 bash sleep 30 要跑满 30s，dispatched 早就翻篇）
    card = None
    for _ in range(300):
        card = one(db, "SELECT json_extract(payload,'$.tool_call_id')"
                       " FROM run_events WHERE task_run_id=?"
                       "   AND type='permission.requested'", (task_id,))
        if card:
            break
        await asyncio.sleep(0.2)
    check("bash 审批卡已弹", card is not None)
    r = await client.post(f"{base}/api/tool-approvals/{card[0]}",
                          headers=HEADERS, json={"decision": "allow_once"})
    check("allow_once 提交 200", r.status_code == 200)
    # 等 bash dispatched（审批放行后进入执行）
    dispatched_call = None
    for _ in range(200):
        dispatched_call = one(db, "SELECT call_id FROM tool_calls"
                                 " WHERE task_run_id=? AND tool_name='bash'"
                                 " AND status='dispatched'", (task_id,))
        if dispatched_call:
            break
        await asyncio.sleep(0.1)
    check("bash 已进入执行（dispatched）", dispatched_call is not None)
    await client.post(f"{base}/api/conversations/{conv_id}/instructions",
                      headers=HEADERS, json={"text": "排队指令H"})
    r = await client.post(f"{base}/api/task-runs/{task_id}/cancel",
                          headers=HEADERS)
    check("取消执行中任务 202", r.status_code == 202)
    status = await wait_terminal(db, task_id, 30)
    check("任务已取消", status == "cancelled")
    settled = one(db, "SELECT status FROM tool_calls WHERE call_id=?",
                  (dispatched_call[0],))[0]
    check("取消终态前 dispatched 无结果已转待核验",
          settled == "pending_verification", f"status={settled}")
    order = one(db, "SELECT (SELECT MAX(global_seq) FROM run_events"
                    " WHERE type='tool.pending_verification'"
                    "   AND task_run_id=:t)"
                    " < (SELECT MAX(global_seq) FROM run_events"
                    "     WHERE type='run.cancelled' AND task_run_id=:t)",
                {"t": task_id})[0]
    check("结清事件先于终态事件（时序契约③）", order == 1)
    r = await client.post(f"{base}/api/conversations/{conv_id}/queue/continue",
                          headers=HEADERS)
    code = r.json()["error"]["code"] if r.status_code != 202 else "OK"
    check("继续队列被待核验阻断 409", code == "PENDING_VERIFICATION",
          f"http={r.status_code} code={code}")


async def run(client: httpx.AsyncClient, base: str, db, data_dir: Path,
              seed: Path):
    await scenario_a(client, base, db, seed)
    await scenario_b(client, base, db, data_dir)
    await scenario_c_and_e(client, base, db, data_dir)
    await scenario_d(client, base, db, data_dir)
    await scenario_f(client, base, db)
    await scenario_h(client, base, db)
    await scenario_g(client, base, db, seed)
    check_result = verify_with_anchor(db.write_conn,
                                      data_dir / "chain-head.txt")
    check("审计链校验全绿", check_result.ok,
          f"{check_result.checked_count} 条")
    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：", *FAIL, sep="\n  - ")
    return 0 if not FAIL else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="/tmp/agentcrew-c8-demo")
    parser.add_argument("--keep", action="store_true",
                        help="保留 data 目录供检查")
    ns = parser.parse_args()
    data_dir = Path(ns.data_dir)
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    shutil.copy(REPO / "backend" / "data" / "config.json",
                data_dir / "config.json")

    seed = data_dir / "seed"
    seed.mkdir()
    (seed / "c8-素材.txt").write_text(
        "AgentCrew 是一个运行在 macOS 上的桌面智能体基座。"
        "它由 Electron 壳与 Python FastAPI 边车进程组成，"
        "采用事件溯源架构，所有执行事实追加进 SQLite 的 run_events 表，"
        "投影表可由事件全量重建。M0 里程碑交付 ReAct 主循环、五个内置工具、"
        "三级权限闸门与中断恢复。")
    for n in (1, 2, 3):
        (seed / f"g{n}.txt").write_text(f"这是第 {n} 个守门演示文件，"
                                        f"内容编号 G-{n}。")

    (db, channel, store, settings, sessions, approvals, questions,
     run_manager, app) = build(data_dir)

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_config=None, lifespan="on",
        timeout_graceful_shutdown=5))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    print(f"[READY] 真实服务 {base}（RunManager 已随 lifespan 启动）")

    async def runner():
        async with httpx.AsyncClient(
                timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            return await run(client, base, db, data_dir, seed)

    code = asyncio.run(runner())
    print("\n[全部场景完成]")
    server.should_exit = True
    time.sleep(1.0)
    channel.close()
    db.close()
    if not ns.keep:
        shutil.rmtree(data_dir, ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
