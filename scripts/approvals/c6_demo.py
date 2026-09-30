#!/usr/bin/env python3
"""M0-C6 验收演示（进程内直调 + 真实 HTTP）。

步骤：
  1. 进程内触发 write_file → 三级闸门 ask → PERMISSION_REQUESTED 落库
  2. curl GET /api/task-runs/:id/approvals?status=pending → 审批卡可见
  3. curl POST /api/tool-approvals/:callId {"decision":"allow_once"} → 工具执行
     文件真实写入 + RESOLVED 落库 + 审计链追加
  4. 同决定重试 → 200 幂等；不同决定 → 409 APPROVAL_STALE
  5. allow_always → 规则表追加 + 同类第二次不弹卡
  6. reject → 工具不执行
  7. 审计链校验脚本全绿

用法：
  cd backend && uv run python ../scripts/approvals/c6_demo.py --data-dir /tmp/c6-demo
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import httpx
import uvicorn

from agentcrew_core.tools import ToolInvocation, WorkContext, new_call_id
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

CONV, RUN, AGENT = "conv-c6", "run-c6", "agent-c6"
TOKEN = secrets.token_hex(24)


def setup(data_dir: Path):
    db = Database(data_dir / "agentcrew.db")
    run_migrations(db.write_conn, data_dir / "backups")
    db.write_conn.execute(
        "INSERT OR IGNORE INTO conversations (id, workspace_id, agent_id,"
        " created_at, updated_at) VALUES (?, 'ws', ?, '2026-01-01', '2026-01-01')",
        (CONV, AGENT))
    db.write_conn.execute(
        "INSERT OR IGNORE INTO task_runs (id, conversation_id, instruction,"
        " status, created_at, updated_at) VALUES (?, ?, 'c6 demo', 'running',"
        " '2026-01-01', '2026-01-01')", (RUN, CONV))
    channel = WriteChannel(db.write_conn)
    bus = EventBus()
    store = EventStore(channel, publisher=bus.publish)
    approvals = ApprovalService(db, store, data_dir / "chain-head.txt")
    from agentcrew_core.tools import ToolScheduler, build_default_registry

    scheduler = ToolScheduler(build_default_registry(), gate=approvals.gate)
    approvals.scheduler = scheduler
    return db, channel, bus, store, approvals


async def fire_tool(approvals, scope: Path, artifacts: Path, tool: str,
                    inp: dict):
    """启动带闸门工具执行（弹卡时挂起，由 curl 决定恢复）。"""
    inv = ToolInvocation(new_call_id(), tool, inp)
    ctx = WorkContext(scope=[scope], protected=[], artifacts_dir=artifacts,
                      task_run_id=RUN)
    return await approvals.run_tool(task_run_id=RUN, conversation_id=CONV,
                                    agent_id=AGENT, invocation=inv, ctx=ctx)


async def wait_for_request(db, after_seq=0, timeout=10):
    """等一张**新于 after_seq** 的审批卡（避免拿到已处理的旧卡——demo 竞态）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = db.read_conn.execute(
            "SELECT global_seq, json_extract(payload, '$.tool_call_id')"
            " FROM run_events WHERE type = 'permission.requested'"
            " AND global_seq > ? ORDER BY global_seq DESC LIMIT 1",
            (after_seq,)).fetchone()
        if row:
            return row[0], row[1]
        await asyncio.sleep(0.05)
    raise AssertionError("等待审批卡超时")


async def latest_event_seq(db):
    row = db.read_conn.execute(
        "SELECT COALESCE(MAX(global_seq), 0) FROM run_events").fetchone()
    return row[0]


