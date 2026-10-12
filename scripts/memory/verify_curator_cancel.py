"""实际治理准备文件变更期间发送前台指令，检查后续写入与成功水位。"""

import argparse
import asyncio
import json
import shutil
import signal
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_core.memory import render_entries, transform
from agentcrew_server.config import load_config
from agentcrew_server.db.audit import snapshot_chain_head, verify_with_anchor
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.memory.store import MemoryIdentity, MemoryStore
from agentcrew_server.settings import SettingsService
from verify_summaries import query, start, until


async def prepare(root):
    db = Database(root / "agentcrew.db")
    channel = WriteChannel(db.write_conn)
    settings = SettingsService(load_config(root, {}), root, channel, root / "chain-head.txt")
    store = MemoryStore(db, EventStore(channel), root, settings)
    identity = MemoryIdentity("default", "skill-validator")
    try:
        for kind, store_id in (("user", "owner"), ("workspace", "default")):
            before = store._load(kind, store_id)
            entries = before["metadata"]["entries"]
            for index in range(60):
                entries = transform(entries, [{"action": "add", "text": f"治理暂停{kind}材料{index:02d}。"}],
                    identity.source("curator-cancel-input"), "自有治理暂停核查数据",
                    (datetime.now(timezone.utc) - timedelta(days=31)).isoformat())
            change_id = f"curator-cancel-input-{kind}"
            plan = store._plan(identity, before, entries, render_entries(entries), "自有历史材料输入",
                datetime.now(timezone.utc).isoformat(), change_id, None, "create", [])
            assert len(render_entries(entries)) <= store.quota(kind)
            assert "error" not in await store._prepare_and_finish(change_id, change_id, plan)
    finally:
        await channel.execute(lambda conn: snapshot_chain_head(conn, root / "chain-head.txt"))
        channel.close()
        db.close()


async def verify(seed, root, output):
    shutil.copytree(seed, root, ignore=shutil.ignore_patterns("requests.jsonl", "streams.jsonl", "main-streams.jsonl", "service.log", "instance.lock"))
    await prepare(root)
    original_watermark = query(root, "SELECT last_curate_at FROM memory_governance WHERE workspace_id='default' AND agent_id='skill-validator'")[0]["last_curate_at"]
    token = uuid.uuid4().hex
    process, log, port = await start(root, token)
    result, error = None, None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30,
                headers={"Authorization": f"Bearer {token}"}) as client:
            response = await client.post("/api/memory/curate/run", json={"workspace_id": "default", "agent_id": "skill-validator", "client_request_id": uuid.uuid4().hex})
            assert response.status_code == 202
            job_id = response.json()["data"]["id"]
            prepared = await until(root, "SELECT change_id,status FROM memory_changes WHERE json_extract(plan,'$.identity.job_id')=? AND status='prepared'", (job_id,), interval=0.001)
            started = time.monotonic()
            new = await client.post("/api/conversations", json={"agent_id": "skill-validator", "client_request_id": uuid.uuid4().hex,
                "instruction": "新的前台任务：请只确认已经收到任务，直接文字回复。"})
            seconds = time.monotonic() - started
            assert new.status_code == 201 and seconds < 2
            state = query(root, "SELECT status,report FROM memory_jobs WHERE id=?", (job_id,))[0]
            assert state["status"] == "cancelled"
            changes = query(root, "SELECT change_id,status,store_type FROM memory_changes WHERE json_extract(plan,'$.identity.job_id')=?", (job_id,))
            assert changes and all(change["status"] == "committed" for change in changes)
            assert all(change["store_type"] == "user" for change in changes)
            remaining = query(root, "SELECT state,COUNT(*) AS count FROM memory_entries WHERE store_type='workspace' AND text LIKE '治理暂停workspace材料%' GROUP BY state")
            assert remaining == [{"state": "active", "count": 60}]
            current_watermark = query(root, "SELECT last_curate_at FROM memory_governance WHERE workspace_id='default' AND agent_id='skill-validator'")[0]["last_curate_at"]
            assert current_watermark == original_watermark
            task_id = new.json()["data"]["task_run_id"]
            assert (await until(root, "SELECT status FROM task_runs WHERE id=? AND status IN ('completed','failed')", (task_id,)))[0]["status"] == "completed"
            result = {"job_id": job_id, "observed_prepared": prepared, "foreground_seconds": seconds, "state": state,
                "changes": changes, "unmodified_workspace_entries": remaining, "watermark_before": original_watermark,
                "watermark_after": current_watermark, "new_task_run_id": task_id}
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
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"result": result, "error": error, "service_exit": exit_code,
            "audit": {"ok": audit.ok, "checked_count": audit.checked_count}}, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-data-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.seed_data_dir.absolute(), args.data_dir.absolute(), args.output.absolute()))
