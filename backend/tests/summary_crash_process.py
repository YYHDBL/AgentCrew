"""观察真实摘要事务边界；持久化测试由父进程实际 SIGKILL。"""

import asyncio
import os
import signal
import sys
from pathlib import Path

from agentcrew_server.bus import EventBus
from agentcrew_server.config import load_config
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.memory.jobs import MemoryJobs
from agentcrew_server.memory.store import MemoryStore
from agentcrew_server.settings import SettingsService


async def main():
    root, job_id, boundary = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    db = Database(root / "agentcrew.db")
    channel = WriteChannel(db.write_conn)
    settings = SettingsService(load_config(root, {}), root, channel, root / "chain-head.txt")
    jobs = MemoryJobs(MemoryStore(db, EventStore(channel), root, settings), EventBus(), settings)
    await jobs._status(job_id, "running")
    if boundary == "committed":
        await jobs.complete(job_id, "已保存材料核查批次0")
    print(boundary, flush=True)
    os.kill(os.getpid(), signal.SIGSTOP)
    raise RuntimeError("摘要验收进程应由父进程 SIGKILL")


if __name__ == "__main__":
    asyncio.run(main())
