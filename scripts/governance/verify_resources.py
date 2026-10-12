"""从实际M0/M1验收库建立独立副本，验证升级及种子数据完整性。"""

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.db.audit import verify_internal, verify_with_anchor
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.governance.resources import Resources
from agentcrew_server.governance.seed import seed
from agentcrew_server.memory.store import MemoryStore


async def verify(source, destination, output):
    assert not destination.exists(), "独立验收目录必须尚未存在"
    destination.mkdir(parents=True)
    reader = sqlite3.connect(f"file:{source / 'agentcrew.db'}?mode=ro", uri=True)
    reader.execute("VACUUM INTO ?", (str(destination / "agentcrew.db"),))
    reader.close()
    for directory in ("workspaces", "agents", "skills", "artifacts", "conversations", "archive", "memory-snapshots", "checkpoints"):
        if (source / directory).exists():
            shutil.copytree(source / directory, destination / directory)
    for filename in ("USER.md", "USER.meta.json", "chain-head.txt"):
        if (source / filename).exists():
            shutil.copy2(source / filename, destination / filename)
    db = Database(destination / "agentcrew.db")
    tables = ("conversations", "task_runs", "run_attempts", "run_events", "tool_calls", "messages", "task_materials",
              "artifacts", "audit_log", "agent_permission_rules", "memory_stores", "memory_ledger", "memory_skills", "memory_snapshots", "memory_jobs")
    before = {table: [tuple(row) for row in db.read_conn.execute(f"SELECT * FROM {table}")] for table in tables}
    files = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in destination.rglob("*") if p.is_file() and p.suffix in {".md", ".json"}}
    migration = run_migrations(db.write_conn, destination / "backups")
    channel = WriteChannel(db.write_conn)
    events = EventStore(channel)
    resources = Resources(db, events, destination)
    memory = MemoryStore(db, events, destination)
    try:
        await seed(resources, memory)
        await seed(resources, memory)
        assert run_migrations(db.write_conn, destination / "backups").status == "up_to_date"
        for table, old in before.items():
            assert [tuple(row) for row in db.read_conn.execute(f"SELECT * FROM {table} LIMIT ?", (len(old),))] == old, table
        assert files == {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in files}
        assert db.read_conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert verify_internal(db.read_conn).ok
        assert verify_with_anchor(db.read_conn, destination / "chain-head.txt").ok
        queries = ["SELECT conversation_id,workspace_id,agent_id,effective_user_id FROM governance_conversations ORDER BY conversation_id",
                   "SELECT resource_type,resource_id,grantee_type,grantee_id,revoked_at FROM grants ORDER BY id",
                   "SELECT global_seq,type FROM run_events WHERE type LIKE 'governance.%' ORDER BY global_seq",
                   "SELECT seq,action,hash FROM audit_log WHERE action LIKE 'governance.%' ORDER BY seq"]
        record = {"source_directory": source.name, "migration": migration.applied_versions,
            "history": [{"table": table, "before_count": len(rows), "after_count": db.read_conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], "preserved": True} for table, rows in before.items()],
            "files": [{"path": str(path.relative_to(destination)), "sha256": digest, "mtime_ns": mtime} for path, (digest, mtime) in sorted(files.items())],
            "queries": [{"sql": query, "rows": [dict(row) for row in db.read_conn.execute(query)]} for query in queries],
            "foreign_keys": True, "internal_audit": True, "anchor_verified": True}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"history_counts": {table: len(rows) for table, rows in before.items()}, "file_count": len(files), "migration": migration.applied_versions, "two_level_audit": True}))
    finally:
        channel.close()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(verify(arguments.source.resolve(), arguments.destination.resolve(), arguments.output.resolve()))
