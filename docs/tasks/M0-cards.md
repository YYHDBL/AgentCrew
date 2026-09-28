# 07 · M0 任务卡拆分

> 版本：v1.0 ｜ 日期：2026-09-28 ｜ 前置：docs 01–06（v1.3，契约冲突已清）
> 职责划分：产品方向已在 docs 01 定稿，本文档只做**实施顺序与任务卡**；技术选型由本文档直接给出并附依据；只有必须由用户决定的产品边界才提问。

---

## 0. 领取协议（对开发 Agent 的约束）

1. **一次只领一张卡**，按 C1→C12 顺序；前一卡"真实运行验收"通过并提交推送后，才领下一张
2. 每张卡完成时：代码 + 一份验收记录 `docs/acceptance/M0-Cx.md`（粘贴真实命令与真实输出），然后 commit + push
3. **测试纪律（docs 01 v1.3）**：禁止 mock / 假测试——真实 GLM、真实文件、真实进程中断；自有纯函数（reducer / 判定 / 调度计算）的参数化单测不属于 mock，正常写
4. 代码位置：后端 `backend/`（`agentcrew_core` 纯库 + `agentcrew_server` FastAPI，分层纪律见 docs 02 §9）；前端 `desktop/`；验收用的一切种子数据脚本放 `scripts/seed/`
5. 卡内发现文档矛盾：以 docs 02 §2.3 持久化契约为准绳，改文档并在验收记录里注明，不开新讨论

---

## 1. 依赖图与顺序

```
C1 骨架 ──→ C2 数据层 ──→ C3 总线/SSE/鉴权 ──→ C6 审批闸门 ──→ C8 ReAct循环 ──→ C9 恢复 ──→ C12 贯穿验收
                │              │                                ↑
                └──→ C4 Provider ────────────────────────────────┤
                └──→ C5 工具注册表 ──────────────────────────────┤
                       C7 FSM/排队（依赖 C2,C3）──────────────────┘
C10 Electron壳（仅依赖 C1）──→ C11 前端聊天页（依赖 C3,C7,C10）──→ C12
```

严格领取顺序：**C1→C2→C3→C4→C5→C6→C7→C8→C9→C10→C11→C12**。

---

## 2. 任务卡

### C1 · 工程骨架与启动

- **依赖**：无
- **交付物（接口与数据变更）**：`backend/pyproject.toml`（uv 管理，依赖：fastapi、uvicorn、sse-starlette、pydantic、aiosqlite/内置 sqlite3、httpx）；分包 `agentcrew_core/`（events/loop/tools/provider/ports）与 `agentcrew_server/`（api/db/run_manager）；`python -m agentcrew_server --port N --data-dir D --parent-pid P` 入口（读 `AGENTCREW_TOKEN` 环境变量）；`GET /api/health`；stdout 就绪标记 `AGENTCREW_READY {"port":N}`（**绝不含 token**）；日志原子落 `data/logs/sidecar.log`
- **失败状态**：端口被占（换端口重试 3 次后报错退出）；data-dir 不可写（明确报错非崩溃）；未知异常（捕获→日志→非零退出码）
- **真实运行验收**：命令行启动→stdout 出现就绪标记→`curl /api/health` 返回 ok→Ctrl-C 干净退出；日志文件有内容且**全文搜不到 token**

### C2 · 数据层：建表 + 事件追加 + 投影

- **依赖**：C1
- **交付物**：SQLite WAL 连接管理；版本化迁移（`schema_migrations` 表）；按 docs 02 §2 建全部 M0 表（含 `run_events.global_seq` 自增主键、`tool_calls.call_id UNIQUE + side_effect_class + input_hash + pending_verification`）；EventStore（追加事件 + 同步更新投影表，单事务）；`agent_permission_rules`、`audit_log`（哈希链字段）表一并建好备用
- **失败状态**：迁移冲突（版本表检测，拒绝启动并报当前/目标版本）；并发写（WAL + busy_timeout，写失败重试 3 次）；事件 seq 冲突（UNIQUE 兜底，抛出而非覆盖）
- **真实运行验收**：迁移脚本连跑两次幂等；用 `sqlite3` CLI 逐表 `.schema` 核对与 docs 02 一致；脚本向种子会话追加 3 条事件→查询投影表已同步；重放器从事件重建投影与增量一致

### C3 · 事件总线 + SSE + 鉴权

- **依赖**：C2
- **交付物**：进程内发布/订阅总线（每会话一个通道 + 全局通配）；Bearer 中间件（校验 `AGENTCREW_TOKEN`，无/错→401）；`GET /api/task-runs/:id/events?from=seq` 与 `GET /api/conversations/:id/stream?from=global_seq`（sse-starlette；from=0 重放历史后转实时；`retry:` 与心跳注释行）
- **失败状态**：客户端断开（清理订阅不泄漏）；慢消费者（每连接独立队列，溢溢丢帧并标记需 `resync`）；订阅不存在的任务（404 而非空挂）
- **真实运行验收**：无 token curl → 401；带 token + 种子数据（C2 脚本插入）→ 先收到历史事件再收到实时事件；`curl --no-buffer` 全程可读；断开重连带 `from=<最后global_seq>` → 恰好续播无重复无遗漏

