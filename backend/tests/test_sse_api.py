"""SSE 端点测试：真实 uvicorn（线程）+ 真实 HTTP 流式客户端。

覆盖：补播→实时无缝交接（无丢失无重复）、排他游标续播、404、连接上限 503、
慢消费者 resync（裸 socket 不读 + 压缩 SO_RCVBUF）、shutdown 帧、JSON 分页。
"""

import asyncio
import json
import logging
import socket
import threading
import time

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

TOKEN = "tok-sse-1234567890"
CONV = "conv-1"
RUN = "run-1"


class LiveServer:
    def __init__(self, tmp_path, *, bus_kwargs=None):
        self.db = Database(tmp_path / "t.db")
        run_migrations(self.db.write_conn, tmp_path / "backups")
        self.db.write_conn.execute(
            "INSERT INTO conversations (id, workspace_id, agent_id, created_at,"
            " updated_at) VALUES (?, 'ws', 'agent', '2026-01-01', '2026-01-01')",
            (CONV,),
        )
        # 预置 task_runs 父行（直接 SQL，不产生事件——帧序断言从 1 开始数）
        self.db.write_conn.execute(
            "INSERT INTO task_runs (id, conversation_id, instruction, status,"
            " created_at, updated_at) VALUES (?, ?, 'seed', 'running',"
            " '2026-01-01', '2026-01-01')",
            (RUN, CONV),
        )
        self.channel = WriteChannel(self.db.write_conn)
        self.bus = EventBus(**(bus_kwargs or {}))
        self.store = EventStore(self.channel, publisher=self.bus.publish)
        self.runtime = RuntimeState(
            log=logging.getLogger("test.sse"), data_dir=tmp_path, db=self.db,
            write_channel=self.channel, token=TOKEN, bus=self.bus,
            event_store=self.store,
        )
        self.app = create_app(self.runtime)
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        self.port = probe.getsockname()[1]
        probe.close()
        self.server = uvicorn.Server(uvicorn_config(self.app, self.port))

    def start(self):
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(100):
            if self.server.started:
                return
            time.sleep(0.05)
        raise RuntimeError("测试服务器未启动")

    def stop(self):
        # 与 cli.serve 的 _begin_graceful 同序：先投递 shutdown 帧再停收，
        # 否则 SSE 连接与 uvicorn 关闭流程互等
        self.bus.shutdown_all()
        self.server.should_exit = True
        for _ in range(100):
            if not self.server.started:
                break
            time.sleep(0.05)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {TOKEN}"}

    def append(self, n: int, *, pad: int = 0):
        async def _go():
            for i in range(n):
                await self.store.append(
                    task_run_id=RUN, conversation_id=CONV,
                    type=T.QUESTION_REQUESTED,
                    payload={"q": f"事件 {i}", "pad": "x" * pad},
                )
        asyncio.run(_go())

    def close(self):
        self.channel.close()
        self.db.close()


def uvicorn_config(app, port):
    return uvicorn.Config(
        app, host="127.0.0.1", port=port, log_config=None, lifespan="on",
        timeout_graceful_shutdown=5,
    )


def _client() -> httpx.Client:
    return httpx.Client()


import pytest


@pytest.fixture
def server(tmp_path):
    srv = LiveServer(tmp_path)
    srv.start()
    yield srv
    srv.stop()
    srv.close()


def _read_sse(client, base, path, headers, *, min_frames=None, timeout=8.0):
    """流式读 SSE：返回 (数据帧列表, 控制帧列表[(event, dict)], 首行)。

    无更多数据时按 httpx 读超时自然收尾（服务器保持连接不断开）。
    """
    data_objs, controls, first_line = [], [], None
    with client.stream("GET", base + path, headers=headers, timeout=2.0) as resp:
        assert resp.status_code == 200
        buf, current_event = "", None
        try:
            for line in resp.iter_lines():
                if first_line is None and line:
                    first_line = line
                if line == "":
                    if buf:
                        if current_event in ("resync", "shutdown"):
                            controls.append((current_event, json.loads(buf)))
                        else:
                            data_objs.append(json.loads(buf))
                    buf, current_event = "", None
                    if min_frames is not None and len(data_objs) >= min_frames:
                        break
                    continue
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    buf += line.split(":", 1)[1].strip()
        except (httpx.TimeoutException, httpx.ReadError):
            pass  # 读超时 = 服务器暂无更多帧，按收尾处理
        if buf:
            data_objs.append(json.loads(buf))
    return data_objs, controls, first_line


