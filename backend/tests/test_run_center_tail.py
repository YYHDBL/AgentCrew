"""运行中心按已提交水位读取真实历史之后的尾部。"""

import sqlite3

from test_run_center_api import executions, server
from test_governance_identity import request


def test_tail_starts_after_frozen_head_and_keeps_pagination(server, executions):
    with sqlite3.connect(server["root"] / "agentcrew.db") as connection:
        task = executions["task"]
        rows = connection.execute("SELECT seq,global_seq FROM run_events WHERE task_run_id=? ORDER BY seq", (task,)).fetchall()
    frozen = rows[9][1]
    assert len(rows) > 30
    through = rows[-1][1]
    expected = [row for row in rows if frozen < row[1] <= through]
    actual = []
    after = 0
    while True:
        response = request(server, "GET", f"/api/task-runs/{task}/events?limit=3&after_seq={after}&after_global_seq={frozen}&through_global_seq={through}")
        assert response.status_code == 200, response.text
        page = response.json()["data"]
        assert all(frozen < frame["global_seq"] <= through for frame in page["items"])
        actual.extend((frame["seq"], frame["global_seq"]) for frame in page["items"])
        assert page["at_global_seq"] == through
        if not page["has_more"]:
            break
        after = page["next_after_seq"]
    assert actual == expected


def test_tail_rejects_negative_watermark(server, executions):
    with sqlite3.connect(server["root"] / "agentcrew.db") as connection:
        task = executions["task"]
    response = request(server, "GET", f"/api/task-runs/{task}/events?limit=3&after_global_seq=-1")
    assert response.status_code == 422
