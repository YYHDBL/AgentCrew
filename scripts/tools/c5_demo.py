#!/usr/bin/env python3
"""M0-C5 验收演示脚本（可复核：输出与 docs/acceptance/M0-C5.md 引用一致）。

真实执行六项：Seatbelt 越界写拒绝 / 沙盒断网（本地端口对照） / token 不进
子进程 / 受保护路径拒绝 / find 只读与否决 / scope 内受保护双向禁。

用法：cd backend && uv run python ../scripts/tools/c5_demo.py
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_core.tools import (
    ToolInvocation,
    WorkContext,
    build_default_registry,
    build_protected_paths,
    new_call_id,
)
from agentcrew_core.tools.scheduler import ToolScheduler


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="c5-demo-"))
    (tmp / "ws").mkdir()
    (tmp / "data").mkdir()
    (tmp / "home" / ".ssh").mkdir(parents=True)
    (tmp / "data" / "agentcrew.db").write_text("DB")
    os.environ["AGENTCREW_TOKEN"] = "tok-should-never-leak-987654"
    ctx = WorkContext(
        scope=[tmp / "ws"], protected=build_protected_paths(tmp / "data", tmp / "home"),
        artifacts_dir=tmp / "art", task_run_id="demo",
    )
    # 对照用 ctx2：整个 tmp 都在 scope（受保护路径落在 scope 内的场景）
    ctx2 = WorkContext(
        scope=[tmp], protected=build_protected_paths(tmp / "data", tmp / "home"),
        artifacts_dir=tmp / "art", task_run_id="demo",
    )
    scheduler = ToolScheduler(build_default_registry())

    async def go(name, inp, context=None):
        return await scheduler.run(
            ToolInvocation(new_call_id(), name, inp), context or ctx)

    r1 = await go("bash", {"command": "echo x > /tmp/c5-demo-outside.txt"})
    print(f"[demo] 越界写 → ok={r1.ok} error={r1.error} "
          f"stderr={r1.details.get('stderr', '')!r}")

    # 网络对照证明：本地真实监听，沙盒外连通、沙盒内被拒
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    control = socket.create_connection(("127.0.0.1", port), timeout=2)
    control.close()
    r2 = await go("bash", {"command":
                  f"curl -s -m 3 -o /dev/null http://127.0.0.1:{port}/",
                  "timeout_ms": 8_000})
    print(f"[demo] 网络对照 → 沙盒外连接 127.0.0.1:{port} 成功；"
          f"沙盒内 ok={r2.ok} exit={r2.details.get('exit_code')}")
    server.close()

    r3 = await go("bash", {"command": "echo TOKEN=[$AGENTCREW_TOKEN]"})
    print(f"[demo] echo TOKEN → ok={r3.ok} output={r3.output.strip()!r}")

    r4 = await go("read_file", {"path": str(tmp / "data" / "agentcrew.db")})
    print(f"[demo] 读 agentcrew.db → ok={r4.ok} error={r4.error}")

    r5 = await go("bash", {"command": f"find {tmp / 'ws'} -name '*.txt'"})
    print(f"[demo] find -name → ok={r5.ok}（只读判定={r5.details.get('read_only_verdict')}）")
    r6 = await go("bash", {"command": f"find {tmp / 'ws'} -name '*.txt' -delete"})
    print(f"[demo] find -delete → 判定只读={r6.details.get('read_only_verdict')} "
          f"原因={r6.details.get('verdict_reason')!r}（C6 起走审批）")

    r7 = await go("bash", {"command":
                  f"echo hacked > {tmp / 'data' / 'agentcrew.db'}"}, ctx2)
    intact = (tmp / "data" / "agentcrew.db").read_text() == "DB"
    print(f"[demo] scope 内受保护写 → ok={r7.ok} "
          f"stderr={r7.details.get('stderr', '')!r}；原文件完好={intact}")

    del os.environ["AGENTCREW_TOKEN"]


if __name__ == "__main__":
    asyncio.run(main())
