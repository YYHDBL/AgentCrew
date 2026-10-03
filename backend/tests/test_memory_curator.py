"""真实历史时间、三库与 Skill 的确定性治理；不调用模型。"""

import asyncio
import json
import pytest
from datetime import datetime, timedelta, timezone

from agentcrew_core.memory import render_entries, transform
from agentcrew_core.memory.curation import curation_plan
from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.memory.skills import MemorySkills
from agentcrew_server.memory.search import MemorySearch
from agentcrew_server.memory.snapshots import MemorySnapshots
from agentcrew_server.memory.store import MemoryIdentity
from test_session_summaries import services, task

OWNER = MemoryIdentity("default", "default")


async def historical(store, store_type, store_id, texts):
    before = store._load(store_type, store_id)
    entries = []
    for text, days in texts:
        stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        entries = transform(entries, [{"action": "add", "text": text}], OWNER.source("history-input"),
            "自有治理数据的历史时间输入", stamp)
    plan = store._plan(OWNER, before, entries, render_entries(entries), "自有历史资料",
        datetime.now(timezone.utc).isoformat(), f"historical-{store_type}-{store_id}", None, "create", [])
    return await store._prepare_and_finish(f"historical-{store_type}-{store_id}", f"history-{store_type}-{store_id}", plan)


def test_fourteen_thirty_days_pinned_usage_archive_and_restore(services):
    store, sessions, jobs = services
    async def check():
        conversation, task_id, _event = await task(store, sessions)
        await historical(store, "user", "owner", [("新偏好", 13), ("陈旧偏好", 14.1), ("归档偏好", 30.1), ("固定偏好", 45)])
        await historical(store, "soul", "default", [("我负责核验自有材料。", 0)])
        snapshots = MemorySnapshots(store)
        await snapshots.ensure(conversation, task_id)
        frozen = snapshots.path(conversation)
        frozen_before = (frozen.read_bytes(), frozen.stat().st_mtime_ns)
        initial = await store.read(OWNER, "user", "owner")
        pinned = initial["entries"][-1]["entry_hash"]
        await store.change(OWNER, "user", "owner", change_id="pin-old", expected_revision=initial["revision"],
            basis="人类明确固定", operations=[{"action": "pin", "entry_hash": pinned}])
        result = await jobs.curator.enqueue("default", "default", "manual-one")
        await jobs.curator.run(result)
        entries = (await store.read(OWNER, "user", "owner"))["entries"]
        assert [entry["state"] for entry in entries] == ["active", "stale", "archived", "pinned"]
        report = jobs.review.view(result)["report"]
        assert len(report["stale"]) == len(report["archived"]) == 1
        archive = store.data_dir / report["archived"][0]["archive_path"]
        assert (archive / "entry.md").read_text() == "归档偏好"
        assert json.loads((archive / "entry.meta.json").read_text())["original_path"] == "USER.md"
        search = MemorySearch(store)
        assert (await search.search(OWNER, "归档偏好"))["items"] == []
        assert (await search.search(OWNER, "归档偏好", archived=True))["items"][0]["state"] == "archived"
        row = (await store.read(OWNER, "user", "owner"))
        await store.change(OWNER, "user", "owner", change_id="restore-old", expected_revision=row["revision"],
            basis="人类恢复归档材料", operations=[{"action": "restore", "entry_hash": entries[2]["entry_hash"]}])
        assert (await store.read(OWNER, "user", "owner"))["entries"][2]["state"] == "active"
        next_session = await sessions.create_conversation(instruction="恢复归档后读取新会话记忆")
        fresh = await snapshots.ensure(next_session["conversation"]["id"], next_session["task_run_id"])
        assert "归档偏好" in fresh["stores"][0]["text"]
        assert (frozen.read_bytes(), frozen.stat().st_mtime_ns) == frozen_before
        assert store.db.read_conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
        assert store.db.read_conn.execute("SELECT COUNT(*) FROM memory_job_calls WHERE job_id=?", (result,)).fetchone()[0] == 0
        assert verify_with_anchor(store.db.read_conn, store.data_dir / "chain-head.txt").ok
    asyncio.run(check())


def test_startup_seven_days_idle_and_idempotent_manual_trigger(services):
    store, sessions, jobs = services
    async def check():
        await task(store, sessions)
        await historical(store, "workspace", "default", [("历史工作区资料", 35)])
        first = await jobs.curator.enqueue("default", "default", "manual-governance")
        assert await jobs.curator.enqueue("default", "default", "manual-governance") == first
        await jobs.curator.run(first)
        assert not jobs.curator.due("default", "default")
        old = (datetime.now(timezone.utc) - timedelta(days=7, seconds=1)).isoformat()
        await store.events.channel.execute(lambda conn: conn.execute("UPDATE memory_governance SET last_curate_at=?", (old,)))
        assert jobs.curator.due("default", "default")
        await sessions.send_instruction(store.db.read_conn.execute("SELECT id FROM conversations LIMIT 1").fetchone()[0], "新的前台指令")
        assert await jobs.curator.startup() == []
        assert store.db.read_conn.execute("SELECT COUNT(*) FROM memory_jobs WHERE kind='curate'").fetchone()[0] == 1
    asyncio.run(check())


