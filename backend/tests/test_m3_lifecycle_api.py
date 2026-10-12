"""M3-14：通知、桌面偏好、电源事件与退出影响的真实 HTTP 回归。"""

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from test_governance_identity import server, request, demo  # noqa: F401


def proposal(schedule, name, **changes):
    return {"change_id": uuid.uuid4().hex, "workspace_id": "office", "agent_id": "xiaowen",
            "name": name, "schedule": schedule,
            "target": {"instruction": "只用一句简短文字确认收到这条测试指令。", "execution_mode": "new_conversation", "conversation_id": None},
            "pre_authorized": [], **changes}


def sql(server, query, values=()):
    with sqlite3.connect(Path(server["root"]) / "agentcrew.db") as conn:
        return conn.execute(query, values).fetchall()


def wait_for(predicate, timeout=10, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def past_at_job(server, name, milliseconds_past=60000):
    response = request(server, "POST", "/api/cron/jobs", body=proposal(
        {"kind": "at", "at_ms": time.time_ns() // 1_000_000 - milliseconds_past, "tz": "Asia/Shanghai"}, name))
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_desktop_preferences_lifecycle(server):  # noqa: F811
    default = request(server, "GET", "/api/settings/desktop")
    assert default.status_code == 200, default.text
    assert default.json()["data"] == {"close_to_quit": False, "prevent_sleep": True}
    member = demo(server, "lilei")
    assert request(server, "GET", "/api/settings/desktop", identity=member).status_code == 200
    denied = request(server, "PATCH", "/api/settings/desktop", identity=member,
                     body={"close_to_quit": True, "prevent_sleep": False})
    assert denied.status_code == 403
    assert request(server, "PATCH", "/api/settings/desktop", body={"close_to_quit": True}).status_code == 422
    assert request(server, "PATCH", "/api/settings/desktop", body={"close_to_quit": True, "prevent_sleep": False, "extra": 1}).status_code == 422
    assert request(server, "PATCH", "/api/settings/desktop", body={"close_to_quit": 1, "prevent_sleep": False}).status_code == 422
    saved = request(server, "PATCH", "/api/settings/desktop", body={"close_to_quit": True, "prevent_sleep": False})
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"] == {"close_to_quit": True, "prevent_sleep": False}
    row = sql(server, "SELECT close_to_quit,prevent_sleep FROM desktop_preferences WHERE id=1")
    assert row == [(1, 0)]
    audit = sql(server, "SELECT action,resource_id FROM audit_log WHERE action='settings.desktop_updated' ORDER BY seq DESC LIMIT 1")
    assert audit == [("settings.desktop_updated", "desktop")]
    restored = request(server, "PATCH", "/api/settings/desktop", body={"close_to_quit": False, "prevent_sleep": True})
    assert restored.json()["data"] == {"close_to_quit": False, "prevent_sleep": True}


def test_notifications_scoping_read_and_presented_claim(server):  # noqa: F811
    first = past_at_job(server, "M3-14 通知错过一")
    second = past_at_job(server, "M3-14 通知错过二")
    listed = None

    def refresh():
        nonlocal listed
        response = request(server, "GET", "/api/notifications")
        assert response.status_code == 200, response.text
        listed = response.json()["data"]
        return len(listed["items"]) >= 2

    assert wait_for(refresh), "错过计划没有产生持久通知"
    items = listed["items"]
    assert items[0]["global_seq"] >= items[1]["global_seq"]
    by_job = {item["job_id"]: item for item in items}
    for job in (first, second):
        entry = by_job[job["id"]]
        assert entry["kind"] == "cron.missed"
        assert entry["severity"] == "warning"
        assert entry["task_run_id"] is None
        assert entry["read_at"] is None and entry["presented_at"] is None
        assert entry["message"].startswith("M3-14 通知错过")
    assert listed["unread_count"] >= 2

    member = demo(server, "lilei")
    scoped = request(server, "GET", "/api/notifications", identity=member).json()["data"]
    assert scoped["items"] == [] and scoped["unread_count"] == 0
    forbidden = request(server, "PATCH", f"/api/notifications/{items[0]['id']}", identity=member, body={"action": "read"})
    assert forbidden.status_code == 403

    single = request(server, "GET", "/api/notifications?limit=1").json()["data"]
    assert len(single["items"]) == 1 and single["next_after"] is not None
    second_page = request(server, "GET", f"/api/notifications?limit=1&after={single['next_after']}").json()["data"]
    assert [item["id"] for item in second_page["items"]] == [items[1]["id"]]
    assert request(server, "GET", "/api/notifications?after=" + "0" * 64 + ":" + items[1]["id"]).status_code == 422
    assert request(server, "PATCH", f"/api/notifications/{items[0]['id']}", body={"action": "reopen"}).status_code == 422
    assert request(server, "PATCH", "/api/notifications/missing-id", body={"action": "read"}).status_code == 404

    target = items[0]
    read = request(server, "PATCH", f"/api/notifications/{target['id']}", body={"action": "read"})
    assert read.status_code == 200, read.text
    assert read.json()["data"]["notification"]["read_at"] is not None
    assert read.json()["data"]["claimed"] is True
    replay_read = request(server, "PATCH", f"/api/notifications/{target['id']}", body={"action": "read"}).json()["data"]
    assert replay_read["claimed"] is False and replay_read["notification"]["read_at"] == read.json()["data"]["notification"]["read_at"]

    presented = request(server, "PATCH", f"/api/notifications/{target['id']}", body={"action": "presented"}).json()["data"]
    assert presented["claimed"] is True and presented["notification"]["presented_at"] is not None
    replay = request(server, "PATCH", f"/api/notifications/{target['id']}", body={"action": "presented"}).json()["data"]
    assert replay["claimed"] is False and replay["notification"]["presented_at"] == presented["notification"]["presented_at"]

    after = request(server, "GET", "/api/notifications").json()["data"]
    assert after["unread_count"] == listed["unread_count"] - 1
    updated = sql(server, "SELECT count(*) FROM run_events WHERE type='notification.updated' AND json_extract(payload,'$.notification_id')=?",
                  (target["id"],))[0][0]
    assert updated == 2  # read 与 presented 各一次；重放没有追加
    payload = json.loads(sql(server, "SELECT payload FROM run_events WHERE type='notification.updated' AND json_extract(payload,'$.notification_id')=? ORDER BY global_seq DESC LIMIT 1",
                             (target["id"],))[0][0])
    assert payload["read_at"] is not None and payload["presented_at"] is not None and payload["scope"]["workspace_id"] == "office"


def test_power_event_permission_validation_and_persistence(server):  # noqa: F811
    member = demo(server, "lilei")
    assert request(server, "POST", "/api/runtime/power-event", identity=member,
                   body={"event": "suspend", "observed_at": "2026-10-09T01:02:03+00:00"}).status_code == 403
    assert request(server, "POST", "/api/runtime/power-event", body={"event": "restart", "observed_at": "2026-10-09T01:02:03+00:00"}).status_code == 422
    assert request(server, "POST", "/api/runtime/power-event", body={"event": "suspend", "observed_at": "2026-10-09T01:02:03"}).status_code == 422
    assert request(server, "POST", "/api/runtime/power-event", body={"event": "suspend", "observed_at": "not-a-time", "extra": 1}).status_code == 422

    observed = datetime.now(timezone.utc).isoformat()
    for event in ("suspend", "resume"):
        response = request(server, "POST", "/api/runtime/power-event", body={"event": event, "observed_at": observed})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["global_seq"] >= 1
    rows = sql(server, "SELECT global_seq,payload FROM run_events WHERE type='runtime.power_changed' ORDER BY global_seq")
    assert len(rows) >= 2
    kinds = [json.loads(row[1])["event"] for row in rows[-2:]]
    assert kinds == ["suspend", "resume"]
    body = json.loads(rows[-1][1])
    assert body["observed_at"] == observed and body["source"] == "electron_power_monitor"
    assert body["scope"]["owner_id"] == "owner" and body["scope"]["workspace_id"] is None


def test_exit_impact_real_snapshot(server):  # noqa: F811
    upcoming = request(server, "POST", "/api/cron/jobs", body=proposal(
        {"kind": "at", "at_ms": time.time_ns() // 1_000_000 + 3600000, "tz": "Asia/Shanghai"}, "M3-14 未来计划"))
    assert upcoming.status_code == 201, upcoming.text
    member = demo(server, "lilei")
    assert request(server, "GET", "/api/runtime/exit-impact", identity=member).status_code == 403
    response = request(server, "GET", "/api/runtime/exit-impact")
    assert response.status_code == 200, response.text
    impact = response.json()["data"]
    for key in ("active_runs", "pending_verifications", "upcoming_jobs", "background_jobs", "at_global_seq", "observed_at"):
        assert key in impact
    head = sql(server, "SELECT coalesce(max(global_seq),0) FROM run_events")[0][0]
    assert impact["at_global_seq"] == head
    datetime.fromisoformat(impact["observed_at"])
    assert upcoming.json()["data"]["id"] in [job["id"] for job in impact["upcoming_jobs"]]
