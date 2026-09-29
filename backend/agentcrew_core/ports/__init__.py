"""core 到外部系统的 Port 接口定义（EventStore / Clock / AuditWriter 等）。

随各卡片逐个引入；core 依赖它们而非具体实现（harness-session.md §9）。
"""
