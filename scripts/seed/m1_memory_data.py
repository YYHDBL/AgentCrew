"""生成 M1 贯穿验收的真实文件与可归档自有记忆材料。"""

import argparse
import asyncio
import csv
import hashlib
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.config import load_config
from agentcrew_server.db.audit import snapshot_chain_head
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.memory.store import MemoryIdentity, MemoryStore
from agentcrew_server.settings import SettingsService


async def compound(root, conversation, reset):
    db = Database(root / "agentcrew.db")
    run_migrations(db.write_conn, root / "backups")
    row = db.read_conn.execute("SELECT workspace_id,agent_id FROM conversations WHERE id=?", (conversation,)).fetchone()
    if row is None:
        raise ValueError("贯穿材料必须使用真实已有会话")
    channel = WriteChannel(db.write_conn)
    settings = SettingsService(load_config(root, {}), root, channel, root / "chain-head.txt")
    store = MemoryStore(db, EventStore(channel), root, settings)
    identity = MemoryIdentity(row[0], row[1], conversation_id=conversation)
    try:
        current = await store.read(identity, "workspace", row[0])
        prefix = "贯穿核验临时材料"
        existing = [entry for entry in current["entries"] if entry["text"].startswith(prefix)]
        if existing and not reset:
            raise ValueError("贯穿材料已经存在；明确传入 --reset 才能恢复自有材料")
        if existing and (len(existing) != 60 or {entry["text"] for entry in existing}
                != {f"{prefix}{index:02d}：保留原始编号。" for index in range(60)}):
            raise ValueError("已有材料与生成的原始材料不一致，停止重置")
        change_id = uuid.uuid4().hex
        operations = [{"action": "restore", "entry_hash": entry["entry_hash"]}
            for entry in existing if entry["state"] == "archived"] if existing else [
                {"action": "add", "text": f"{prefix}{index:02d}：保留原始编号。"} for index in range(60)]
        if existing and any(entry["state"] != "archived" for entry in existing):
            raise ValueError("重置要求六十条原始材料全部处于归档状态")
        result = await store.change(identity, "workspace", row[0], change_id=change_id,
            expected_revision=current["revision"], basis="所有者生成的真实贯穿归档核验材料",
            operations=operations)
        if "error" in result:
            raise ValueError(f"生成贯穿记忆材料失败：{result}")
        value = await store.read(identity, "workspace", row[0])
        entries = [entry for entry in value["entries"] if entry["text"].startswith(prefix)]
        if len(entries) != 60:
            raise ValueError("贯穿记忆材料数量不正确")
        return {"conversation_id": conversation, "workspace_id": row[0], "agent_id": row[1],
            "change": result, "entries": [{key: entry[key] for key in ("entry_id", "entry_hash", "text")} for entry in entries],
            "memory_path": str(store.path("workspace", row[0])), "memory_sha256": hashlib.sha256(store.path("workspace", row[0]).read_bytes()).hexdigest()}
    finally:
        await channel.execute(lambda conn: snapshot_chain_head(conn, root / "chain-head.txt"))
        channel.close()
        db.close()


def office(root, reset):
    source = root / "office-materials"
    source.mkdir(parents=True, exist_ok=reset)
    for index in range(20):
        suffix = (".txt", ".md", ".csv", "")[index % 4]
        path = source / f"材料{index:02d}{suffix}"
        if suffix == ".csv":
            with path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.writer(output)
                writer.writerow(["编号", "来源", "数量"])
                writer.writerow([index, "M1 真实贯穿材料", index + 1])
        else:
            path.write_text(f"材料编号 {index:02d}\n来源：M1 真实贯穿材料\n需要保留原始编号和完整内容。\n", encoding="utf-8")
    return {"directory": str(source), "files": [{"path": str(path), "bytes": path.stat().st_size,
        "mtime_ns": str(path.stat().st_mtime_ns), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(source.iterdir())]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--conversation-id")
    parser.add_argument("--reset", action="store_true", help="恢复已归档的原始自有材料，或重新生成二十份本地材料")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.data_dir.absolute()
    result = asyncio.run(compound(root, args.conversation_id, args.reset)) if args.conversation_id else office(root, args.reset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