def test_replay_then_live_no_gap_no_duplicate(server):
    server.append(5)  # 历史
    produced = threading.Event()

    def produce():
        time.sleep(0.3)  # 客户端补播历史进行中，同进程持续产生新事件
        server.append(5)
        produced.set()

    threading.Thread(target=produce).start()
    data, controls, first = _read_sse(
        _client(), server.base, f"/api/conversations/{CONV}/stream?from=0",
        server.headers(), min_frames=10,
    )
    assert produced.wait(timeout=5), "生产者应已完成（读满 10 帧与 set 之间存在微秒级竞态）"
    seqs = [f["global_seq"] for f in data]
    assert seqs == list(range(1, 11)), f"历史+实时无丢失无重复，实际 {seqs}"
    assert first.startswith("retry:"), "流首应有 retry 建议行"
    assert controls == []


def test_exclusive_cursor_resume(server):
    server.append(5)
    data, _, _ = _read_sse(
        _client(), server.base, f"/api/conversations/{CONV}/stream?from=0",
        server.headers(), min_frames=5,
    )
    last = data[-1]["global_seq"]
    server.append(3)
    data2, _, _ = _read_sse(
        _client(), server.base,
        f"/api/conversations/{CONV}/stream?from={last}", server.headers(),
        min_frames=3,
    )
    assert [f["global_seq"] for f in data2] == [last + 1, last + 2, last + 3]


