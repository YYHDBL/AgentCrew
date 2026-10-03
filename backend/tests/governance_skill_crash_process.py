"""观察真实文件和SQLite写入边界，供不可变版本SIGKILL验收。"""

import asyncio
import os
import signal
import sys
import threading
from pathlib import Path

from agentcrew_server.db.audit import snapshot_chain_head
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.governance.resources import Resources
from agentcrew_server.governance.seed import seed
from agentcrew_server.governance.skills import SkillVersions
from agentcrew_server.memory.skills import MemorySkills
from agentcrew_server.memory.store import MemoryIdentity, MemoryStore


async def main():
    root, boundary = Path(sys.argv[1]), sys.argv[2]
    db = Database(root / "agentcrew.db")
    run_migrations(db.write_conn, root / "backups")
    channel = WriteChannel(db.write_conn)
    events = EventStore(channel)
    memory = MemoryStore(db, events, root)
    resources = Resources(db, events, root)
    await seed(resources, memory)
    versions = SkillVersions(resources, memory)
    await versions.register_history()
    memory.skill_versions = versions
    await channel.execute(lambda conn: snapshot_chain_head(conn, root / "chain-head.txt"))
    stopped = False
    def observe(frame, event, arg):
        nonlocal stopped
        if stopped or frame.f_globals.get("__name__") != "agentcrew_server.memory.store":
            return
        name = frame.f_code.co_name
        selected = boundary == "prepared" and name == "_prepare_tx" and event == "return" or \
            boundary == "first_file" and name == "_atomic_replace" and event == "return" or \
            boundary == "files_replaced" and name == "_commit_tx" and event == "call" or \
            boundary == "committed" and name == "_commit_tx" and event == "return"
        if selected:
            stopped = True
            print(boundary, flush=True)
            os.kill(os.getpid(), signal.SIGSTOP)
    threading.setprofile_all_threads(observe)
    await MemorySkills(memory).change(MemoryIdentity("office", "xiaowen"), "真实恢复流程", action="create",
        change_id="skill-version-sigkill", expected_revision=0, basis="真实SIGKILL核查", description="核对正文、版本和完整文件",
        text="# 完整恢复流程\n核对原始材料与持久化版本。", files={"references/procedure.md": "核对原始来源"})
    raise RuntimeError("验收边界未被观察到")


if __name__ == "__main__":
    asyncio.run(main())
