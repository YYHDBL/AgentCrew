"""M1-10：真实当前时间、历史自有数据、启动治理及实际 HTTP 验收。"""

import argparse
import asyncio
import hashlib
import json
import shutil
import signal
import sys
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_core.memory import render_entries, transform
from agentcrew_server.config import load_config
from agentcrew_server.db.audit import snapshot_chain_head, verify_with_anchor
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.memory.skills import MemorySkills
from agentcrew_server.memory.store import MemoryIdentity, MemoryStore
from agentcrew_server.settings import SettingsService
from verify_review_controls import files
from verify_summaries import query, start, until


async def owned_data(root, *, restore=False):
    db = Database(root / "agentcrew.db")
    run_migrations(db.write_conn, root / "backups")
    channel = WriteChannel(db.write_conn)
    events = EventStore(channel)
    settings = SettingsService(load_config(root, {}), root, channel, root / "chain-head.txt")
    store = MemoryStore(db, events, root, settings)
    identity = MemoryIdentity("default", "skill-validator")
    try:
        if restore:
            value = await store.read(identity, "user", "owner")
            entry = next(entry for entry in value["entries"] if entry["text"] == "归档恢复偏好：汇报使用简洁中文。")
            result = await store.change(identity, "user", "owner", change_id="curator-real-restore", expected_revision=value["revision"],
                basis="人类恢复完整的自有历史偏好", operations=[{"action": "restore", "entry_hash": entry["entry_hash"]}])
            assert "error" not in result
            return result
        soul = await store.read(identity, "soul", "skill-validator")
        if not soul["entries"]:
            configured = await store.change(identity, "soul", "skill-validator", change_id="curator-acceptance-soul", expected_revision=soul["revision"],
                basis="所有者配置真实治理验收员工岗位", operations=[{"action": "add", "text": "负责核查记忆治理与原始材料，遵守当前授权，依据真实工具与模型结果说明。"}])
            assert "error" not in configured
        value = store._load("user", "owner")
        entries = value["metadata"]["entries"]
        current = datetime.now(timezone.utc)
        for text, age in (("陈旧核验偏好：保留来源信息。", 14.1), ("归档恢复偏好：汇报使用简洁中文。", 30.1), ("固定核验偏好：保留核验编号。", 45)):
            entries = transform(entries, [{"action": "add", "text": text}], identity.source("curator-history-input"),
                "自有治理数据的历史时间输入", (current - timedelta(days=age)).isoformat())
            assert isinstance(entries, list), entries
        plan = store._plan(identity, value, entries, render_entries(entries), "历史使用时间为治理验收输入，使用真实当前时间判定",
            current.isoformat(), "curator-history-input", None, "create", [])
        assert "error" not in await store._prepare_and_finish("curator-history-input", "owned-history-input", plan)
        value = await store.read(identity, "user", "owner")
        fixed = next(entry for entry in value["entries"] if entry["text"].startswith("固定核验偏好"))
        assert "error" not in await store.change(identity, "user", "owner", change_id="curator-real-pin", expected_revision=value["revision"],
            basis="人类固定历史核验偏好", operations=[{"action": "pin", "entry_hash": fixed["entry_hash"]}])
        skills = MemorySkills(store)
        for index, name in enumerate(("治理引用样本", "治理归档样本")):
            result = await skills.change(identity, name, action="create", change_id=f"curator-skill-{index}", expected_revision=0,
                basis="人类创建的自有历史治理材料", description="核查材料来源与编号", text=f"{name}\n读取材料，核查来源，保留编号。",
                files={"references/source.md": "完整自有支撑材料与原始来源"})
            assert "error" not in result
            skill_id = db.read_conn.execute("SELECT id FROM memory_skills WHERE name=?", (name,)).fetchone()[0]
            before = store._load("skill", skill_id)
            entries = deepcopy(before["metadata"]["entries"])
            entries[0]["created_at"] = (current - timedelta(days=31)).isoformat()
            change_id = f"curator-skill-history-{index}"
            plan = store._plan(identity, before, entries, render_entries(entries), "自有技能的历史时间输入",
                current.isoformat(), change_id, None, "update", [])
            assert "error" not in await store._prepare_and_finish(change_id, change_id, plan)
            if index == 0:
                await channel.execute(lambda conn: conn.execute("INSERT INTO skill_references VALUES('acceptance','curator-owned-reference',?,1,?)", (skill_id, current.isoformat())))
        return {"time_basis": "真实 UTC 当前时间", "historical_values": "自有条目与技能的治理输入，未改变系统时钟"}
    finally:
        await channel.execute(lambda conn: snapshot_chain_head(conn, root / "chain-head.txt"))
        channel.close()
        db.close()


