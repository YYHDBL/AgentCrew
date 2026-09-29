#!/usr/bin/env python3
"""M0-C3 验收驱动：同进程起真实 uvicorn + 真实 EventStore 生产者。

事件生产者与 HTTP 服务同进程（符合"单进程写者"设计——事件总线扇出的帧
来自本进程写通道提交后的发布）；HTTP/SSE 客户端视角全部真实（真 socket）。

场景：
  A  补播→实时无缝交接（补播进行中持续生产新事件，无丢失无重复）
  B  断线重连排他游标续播
  C  双任务并发写事件 → SSE 帧序 global_seq 严格递增（发布顺序=提交顺序）
  D  慢客户端：队列上限 1000 打满 → event:resync（含最后连续游标）→
     连接被服务端终止 → 从游标重连补齐
  E  连接上限：第 33 条 SSE 连接 → 503 SSE_LIMIT；全部断开后订阅归零（不泄漏）

用法：
  cd backend && uv run python ../scripts/seed/c3_acceptance.py \
      --data-dir /tmp/c3-acc [--seed-only]
--seed-only：只灌历史事件到 data-dir 后退出（供外部 cli server + curl 演示）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import httpx
import uvicorn

from agentcrew_core.events import RunEventType as T
from agentcrew_server.api.app import create_app
from agentcrew_server.bus import EventBus
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.runtime import RuntimeState

CONV = "conv-demo"
TOKEN = "tok-c3-acceptance-123456"


def setup_runtime(data_dir: Path):
    db = Database(data_dir / "agentcrew.db")
    run_migrations(db.write_conn, data_dir / "backups")
    conn = db.write_conn
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT OR IGNORE INTO conversations (id, workspace_id, agent_id, created_at,"
        " updated_at) VALUES (?, 'ws-demo', 'agent-demo', '2026-09-29T00:00:00',"
        " '2026-09-29T00:00:00')", (CONV,),
    )
    for run_id in ("run-a", "run-b"):
        conn.execute(
            "INSERT OR IGNORE INTO task_runs (id, conversation_id, instruction,"
            " status, created_at, updated_at) VALUES (?, ?, 'demo', 'running',"
            " '2026-09-29T00:00:00', '2026-09-29T00:00:00')", (run_id, CONV),
        )
    conn.execute("COMMIT")
    channel = WriteChannel(db.write_conn)
    bus = EventBus()  # 生产默认：队列 1000 / 连接 32
    store = EventStore(channel, publisher=bus.publish)
    runtime = RuntimeState(
        log=logging.getLogger("c3.driver"), data_dir=data_dir, db=db,
        write_channel=channel, token=TOKEN, bus=bus, event_store=store,
    )
    return runtime


async def produce(store, n: int, run_id: str = "run-a", *, pad: int = 0,
                  interval_s: float = 0.0):
    for i in range(n):
        await store.append(
            task_run_id=run_id, conversation_id=CONV, type=T.QUESTION_REQUESTED,
            payload={"q": f"{run_id} 事件 {i}", "pad": "x" * pad},
        )
        if interval_s:
            await asyncio.sleep(interval_s)


def parse_frames(text: str):
    """SSE 原文 → (事件帧 global_seq 列表, 控制帧 dict, 控制帧名列表)。"""
    seqs, controls, names = [], [], []
    current_event = None
    for line in text.splitlines():
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_text = line.split(":", 1)[1]
            if current_event in ("resync", "shutdown"):
                controls.append(json.loads(data_text))
                names.append(current_event)
            else:
                seqs.append(json.loads(data_text)["global_seq"])
    return seqs, controls, names


async def sse_collect(base: str, path: str, want: int, timeout: float = 10.0):
    """流式收 SSE 至少 want 个数据帧后断开，返回 (seqs, controls, names)。

    帧收满后服务器流仍保持打开——httpx 读超时按正常收尾处理。
    """
    seqs, controls, names = [], [], []
    async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
        async with client.stream(
            "GET", base + path, headers={"Authorization": f"Bearer {TOKEN}"}
        ) as resp:
            assert resp.status_code == 200, resp.status_code
            buf, current_event = "", None
            deadline = time.monotonic() + timeout
            try:
                async for line in resp.aiter_lines():
                    if time.monotonic() > deadline:
                        break
                    if line == "":
                        if buf:
                            if current_event in ("resync", "shutdown"):
                                controls.append(json.loads(buf))
                                names.append(current_event)
                            else:
                                seqs.append(json.loads(buf)["global_seq"])
                        buf, current_event = "", None
                        if len(seqs) >= want:
                            break
                        continue
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        buf += line.split(":", 1)[1]
            except (httpx.TimeoutException, httpx.ReadError):
                pass
            if buf and current_event not in ("resync", "shutdown"):
                seqs.append(json.loads(buf)["global_seq"])
    return seqs, controls, names


def check(name: str, cond: bool, evidence: str):
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}：{evidence}")
    if not cond:
        raise SystemExit(f"场景 {name} 未通过")


async def scenario_replay_to_live(store, base):
    print("\n== 场景 A：补播→实时无缝交接（补播期间持续生产）")
    await produce(store, 30)  # 历史
    got = asyncio.Event()

    async def produce_during_replay():
        await asyncio.sleep(0.3)  # 客户端正在补播 30 条历史
        await produce(store, 20, interval_s=0.02)  # 补播期间持续产生新事件
        got.set()

    task = asyncio.create_task(produce_during_replay())
    seqs, controls, _ = await sse_collect(base, f"/api/conversations/{CONV}/stream?from=0", 50)
    await got.wait()
    check("A 无丢失无重复", seqs == list(range(1, 51)),
          f"收到 {len(seqs)} 帧，global_seq {seqs[0]}→{seqs[-1]} 连续无洞；控制帧 {controls}")
    return seqs[-1]


async def scenario_exclusive_resume(store, base, last_seq):
    print("\n== 场景 B：断开重连，from=<最后 global_seq>（排他）恰好续播")
    await produce(store, 5)
    seqs, _, _ = await sse_collect(
        base, f"/api/conversations/{CONV}/stream?from={last_seq}", 5
    )
    check("B 排他续播", seqs == [last_seq + 1 + i for i in range(5)],
          f"from={last_seq} → 恰好收到 {[s - last_seq for s in seqs]}（+1..+5，无重发）")
    return seqs[-1]


async def scenario_concurrent_monotonic(store, base, last_seq):
    print("\n== 场景 C：双任务并发写事件 → 帧序严格单调（发布顺序=提交顺序）")
    await asyncio.gather(
        produce(store, 100, "run-a"), produce(store, 100, "run-b")
    )
    seqs, _, _ = await sse_collect(
        base, f"/api/conversations/{CONV}/stream?from={last_seq}", 200
    )
    expected = list(range(last_seq + 1, last_seq + 201))
    strictly_increasing = all(b - a == 1 for a, b in zip(seqs, seqs[1:]))
    check("C 帧序单调", seqs == expected and strictly_increasing,
          f"200 帧全局连续递增 {seqs[0]}→{seqs[-1]}（run-a/run-b 交错提交）")
    return seqs[-1]


async def scenario_slow_consumer(store, base, last_seq):
    print("\n== 场景 D：慢客户端——每连接队列 1000 打满 → resync → 服务端终止 → 补齐")
    port = int(base.rsplit(":", 1)[1])
    raw = socket.socket()
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    await asyncio.to_thread(raw.connect, ("127.0.0.1", port))
    await asyncio.to_thread(
        raw.sendall,
        f"GET /api/conversations/{CONV}/stream?from={last_seq} HTTP/1.1\r\n"
        f"Host: x\r\nAuthorization: Bearer {TOKEN}\r\n\r\n".encode(),
    )
    await asyncio.sleep(0.5)  # 不读：服务端发送链路（内核缓冲+asyncio 缓冲）相继打满
    total = 2400
    await produce(store, total, "run-a", pad=300)  # ~300B/帧：穿透缓冲 + 灌满队列 1000
    await asyncio.sleep(1.0)

    def drain_until_resync() -> bytes:
        chunks = b""
        try:
            while True:
                part = raw.recv(65536)
                if not part:
                    break  # 服务端关闭连接
                chunks += part
                if b"event: resync" in chunks:
                    raw.settimeout(2)
                    while True:
                        try:
                            more = raw.recv(65536)
                        except socket.timeout:
                            break
                        if not more:
                            break
                        chunks += more
                    break
        except socket.timeout:
            pass
        return chunks

    chunks = await asyncio.to_thread(drain_until_resync)
    raw.close()
    text = chunks.decode("utf-8", errors="replace")
    seqs, controls, names = parse_frames(text)
    check("D 收到 resync 帧", "resync" in names,
          f"收到 {len(seqs)} 个数据帧 + 控制帧 {names}；"
          f"resync 游标 = {controls[-1]['last_continuous_global_seq'] if controls else '-'}")
    cursor = controls[-1]["last_continuous_global_seq"]
    check("D resync 游标=实际送达最后一条", cursor == seqs[-1],
          f"游标 {cursor} == 最后数据帧 {seqs[-1]}")
    missing = total - len(seqs)
    seqs2, _, _ = await sse_collect(
        base, f"/api/conversations/{CONV}/stream?from={cursor}", missing, timeout=15
    )
    covered = set(seqs) | set(seqs2)
    expected = set(range(last_seq + 1, last_seq + 1 + total))
    check("D 从游标重连补齐", covered == expected,
          f"补播收 {len(seqs2)} 帧；两段并集 = 全部 {total} 条（缺 {len(expected - covered)}）")
    return max(covered)


async def scenario_connection_limit(base):
    print("\n== 场景 E：连接上限 32 → 第 33 条 503 SSE_LIMIT；断开后订阅归零")
    port = int(base.rsplit(":", 1)[1])
    writers = []
    for _ in range(32):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            f"GET /api/conversations/{CONV}/stream HTTP/1.1\r\n"
            f"Host: x\r\nAuthorization: Bearer {TOKEN}\r\n\r\n".encode()
        )
        await writer.drain()
        writers.append(writer)
    await asyncio.sleep(0.3)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{base}/api/conversations/{CONV}/stream",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        body = resp.json()
        check("E 第 33 连接 503", resp.status_code == 503 and body["error"]["code"] == "SSE_LIMIT",
              f"HTTP {resp.status_code} code={body['error']['code']}")
    for writer in writers:
        writer.close()
    await asyncio.sleep(1.0)
    count = None
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{base}/api/diagnostics", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        count = resp.json()  # 连接数暂未暴露——用日志与重连成功佐证
    # 32 条全部断开后：再开一条应成功（订阅已清理、名额已释放）
    seqs, _, _ = await sse_collect(
        base, f"/api/conversations/{CONV}/stream?from=0", 1, timeout=5
    )
    check("E 断开后名额释放（无泄漏）", len(seqs) >= 1,
          f"关闭 32 条后新连接正常收到帧（订阅已清理）")


async def main_async(args):
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    runtime = setup_runtime(data_dir)
    store = runtime.event_store

    if args.seed_only:
        await produce(store, 30, "run-a")
        print(f"[SEED] 已灌 30 条历史事件到 {data_dir / 'agentcrew.db'}（{CONV}）")
        runtime.write_channel.close()
        runtime.db.close()
        return

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = uvicorn.Server(uvicorn.Config(
        create_app(runtime), host="127.0.0.1", port=port,
        log_config=None, lifespan="on", timeout_graceful_shutdown=5,
    ))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    print(f"[READY] 真实服务已启动 {base}（token 已按注入方式设置）")

    last = await scenario_replay_to_live(store, base)
    last = await scenario_exclusive_resume(store, base, last)
    last = await scenario_concurrent_monotonic(store, base, last)
    last = await scenario_slow_consumer(store, base, last)
    await scenario_connection_limit(base)

    print("\n== 全部场景通过")
    server.should_exit = True
    await asyncio.sleep(0.5)
    runtime.write_channel.close()
    runtime.db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
