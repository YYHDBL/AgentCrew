"""EventStore 单测：追加/投影同步/重放一致/同事务回滚/seq 冲突（真实 SQLite）。"""

import asyncio
import sqlite3

import pytest

from agentcrew_core.events import RunEventType as T
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.projections import PROJECTION_TABLES, rebuild_projections
from agentcrew_server.db.write_channel import WriteChannel


def _setup(tmp_path, name="t.db"):
    db = Database(tmp_path / name)
    run_migrations(db.write_conn, tmp_path / "backups")
    db.write_conn.execute(
        "INSERT INTO conversations (id, workspace_id, agent_id, created_at, updated_at)"
        " VALUES ('conv-1', 'ws-1', 'agent-1', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
    )
    channel = WriteChannel(db.write_conn)
    store = EventStore(channel)
    return db, channel, store


async def _append_mini_run(store, run_id="run-1", conv="conv-1"):
    """一次完整小任务：排队→开始→step→llm→tool→完成（9 条事件）。"""
    seq_events = [
        (T.RUN_QUEUED, {"instruction": "读取 /tmp/a.md 并总结"}, 1),
        (T.RUN_STARTED, {"attempt_no": 1, "attempt_id": "att-1"}, 1),
        (T.STEP_STARTED, {"step_id": "step-1", "ordinal": 1, "model_slot": "main"}, 1),
        (T.LLM_REQUEST_STARTED, {"llm_call_id": "llm-1", "step_id": "step-1", "model": "glm-4.6"}, 1),
        (T.TOOL_PREPARED, {"call_id": "call-1", "tool_name": "read_file",
                           "side_effect_class": "verifiable", "input_hash": "h1",
                           "risk_level": "low", "input": {"path": "/tmp/a.md"},
                           "step_id": "step-1"}, 1),
        (T.TOOL_DISPATCHED, {"call_id": "call-1"}, 1),
        (T.TOOL_COMPLETED, {"call_id": "call-1", "output_summary": "文件内容…"}, 1),
        (T.LLM_REQUEST_DONE, {"llm_call_id": "llm-1", "prompt_tokens": 10,
                              "completion_tokens": 20, "latency_ms": 100}, 1),
        (T.STEP_COMPLETED, {"step_id": "step-1", "input_tokens": 10,
                            "output_tokens": 20, "latency_ms": 150}, 1),
        (T.RUN_COMPLETED, {"final_text": "总结：这是一份测试文档"}, 1),
    ]
    envelopes = []
    for type, payload, attempt in seq_events:
        envelopes.append(await store.append(
            task_run_id=run_id, conversation_id=conv, type=type,
            payload=payload, attempt_no=attempt,
        ))
    return envelopes


def test_append_sequence_and_projections(tmp_path):
    db, channel, store = _setup(tmp_path)
    envelopes = asyncio.run(_append_mini_run(store))

    # global_seq 严格递增；任务内 seq 连续 1..10
    seqs = [e.global_seq for e in envelopes]
    assert seqs == sorted(set(seqs)) and len(seqs) == 10
    assert [e.seq for e in envelopes] == list(range(1, 11))

    r = db.read_conn
    assert r.execute("SELECT status FROM task_runs WHERE id='run-1'").fetchone()[0] == "completed"
    assert r.execute("SELECT count(*) FROM messages").fetchone()[0] == 2  # user + assistant
    roles = [row[0] for row in r.execute("SELECT role FROM messages ORDER BY created_at")]
    assert roles == ["user", "assistant"]
    step = r.execute("SELECT status, input_tokens, output_tokens FROM steps").fetchone()
    assert step == ("completed", 10, 20)
    llm = r.execute("SELECT prompt_tokens, completion_tokens FROM llm_calls").fetchone()
    assert llm == (10, 20)
    tool = r.execute("SELECT status, tool_name, side_effect_class FROM tool_calls").fetchone()
    assert tool == ("completed", "read_file", "verifiable")
    attempt = r.execute("SELECT kind, status, outcome FROM run_attempts").fetchone()
    assert attempt == ("initial", "completed", "completed")

    channel.close()
    db.close()


