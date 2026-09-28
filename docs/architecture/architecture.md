# 总架构

> 维护者：后端 Agent ｜ 详设五份：[harness-session](./harness-session.md) · [memory-system](./memory-system.md) · [governance](./governance.md) · [cron-and-audit](./cron-and-audit.md) · [desktop-shell](./desktop-shell.md)

## 技术栈定稿（依据见 alignment-record Q1–Q17）

| 项 | 决定 |
|---|---|
| 语言 | Python（后端 + Agent 核心全 Python） |
| 桌面 | Electron + React 前端 + Python sidecar；前端由 AI 生成维护，设计归设计 Agent |
| Harness | 全自研（循环/工具注册/权限门/事件/多供应商薄适配） |
| 运行时 | 单进程内嵌：FastAPI + asyncio，事件总线进程内扇出；Port 接口留拆分缝 |
| 存储 | SQLite + WAL；执行过程全事件溯源（持久化契约见 harness-session §2.3） |
| 模型 | GLM 双槽（main + aux flash）；DeepSeek 第二供应商（M4） |
| SSE | sse-starlette（后端）/ @microsoft/fetch-event-source（前端），Bearer 头 + global_seq 游标 |
| 形态 | 单用户本地，种子组织演示企业机制；**打包仅 macOS** |

## 进程拓扑

```
Electron Main（窗口/托盘/sidecar 监管/原生能力；token 经 env 注入）
  └─ spawn → Python sidecar（FastAPI + AgentRuntime 单进程，docs 详：desktop-shell）
       ├─ RunManager：每 TaskRun 一个 asyncio 任务
       ├─ 事件总线（内存扇出 + run_events 落盘 + SSE 出口）
       └─ 工具子进程（bash 等）
Renderer（React 四屏）──HTTP+SSE(127.0.0.1, Bearer)──→ sidecar
Preload：窄 API（getBackendPort/getToken/notify/openPath/setKeepAwake）
```

## 一条请求的链路

用户派活 → 创建 task_run（绑 AgentSpec 快照）→ 循环每步：三级权限闸门（元数据→员工规则→审批四选项）→ 工具调度（call_id + 副作用三分类）→ 发事件 → 落盘 + SSE 扇出 → 前端 reduce 成界面。刷新 = from=0 重放；崩溃 = sidecar 拉起 → 启动对账收敛 interrupted → 显式 resume 开新 attempt（账本注入，可核验自动核验 / 结果不明暂停待核验）。

## 模块与里程碑

模块范围（含"明确不做"）见 [alignment-record](../decisions/alignment-record.md) §4；里程碑 M0–M5 见同文档 §5（M0 已细化为 [tasks/M0-cards](../tasks/M0-cards.md)）。分包纪律（agentcrew_core 纯库 / agentcrew_server 装配）见 harness-session §9。
