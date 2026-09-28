# 06 · 桌面壳与前端详设

> 模块深潜 #5 ｜ 日期：2026-09-28 ｜ 状态：**已定稿（F1 对齐，其余直接设计）——设计阶段收官**
> 上游依据：[01-设计对齐纪要](../decisions/alignment-record.md)（Q7 桌面形态/前端 AI 生成、Q16 四屏深度）
> 参考源码：`workMate/AionUi/packages/web-host/src/backend-launcher.ts`（sidecar 监管）、`workMate/eigent/electron/main/init.ts`（spawn）、`workMate/ZCode/packages/desktop/`（安全边界）

---

## 0. 本模块对齐记录

| # | 决策点 | 结论 |
|---|---|---|
| F1 | 关窗行为 | **托盘常驻 + 显式退出**：关窗缩托盘、后端继续值班的；退出前警告（活跃任务/未触发定时任务）；设置提供"关窗即退出"开关 |
| （承前） | 前端生产方式 | 100% AI 生成维护；用户只做设计决策与验收（Q7） |
| （承前） | 四屏深度 | Cowork / Run Center 深；Agent Studio / Admin Center 够用（Q16） |

---

## 1. 进程拓扑与 sidecar 监管

```
Electron Main（窗口/托盘/sidecar 监管/原生能力）
  └─ spawn → Python sidecar（FastAPI + AgentRuntime，单进程，docs/02）
       └─ asyncio tasks / 工具子进程（bash 等）
Renderer（React 四屏）──HTTP + SSE──→ sidecar（127.0.0.1 随机端口）
Preload：contextBridge 窄 API（原生对话框/通知/打开路径/keepAwake）
```

**sidecar 生命周期**（抄 AionUi BackendLifecycleManager，细节简化）：
1. 启动：在 127.0.0.1 挑随机可用端口 → 主进程**生成一次性随机 token 并经环境变量 `AGENTCREW_TOKEN` 注入 sidecar**（v1.2：token 绝不进 stdout，就绪标记只含端口）→ spawn `python -m agentcrew_server --port N --data-dir D --parent-pid <main_pid>` → stdout 就绪标记 `AGENTCREW_READY {"port":N}` 与 `/api/health` 轮询赛跑（30s 超时）；token 由主进程经 preload 注入渲染层，所有请求携带 `Authorization: Bearer`——无凭证的本机进程无法调用接口
2. 监管：崩溃自动重启（60 秒窗口内最多 3 次，指数退避；超限弹窗报错附日志路径）；`--parent-pid` 让 sidecar 在主进程死亡时自杀，不孤儿
3. 日志：sidecar stdout/stderr 全量落 `data/logs/sidecar.log`（前端"查看日志"直达）
4. 单实例锁：`data/instance.lock`，二次启动聚焦既有窗口

**开发/生产双模式**：
- 开发：`pnpm dev` 一键并发拉起 uvicorn（--reload）+ Vite + Electron（Vite dev server 页面装进 Electron 窗口，热更新）
- 生产（M4）：electron-builder 打包；sidecar 用 PyInstaller onefile 预编译进 `resources/sidecar/<platform>/`，按平台选用

## 2. 托盘与生命周期（F1）

- 关窗 → 隐藏窗口 + 缩托盘（数字员工继续值班：cron、长任务、完成通知）
- 托盘菜单：打开主窗口 / 当前任务状态一行摘要 / 真正退出
- 退出前检查：有 running/waiting 任务或 24h 内待触发 cron → 确认弹窗列明后果
- 设置项：`关窗即退出`（默认关）、`阻止系统休眠`（默认开，保 cron；AionUi 同款）
- 桌面通知：任务完成/失败、审批请求、定时任务被拒（C1 的"早晨通知"）、记忆更新
- 审计链校验失败 → 应用启动进入**只读诊断模式**（docs/04 §3：断点定位/导出/校验/恢复入口可用，agent 与工具操作禁用），不进入正常值班（v1.1）

## 3. Electron 安全边界

- `contextIsolation: true`，`nodeIntegration: false`，无 remote
- preload 只暴露白名单 API：`selectFile/saveFile/openPath/notify/setKeepAwake/getBackendPort`——渲染层拿不到 fs/进程
- 渲染层到后端：`window.__backendPort` + `AGENTCREW_TOKEN` 请求头 + fetch/SSE 直连本地回环；所有数据访问都过 sidecar 的权限闸门

## 4. SSE 与事件流

