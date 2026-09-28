# ADR-003 · 单进程内嵌运行时

状态：已接受（Q9）

**背景**：桌面应用，单用户本地；可选 API 与 Runtime 双进程 + 自研 IPC。
**决策**：FastAPI 单进程内嵌 AgentRuntime，每 TaskRun 一个 asyncio 任务；进程内事件总线（内存扇出 + 落盘）；Runtime 挂 Port 接口留拆分缝。
**理由**：多进程解决多用户隔离与外部引擎托管，本场景收益趋零而协议成本最高（ZCode 为此做了四层 RPC）；崩溃恢复走事件重放 + Electron sidecar 监管。
**影响**：不做 claim/lease（cron 单实例）；进程拆分留作未来不承诺项。
