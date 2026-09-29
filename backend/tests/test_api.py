"""API 装配自测：真实 ASGI 栈（TestClient + 临时真实 SQLite 文件）。

验证对象是自家中间件与装配代码，不是 mock 外部系统（ADR-006）。
C3 起全站（除 /api/health）要求 Bearer，请求统一带 Authorization 头。
"""

import logging

import pytest
from fastapi.testclient import TestClient

from agentcrew_server.api.app import create_app
from agentcrew_server.api.errors import ApiError, ErrorCode
from agentcrew_server.db.database import Database
from agentcrew_server.runtime import RuntimeState

TOKEN = "tok-api-1234567890"


def _make_runtime(tmp_path, name="t.db") -> RuntimeState:
    db = Database(tmp_path / name)
    return RuntimeState(
        log=logging.getLogger("test.agentcrew"), data_dir=tmp_path, db=db,
        token=TOKEN,
    )


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path):
    runtime = _make_runtime(tmp_path)
    app = create_app(runtime)
    with TestClient(app) as c:  # with 触发 lifespan（含 shutdown）
        yield c
    assert runtime.db.closed


def test_health_success_envelope(client):
    resp = client.get("/api/health")  # 契约豁免鉴权
    assert resp.status_code == 200
    assert resp.json() == {"data": {"status": "ok"}}


def test_cors_preflight_and_actual_request(client):
    preflight = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"

    actual = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert actual.status_code == 200
    assert actual.headers["access-control-allow-origin"] == "*"


def test_404_uses_error_envelope(client, auth):
    resp = client.get("/api/no-such-route", headers=auth)
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"]


def test_405_uses_error_envelope(client, auth):
    resp = client.post("/api/health", headers=auth)
    assert resp.status_code == 405
    assert resp.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_api_error_maps_status_from_table(tmp_path):
    runtime = _make_runtime(tmp_path, "t2.db")
    app = create_app(runtime)

    @app.get("/api/raise-api-error")
    async def raise_api_error():
        raise ApiError(ErrorCode.QUEUE_EMPTY, "队列为空", detail={"reason": "空"})

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get(
            "/api/raise-api-error", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "QUEUE_EMPTY"
    assert body["error"]["message"] == "队列为空"
    assert body["error"]["detail"] == {"reason": "空"}
    assert runtime.db.closed  # lifespan shutdown 关闭了数据库


def test_unhandled_exception_becomes_internal_error_envelope(tmp_path):
    runtime = _make_runtime(tmp_path, "t3.db")
    app = create_app(runtime)

    @app.get("/api/boom")
    async def boom():
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/boom", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"
