"""对真实已完成工具响应执行受控 Skill 审查，验证当前提示与索引。"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path

from anyio import CancelScope

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.bus import EventBus
from agentcrew_server.config import load_config
from agentcrew_server.db.audit import snapshot_chain_head, verify_with_anchor
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.memory.jobs import MemoryJobs
from agentcrew_server.memory.store import MemoryStore
from agentcrew_server.settings import SettingsService
from observed_server import observe


async def verify(seed, root, output):
    shutil.copytree(seed, root, ignore=shutil.ignore_patterns("requests.jsonl", "streams.jsonl", "main-streams.jsonl", "service.log", "instance.lock"))
    os.environ["MEMORY_REQUEST_EVIDENCE"] = str(root / "requests.jsonl")
    sys.setprofile(observe)
    threading.setprofile(observe)
    db = Database(root / "agentcrew.db")
    run_migrations(db.write_conn, root / "backups")
    channel, bus = WriteChannel(db.write_conn), EventBus()
    events = EventStore(channel, publisher=bus.publish)
    settings = SettingsService(load_config(root, {}), root, channel, root / "chain-head.txt")
    store = MemoryStore(db, events, root, settings)
    jobs = MemoryJobs(store, bus, settings)
    try:
        source = db.read_conn.execute("SELECT e.global_seq,e.task_run_id FROM run_events e JOIN task_runs t ON t.id=e.task_run_id "
            "WHERE e.type='llm.request_done' AND json_array_length(json_extract(e.payload,'$.tool_uses'))>0 "
            "AND t.status='completed' ORDER BY e.global_seq DESC LIMIT 1").fetchone()
        job_id = await jobs.review.enqueue("skill_review", f"controlled-real:{uuid.uuid4().hex}", source[0], source[1])
        await jobs._run(job_id, CancelScope())
        job = jobs.review.view(job_id)
        assert job["status"] == "completed" and job["report"]["skill_index_items"] > 0
        await channel.execute(lambda conn: snapshot_chain_head(conn, root / "chain-head.txt"))
        audit = verify_with_anchor(db.read_conn, root / "chain-head.txt")
        assert audit.ok
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"source_global_seq": source[0], "source_task_run_id": source[1], "job": job,
            "audit": {"ok": audit.ok, "checked_count": audit.checked_count}}, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"job_id": job_id, "status": job["status"], "skill_index_items": job["report"]["skill_index_items"]}))
    finally:
        await jobs.shutdown()
        channel.close()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-data-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.seed_data_dir.absolute(), args.data_dir.absolute(), args.output.absolute()))
