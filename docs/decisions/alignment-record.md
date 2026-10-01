# AgentCrew 设计对齐纪要

> 版本：v1.1 ｜ 日期：2026-09-28 ｜ 状态：**已定稿（经外部审查修订）**
> 本文档是 AgentCrew 项目设计对齐（grilling 决策树，17 轮问答）的完整记录，是后续所有模块设计与实现的唯一依据。
>
> **v1.1 修订记录（外部审查采纳，2026-09-28）**：① 本地请求 token 认证 + 身份语义澄清（虚拟成员=演示身份切换）；② 权限闸门前移 M0 并收紧（删除 python 前缀放行、路径 realpath 规范化、HTTP 域名约束、审批绑定参数哈希）；③ 幂等重设计（唯一调用身份 + 工具副作用三分类，结果不明暂停自动重试待核验）；④ 真实办公任务贯穿主线（M0 起固定验收载体）；⑤ 事实源分级（执行事件 vs 业务状态表）+ 会话级全局游标；⑥ 定时授权改交集语义 + 触发时复查 + 运行记录唯一约束；⑦ 记忆来源标注 + 高风险事实需核验；⑧ 审计链头外置快照 + 校验失败进只读诊断模式；⑨ M4 接入 DeepSeek 第二供应商；⑩ 指标命名诚实化（任务完成率/无错误率，与结果核验分开报告）。**任务回归集：暂缓**——实施期再调研设计（用户决定，非否决）。测试哲学：FauxProvider 跑单元/回归 + 真实 GLM 跑验收演示。
>
> **v1.2 修订（开工前二次审查，2026-09-28）**：SSE 改带鉴权 fetch 流（原生 EventSource 无法携带 Authorization 头，重连与游标由前端显式实现）；token 改经环境变量注入 sidecar、绝不进 stdout/日志；bash 只读白名单移除 find（-exec 漏洞）并加元字符一票否决；恢复后模型重发调用 = 新决定（同参跳过会错误合并两次有意执行，防重复改由副作用分类承担）；run_events 增 global_seq 会话级游标；新增**持久化契约**（docs/02 §2.3：事件载荷全文 + 工件保留 + 业务表边界）；种子规则去 python、"登录"改"演示身份切换"；审计链头每 100 条周期快照（崩溃也有锚点，两级校验）；**打包仅 macOS（用户拍板不做 Windows）**；Provider 真实凭证首周验证纪律（含 DeepSeek reasoning_content 回传）。测试哲学由 v1.3 修正（用户最终确认禁止 mock）。
>
> **v1.3 修订（M0 拆分前契约修正，2026-09-28）**：SSE 解析用现成库——后端 `sse-starlette`、前端 `@microsoft/fetch-event-source`（Eigent 同款），不自研解析；外部幂等键改由 `(task_run_id, call_id)` 派生——**同任务两次合法相同请求各得各键、绝不合并**，同键仅用于同一次逻辑调用的重试/核验后重执行；**测试禁止 mock 与假测试（用户确认，覆盖此前双层方案）**：真实 GLM、真实文件产物、真实进程中断，自有纯函数的参数化单测不属于 mock。M0 任务卡拆分见 [../tasks/M0-cards.md](../tasks/M0-cards.md)。
>
> **v1.6 修订（2026-09-30，用户裁定）**：**定位修订——AgentCrew 是通用 Agent 基座，办公是后续专项优化**；**工具 less-is-more**：M0 核心四件套 read_file/write_file/bash/http_request，设"工具准入纪律"（治理语义或上下文友好度不可替代才准入；禁止 read_word 式碎片工具；grep/glob/edit/list_dir 不设，bash+read 覆盖，专项工具按需后加）；**bash 子进程环境变量白名单**（仅 PATH/HOME/LANG/TZ/TERM，绝不传 AGENTCREW_TOKEN/API key——堵"子进程读 token 反打接口"的击穿孔）；**沙箱提前**：macOS 自带 Seatbelt（sandbox-exec），M2 落地于 bash 工具（从"明确不做"移出；参考 CC/EasyMint 分层——三级闸门即判定层，Seatbelt 为强制层）；补循环稳定性三招 + 环境信息注入 + 流式 tool_call 拼装 + 守门参数总表（docs 02 v1.5/v1.6）。数据保留/归档策略待 M4 打磨期定。
>
> **v1.7 修订（2026-09-30，第三轮外部审查回稿）**：四条致命全采纳——① **M0 即给 bash 上最小 macOS Seatbelt profile**（用户裁定 F1=A：写限任务 scope、网络全禁、凭据禁读——http_request 恢复"唯一网络入口"强承诺；M2 升完整 profile）；② **受保护路径品类**（agentcrew.db/config/chain-head/logs/USER.md/soul.md/MEMORY.md/凭据目录——scope 内读写双向硬禁，PROTECTED_PATH）；③ 副作用分类精化（write_file=原子写 tmp+rename+fsync+内容 sha256 核验；HTTP 默认 outcome_unknown，external_idempotency 仅限连接器显式声明；传输重试/中断核验/恢复后新动作三层分写）；④ 事件契约补全（llm.request_done 载荷含全部 tool_use 块；tool_calls 增 not_executed、task_runs 增 waiting_verification 枚举）。建议改采纳：**find 参数否决重入只读白名单**（用户裁定⑥=B：参数含 -exec/-delete 等即否决）；ask_user 交互原语补入 M0 五件套；终态时序契约（流关+子进程收割+待核验落库后才发终态）；attempt 记 context_fingerprint；环境块移 prompt 尾部；失败自动接续注入护栏；产物打开前实检；授权界面读写分列；审计链断言诚实化（真锚点=库外备份 chain-head.txt）。驳回（附理由）：队列项独立性声明（同会话上下文连续已可见失败，护栏提示足够）；内容注入扫描扩大化（不可能靠扫描防注入，防线=强制边界+闸门+人审，姿态已写明）。
>
> **v1.8 修订（2026-09-30，第四轮外审回稿——服务层事务/流边界 + 契约与任务卡可执行性）**：五条致命全采纳——① **统一写通道**：全部库变更经单一串行写 executor；事件+投影同事务；提交后才向 SSE 发布；禁止跨外部等待持有事务（工具执行=prepared 事务/执行无事务/结果事务三段）；② **SSE 边界协议**：游标排他；先注册实时监听再读历史至 head 的无遗漏交接（缓冲去重）；FSM 快照带 at_global_seq 配对续播；慢消费者队列 1000 溢出→服务端终止并发 resync 帧（含最后连续游标）；SSE 连接上限 32；新增 shutdown/ping 控制帧；③ **settings 版本化**：每次修改生成 config_version，任务与 aux 执行绑定不可变配置快照（入 attempt 指纹），PATCH 只切换后续执行；env 覆盖字段返回 ignored_fields；api_key 缺省不变/显式 api_key_clear 清除；先写文件后切内存，失败 500 CONFIG_WRITE_FAILED；④ **任务卡验收链修复**：C1 只验配置链/CORS/干净退出（settings 移 C7、SIGTERM 运行中任务移 C9）；C7 验收限于 API 层（无 runner 时 task_run 停留 queued 属预期），运行时行为全移 C8；C6"脚本模拟"改为进程内直调自家服务（非 mock 外部系统），完整审批交互移 C8；新增 **POST /api/questions/:requestId/answer**（answer=null=取消按拒绝回答）+ GET questions pending；⑤ **openapi v0.3 成为可执行契约**：M0 操作全 schema（请求体/成功信封/错误引用），PATCH 补请求体，信封 {"data"}/{"error"} 全局统一（204 与 SSE 例外已注明），messages 分页（limit≤200+before 游标），events JSON 模式 after_seq 排他+limit≤500，客户端幂等 client_request_id（创建/指令），审批同决定重试幂等/不同决定 409。建议改全采纳：升级前 db 快照（backups/ 保留 3 份）+迁移事务边界+失败诊断入口；优雅关闭 10s 预算（SSE 发 shutdown 帧存游标→限时收割→PASSIVE checkpoint 不无限等待）；请求上限与资源上限分离（材料在复制时强制查体积）；data-model 补全 13 表清单；"表≤12"表述修正。可保留确认：CORS allow-all、API 不版本化（db 迁移独立版本化）、SQLite WAL 单进程。
>
> **v1.9 修订（2026-09-30，第五轮复核回稿）**：① **升级快照改 `VACUUM INTO`**（自洽快照含 WAL 已提交事务；禁止裸复制主库文件——WAL 下会丢事务）；恢复语义分层澄清（迁移回滚=只还原 db；灾备=整目录）；② **发布顺序=提交顺序**：发布在写通道内、提交后同步入队（非阻塞），SSE 队列按 global_seq 严格递增——杜绝乱序窗口导致的游标跳漏；③ **M0 读取边界诚实化**：Seatbelt M0 只强制"写范围+网络+凭据读"，scope 外常规读取 M0 仍靠判定层，M2 才有内核强制——不提前宣称；④ **循环伪代码补 assistant tool_use 入历史**（结果配对的前提，运行时与事件载荷口径一致）；⑤ **C3 验收的 at_global_seq 检查移至 C7**（接口归属修正）。审查者提醒成立：仓库尚无 backend/ 实现，以上均为设计层复核——真实并发/恢复/权限验证由任务卡的真实验收承担。

