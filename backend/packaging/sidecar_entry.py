"""PyInstaller 打包入口：参数与退出码原样走 agentcrew_server.cli.main()。

onefile 形态下本入口运行在 PyInstaller 解包后的子进程中；信号由父进程
转发，父进程看护（--parent-pid）与就绪标记语义与开发模式完全一致。
"""

from __future__ import annotations

import sys

from agentcrew_server.cli import main

if __name__ == "__main__":
    sys.exit(main())