async def main_async(args):
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db, channel, bus, store, approvals = setup(data_dir)
    scope = data_dir / "workspace"
    scope.mkdir(exist_ok=True)
    artifacts = data_dir / "artifacts"

    runtime = RuntimeState(
        log=logging.getLogger("c6"), data_dir=data_dir, db=db,
        token=TOKEN, bus=bus, event_store=store, approvals=approvals,
    )
    app = create_app(runtime)
    probe = __import__("socket").socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_config=None, lifespan="on",
        timeout_graceful_shutdown=5,
    ))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    print(f"[READY] 真实服务 {base}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        # ── ① 弹卡 ──
        target1 = scope / "report.md"
        task1 = asyncio.ensure_future(fire_tool(
            approvals, scope, artifacts, "write_file",
            {"path": str(target1), "content": "# 报告"}))
        seq0 = await latest_event_seq(db)
        _, call1 = await wait_for_request(db, after_seq=seq0)
        print(f"\n① 弹卡 call_id={call1[:12]}…")

        # ── ② pending 卡可见 ──
        resp = await client.get(f"{base}/api/task-runs/{RUN}/approvals",
                                headers=headers)
        cards = resp.json()["data"]
        print(f"② GET pending → HTTP {resp.status_code}，{len(cards)} 张卡："
              f"tool={cards[0]['tool']} risk={cards[0]['risk']} "
              f"preview={cards[0]['always_scope_preview']}")

        # ── ③ 批准 → 工具执行 + 落库 + 审计 ──
        audit_before = db.read_conn.execute(
            "SELECT count(*) FROM audit_log").fetchone()[0]
        resp = await client.post(f"{base}/api/tool-approvals/{call1}",
                                 headers=headers,
                                 json={"decision": "allow_once"})
        print(f"③ POST allow_once → HTTP {resp.status_code} "
              f"data={resp.json()['data']}")
        result1 = await asyncio.wait_for(task1, timeout=15)
        print(f"   工具执行 ok={result1.ok}；文件存在={target1.exists()} "
              f"内容={target1.read_text()!r}")
        audit_after = db.read_conn.execute(
            "SELECT count(*) FROM audit_log").fetchone()[0]
        print(f"   审计链：{audit_before} → {audit_after} 条")

        # ── ④ 幂等 / 409 ──
        resp = await client.post(f"{base}/api/tool-approvals/{call1}",
                                 headers=headers,
                                 json={"decision": "allow_once"})
        d = resp.json()["data"]
        print(f"④ 同决定重试 → HTTP {resp.status_code} "
              f"idempotent_replay={d['idempotent_replay']}")
        resp = await client.post(f"{base}/api/tool-approvals/{call1}",
                                 headers=headers,
                                 json={"decision": "reject_always"})
        print(f"   不同决定 → HTTP {resp.status_code} "
              f"code={resp.json()['error']['code']}")

        # ── ⑤ allow_always → 规则表 + 第二次同类不弹卡 ──
        target2 = scope / "data.csv"
        task2 = asyncio.ensure_future(fire_tool(
            approvals, scope, artifacts, "write_file",
            {"path": str(target2), "content": "a,b\n1,2"}))
        seq4 = await latest_event_seq(db)
        _, call2 = await wait_for_request(db, after_seq=seq4)
        resp = await client.post(f"{base}/api/tool-approvals/{call2}",
                                 headers=headers,
                                 json={"decision": "allow_always"})
        print(f"⑤ allow_always → HTTP {resp.status_code}")
        await asyncio.wait_for(task2, timeout=15)
        rules = db.read_conn.execute(
            "SELECT tool_name, pattern, effect FROM agent_permission_rules"
        ).fetchall()
        print(f"   规则表追加：{rules}")
        result3 = await fire_tool(approvals, scope, artifacts, "write_file",
                                  {"path": str(scope / "auto.txt"),
                                   "content": "同目录自动放行"})
        print(f"   同目录第二次写 ok={result3.ok}（不弹卡直接放行）")
        pending = await client.get(
            f"{base}/api/task-runs/{RUN}/approvals?status=pending",
            headers=headers)
        print(f"   当前 pending = {len(pending.json()['data'])}（应为 0）")

        # ── ⑥ reject → 不执行（用非只读 bash：write_file 已被 ⑤ 的规则放行）──
        target3 = scope / "rejected-by-bash.txt"
        task3 = asyncio.ensure_future(fire_tool(
            approvals, scope, artifacts, "bash",
            {"command": f"echo denied > {target3}"}))
        seq5 = await latest_event_seq(db)
        _, call3 = await wait_for_request(db, after_seq=seq5)
        resp = await client.post(f"{base}/api/tool-approvals/{call3}",
                                 headers=headers,
                                 json={"decision": "reject_once"})
        result3 = await asyncio.wait_for(task3, timeout=15)
        print(f"⑥ reject_once → 工具 ok={result3.ok} "
              f"error={result3.error}；文件存在={target3.exists()}")

        # ── ⑦ 审计链校验 ──
        verify = verify_with_anchor(db.write_conn,
                                    data_dir / "chain-head.txt")
        count = db.read_conn.execute(
            "SELECT count(*) FROM audit_log").fetchone()[0]
        print(f"⑦ 审计链 {count} 条，校验 ok={verify.ok}"
              f"{'（reason=' + str(verify.reason) + '）' if not verify.ok else ''}")

    print("\n[全部场景完成]")
    server.should_exit = True
    await asyncio.sleep(0.5)
    channel.close()
    db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
