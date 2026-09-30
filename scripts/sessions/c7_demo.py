#!/usr/bin/env python3
"""M0-C7 验收演示（真实服务 + 真实 HTTP + 真实文件复制 + 真实闸门）。

步骤：
  1. GET /api/limits
  2. POST /api/conversations：3 个真实文件 + 1 个不存在路径 + 1 个真实文件夹
     → materials 逐文件结果正确、scope 展示一致、task_run 停留 queued（无 runner 属预期）
  3. client_request_id 同值重试 → 不产生重复任务
  4. POST instructions → 入队可见（state 队列明细）
  5. 模拟 C8 runner：run.started + 真实闸门弹卡（C6 机制）→ waiting_approvals=1
     → POST instructions 409 APPROVAL_PENDING（reducer 真值表驱动）
  6. 批准 → running；再发一条指令 → 入队
  7. queue/cancel → 取消排队项且 messages 保留发送记录
  8. 模拟 runner 取消（run.cancelled + queue.paused）→ idle + paused
     → queue/continue → 队首出队成新任务
  9. state 快照 at_global_seq → 从该游标续播流 → ≤at 的事件被跳过
 10. settings：GET 脱敏；PATCH 守门参数 → 版本+审计链；env 覆盖字段 ignored_fields；
     api_key_clear；只读目录 → 500 CONFIG_WRITE_FAILED 且内存不变

说明：C7 无 runner，步骤 5/8 的 run.started / run.cancelled 由本脚本经真实
EventStore 追加（模拟 C8 runner 将来的行为；FSM/投影走的就是真实路径）。

用法：
  cd backend && uv run python ../scripts/sessions/c7_demo.py --data-dir /tmp/c7-demo
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import socket
import stat
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import httpx
import uvicorn

from agentcrew_core.events import RunEventType as T
from agentcrew_core.tools import ToolInvocation, new_call_id
from agentcrew_server.api.app import create_app
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.bus import EventBus
from agentcrew_server.config import load_config
from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.runtime import RuntimeState
from agentcrew_server.sessions import SessionService
from agentcrew_server.settings import SettingsService

TOKEN = secrets.token_hex(24)
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


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
    from agentcrew_core.tools import ToolScheduler, build_default_registry
    approvals.scheduler = ToolScheduler(build_default_registry(), gate=approvals.gate)
    runtime = RuntimeState(
        log=logging.getLogger("c7"), data_dir=data_dir, db=db,
        token=TOKEN, bus=bus, event_store=store, approvals=approvals,
        settings=settings, sessions=sessions,
    )
    return db, channel, store, settings, sessions, approvals, create_app(runtime)


async def append_event(store, **kw):
    return await store.append(**kw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    ns = parser.parse_args()
    data_dir = Path(ns.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db, channel, store, settings, sessions, approvals, app = build(data_dir)

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
    print(f"[READY] 真实服务 {base}")

    # 种子材料：3 个真实文件 + 1 个不存在路径
    src = data_dir / "inbox"
    src.mkdir(exist_ok=True)
    (src / "发票.xlsx").write_text("发票内容")
    (src / "合同.pdf").write_text("合同内容")
    (src / "笔记.txt").write_text("笔记内容")
    folder = data_dir / "授权文件夹"
    folder.mkdir(exist_ok=True)
    (folder / "已有材料.txt").write_text("夹内已有材料")

    async def run(client: httpx.AsyncClient):
        # ── 1 limits ──
        r = await client.get(f"{base}/api/limits", headers=HEADERS)
        print(f"\n① GET /api/limits → {r.status_code} {r.json()['data']}")

        # ── 2 创建：材料导入 ──
        r = await client.post(f"{base}/api/conversations", headers=HEADERS, json={
            "instruction": "整理 inbox 里的材料并汇总",
            "client_request_id": "demo-req-1",
            "import_files": [str(src / "发票.xlsx"), str(src / "合同.pdf"),
                             str(src / "笔记.txt"), str(src / "不存在.docx")],
            "folders": [str(folder)],
        })
        data = r.json()["data"]
        conv_id = data["conversation"]["id"]
        task1 = data["task_run_id"]
        print(f"② POST /api/conversations → {r.status_code}；"
              f"materials：成功 {sum(1 for m in data['materials'] if not m['error'])} 个 / "
              f"失败 {sum(1 for m in data['materials'] if m['error'])} 个"
              f"（{[m['error'] for m in data['materials'] if m['error']]}）")
        mat_dir = data["scope"]["materials_dir"]
        copied = sorted(p.name for p in Path(mat_dir).iterdir())
        print(f"   materials/ 实际文件：{copied}；原文件未动：{(src / '发票.xlsx').exists()}")
        print(f"   scope：folders={data['scope']['folders']}")
        status = db.read_conn.execute(
            "SELECT status FROM task_runs WHERE id=?", (task1,)).fetchone()[0]
        print(f"   task_run 状态 = {status}（无 runner 停留 queued 属预期）")

        # ── 3 幂等重试 ──
        r = await client.post(f"{base}/api/conversations", headers=HEADERS, json={
            "instruction": "整理 inbox 里的材料并汇总",
            "client_request_id": "demo-req-1"})
        same = r.json()["data"]["task_run_id"] == task1
        n = db.read_conn.execute("SELECT count(*) FROM task_runs").fetchone()[0]
        print(f"③ client_request_id 重试同值 → 同一任务={same}，任务总数={n}")

        # ── 4 第二条指令入队 ──
        r = await client.post(f"{base}/api/conversations/{conv_id}/instructions",
                              headers=HEADERS, json={"text": "顺便生成清单.csv"})
        print(f"④ POST instructions（starting 中）→ {r.status_code} {r.json()['data']}")

        # ── 5 审批挂起 → 409 ──
        await append_event(store, task_run_id=task1, conversation_id=conv_id,
                           type=T.RUN_STARTED,
                           payload={"attempt_no": 1, "attempt_id": "att-1",
                                    "kind": "initial"})
        ctx = sessions.build_work_context(conv_id, task1)
        gated = asyncio.ensure_future(approvals.run_tool(
            task_run_id=task1, conversation_id=conv_id, agent_id="default",
            invocation=ToolInvocation(
                new_call_id(), "write_file",
                {"path": str(Path(ctx.cwd) / "汇总.md"), "content": "汇总"}),
            ctx=ctx))
        for _ in range(100):
            if sessions.state_snapshot(conv_id)["waiting_approvals"]:
                break
            await asyncio.sleep(0.05)
        snap = sessions.state_snapshot(conv_id)
        print(f"⑤ run.started + 真实弹卡 → state={snap['state']} "
              f"waiting_approvals={snap['waiting_approvals']} "
              f"can_queue={snap['can_queue']}")
        r = await client.post(f"{base}/api/conversations/{conv_id}/instructions",
                              headers=HEADERS, json={"text": "审批期间的插话"})
        print(f"   POST instructions → {r.status_code} "
              f"{r.json()['error']['code']}（reducer 真值表驱动）")
        call_id = db.read_conn.execute(
            "SELECT json_extract(payload, '$.tool_call_id') FROM run_events"
            " WHERE type='permission.requested'").fetchone()[0]
        await approvals.submit(call_id, "allow_once")
        await asyncio.wait_for(gated, timeout=15)
        snap = sessions.state_snapshot(conv_id)
        print(f"   批准后 → waiting_approvals={snap['waiting_approvals']} "
              f"can_queue={snap['can_queue']}；文件真实写入="
              f"{(Path(ctx.cwd) / '汇总.md').exists()}")

        # ── 6 批准后再发一条 → 入队 ──
        r = await client.post(f"{base}/api/conversations/{conv_id}/instructions",
                              headers=HEADERS, json={"text": "再排一条：发周报"})
        print(f"⑥ running 中 POST instructions → {r.status_code} {r.json()['data']}")

        # ── 7 queue/cancel ──
        snap = sessions.state_snapshot(conv_id)
        victim = snap["queue"][0]["id"]
        r = await client.post(f"{base}/api/conversations/{conv_id}/queue/cancel",
                              headers=HEADERS, json={"item_ids": [victim]})
        msgs = [row[0] for row in db.read_conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ?"
            " ORDER BY created_at", (conv_id,)).fetchall()]
        print(f"⑦ queue/cancel → {r.status_code} {r.json()['data']}；"
              f"messages 保留发送记录：{msgs}")

        # ── 8 取消当前任务 → 暂停 → 继续 ──
        await append_event(store, task_run_id=task1, conversation_id=conv_id,
                           type=T.RUN_CANCELLED, payload={})
        await append_event(store, task_run_id=None, conversation_id=conv_id,
                           type=T.QUEUE_PAUSED, payload={})
        snap = sessions.state_snapshot(conv_id)
        print(f"⑧ run.cancelled → state={snap['state']} "
              f"paused={snap['queue_paused']} 队列={[i['text'] for i in snap['queue']]} "
              f"can_continue_queue={snap['can_continue_queue']}")
        r = await client.post(f"{base}/api/conversations/{conv_id}/queue/continue",
                              headers=HEADERS)
        snap = sessions.state_snapshot(conv_id)
        new_task = snap["current_task_run_id"]
        row = db.read_conn.execute(
            "SELECT instruction, status FROM task_runs WHERE id=?",
            (new_task,)).fetchone()
        print(f"   queue/continue → HTTP {r.status_code}（无响应体）；队首出队："
              f"instruction={row[0]!r} status={row[1]}；队列剩余="
              f"{[i['text'] for i in snap['queue']]}")

        # ── 9 at_global_seq 配对续播 ──
        r = await client.get(f"{base}/api/conversations/{conv_id}/state",
                             headers=HEADERS)
        at = r.json()["data"]["at_global_seq"]
        await append_event(store, task_run_id=None, conversation_id=conv_id,
                           type=T.QUEUE_ITEM_ENQUEUED,
                           payload={"item_id": "after-snapshot", "text": "快照之后"})
        frames = []
        async with client.stream(
                "GET", f"{base}/api/conversations/{conv_id}/stream?from={at}",
                headers=HEADERS) as resp:
            buf = ""
            async for line in resp.aiter_lines():
                if line == "":
                    if buf:
                        frames.append(json.loads(buf))
                        break
                    continue
                if line.startswith("data:"):
                    buf += line.split(":", 1)[1].strip()
        print(f"⑨ at_global_seq={at} → 续播恰收 1 帧 global_seq="
              f"{[f['global_seq'] for f in frames]}（≤{at} 已被快照吸收跳过）"
              f" payload.text={frames[0]['payload']['text']}")

        # ── 10 settings ──
        r = await client.get(f"{base}/api/settings", headers=HEADERS)
        view = r.json()["data"]
        audit_before = db.read_conn.execute(
            "SELECT count(*) FROM audit_log").fetchone()[0]
        r = await client.patch(f"{base}/api/settings", headers=HEADERS,
                               json={"gates": {"max_steps": 12},
                                     "models": {"main": {"api_key": "sk-demo-key-8899"}}})
        patched = r.json()["data"]["settings"]
        audit_after = db.read_conn.execute(
            "SELECT count(*) FROM audit_log").fetchone()[0]
        print(f"⑩ GET settings → config_version={view['config_version']} "
              f"hint={view['models']['main']['api_key_hint']!r} "
              f"configured={view['models']['main']['api_key_configured']}")
        print(f"   PATCH → 版本 {view['config_version']}→{patched['config_version']}，"
              f"max_steps={patched['gates']['max_steps']}，"
              f"hint={patched['models']['main']['api_key_hint']!r}，"
              f"审计链 {audit_before}→{audit_after}")
        r = await client.patch(f"{base}/api/settings", headers=HEADERS,
                               json={"api_key_clear": ["main"]})
        slot = r.json()["data"]["settings"]["models"]["main"]
        print(f"   api_key_clear → configured={slot['api_key_configured']} "
              f"hint={slot['api_key_hint']!r}；"
              f"文件含 key={'sk-demo-key-8899' in (data_dir / 'config.json').read_text()}")
        # 写失败：目录只读
        os.chmod(data_dir, stat.S_IREAD | stat.S_IEXEC)
        try:
            r = await client.patch(f"{base}/api/settings", headers=HEADERS,
                                   json={"gates": {"max_steps": 99}})
            err = r.json()["error"]
            print(f"   只读目录 PATCH → {r.status_code} {err['code']}；"
                  f"内存不变 max_steps="
                  f"{settings.config.values['gates']['max_steps']}")
        finally:
            os.chmod(data_dir, stat.S_IRWXU)
        check = verify_with_anchor(db.write_conn, data_dir / "chain-head.txt")
        print(f"   审计链校验 ok={check.ok}（{check.checked_count} 条）")

    async def runner():
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            await run(client)

    asyncio.run(runner())
    print("\n[全部场景完成]")
    server.should_exit = True
    time.sleep(0.5)
    channel.close()
    db.close()


if __name__ == "__main__":
    main()