def test_actual_skill_reference_exemption_archive_support_materials(services):
    store, sessions, jobs = services
    async def check():
        await task(store, sessions)
        skills = MemorySkills(store)
        for index, name in enumerate(("引用技能", "闲置技能")):
            created = await skills.change(OWNER, name, action="create", change_id=f"create-skill-{index}", expected_revision=0,
                basis="人类创建的自有技能资料", description="材料核验步骤", text=f"{name}\n核验来源与编号。",
                files={"references/source.md": "完整支撑材料"})
            assert "error" not in created
        ids = [row[0] for row in store.db.read_conn.execute("SELECT id FROM memory_skills ORDER BY name")]
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        await store.events.channel.execute(lambda conn: conn.executemany("INSERT INTO memory_usage VALUES(?,1,?)",
            [(row[0], old) for row in store.db.read_conn.execute("SELECT entry_id FROM memory_entries WHERE store_type='skill'").fetchall()]))
        referenced = store.db.read_conn.execute("SELECT id FROM memory_skills WHERE name='引用技能'").fetchone()[0]
        await store.events.channel.execute(lambda conn: conn.execute("INSERT INTO skill_references VALUES('acceptance','real-owned-reference',?,1,?)", (referenced, old)))
        job = await jobs.curator.enqueue("default", "default", "skill-governance")
        await jobs.curator.run(job)
        report = jobs.review.view(job)["report"]
        assert any(row["reason"] == "active_skill_reference" for row in report["exempt"])
        assert len(report["archived"]) == 1
        archive = store.data_dir / report["archived"][0]["archive_path"]
        assert (archive / "references/source.md").read_text() == "完整支撑材料"
        assert json.loads((archive / "entry.meta.json").read_text())["original_files"]["references/source.md"]
        assert len((await skills.index(OWNER))["items"]) == 1
        assert store.db.read_conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
    asyncio.run(check())


def test_real_usage_protects_old_entries_and_quota_preserves_pinned(services):
    store, sessions, jobs = services
    async def check():
        await task(store, sessions)
        await historical(store, "user", "owner", [("低命中工作习惯", 0), ("高命中工作习惯", 35), ("固定工作习惯", 0)])
        initial = await store.read(OWNER, "user", "owner")
        high = initial["entries"][1]
        await store.events.channel.execute(lambda conn: conn.execute("INSERT INTO memory_usage VALUES(?,12,?)",
            (high["entry_id"], datetime.now(timezone.utc).isoformat())))
        await store.change(OWNER, "user", "owner", change_id="quota-pin", expected_revision=initial["revision"],
            basis="人类固定工作习惯", operations=[{"action": "pin", "entry_hash": initial["entries"][2]["entry_hash"]}])
        await jobs.settings.patch({"memory": {"user_quota": 24}})
        job = await jobs.curator.enqueue("default", "default", "quota-pass")
        await jobs.curator.run(job)
        entries = (await store.read(OWNER, "user", "owner"))["entries"]
        assert [entry["state"] for entry in entries] == ["archived", "active", "pinned"]
        assert entries[1]["hits"] == 12
        before = (store.path("user", "owner").read_bytes(), store.path("user", "owner").stat().st_mtime_ns)
        success = store.db.read_conn.execute("SELECT last_curate_at FROM memory_governance").fetchone()[0]
        await jobs.settings.patch({"memory": {"user_quota": 1}})
        failed = await jobs.curator.enqueue("default", "default", "quota-protected")
        with pytest.raises(ValueError, match="固定条目"):
            await jobs.curator.run(failed)
        assert jobs.get(failed)["status"] == "failed"
        assert (store.path("user", "owner").read_bytes(), store.path("user", "owner").stat().st_mtime_ns) == before
        assert store.db.read_conn.execute("SELECT last_curate_at FROM memory_governance").fetchone()[0] == success
        assert store.db.read_conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
    asyncio.run(check())


@pytest.mark.parametrize("days,expected", [(13.999, None), (14, "stale"), (29.999, "stale"), (30, "archive")])
def test_actual_utc_exact_boundaries(days, expected):
    current = datetime.now(timezone.utc)
    entries = transform([], [{"action": "add", "text": "确定性历史时间边界"}], OWNER.source("boundary-input"),
        "自有时间边界数据", (current - timedelta(days=days)).isoformat())
    actions, exemptions = curation_plan(entries, now=current, quota=1400)
    assert ([row["action"] for row in actions] if actions else []) == ([expected] if expected else [])
    assert exemptions == []