def test_replay_rebuilds_identical_projections(tmp_path):
    db, channel, store = _setup(tmp_path)
    asyncio.run(_append_mini_run(store))
    # 再来一个带产物与材料的任务，覆盖更多投影
    asyncio.run(store.append(
        task_run_id="run-1", conversation_id="conv-1",
        type=T.ARTIFACT_CREATED,
        payload={"artifact_id": "art-1", "path": "/tmp/out.csv", "name": "out.csv",
                 "ext": ".csv", "tool_call_id": None},
    ))
    asyncio.run(store.append(
        task_run_id="run-1", conversation_id="conv-1", type=T.ARTIFACT_READY,
        payload={"artifact_id": "art-1", "size_bytes": 128},
    ))
    asyncio.run(store.append(
        task_run_id="run-1", conversation_id="conv-1", type=T.MATERIALS_IMPORTED,
        payload={"files": [{"original_path": "/Users/x/a.pdf", "stored_name": "a.pdf",
                            "size_bytes": 5, "error": None}]},
    ))

    def dump():
        out = {}
        for table in PROJECTION_TABLES:
            cols = [c[1] for c in db.read_conn.execute(f"PRAGMA table_info({table})")]
            order = ", ".join(f'"{c}"' for c in cols)
            rows = db.read_conn.execute(
                f"SELECT {order} FROM {table} ORDER BY {order}"
            ).fetchall()
            out[table] = [tuple(row) for row in rows]
        return out

    before = dump()
    count = rebuild_projections(db.write_conn)
    assert count == 13  # 10 + 3
    assert dump() == before, "重放器重建结果与在线投影不一致"

    channel.close()
    db.close()


def test_projection_failure_rolls_back_event_too(tmp_path):
    db, channel, store = _setup(tmp_path)
    asyncio.run(_append_mini_run(store))
    count_before = db.read_conn.execute("SELECT count(*) FROM run_events").fetchone()[0]

    # 故障注入：ARTIFACT_CREATED 指向不存在的会话 → FK 违反 → 整个事务回滚
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(store.append(
            task_run_id="run-1", conversation_id="conv-不存在的会话",
            type=T.ARTIFACT_CREATED,
            payload={"artifact_id": "art-x", "path": "/tmp/x", "name": "x"},
        ))
    count_after = db.read_conn.execute("SELECT count(*) FROM run_events").fetchone()[0]
    assert count_after == count_before, "投影失败后事件不应入库（同事务）"
    assert db.read_conn.execute(
        "SELECT count(*) FROM artifacts WHERE id='art-x'"
    ).fetchone()[0] == 0

    channel.close()
    db.close()


def test_seq_conflict_raises_not_overwrites(tmp_path):
    db, channel, store = _setup(tmp_path)
    asyncio.run(_append_mini_run(store))
    # 直接 SQL 构造重复 (task_run_id, seq)：UNIQUE 兜底必须抛错而非覆盖
    with pytest.raises(sqlite3.IntegrityError):
        db.write_conn.execute(
            "INSERT INTO run_events (id, task_run_id, seq, conversation_id, type,"
            " payload, created_at) VALUES ('manual-1', 'run-1', 1, 'conv-1',"
            " 'run.queued', '{}', '2026-01-01T00:00:00')"
        )
    first = db.read_conn.execute(
        "SELECT id FROM run_events WHERE task_run_id='run-1' AND seq=1"
    ).fetchone()[0]
    assert first != "manual-1"

    channel.close()
    db.close()


def test_publisher_called_after_commit_in_order(tmp_path):
    db, channel, _ = _setup(tmp_path)
    published = []
    store = EventStore(channel, publisher=published.append)
    envelopes = asyncio.run(_append_mini_run(store))
    assert [p.global_seq for p in published] == [e.global_seq for e in envelopes]
    assert published[0].type == T.RUN_QUEUED

    channel.close()
    db.close()


