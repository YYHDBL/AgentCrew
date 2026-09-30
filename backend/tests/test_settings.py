"""配置 API 测试（M0-C7：真实文件、真实审计链、真实只读目录）。

覆盖：GET 脱敏（key 只回尾 4 位 + effective_source）；PATCH 改守门参数 →
版本递增、GET 生效、审计链 +1；env 覆盖字段 → 200 + ignored_fields；
api_key_clear 真实清槽；文件写失败（只读目录）→ 500 CONFIG_WRITE_FAILED
且内存不变；非法值 422。
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcrew_server.config import load_config
from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.db.database import Database
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.runtime import RuntimeState
from agentcrew_server.settings import SettingsService

TOKEN = "tok-settings-1234567890"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _make_settings(tmp_path: Path, env: dict | None = None,
                   file_obj: dict | None = None):
    if file_obj is not None:
        (tmp_path / "config.json").write_text(
            json.dumps(file_obj), encoding="utf-8")
    config = load_config(tmp_path, env if env is not None else {})
    db = Database(tmp_path / "t.db")
    from agentcrew_server.db.migrations import run_migrations
    run_migrations(db.write_conn, tmp_path / "backups")
    channel = WriteChannel(db.write_conn)
    service = SettingsService(config, tmp_path, channel,
                              tmp_path / "chain-head.txt",
                              env=env if env is not None else None)
    runtime = RuntimeState(
        log=__import__("logging").getLogger("t.settings"), data_dir=tmp_path,
        db=db, token=TOKEN, settings=service,
    )
    from agentcrew_server.api.app import create_app
    return service, db, channel, create_app(runtime)


def _audit_count(db) -> int:
    return db.read_conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]


def test_get_masks_api_key(tmp_path):
    service, db, channel, app = _make_settings(tmp_path, file_obj={
        "models": {"main": {"api_key": "sk-secret-9876", "model": "glm-4.7"}},
        "config_version": 3})
    with TestClient(app) as c:
        view = c.get("/api/settings", headers=AUTH).json()["data"]
    assert view["config_version"] == "3"
    slot = view["models"]["main"]
    assert slot["api_key_configured"] is True
    assert slot["api_key_hint"] == "9876"
    assert "sk-secret" not in json.dumps(view)  # 全文无完整 key
    assert slot["effective_source"] == "file"
    assert slot["model"] == "glm-4.7"
    channel.close(); db.close()


def test_patch_bumps_version_effective_and_audits(tmp_path):
    service, db, channel, app = _make_settings(tmp_path)
    with TestClient(app) as c:
        before = _audit_count(db)
        resp = c.patch("/api/settings", headers=AUTH,
                       json={"gates": {"max_steps": 12}})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ignored_fields"] == []
        assert data["settings"]["gates"]["max_steps"] == 12
        assert data["settings"]["config_version"] == "1"  # 0 → 1
        view = c.get("/api/settings", headers=AUTH).json()["data"]
        assert view["gates"]["max_steps"] == 12
        assert service.config.values["gates"]["max_steps"] == 12  # 内存已切换
        # 文件真实落盘且原子（无 .tmp 残留）
        on_disk = json.loads((tmp_path / "config.json").read_text())
        assert on_disk["gates"]["max_steps"] == 12
        assert not (tmp_path / "config.json.tmp").exists()
        assert _audit_count(db) == before + 1
        action, detail = db.read_conn.execute(
            "SELECT action, detail FROM audit_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        assert action == "settings.updated"
        assert json.loads(detail)["fields"] == ["gates.max_steps"]
        # 再改一次：版本递增
        resp2 = c.patch("/api/settings", headers=AUTH,
                        json={"gates": {"max_steps": 13}})
        assert resp2.json()["data"]["settings"]["config_version"] == "2"
        assert verify_with_anchor(db.write_conn,
                                  tmp_path / "chain-head.txt").ok
    channel.close(); db.close()


def test_patch_env_covered_field_ignored(tmp_path):
    env = {"AGENTCREW_GATES__MAX_STEPS": "7"}
    service, db, channel, app = _make_settings(tmp_path, env=env)
    with TestClient(app) as c:
        # GET：env 生效 + 来源 env
        view = c.get("/api/settings", headers=AUTH).json()["data"]
        assert view["gates"]["max_steps"] == 7
        assert view["models"]["main"]["effective_source"] == "file"
        # PATCH 该字段：200 + ignored_fields；文件已存但生效值仍是 env 的
        resp = c.patch("/api/settings", headers=AUTH,
                       json={"gates": {"max_steps": 99}})
        assert resp.status_code == 200
        assert resp.json()["data"]["ignored_fields"] == ["gates.max_steps"]
        assert resp.json()["data"]["settings"]["gates"]["max_steps"] == 7
        on_disk = json.loads((tmp_path / "config.json").read_text())
        assert on_disk["gates"]["max_steps"] == 99  # 文件已存（非生效值）
    channel.close(); db.close()


def test_env_model_slot_effective_source(tmp_path):
    env = {"AGENTCREW_MAIN__API_KEY": "sk-from-env-abcd"}
    service, db, channel, app = _make_settings(tmp_path, env=env)
    with TestClient(app) as c:
        view = c.get("/api/settings", headers=AUTH).json()["data"]
        assert view["models"]["main"]["effective_source"] == "env"
        assert view["models"]["main"]["api_key_hint"] == "abcd"
        assert view["models"]["aux"]["effective_source"] == "file"
    channel.close(); db.close()


def test_api_key_patch_and_clear(tmp_path):
    service, db, channel, app = _make_settings(tmp_path, file_obj={
        "models": {"main": {"api_key": "sk-keep-me-7777"}}})
    with TestClient(app) as c:
        # 缺省 = 不变：只改 model，key 保留
        c.patch("/api/settings", headers=AUTH,
                json={"models": {"main": {"model": "glm-4.7"}}})
        assert "sk-keep-me-7777" in (tmp_path / "config.json").read_text()
        # PATCH 换 key：生效（内存/文件），响应只回尾 4 位
        resp = c.patch("/api/settings", headers=AUTH,
                       json={"models": {"main": {"api_key": "sk-new-key-2222"}}})
        assert resp.json()["data"]["settings"]["models"]["main"]["api_key_hint"] == "2222"
        assert service.config.api_keys() == ["sk-new-key-2222", ""]
        assert "sk-new-key-2222" in (tmp_path / "config.json").read_text()
        # api_key_clear 真实清槽
        resp = c.patch("/api/settings", headers=AUTH,
                       json={"api_key_clear": ["main"]})
        slot = resp.json()["data"]["settings"]["models"]["main"]
        assert slot["api_key_configured"] is False and slot["api_key_hint"] == ""
        assert service.config.api_keys() == ["", ""]
        assert "sk-new-key-2222" not in (tmp_path / "config.json").read_text()
        # 审计 detail 绝不含 key 值
        for (_, detail) in db.read_conn.execute(
                "SELECT action, detail FROM audit_log").fetchall():
            assert "sk-" not in detail
    channel.close(); db.close()


def test_write_failure_500_memory_unchanged(tmp_path):
    service, db, channel, app = _make_settings(tmp_path, file_obj={
        "gates": {"max_steps": 20}})
    with TestClient(app) as c:
        # 目录只读 → 原子写失败（真实 OSError，非模拟）
        os.chmod(tmp_path, stat.S_IREAD | stat.S_IEXEC)
        try:
            resp = c.patch("/api/settings", headers=AUTH,
                           json={"gates": {"max_steps": 40}})
            assert resp.status_code == 500
            assert resp.json()["error"]["code"] == "CONFIG_WRITE_FAILED"
            # 内存不变
            assert service.config.values["gates"]["max_steps"] == 20
            view = c.get("/api/settings", headers=AUTH).json()["data"]
            assert view["gates"]["max_steps"] == 20
            assert view["config_version"] == "0"
        finally:
            os.chmod(tmp_path, stat.S_IRWXU)
        # 恢复后可写
        resp = c.patch("/api/settings", headers=AUTH,
                       json={"gates": {"max_steps": 40}})
        assert resp.status_code == 200
    channel.close(); db.close()


def test_patch_invalid_values_422(tmp_path):
    service, db, channel, app = _make_settings(tmp_path)
    with TestClient(app) as c:
        for bad in ({"gates": {"max_steps": 0}},
                    {"gates": {"bogus": 1}},
                    {"unknown_section": {}},
                    {"api_key_clear": ["main", "bogus"]}):
            resp = c.patch("/api/settings", headers=AUTH, json=bad)
            assert resp.status_code == 422, bad
            assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        # 文件与内存均未动
        assert not (tmp_path / "config.json").exists()
        assert service.config.values["gates"]["max_steps"] == 40  # 默认值
    channel.close(); db.close()