---

## 0. 一句话定位

**AgentCrew：桌面端的数字员工平台**——你在自己的电脑上创建、配置、雇用 AI 数字员工，它们真实地替你干活（整理文档、处理表格、定时巡检），全过程被记录、审批、审计、可回放。

**项目哲学（对标 pi）**：易于学习、易于理解、麻雀虽小五脏俱全——具备企业级产品的全部核心机制，但代码简单、概念少、可供开发者按自己的业务需求二次开发。"简单 = 概念少而非代码少"。

**首要目的**：简历/求职作品集（AI 应用 / Agent 开发工程师方向），兼顾真实自用；开源社区运营不作为目标。

---

## 1. 技术栈定稿

| 项 | 决定 | 依据 |
|---|---|---|
| 语言 | **Python**（后端 + Agent 核心全 Python） | 求职方向为 AI 应用工程师（Python 生态）；Eigent / learn-workbuddy / hermes-agent 三套高质量 Python 参考 |
| 桌面形态 | **Electron + React 前端 + Python sidecar 后端** | Eigent / AionUi 同款拓扑；sidecar 管理、SSE 事件流有现成参考 |
| 前端生产方式 | **前端 100% 由 AI 生成与维护**，用户不写不学 React，只做设计决策与验收 | 用户明确约束。对冲：用户须能讲清（设计层面）Electron 三进程、preload 窄 API、SSE 消费、UI 状态 = 事件投影 |
| Harness | **全自研**（循环、工具注册、权限门、事件发射、多供应商薄适配层） | 简历含金量核心；learn-workbuddy 教学实现兜底；拒绝 litellm / LangGraph / fork Eigent |
| 运行时宿主 | **单进程内嵌**：FastAPI + asyncio，每个 TaskRun 一个 asyncio 任务，进程内事件总线 | 单用户桌面场景；崩溃靠 Electron sidecar 监管 + 事件重放对账；Runtime 挂 Port 接口留拆分缝 |
| 存储 | **SQLite + WAL**，全事件溯源 | `run_events` append-only 是唯一事实源，其余表全是投影 |
| 模型 | **双槽 + GLM**：主力槽 GLM（对话/工具决策），aux 槽 GLM flash（压缩摘要、记忆提炼、轨迹审计、标题） | 后台廉价任务是刚需；三级路由留作扩展点 |
| 使用形态 | 单用户本地运行，预置种子组织（owner + 虚拟成员），不部署服务器 | 企业机制真实存在且生效即可，不背服务端包袱 |

