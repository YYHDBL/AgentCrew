"""API 装配自测：真实 ASGI 栈（TestClient + 临时真实 SQLite 文件）。

验证对象是自家中间件与装配代码，不是 mock 外部系统（ADR-006）。
"""

import logging

import pytest
from fastapi.testclient import TestClient

from agentcrew_server.api.app import create_app
from agentcrew_server.api.errors import ApiError, ErrorCode
from agentcrew_server.db.database import Database
from agentcrew_server.runtime import RuntimeState


def _make_client(tmp_path, raise_server_exceptions: bool = True) -> TestClient:
    db = Database(tmp_path / "test.db")
    runtime = RuntimeState(
        log=logging.getLogger("test.agentcrew"), data_dir=tmp_path, db=db
    )
    app = create_app(runtime)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_health_success_envelope(tmp_path):
    with _make_client(tmp_path) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"data": {"status": "ok"}}


def test_cors_preflight_and_actual_request(tmp_path):
    with _make_client(tmp_path) as client:
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


def test_404_uses_error_envelope(tmp_path):
    with _make_client(tmp_path) as client:
        resp = client.get("/api/no-such-route")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"]


def test_405_uses_error_envelope(tmp_path):
    with _make_client(tmp_path) as client:
        resp = client.post("/api/health")
    assert resp.status_code == 405
    assert resp.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_api_error_maps_status_from_table(tmp_path):
    db = Database(tmp_path / "t.db")
    app = create_app(
        RuntimeState(log=logging.getLogger("t"), data_dir=tmp_path, db=db)
    )

    @app.get("/api/raise-api-error")
    async def raise_api_error():
        raise ApiError(ErrorCode.QUEUE_EMPTY, "队列为空", detail={"reason": "空"})

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/raise-api-error")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "QUEUE_EMPTY"
    assert body["error"]["message"] == "队列为空"
    assert body["error"]["detail"] == {"reason": "空"}
    assert db.closed  # lifespan shutdown 关闭了数据库


def test_unhandled_exception_becomes_internal_error_envelope(tmp_path):
    db = Database(tmp_path / "t2.db")
    app = create_app(
        RuntimeState(log=logging.getLogger("t2"), data_dir=tmp_path, db=db)
    )

    @app.get("/api/boom")
    async def boom():
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/boom")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"