### C4 · Provider 薄适配层（GLM 真实接入）

- **依赖**：C1（可与 C2/C3 并行开发，验收独立）
- **交付物**：`Provider` 协议 + `StreamEvent` 归一（text_delta/tool_call/usage/done/error）；GLM 适配器（main/aux 双槽配置化，走 OpenAI 兼容端点）；错误分类（网络/限流/鉴权/内容过滤/未知）；重试（限流与网络错误，退避，上限 3）
- **失败状态**：坏 key（分类=auth，不重试，冒泡为可读错误）；流中断（分类+可重试一次续跑）；usage 缺失（记 0 并打警告日志，不失败）
- **真实运行验收**：**用真实 key** 跑三个脚本并记录到 `docs/tech/provider-verification.md`：① 无工具纯文本流式；② 带工具定义→模型真实返回 tool_use→归一为内部 ToolCall；③ 坏 key→错误分类正确。usage 字段真实打印

### C5 · 工具注册表与五个内置工具

- **依赖**：C1
- **交付物**：`ToolMetadata` 全字段 + 注册表 + 调度器（destructive 串行 / concurrent_safe·read_only 并行 / 上限 4）；`call_id` 生成与三分类声明；五工具：`read_file`（verifiable，realpath 规范）、`write_file`（verifiable）、`list_dir`（read_only）、`bash`（白名单+元字符判定→分级；超时杀进程组）、`http_request`（`allowed_hosts` 约束；外部幂等键随请求头发送）；输出 >32KB 落 `data/artifacts/<task_run_id>/` 工件留指针
- **失败状态**：工具超时（timeout_ms，杀子进程组，返回 error 结果不崩任务）；命令注入判定为非只读（走审批，不是拒绝）；工件目录写失败（工具报错+审计）
- **真实运行验收**：判定函数参数化单测（白名单×元字符矩阵）；五个工具各真实执行一次（真文件真命令真 http 请求一个真实 URL）；大输出真实触发外部化并检查工件文件存在

### C6 · 审批闸门（M0 部分）

- **依赖**：C2、C3、C5
- **交付物**：闸门判定纯函数（元数据分级 → deny/allow 规则（agent_permission_rules）→ ASK；realpath 与域名匹配在 C5 判定函数上）；审批生命周期：`PERMISSION_REQUESTED`（含四选项与 `input_hash`）→ 挂起工具任务 → `POST /api/tool-approvals/:call_id`（decision 四值）→ `PERMISSION_RESOLVED` → 继续/拒绝；`allow_always` 写规则表；每次决定写 audit_log 哈希链
- **失败状态**：审批期间用户取消任务（释放挂起，tool 标 cancelled）；重复提交同一审批（幂等返回首次决定）；审批请求携带的 input_hash 与当前调用不一致（拒绝，要求重新请求）
- **真实运行验收**：判定矩阵参数化单测；起服务后用种子数据触发一次真实审批流（脚本模拟的任务编排器调用闸门→curl 批准→观察到继续执行的下游事件）；audit_log 三条记录链哈希连续可验

### C7 · 会话 FSM 与指令排队

- **依赖**：C2、C3
- **交付物**：`reduce(state, event)` 纯函数（四态 + 等待计数器，docs 02 §4 迁移表）；`can_send/can_queue/can_cancel` 真值表函数；`POST /api/conversations`、`GET /api/conversations`、`POST /api/conversations/:id/instructions`（can_send 直跑 / can_queue 入队 / 否则 409 + 原因）、`GET .../state`；`pending_queue` 持久化与出队
- **失败状态**：非法迁移（InvalidTransition，拒绝并返回当前合法动作列表）；并发同会话两条指令（锁序化，第二条必入队）；审批挂起时发指令（409 + waiting_approvals 提示）
- **真实运行验收**：reducer 参数化单测覆盖迁移表全行；起服务建真实会话连发两条指令→第二条在队列中可见（GET state）；FSM 快照随事件实时变化

### C8 · ReAct 主循环与 RunManager

- **依赖**：C3、C4、C5、C6、C7
- **交付物**：`run_task` 循环（docs 02 §5：事件发射、工具结果回填、messages 投影、回合上限 40、token 预算守门）；RunManager（每 TaskRun 一个 asyncio 任务、注册表、取消传播到工具与模型流）；任务终态回写 + pending_queue 自动出队下一个
- **失败状态**：模型连续工具调用死循环（回合上限→RUN_FAILED 带原因）；工具全失败（结果回填让模型自决，不提前终止）；asyncio 任务异常（捕获→RUN_FAILED→错误入事件流）
- **真实运行验收**：真实 GLM 任务"读取 <真实文件> 并总结内容"→SSE 全程收到 STEP/LLM/TOOL 事件→messages 投影含最终回复；再跑一个多步任务（读两个文件→合并写第三个文件，写操作真实弹审批）；回合上限用 2 的小配置真实触发一次

### C9 · 中断恢复

