"""agentcrew_core：纯库层（harness-session.md §9 分包纪律）。

本包不 import FastAPI / sqlite3；HTTP 服务与 SQLite 装配在 agentcrew_server，
存储等依赖经 ports/ 的 Port 接口注入。GLM 适配器与内置工具在本包内实现，
其磁盘/子进程/网络副作用受 WorkContext 的 scope、protected、allowed_hosts
约束。M0 各卡片按需填充下列子包，C1 只建立骨架。
"""