async def verify(seed, root, output):
    shutil.copytree(seed, root, ignore=shutil.ignore_patterns("requests.jsonl", "streams.jsonl", "main-streams.jsonl", "service.log", "instance.lock"))
    material = await owned_data(root)
    before_calls = query(root, "SELECT COUNT(*) AS count FROM llm_calls")[0]["count"]
    before_files = files(root)
    token = uuid.uuid4().hex
    process, log, port = await start(root, token)
    exchanges, restored, recall, error = [], None, None, None
    models, after_govern_calls = None, None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30,
                headers={"Authorization": f"Bearer {token}"}) as client:
            startup = await until(root, "SELECT id,status FROM memory_jobs WHERE kind='curate' AND status='completed'")
            await until(root, "SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM memory_jobs WHERE kind='curate' AND status IN ('queued','running'))")
            config = (await client.get("/api/settings")).json()["data"]
            models = {slot: {key: value for key, value in entry.items() if key not in {"api_key_hint"}} for slot, entry in config["models"].items()}
            body = {"workspace_id": "default", "agent_id": "skill-validator", "client_request_id": uuid.uuid4().hex}
            for _ in range(2):
                response = await client.post("/api/memory/curate/run", json=body)
                exchanges.append({"method": "POST", "path": "/api/memory/curate/run", "status": response.status_code, "body": body, "response": response.json()})
                assert response.status_code == 202
            ids = [row["response"]["data"]["id"] for row in exchanges]
            assert ids[0] == ids[1]
            await until(root, "SELECT id FROM memory_jobs WHERE id=? AND status='completed'", (ids[0],))
            response = await client.post("/api/memory/curate/run", json={**body, "agent_id": "default"})
            assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
            exchanges.append({"method": "POST", "path": "/api/memory/curate/run", "status": 409, "response": response.json()})
            assert (await client.post("/api/memory/curate/run", json=body, headers={"Authorization": "Bearer invalid"})).status_code == 401
            assert (await client.post("/api/memory/curate/run", json={**body, "workspace_id": ""})).status_code == 422
            for job in startup:
                response = await client.get(f"/api/memory/jobs/{job['id']}")
                assert response.status_code == 200 and response.json()["data"]["model"] is None
            after_govern_calls = query(root, "SELECT COUNT(*) AS count FROM llm_calls")[0]["count"]
            assert after_govern_calls == before_calls
        process.send_signal(signal.SIGTERM)
        assert await process.wait() == 0
        log.close()
        restored = await owned_data(root, restore=True)
        process, log, port = await start(root, token)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30, headers={"Authorization": f"Bearer {token}"}) as client:
            response = await client.post("/api/conversations", json={"agent_id": "skill-validator", "client_request_id": uuid.uuid4().hex,
                "instruction": "请根据当前 USER 记忆，用一句话说明归档恢复偏好的内容，直接文字回复。"})
            assert response.status_code == 201
            result = response.json()["data"]
            busy = await client.post("/api/memory/curate/run", json={**body, "client_request_id": uuid.uuid4().hex})
            assert busy.status_code == 409 and busy.json()["error"]["code"] == "SYSTEM_BUSY"
            terminal = await until(root, "SELECT status FROM task_runs WHERE id=? AND status IN ('completed','failed')", (result["task_run_id"],))
            assert terminal[0]["status"] == "completed"
            snapshot = root / "conversations" / result["conversation"]["id"] / "memory-snapshot.json"
            assert "归档恢复偏好" in json.loads(snapshot.read_text())["system_block"]
            reply = query(root, "SELECT payload FROM run_events WHERE task_run_id=? AND type='run.completed'", (result["task_run_id"],))[0]
            recall = {"conversation_id": result["conversation"]["id"], "task_run_id": result["task_run_id"],
                "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(), "actual_reply": json.loads(reply["payload"])["final_text"]}
    finally:
        exc = sys.exception()
        if exc is not None:
            error = f"{type(exc).__name__}: {exc}"
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
        exit_code = await asyncio.wait_for(process.wait(), 15)
        log.close()
        db = Database(root / "agentcrew.db")
        audit = verify_with_anchor(db.read_conn, root / "chain-head.txt")
        db.close()
        sqls = ["SELECT * FROM memory_governance", "SELECT * FROM memory_jobs WHERE kind='curate' ORDER BY created_at,id",
            "SELECT * FROM memory_ledger WHERE json_extract(source,'$.actor_type')='curator' ORDER BY id",
            "SELECT store_type,store_id,entry_id,state,hits,last_hit_at FROM memory_entries ORDER BY store_type,store_id,entry_id",
            "SELECT global_seq,type,payload FROM run_events WHERE type IN ('memory.curated','memory.archived') ORDER BY global_seq",
            "PRAGMA foreign_key_check"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"material": material, "models": models, "http": exchanges,
            "governance_llm_before": before_calls, "governance_llm_after": after_govern_calls,
            "restored": restored, "recall": recall, "files_before": before_files, "files_after": files(root),
            "queries": [{"sql": sql, "rows": query(root, sql)} for sql in sqls],
            "audit": {"ok": audit.ok, "checked_count": audit.checked_count}, "error": error, "service_exit": exit_code}, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-data-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.seed_data_dir.absolute(), args.data_dir.absolute(), args.output.absolute()))
