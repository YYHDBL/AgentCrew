"""鉴权中间件测试（真实 ASGI 栈）。"""

import logging

import pytest
from fastapi.testclient import TestClient

from agentcrew_server.api.app import create_app
from agentcrew_server.db.database import Database
from agentcrew_server.runtime import RuntimeState

TOKEN = "tok-abcdef123456"


@pytest.fixture
def client(tmp_path):
    db = Database(tmp_path / "t.db")
    runtime = RuntimeState(
        log=logging.getLogger("test.auth"), data_dir=tmp_path, db=db, token=TOKEN
    )
    with TestClient(create_app(runtime)) as c:
        yield c
    assert db.closed


def test_missing_token_401(client):
    resp = client.get("/api/diagnostics")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_wrong_token_401(client):
    resp = client.get("/api/diagnostics", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_correct_token_passes(client):
    resp = client.get("/api/diagnostics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.json() == {"data": {"mode": "normal"}}


def test_health_exempt_without_token(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"data": {"status": "ok"}}


def test_malformed_header_401(client):
    resp = client.get("/api/diagnostics", headers={"Authorization": TOKEN})
    assert resp.status_code == 401


def test_non_ascii_header_401_not_500():
    """外审回稿修复：非 ASCII 请求头走 bytes 比较 → 401，不得 500。

    直接调 ASGI 中间件（原始 bytes 头是 ASGI 层的真实形态，绕开 httpx
    对请求头编码的预处理）。
    """
    import asyncio

    from agentcrew_server.api.auth import BearerAuthMiddleware

    captured: dict = {}

    async def app(scope, receive, send):
        captured["reached_app"] = True

    middleware = BearerAuthMiddleware(app, TOKEN)

    async def run(raw: bytes) -> int:
        captured.clear()
        scope = {
            "type": "http", "path": "/api/diagnostics",
            "headers": [(b"authorization", raw)],
        }
        sent: list = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await middleware(scope, receive, send)
        start = next((m for m in sent if m["type"] == "http.response.start"), None)
        return start["status"] if start else 200

    # UTF-8 中文凭证（非 ASCII 字节）与纯垃圾字节：都必须是 401
    assert asyncio.run(run("Bearer 口令123".encode("utf-8"))) == 401
    assert asyncio.run(run(b"\xff\xfe\xfd")) == 401
    assert "reached_app" not in captured
    # 正确凭证仍放行
    assert asyncio.run(run(f"Bearer {TOKEN}".encode())) in (200, 404)
