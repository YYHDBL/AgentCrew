"""只读诊断模式端点测试：503 DIAGNOSTIC_MODE 守卫 + /api/diagnostics。"""

import logging

from fastapi.testclient import TestClient

from agentcrew_server.api.app import create_app
from agentcrew_server.db.database import Database
from agentcrew_server.runtime import DiagnosticInfo, RuntimeState


def _client(tmp_path, diagnostic=None):
    db = Database(tmp_path / "t.db")
    runtime = RuntimeState(
        log=logging.getLogger("test.diag"), data_dir=tmp_path, db=db,
        diagnostic=diagnostic,
    )
    return TestClient(create_app(runtime))


def test_normal_mode_diagnostics(tmp_path):
    with _client(tmp_path) as client:
        resp = client.get("/api/diagnostics")
    assert resp.status_code == 200
    assert resp.json() == {"data": {"mode": "normal"}}


def test_diagnostic_mode_blocks_business_but_keeps_diag(tmp_path):
    diag = DiagnosticInfo(reason="迁移失败：迁移 v2 第 1 条语句失败")
    with _client(tmp_path, diag) as client:
        health = client.get("/api/health")
        assert health.status_code == 200

        diag_resp = client.get("/api/diagnostics")
        assert diag_resp.status_code == 200
        body = diag_resp.json()["data"]
        assert body["mode"] == "diagnostic"
        assert "迁移失败" in body["reason"]
        assert "backups" in body["hint"]

        blocked = client.get("/api/conversations")  # 业务端点（C3+ 路由）→ 503
        assert blocked.status_code == 503
        err = blocked.json()["error"]
        assert err["code"] == "DIAGNOSTIC_MODE"
        assert err["detail"]["reason"].startswith("迁移失败")