---

## 2. 十七轮决策全记录

| # | 决策点 | 结论 |
|---|---|---|
| Q1 | 项目首要目的 | 简历作品集为主，真实自用为辅；开源仅作工程风格 |
| Q2 | 使用形态 | 单用户本地运行（种子组织演示企业机制） |
| Q3 | 首个数字员工场景 | 通用办公助理（文件/文档/HTTP）；数据分析为第二 demo；cron 做定时演示 |
| Q4 | 执行边界 | 真实执行 + 权限分级（只读放行 / 写操作审批 / 危险拒绝）+ 幂等 + 审计 |
| Q5 | 节奏 | 全职冲刺（一个月量级），按饱满 MVP 砍范围 |
| Q6 | 求职方向 → 语言 | AI 应用/Agent 工程师 → **Python** |
| Q7 | 桌面壳 | Electron + React + Python sidecar；**前端 AI 生成，用户零 React** |
| Q8 | Harness 自研程度 | 全自研，含 provider 薄适配层 |
| Q9 | 进程模型 | 单进程内嵌（asyncio），Port 接口留拆分缝 |
| Q10 | 存储模型 | 全事件溯源（v1.2 限定表述：事件表是**执行过程**的事实源；grant/记忆/定时为业务状态表——见持久化契约 docs/02 §2.3） |
| Q11 | 多 Agent | **优先级后移**；SubAgent = fork 出的**完整 ReAct Agent**（subagent-as-tool），无静态 DAG 执行器；"计划"至多是 todo 工具；简历表述相应改写 |
| Q12 | Memory 深度 | **全项**：三层记忆 + 预算 + 外部化 + 自动压缩 + 写入判定 + 更新合并 + TTL + workspace 隔离；并按 hermes 蓝图实现自管理 |
| Q13 | Evaluation | **不做自带数据集**；一切行为落盘 + 代码可算指标自动收集 + **轨迹审计 Agent**（LLM 审查运行轨迹给改进建议）；评测平台 = 跑器 + 看板（给外部评测集即可跑 + 归因） |
| Q14 | 权限治理 | 固定三角色（owner/admin/member）+ grant 授权表（默认拒绝）+ 审批四选项 + 幂等三态账本 + 哈希链审计；不做动态角色与凭据双模式 |
| Q15 | 模型接入 | 双槽（主力 + aux 廉价）+ GLM |
| Q16 | 四屏深度 | 四屏俱全：Cowork 与 Run Center 做深，Agent Studio 与 Admin Center 够用 |
| Q17 | 收尾定稿 | cron：错过标记不补跑、支持复用/新建会话两种模式、配置落库重启恢复；远程访问砍掉；包结构按 pi 分层纪律由 AI 设计；里程碑按数字编号 |

