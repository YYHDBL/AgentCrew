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
