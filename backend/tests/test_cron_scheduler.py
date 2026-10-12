"""M3-04：自有时刻参数计算和真实秒级调度。"""

from datetime import datetime, timezone
import time
import uuid

import pytest

from agentcrew_core.cron.schedule import next_run_at
from test_cron_store import proposal
from test_governance_identity import server, request


def milliseconds(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def test_every_keeps_anchor_and_cron_uses_timezone():
    schedule = {"kind": "every", "every_ms": 1000, "tz": "UTC"}
    assert next_run_at(schedule, 5900, 1000) == 6000
    assert next_run_at(schedule, 500, 1000) == 2000
    current = milliseconds("2026-10-05T00:00:00+00:00")
    assert next_run_at({"kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Shanghai"}, current, current) == milliseconds("2026-10-05T01:00:00+00:00")


def test_dst_skips_missing_wall_time_and_uses_first_fold():
    current = milliseconds("2026-03-08T05:00:00+00:00")
    assert next_run_at({"kind": "cron", "expr": "30 2 * * *", "tz": "America/New_York"}, current, current) == milliseconds("2026-03-09T06:30:00+00:00")
    start = milliseconds("2026-11-01T04:00:00+00:00")
    schedule = {"kind": "cron", "expr": "30 1 * * *", "tz": "America/New_York"}
    first = next_run_at(schedule, start, start)
    assert first == milliseconds("2026-11-01T05:30:00+00:00")
    assert next_run_at(schedule, first, start) == milliseconds("2026-11-02T06:30:00+00:00")


def test_large_missed_range_has_count_and_next_without_execution():
    from agentcrew_core.cron.schedule import missed_range
    schedule = {"kind": "every", "every_ms": 1000, "tz": "UTC"}
    assert missed_range(schedule, 1000, 1000100, 0) == {"count": 1000, "through": 1000000, "next": 1001000}
    begin = milliseconds("2026-10-04T00:00:00+00:00")
    end = begin + 86400000 - 1
    cron = {"kind": "cron", "expr": "* * * * * *", "tz": "UTC"}
    result = missed_range(cron, begin, end, begin)
    assert result == {"count": 86400, "through": end - 999, "next": end + 1}


def test_real_at_every_and_second_cron_register_due_once(server):
    now = time.time_ns() // 1_000_000
    configurations = [{"kind": "at", "at_ms": now + 1500, "tz": "UTC"},
                      {"kind": "every", "every_ms": 1000, "tz": "Asia/Shanghai"},
                      {"kind": "cron", "expr": "* * * * * *", "tz": "UTC"}]
    for schedule in configurations:
        response = request(server, "POST", "/api/cron/jobs", body=proposal(schedule))
        assert response.status_code == 201, response.text
        job = response.json()["data"]
        deadline = time.monotonic() + 8
        history = []
        while time.monotonic() < deadline:
            history = request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs?limit=200').json()["data"]["items"]
            if history:
                break
            time.sleep(0.02)
        assert history, job
        assert history[0]["trigger"] == "scheduled"
        assert history[0]["status"] == "fired"
        if schedule["kind"] != "at":
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                history = request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs?limit=200').json()["data"]["items"]
                if any(row["status"] == "skipped" for row in history):
                    break
                time.sleep(0.02)
            assert any(row["status"] == "skipped" for row in history)
            assert len({row["scheduled_at"] for row in history}) == len(history)
        current = request(server, "GET", f'/api/cron/jobs/{job["id"]}').json()["data"]
        assert request(server, "PATCH", f'/api/cron/jobs/{job["id"]}', body={"change_id": job["id"] + "-disable", "expected_revision": current["revision"], "enabled": False}).status_code == 200


def test_modified_expired_at_is_missed_and_old_timer_is_fenced(server):
    stamp = time.time_ns() // 1_000_000
    job = request(server, "POST", "/api/cron/jobs", body=proposal({"kind": "at", "at_ms": stamp + 1200, "tz": "UTC"})).json()["data"]
    time.sleep(0.3)
    changed = request(server, "PATCH", f'/api/cron/jobs/{job["id"]}', body={"change_id": uuid.uuid4().hex,
        "expected_revision": 1, "schedule": {"kind": "at", "at_ms": time.time_ns() // 1_000_000 - 100, "tz": "UTC"}})
    assert changed.status_code == 200, changed.text
    time.sleep(1.5)
    history = request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs').json()["data"]["items"]
    assert len(history) == 1
    assert history[0]["status"] == "missed"
    assert history[0]["revision"] == 2
    assert history[0]["task_run_id"] is None


def test_disabled_plan_has_no_future_occurrence(server):
    job = request(server, "POST", "/api/cron/jobs", body=proposal({"kind": "every", "every_ms": 1000, "tz": "UTC"})).json()["data"]
    assert request(server, "PATCH", f'/api/cron/jobs/{job["id"]}', body={"change_id": uuid.uuid4().hex, "expected_revision": 1, "enabled": False}).status_code == 200
    time.sleep(1.5)
    assert request(server, "GET", f'/api/cron/jobs/{job["id"]}/runs').json()["data"]["items"] == []
