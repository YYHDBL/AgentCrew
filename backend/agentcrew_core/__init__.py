"""agentcrew_core：纯库层（harness-session.md §9 分包纪律）。

本包不 import FastAPI / sqlite3；对模型、磁盘、时钟等外部系统的访问一律经
ports/ 的 Port 接口，由 agentcrew_server 提供真实实现。M0 各卡片按需填充
下列子包，C1 只建立骨架。
"""
