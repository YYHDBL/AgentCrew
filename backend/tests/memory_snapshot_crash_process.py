"""观察真实快照准备、文件替换与提交边界，等待父进程 SIGKILL。"""

import asyncio
import os
import signal
import sys
import threading
from pathlib import Path

from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.memory.snapshots import MemorySnapshots
from agentcrew_server.memory.store import MemoryStore


async def main():
    root, boundary = Path(sys.argv[1]), sys.argv[2]
    db = Database(root / "agentcrew.db")
    store = MemoryStore(db, EventStore(WriteChannel(db.write_conn)), root)
    stopped = False
    def observe(frame, event, arg):
        nonlocal stopped
        if stopped or event != "return":
            return
        module, name = frame.f_globals.get("__name__"), frame.f_code.co_name
        selected = (boundary == "prepared" and module == "agentcrew_server.memory.snapshots" and name == "_prepare_snapshot_tx")
        selected |= (boundary == "file" and module == "agentcrew_server.memory.store" and name == "_atomic_replace" and frame.f_locals["target"].name == "memory-snapshot.json")
        selected |= (boundary == "committed" and module == "agentcrew_server.db.event_store" and name == "_append_tx" and frame.f_locals["type"].value == "memory.snapshot_created")
        if selected:
            stopped = True
            print(boundary, flush=True)
            os.kill(os.getpid(), signal.SIGSTOP)
    threading.setprofile(observe)
    await MemorySnapshots(store).ensure("c1", "task-c1")
    raise RuntimeError("快照验收边界未被观察到")


if __name__ == "__main__":
    asyncio.run(main())