---

## 3. 架构总览

```
┌────────────────────────────────────────────────┐
│ Electron 壳（AI 生成维护）                        │
│  main（窗口/sidecar 拉起与监管）                   │
│  renderer（React 四屏：Cowork/Studio/Run/Admin）  │
│  preload（窄 API 桥）                             │
└───────────────┬────────────────────────────────┘
                │ HTTP + SSE（本地端口，事件流从 seq 重放）
┌───────────────▼────────────────────────────────┐
│ Python sidecar（单进程 FastAPI + asyncio）        │
│  ├─ AgentRuntime（Port 接口后，可拆分留缝）        │
│  │   ReAct 循环 · 工具注册表 · 权限门 · 事件发射    │
│  ├─ 事件总线（进程内发布/订阅，同时落盘）            │
│  ├─ 记忆系统（三层 + hermes 式提炼 fork）           │
│  ├─ 定时调度 · 轨迹审计 Agent · 幂等账本            │
│  └─ REST/SSE API 层                              │
└───────────────┬────────────────────────────────┘
                │
        SQLite（WAL）：run_events（唯一事实源）
        + 投影表 + 治理表 + 记忆表 + cron 表
```

**一条请求的链路**：用户在 Cowork 发任务 → 创建 task_run（绑定 AgentSpec 快照）→ RunManager 启动 asyncio 任务 → 循环每步：权限检查（grant 解析 + 高风险挂起等审批）→ 工具调度 → 发事件 → 落盘 + SSE 扇出 → 前端把事件 reduce 成界面。刷新页面 = 从 seq 0 重放。崩溃 = Electron 拉起后端 → 启动对账收敛为 interrupted → 用户点恢复开新 attempt（副作用账本注入，防重复执行）。

---

## 4. 模块范围定稿

### 4.1 Agent Harness（自研）
- ReAct 循环（while + tool_use/tool_result），全事件发射
- 工具注册表：安全元数据（readOnly / destructive / sideEffectScope / riskLevel / needsApproval / timeoutMs / maxOutputBytes）+ 并发规则（destructive 串行、readOnly 并行）
- 权限门：grant 解析 → 分级（只读放行/写 ASK/危险拒绝）→ 审批四选项（allow_once/always、reject_once/always）+ always 白名单
- provider 薄适配层：GLM（主力 + flash）归一化；接口留多供应商扩展（面试点：tool_use 格式归一化）
- 中断恢复：事件重放 + attempt 模型 + 工具副作用三态账本（prepared/dispatched/completed）