def test_unknown_conversation_404(server):
    with _client() as c:
        resp = c.get(
            f"{server.base}/api/conversations/nope/stream", headers=server.headers()
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_unknown_task_run_404(server):
    with _client() as c:
        resp = c.get(
            f"{server.base}/api/task-runs/nope/events", headers=server.headers()
        )
        assert resp.status_code == 404


def test_task_stream_and_json_pagination(server):
    server.append(7)
    data, _, _ = _read_sse(
        _client(), server.base, f"/api/task-runs/{RUN}/events?from=0",
        server.headers(), min_frames=7,
    )
    assert [f["seq"] for f in data] == list(range(1, 8))

    with _client() as c:
        resp = c.get(
            f"{server.base}/api/task-runs/{RUN}/events?after_seq=2&limit=3",
            headers=server.headers(),
        )
        body = resp.json()["data"]
        assert [item["seq"] for item in body["items"]] == [3, 4, 5]
        assert body["next_after_seq"] == 5


def test_connection_limit_503(tmp_path):
    srv = LiveServer(tmp_path, bus_kwargs={"max_connections": 2})
    srv.start()
    try:
        socks = []
        for _ in range(2):
            s = socket.create_connection(("127.0.0.1", srv.port))
            s.sendall(
                f"GET /api/conversations/{CONV}/stream HTTP/1.1\r\n"
                f"Host: x\r\nAuthorization: Bearer {TOKEN}\r\n\r\n".encode()
            )
            socks.append(s)
        with _client() as c:
            resp = c.get(
                f"{srv.base}/api/conversations/{CONV}/stream",
                headers=srv.headers(),
            )
            assert resp.status_code == 503
            assert resp.json()["error"]["code"] == "SSE_LIMIT"
        for s in socks:
            s.close()
    finally:
        srv.stop()
        srv.close()


def test_slow_consumer_gets_resync_then_catches_up(tmp_path):
    """裸 socket 客户端：压小接收缓冲且不读 → 服务端发送缓冲/asyncio 缓冲
    相继打满 → 生成器停摆 → 每连接队列溢出 → 收到 event:resync（游标 =
    实际送达的最后一条）→ 服务端终止连接 → 从游标重连恰好补齐。

    数据量依据：帧 ~800B（payload 填充），需穿透 客户端 rcvbuf(≈4KB) +
    服务端 sndbuf(≈128KB) + asyncio 高水位(64KB) ≈ 200KB 才能让 send 阻塞。
    """
    srv = LiveServer(
        tmp_path, bus_kwargs={"max_queue": 20, "hard_kill_grace": 30.0}
    )
    srv.start()
    srv.append(3)  # 历史
    total_live = 600
    try:
        raw = socket.socket()
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
        raw.connect(("127.0.0.1", srv.port))
        raw.sendall(
            f"GET /api/conversations/{CONV}/stream?from=0 HTTP/1.1\r\n"
            f"Host: x\r\nAuthorization: Bearer {TOKEN}\r\n\r\n".encode()
        )
        time.sleep(0.3)  # 让历史段与初期帧把内核缓冲灌满、生成器停摆
        srv.append(total_live, pad=768)  # 远超队列上限 20 → 溢出标记
        time.sleep(0.5)

        raw.settimeout(5)
        chunks = b""
        try:
            while True:
                part = raw.recv(65536)
                if not part:
                    break
                chunks += part
                if b"event: resync" in chunks:
                    # 流终止的服务端可见语义：resync 之后不再有任何数据帧
                    #（uvicorn 把空闲 socket 留在 keep-alive 池 ~5s，FIN 晚于流结束）
                    raw.settimeout(1.5)
                    try:
                        while True:
                            tail = raw.recv(65536)
                            if not tail:
                                break
                            chunks += tail
                    except socket.timeout:
                        pass
                    break
        except socket.timeout:
            pass
        raw.close()
        text = chunks.decode("utf-8", errors="replace")
        assert "event: resync" in text, "慢消费者应收到 resync 帧"
        _, _, after_resync = text.partition("event: resync")
        assert '"global_seq"' not in after_resync, "resync 后不应再有数据帧（流已终止）"
        assert b"0\r\n\r\n" in chunks, "响应流应以 chunked 终止块结束（服务端终止的协议级证据）"

        lines = text.splitlines()
        data_seqs, resync_data, current_event = [], None, None
        for line in lines:
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload_text = line.split(":", 1)[1]
                if current_event == "resync":
                    resync_data = json.loads(payload_text)
                elif current_event is None or current_event == "message":
                    data_seqs.append(json.loads(payload_text)["global_seq"])
        assert resync_data is not None
        cursor = resync_data["last_continuous_global_seq"]
        assert cursor == data_seqs[-1], "resync 游标 = 实际送达的最后一条"

        data2, _, _ = _read_sse(
            _client(), srv.base,
            f"/api/conversations/{CONV}/stream?from={cursor}", srv.headers(),
            min_frames=3 + total_live - len(data_seqs),
        )
        got = set(data_seqs) | {f["global_seq"] for f in data2}
        expected = set(range(1, 3 + total_live + 1))
        assert got == expected, f"重连后应补齐全部事件（缺 {sorted(expected - got)}）"
    finally:
        srv.stop()
        srv.close()


def test_shutdown_wins_over_overflow(tmp_path):
    """外审回稿修复：队列已满的订阅在优雅关闭时也必须收到 shutdown 帧
    （标志位优先于溢出 resync），而不是走 resync 分支。"""
    srv = LiveServer(tmp_path, bus_kwargs={"max_queue": 3})
    srv.start()
    srv.append(2)
    try:
        raw = socket.socket()
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
        raw.connect(("127.0.0.1", srv.port))
        raw.sendall(
            f"GET /api/conversations/{CONV}/stream?from=0 HTTP/1.1\r\n"
            f"Host: x\r\nAuthorization: Bearer {TOKEN}\r\n\r\n".encode()
        )
        time.sleep(0.3)  # 不读：让队列灌满溢出
        srv.append(30, pad=600)
        time.sleep(0.3)
        srv.bus.shutdown_all()  # 溢出未恢复时触发优雅关闭
        raw.settimeout(5)
        chunks = b""
        try:
            while True:
                part = raw.recv(65536)
                if not part:
                    break
                chunks += part
        except socket.timeout:
            pass
        raw.close()
        text = chunks.decode("utf-8", errors="replace")
        assert "event: shutdown" in text, f"队满订阅也应收到 shutdown 帧：{text[-200:]}"
        assert "event: resync" not in text, "shutdown 优先于溢出 resync"
    finally:
        srv.stop()
        srv.close()


def test_shutdown_frame_on_graceful_stop(server):
    server.append(2)
    received: list[str] = []

    def reader():
        with _client() as c:
            with c.stream(
                "GET", f"{server.base}/api/conversations/{CONV}/stream?from=0",
                headers=server.headers(), timeout=5.0,
            ) as resp:
                for line in resp.iter_lines():
                    received.append(line)
                    if line == "event: shutdown":
                        break

    t = threading.Thread(target=reader)
    t.start()
    time.sleep(0.5)
    server.stop()  # 与 cli 同序：先投 shutdown 帧，再 should_exit
    t.join(timeout=6)
    assert any(line == "event: shutdown" for line in received), received