- 订阅：`GET /api/task-runs/:id/events?from=<seq>`（SSE，任务内游标）；会话级聚合流 `GET /api/conversations/:id/stream?from=<global_seq>`（内部任务事件 + FSM 快照 + 通知）——**会话级流使用全局单调游标 `global_seq`（事件表自增主键），跨任务稳定有序（v1.1）**
- **带鉴权的流式订阅（v1.2）**：原生 EventSource 无法携带 Authorization 头，改用 **fetch 流**；**重连逻辑由前端显式实现**：保存游标 `global_seq` → 断线后带 `from=<global_seq>` 重新发起（指数退避）→ 服务端续播补齐增量；刷新页面 `from=0` 重放恢复 UI（EventSource 自动重连方案废弃）
- **实现选型（v1.3，不自研 SSE 解析）**：后端 `sse-starlette`；前端 `@microsoft/fetch-event-source`（fetch + 自定义头 + 重连控制，Eigent 同款验证过）
- 重连后先拉一次 FSM 快照（can_send/can_queue/can_cancel + waiting 计数），再消费增量
- 通知类（记忆更新、cron 被拒）走会话流 + 桌面通知双通道

## 5. 前端架构（AI 生成维护）

**技术栈**：React 18 + Vite + TypeScript + Ant Design 5 + Zustand + TanStack Query。类型全部从后端 OpenAPI schema 生成（`pnpm gen:api`）——**前后端契约单一事实源在后端**。

**核心原则：UI 状态 = 事件投影**。前端有一个与服务端 reducer 同构的客户端 reducer（消费同一套事件类型），页面组件只读投影 store：
- 会话页的"正在干活/等待审批/排队中" = FSM 投影
- Run Center 的时间线 = 事件流直接渲染
- 刷新 = from=0 重放 → UI 完整重建

**四屏规格**（Q16 深度分配）：

| 屏 | 深度 | 核心组件 |
|---|---|---|
| Cowork | 深 | 会话列表 / 聊天流（含任务过程卡：步骤、工具调用、审批卡）/ 排队指示 / 记忆更新气泡 / 输入框状态机（can_send/can_queue 驱动） |
| Run Center | 深 | 任务列表 / 事件时间线（步骤↔LLM↔工具钻取）/ Replay 播放器（按 seq 步进）/ 指标卡（token/延迟/步骤）/ 审计报告面板（root_cause 跳转事件流） |
| Agent Studio | 够用 | 员工列表与表单（spec）/ soul 查看器 / skill 版本列表 / **已授权清单**（规则表勾选）/ 记忆管理（三库条目 + 钉住/归档/账本回滚） |
| Admin Center | 够用 | 成员与角色 / grant 授权矩阵 / 连接器配置 / 定时任务管理（含运行历史）/ 审计日志查询 + **验证审计链按钮** |

## 6. "AI 生成前端"协作约定（写给用户）

1. **你不写不改前端代码**；验收方式 = 跑起来看 + 截图圈问题；改需求用自然语言描述（"审批卡片大一点""时间线按步骤折叠"），AI 负责实现
2. **面试四件套**（必须能讲清的设计知识，不涉 React 细节）：① Electron 三进程分工与为什么；② preload 为什么只暴露窄 API；③ SSE 断线重连后为什么不用写恢复逻辑（重放即恢复）；④ 为什么刷新页面 UI 不会乱（状态=事件投影）
3. 前端代码全部进仓库（`agentcrew/desktop/`），提交信息由 AI 写；你只在 PR 层面看"改了哪个页面"
4. 样式纪律：Ant Design 主题变量统一，不做自定义设计系统——把 AI 的发挥空间锁在组件库内，保证一致性

## 7. 验收与测试

- **M0（壳部分）**：`pnpm dev` 一键起全链；杀掉 sidecar 进程 → 自动重启 → 前端自动重连并从断点重放事件流；关窗缩托盘、托盘退出有警告
- **M4（打包）**：macOS dmg 安装包可装可跑（**仅 macOS——用户拍板不做 Windows**，v1.2）
- E2E 冒烟（Playwright）：启动 → 建会话 → 发指令 → 看到审批卡 → 批准 → 任务完成 → Run Center 有时间线
- sidecar 监管单测：就绪超时、崩溃重启窗口、parent-pid 自杀

## 8. 参考

- sidecar 监管蓝本：`workMate/AionUi/packages/web-host/src/backend-launcher.ts`（1097 行，含端口握手/健康赛跑/重启窗口/peer 冲突退避）
- Electron spawn + 打包态路径：`workMate/eigent/electron/main/init.ts`
- 安全边界纪律：`workMate/ZCode/AGENTS.md`（进程与协议章节）