### 4.2 记忆与上下文（hermes 蓝图）
- 三层：Working（本轮上下文）/ Session（会话摘要）/ Long-term（业务事实 + 用户偏好）
- 常驻记忆 = 限额冻结快照（MEMORY + USER 双库路由，超限报错逼模型当场整合）
- 阈值自动压缩：保护头尾 → 确定性裁剪 → aux 模型结构化模板摘要（迭代更新式）
- 大工具输出外部化（落盘留指针）
- background review fork：计数器触发（N turn / N 工具迭代），turn 结束后 fork aux agent 白名单工具提炼记忆；同一机制复用为**轨迹审计 Agent**
- 长期记忆治理全项：LLM 写入判定、更新合并、TTL 过期、workspace 隔离
- Skill：三级渐进披露（索引常驻 ≤60 字符描述 → 按需载全文 → references）+ AI 自写/自改（read-before-write、append-only 账本、只归档不删除）+ 确定性 curator（14 天不用→stale，30 天→归档）

### 4.3 企业治理（B 档）
- 资源模型：organization / workspace / user / role（固定三角色）/ agent / skill / connector
- grant 授权表：每资源一张，授予 user 或 workspace，默认拒绝 → 决定"哪个数字员工能用哪些工具与 skill"
- 幂等：工具调用唯一钥匙 + 副作用三态账本
- 审计：append-only 哈希链（每条带前条 hash）

### 4.4 评测与可观测（用户版本）
- 不造数据集；定义评测集输入格式，外部给一份即可跑
- 代码可算指标自动收集（成功率/工具调用/步骤/token/延迟——事件表聚合）
- 失败归因：点开指标跳到事件流出错步骤
- 轨迹审计 Agent：运行结束后对事件流的 LLM 审查报告（改进建议）
- Run Center 呈现以上全部 + Replay 重放

### 4.5 定时任务
- 任务配置四段式（schedule/target/snapshot/state）落库，重启恢复
- 错过 → 标记 missed 不补跑；支持"复用既有会话 / 每次新会话"两种执行模式；可重试
- `created_by: user | agent`（数字员工可在对话中被指示创建定时任务）

### 4.6 多 Agent（后移至加时赛）
- 无静态 DAG 执行器。Supervisor 本身是 ReAct Agent，通过 `spawn_agent(task)` 工具派活
- SubAgent = fork 的完整 ReAct Agent（独立循环/上下文/工具集，拿目标自主执行，返回结果）
- 简历表述："Supervisor 动态派发子 Agent（子 Agent 为完整 ReAct 循环）"

### 4.7 明确不做（写进 README 的 Future Work）
远程访问（手机控桌面）、多用户服务器、SSO/动态角色/凭据双模式、外部记忆插件生态、LLM 大合并 curator、开源社区运营。（v1.6：沙箱从本清单移出——M2 用 macOS Seatbelt 落地于 bash，见 ADR-008）

---

## 5. 里程碑（数字编号，按序推进）

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| **M0 地基** | Python 后端骨架 + 自研 ReAct 循环 + GLM 双槽接入 + provider 薄适配 + 工具注册表 + **权限闸门基础（元数据分级 + 审批四选项 + bash 只读白名单 + 本地 token 认证）** + 事件溯源落盘 + 事件总线 + SSE + Electron 壳 + 最简聊天页 + **一条贯穿用真实办公任务** | 能对话、能调工具、写操作必弹审批、过程实时可见；杀进程后重启，会话可恢复 |
| **M1 记忆与上下文** | 三层记忆 + 限额冻结快照 + 双库路由 + 阈值压缩（模板摘要）+ 输出外部化 + 提炼 fork + 长期记忆治理全项 + Skill 三级加载 | 长会话不爆上下文；隔天再聊记得住偏好；超限记忆自动整合 |
| **M2 安全与治理深化** | grant 授权表 + 员工规则 pattern（realpath 规范化、完整命令等值、HTTP 域名）+ 幂等副作用账本（三分类 + 待核验）+ 哈希链审计（链头外置快照）+ 组织/角色/资源模型 + 种子组织（演示身份切换）+ **完整 macOS Seatbelt 沙盒：普通文件读写限任务 scope，bash 网络全部禁止，HTTP/MCP 网络走受控连接器** | grant 撤销即时生效；恢复重跑不重复副作用、结果不明暂停待核验；审计链校验通过，失败进入只读诊断；**沙盒越界读取和写入被系统拦截** |
| **M3 监控回放与自动化** | Run Center（时间线/重放/指标/失败归因）+ 轨迹审计 Agent + cron 定时任务 + Agent Studio + Admin Center | 一单任务全链路可视可回放；审计 Agent 出改进报告；定时任务按时触发且错过不补跑 |
| **M4 打磨与演示** | 四屏打磨 + 真实办公场景（文档/表格/HTTP）+ 数据分析第二 demo + **DeepSeek 第二供应商接入** + how-it-works 文档 + 面试问答清单 | 完整 demo 可 15 分钟讲清全部机制；双供应商切换可用 |
| **M5 加时赛** | 多 Agent：spawn_agent 工具 + 完整 ReAct 子 Agent fork + 子运行并入事件流 | Supervisor 能拆活派给子 Agent 并汇总结果 |