- **依赖**：C8
- **交付物**：启动对账（running/waiting_user→RUN_INTERRUPTED；dispatched 无终态：verifiable 自动核验补齐 / 否则 pending_verification 暂停）；`POST /api/task-runs/:id/resume`（新 attempt、事件全文+工件重建上下文、副作用账本注入 system 提示）；`POST /api/task-runs/:id/cancel`
- **失败状态**：重放遇损坏事件行（跳过并显式告警 + 标记任务需人工介入，不静默）；工件文件缺失（重建降级：该工具结果以"工件缺失"占位，任务继续）；resume 时仍有 pending_verification（拒绝 resume，提示先核验）
- **真实运行验收**：真实任务跑到工具调用间隙 `kill -9` 后端→重启→对账为 interrupted→resume→任务完成且**目标文件内容与中断前一致（无重复写入痕迹）**；全程事件流可查

### C10 · Electron 壳与 sidecar 监管

- **依赖**：C1
- **交付物**：`desktop/` Electron 工程（main/preload/renderer 三件套）；sidecar 监管（生成 token→env 注入→spawn→就绪赛跑→崩溃 60s 窗口 3 次指数退避重启→超限弹窗带日志路径）；关窗缩托盘 + 显式退出警告 + 单实例锁；preload 窄 API（getBackendPort/getToken/notify/openPath/setKeepAwake）；`pnpm dev` 一键并发（uvicorn+Vite+Electron）
- **失败状态**：sidecar 启动超时（杀进程→重试→超限弹窗）；端口就绪标记格式异常（按失败处理）；主进程退出（parent-pid 看护 sidecar 自杀）
- **真实运行验收**：`pnpm dev` 一键起全链（窗口+后端健康）；活动监视器杀 sidecar→自动重启→前端自动重连；关窗后托盘可见、cron 级保活；二次启动聚焦既有窗口

### C11 · 前端最简聊天页

- **依赖**：C3、C7、C10
- **交付物**：React+Vite+TS+AntD5；@microsoft/fetch-event-source 订阅会话流（Bearer 头、global_seq 游标、指数退避重连）；聊天流（用户消息+最终回复，流式 delta 打字效果）；任务过程卡（步骤/工具调用/审批卡四选项）；输入框状态机（can_send/can_queue/can_cancel 驱动 + 排队徽标）；FSM 状态条
- **失败状态**：断流（重连+resync 全量快照恢复，不白屏）；审批卡过期（事件驱动消失）；重连风暴（退避上限 30s）
- **真实运行验收**：页面对话真实任务全程可见；审批卡点"允许"任务继续；任务中刷新页面→事件重放→UI 完整恢复；杀后端→自动重连→恢复

### C12 · 贯穿真实任务验收（M0 收口）

- **依赖**：C8、C9、C11（全部）
- **交付物**：`scripts/seed/make_mess.py`（在 `data/demo/inbox/` 生成 20 个真实杂乱文件：混合 pdf/csv/txt/md/无扩展名，内容真实生成）；不新增代码，产出《M0 验收报告》
- **失败状态**：任一验收项失败→定位到卡→返工该卡（不掩盖、不绕过）
- **真实运行验收**（全链、全真实）：
  1. 对话派活"把 inbox 里的文件按类型整理到子文件夹并生成清单.csv"→ 任务完成，**人工核对**整理结果与清单正确
  2. 全程写操作弹过审批且被批准过至少一次"总是允许"（工作区内）
  3. 整理中途 kill -9 后端→重启→恢复→完成，文件无重复副作用
  4. 事件流 SQL 核对：单任务事件序列完整（queued→started→…→completed），llm_calls/tool_calls 投影计数与事件一致
  5. 刷新页面→UI 从事件重放恢复
  6. 审计链校验脚本跑通全绿
  7. 全程截屏存档入验收报告

---

## 3. 技术选型速记（本卡组内定，附依据）

| 选型 | 依据 |
|---|---|
| 后端 SSE：`sse-starlette` | 现成库（v1.3 拍板不自研解析），FastAPI 生态标准件 |
| 前端订阅：`@microsoft/fetch-event-source` | fetch+自定义头+重连控制，Eigent 生产验证同款 |
| SQLite 驱动：内置 `sqlite3` + 自管 WAL（同步封装进 executor） | M0 单机单进程写少读多，避免引入 ORM/异步驱动两重新复杂度；投影/DAO 手写（Eigent 同款） |
| 包管理：`uv` | 当前 Python 生态最快且锁文件干净 |
| 无 ORM | 表少（≤12）、迁移手写可审计、与 docs 02 表定义一一对应，面试可讲性更强 |
| GLM 走 OpenAI 兼容端点 | 官方兼容层成熟，适配层薄；DeepSeek 同协议（M4 直接加第二个适配器） |

## 4. 明确不在 M0 的事（防范围蔓延）

记忆系统（M1）、grant/组织/角色/种子组织（M2）、Run Center/Studio/Admin 页面（M3）、cron 与审计 Agent（M3）、DeepSeek（M4）、打包 dmg（M4，仅 macOS）、多 Agent（M5）。OpenAPI 类型生成推到 C11 之后再评估（M0 前端先手写镜像类型，C12 E2E 兜底契约一致）。
