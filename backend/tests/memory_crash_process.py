"""真实子进程：使用 Python profiler 观察事务边界，再由父进程 SIGKILL。"""

import asyncio
import os
import signal
import sys
import threading
from pathlib import Path

from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.memory.store import MemoryIdentity, MemoryStore
from agentcrew_server.memory.skills import MemorySkills


async def main():
    root = Path(sys.argv[1])
    boundary = sys.argv[2]
    db = Database(root / "agentcrew.db")
    run_migrations(db.write_conn, root / "backups")
    channel = WriteChannel(db.write_conn)
    store = MemoryStore(db, EventStore(channel), root)
    stopped = False

    def observe(frame, event, arg):
        nonlocal stopped
        if stopped or frame.f_globals.get("__name__") != "agentcrew_server.memory.store":
            return
        name = frame.f_code.co_name
        selected = ((boundary == "prepared" and name == "_prepare_tx" and event == "return") or
                    (boundary == "first_file" and name == "_atomic_replace" and event == "return") or
                    (boundary == "files_replaced" and name == "_commit_tx" and event == "call") or
                    (boundary == "committed" and name == "_commit_tx" and event == "return"))
        if selected:
            stopped = True
            print(boundary, flush=True)
            os.kill(os.getpid(), signal.SIGSTOP)

    threading.setprofile(observe)
    if len(sys.argv) > 3 and sys.argv[3] == "skill":
        await MemorySkills(store).change(MemoryIdentity("ws", "agent"), "真实恢复流程", action="create", change_id="sigkill-change",
            expected_revision=0, basis="真实 SIGKILL 验收", description="核对完整正文和支撑文件", text="# 完整恢复流程\n核验文件与账本",
            files={"references/procedure.md": "核对原始来源", "templates/checklist.md": "完整核验清单"})
    else:
        await store.change(MemoryIdentity("ws", "agent"), "user", "owner", change_id="sigkill-change", expected_revision=0,
            basis="真实 SIGKILL 验收", operations=[{"action": "add", "text": "SIGKILL 后恰好提交一次"}])
    raise RuntimeError("验收边界未被观察到")


if __name__ == "__main__":
    asyncio.run(main())
