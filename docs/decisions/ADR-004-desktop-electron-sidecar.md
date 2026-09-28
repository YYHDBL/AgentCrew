# ADR-004 · Electron 壳 + Python sidecar，前端 AI 生成

状态：已接受（Q7 + v1.2 修订 + 分工修订）

**背景**：桌面形态；用户零 React；UI/UX 设计归专门的设计 Agent。
**决策**：Electron main spawn Python sidecar（随机回环端口；token 由主进程生成、**环境变量注入**，绝不经 stdout/日志）；Bearer 鉴权；崩溃监管（60s 窗口 3 次退避）；关窗缩托盘 + 显式退出警告；打包仅 macOS。前端代码由 AI 生成维护，用户只验收。
**理由**：Eigent/AionUi 同款拓扑参考充分；SSE 用现成库（sse-starlette / @microsoft/fetch-event-source，原生 EventSource 不能带请求头）。
**影响**：前端任务卡待 ui-spec 定稿后拆；用户须能讲清三进程/preload/SSE 重放/事件投影四件套（面试对冲）。