def test_publisher_failure_does_not_duplicate_event(tmp_path):
    """外审回稿：发布回调抛 locked/busy 类异常不得触发写通道重试（会重复追加）。"""
    db, channel, _ = _setup(tmp_path)

    def bad_publisher(_event):
        raise sqlite3.OperationalError("database is locked")

    store = EventStore(channel, publisher=bad_publisher)
    asyncio.run(_append_mini_run(store))  # 全程发布失败，但追加必须每条恰好一次
    count = db.read_conn.execute("SELECT count(*) FROM run_events").fetchone()[0]
    assert count == 10
    types = [row[0] for row in db.read_conn.execute(
        "SELECT type FROM run_events ORDER BY global_seq")]
    assert types.count("run.queued") == 1

    channel.close()
    db.close()


def test_projection_unique_violation_rolls_back_event_only(tmp_path):
    """外审回稿：隔离"投影失败"（非事件行 FK 失败）——同事务回滚的直接证据。

    重复 call_id 的事件行本身合法（新 id/新 seq/真实会话），但投影 INSERT
    撞 tool_calls.call_id UNIQUE → 整个事务回滚 → 事件不得入库。
    """
    db, channel, store = _setup(tmp_path)
    asyncio.run(_append_mini_run(store))  # 建 run-1 父行（task_runs/messages 等）
    asyncio.run(store.append(
        task_run_id="run-1", conversation_id="conv-1", type=T.TOOL_PREPARED,
        payload={"call_id": "dup-call", "tool_name": "bash",
                 "side_effect_class": "outcome_unknown", "input_hash": "h",
                 "risk_level": "low", "input": {"cmd": "ls"}},
    ))
    count_ok = db.read_conn.execute("SELECT count(*) FROM run_events").fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(store.append(
            task_run_id="run-1", conversation_id="conv-1", type=T.TOOL_PREPARED,
            payload={"call_id": "dup-call", "tool_name": "bash",
                     "side_effect_class": "outcome_unknown", "input_hash": "h2",
                     "risk_level": "low", "input": {"cmd": "ls -l"}},
        ))
    count_bad = db.read_conn.execute("SELECT count(*) FROM run_events").fetchone()[0]
    assert count_bad == count_ok, "投影 UNIQUE 失败后事件不应入库"
    assert db.read_conn.execute(
        "SELECT count(*) FROM tool_calls WHERE call_id='dup-call'"
    ).fetchone()[0] == 1

    channel.close()
    db.close()


def test_concurrent_appends_across_two_runs(tmp_path):
    """外审回稿：并发追加的真实覆盖——global_seq 唯一连续、任务内 seq 连续、
    发布顺序 == global_seq 升序（单写 executor 串行的直接证据）。"""
    db, channel, _ = _setup(tmp_path)
    published = []
    store = EventStore(channel, publisher=published.append)
    # 先顺序建两个任务的 RUN_QUEUED（task_runs 父行），再并发灌事件
    for run_id in ("run-a", "run-b"):
        asyncio.run(store.append(
            task_run_id=run_id, conversation_id="conv-1", type=T.RUN_QUEUED,
            payload={"instruction": f"任务 {run_id}"},
        ))

    async def burst(run_id: str, n: int):
        for i in range(n):
            await store.append(
                task_run_id=run_id, conversation_id="conv-1",
                type=T.QUESTION_REQUESTED,  # 无投影副作用，隔离并发路径本身
                payload={"q": f"{run_id}#{i}"},
            )

    async def burst_all():
        await asyncio.gather(burst("run-a", 25), burst("run-b", 25))

    asyncio.run(burst_all())

    total = db.read_conn.execute("SELECT count(*) FROM run_events").fetchone()[0]
    assert total == 2 + 50
    gseqs = [row[0] for row in db.read_conn.execute(
        "SELECT global_seq FROM run_events ORDER BY global_seq")]
    assert gseqs == list(range(1, total + 1)), "global_seq 必须无洞连续"
    for run_id in ("run-a", "run-b"):
        seqs = [row[0] for row in db.read_conn.execute(
            "SELECT seq FROM run_events WHERE task_run_id=? ORDER BY seq", (run_id,))]
        assert seqs == list(range(1, len(seqs) + 1))
    pub_seqs = [p.global_seq for p in published]
    assert pub_seqs == sorted(pub_seqs) and len(pub_seqs) == total, \
        "发布顺序必须等于提交顺序（global_seq 严格递增）"

    channel.close()
    db.close()