---

## 6. 简历五条（修订版表述）

1. 自研 Python Agent Harness：ReAct 循环、工具注册表（安全元数据 + 并发调度 + 副作用三分类）、GLM 双槽 + DeepSeek 双供应商归一化适配层、事件溯源持久化、中断恢复（attempt + 副作用账本 + 结果不明待核验）与故障重试。
2. 分层记忆系统：Working / Session / Long-term 三层 + Context Budget + 工具结果外部化 + 阈值自动压缩；长期记忆具备写入判定、更新合并、TTL 与 Workspace 隔离，并由后台提炼 Agent 自管理（hermes 式）。
3. 企业资源模型与权限治理：Organization / Workspace / User / Role / Agent / Skill / Connector + RBAC（真实规则执行 + 演示身份切换）+ grant 能力隔离（默认拒绝）+ 高风险操作四选项审批（绑定参数哈希）+ 幂等控制 + 哈希链 Audit Log（链头外置快照）。
4. Agent 可观测与评测：TaskRun → AgentRun → Step → LLM/Tool 全链路 Trace（事件溯源）、Replay 重放、失败归因、代码可算指标（任务完成率/无错误率/token/延迟，与结果核验分开报告）+ 轨迹审计 Agent；评测平台支持外部评测集接入即跑。
5. 桌面产品（Electron + Python sidecar）：Cowork Workspace / Agent Studio / Run Center / Admin Center 四屏，可创建长期存在的数字员工，经 Skill / 工具 / 连接器执行真实办公任务，含定时自动化。

---

## 7. 参考仓库索引（按需查阅，不通读）

| 做什么 | 查哪里 |
|---|---|
| Agent 循环 / 事件溯源 / Run 恢复 | `workMate/eigent/backend/app/run_journal/`（三件套+对账）；`workMate/learn-workbuddy/`（机制教学） |
| 记忆分层 / 提炼 fork / skill 自管理 | `workMate/hermes-agent/`（**Python，源码级参考**：memory_tool_store、background_review、curator、context_compressor） |
| Session 状态机 / cron / team 语义 | `workMate/AionCore/crates/aionui-session|aionui-cron|aionui-team`（Rust，抄设计不抄代码）+ `workMate/AionUi`（API 契约） |
| 桌面壳 / sidecar 管理 / SSE | `workMate/eigent/electron/main/init.ts`、`workMate/AionUi/packages/web-host/src/backend-launcher.ts` |
| 能力治理 / grant / skill 版本 | `workMate/openwork/ee/packages/den-db/src/schema/sharables/`（config_object + grant 表族） |
| 权限确认 / 工具元数据 | `workMate/AionUi`（四选项契约）、`workMate/ZCode/apps/zcode-cli/packages/core/src/tool/`（ToolMetadata + 调度） |
| 分包纪律 / 文档策略 / "如何做到简单" | `workMate/pi/`（how-pi-works.md、types.ts 契约注释、递进示例） |
| 审计哈希链 / 教学 | `workMate/learn-workbuddy/s23_audit_sandbox/` |
| 桌面壳+引擎集成 / 事件流传输 / 记忆治理对照 / 沙盒分层 | `workMate/EasyMint`（借鉴清单：docs/architecture/reference-easymint.md） |

---

## 8. 下一步

模块深潜（每模块一文档，放入本 docs 目录），建议顺序：
1. **Harness + Session 事件模型**（地基：循环骨架、事件枚举、投影表、恢复协议）
2. Memory 系统详设（hermes 机制裁剪定稿）
3. 权限治理详设（表结构 + grant 解析 + 审批流）
4. 定时任务 + 轨迹审计 Agent
5. 桌面壳与前端事件流
6. 多 Agent（M5 加时赛时再深潜）

> 约束重申：设计先行，未拍板不写代码；前端由 AI 全权生成维护；用户只做设计决策与验收。
