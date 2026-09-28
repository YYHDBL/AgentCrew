# 02 · Harness 与 Session 事件模型详设

> 模块深潜 #1 ｜ 日期：2026-09-28 ｜ 状态：**已定稿（D1–D5 全部对齐）**
> 上游依据：[01-设计对齐纪要](./01-设计对齐纪要.md)。本文档粒度到"可以直接写代码"。

---

## 0. 本模块对齐记录（5 问）

| # | 决策点 | 结论 |
|---|---|---|
| D1 | 会话与任务的关系 | **两层**：会话（长期容器）→ 任务（每条指令一次执行）；AgentRun 表 M0 不建，事件表留 `agent_run_id` 空位，M5 启用 |
| D2 | 事件粒度 | **里程碑事件落盘，流式打字块只走内存总线**；完整回复全文存在"回复完成"事件里，信息无损 |
| D3 | 恢复语义 | **agent 自主恢复**：系统保证事实（事件重放重建上下文 + 副作用账本注入 + 幂等钥匙拦截），模型决定策略 |
| D4 | 会话状态机 | **四态 + 等待计数器**（AionCore 模式）：空闲/启动中/运行中/出错；等审批、等提问是运行中的计数，清零即回 |
| D5 | 同会话并发 | **排队**：一会话同时只跑一个任务，新指令入队自动接续；跨会话并行；随时可取消。插话改向（steering）记为后续增强 |

---

## 1. 概念模型

```
Conversation（会话，长期容器）
  ├─ 归属：workspace × agent（数字员工）
  ├─ 挂靠：长期记忆关联、定时任务（复用会话模式）
  ├─ Message[]（聊天记录投影：用户消息 + 小文最终回复）
  ├─ State（FSM：空闲/启动中/运行中/出错 + 等待计数）
  ├─ PendingQueue（指令排队，D5）
  └─ TaskRun[]（任务，每条用户指令一次）
        ├─ RunAttempt[]（执行尝试；恢复 = 新开 attempt，D3）
        ├─ RunEvent[]（唯一事实源，D2 里程碑级）
        └─ 投影：Step[] / LlmCall[] / ToolCall[]
```

**命名约定**（全代码库统一）：`Conversation`（会话）、`TaskRun`（任务）、`RunAttempt`（尝试）、`RunEvent`（事件）、`Step`（一个模型回合 = 一次模型请求 + 其工具调用批）、`ToolCall`（一次工具调用三态记录）。

---

## 2. 数据模型（SQLite，WAL）

### 2.1 核心表

**conversations**
```
id TEXT PK, workspace_id FK, agent_id FK,
title TEXT,                       -- 自动生成（aux 模型起标题）
status TEXT,                      -- active / archived
agent_spec_snapshot JSON,         -- 创建时数字员工配置快照（模型/skill/连接器/权限），后续改员工不影响本会话
pending_queue JSON,               -- 排队指令 [{id, text, enqueued_at}]
created_at, updated_at
```

**task_runs**
```
id TEXT PK, conversation_id FK,
instruction TEXT,                 -- 用户这条指令原文
status TEXT CHECK IN (queued, running, waiting_user, interrupted, completed, failed, cancelled),
current_attempt_no INT,
version INT,                      -- 乐观锁
cron_job_id TEXT NULL,            -- 定时任务派生时回链
created_at, updated_at, finished_at
```

**run_attempts**
```
id TEXT PK, task_run_id FK, attempt_no INT, UNIQUE(task_run_id, attempt_no),
kind TEXT CHECK IN (initial, resume),     -- 首次 / 恢复
status TEXT, outcome TEXT NULL,           -- 成功/失败/取消的终态描述
resume_reason TEXT NULL,                  -- kind=resume 时：崩溃/手动等
started_at, ended_at
```

**run_events（唯一事实源，append-only）**
```
global_seq INTEGER PRIMARY KEY AUTOINCREMENT,  -- 全局单调游标（会话级 SSE 用，v1.2）
id TEXT UNIQUE,
task_run_id FK, seq INT, UNIQUE(task_run_id, seq),   -- seq 会话内单调递增
conversation_id FK,                        -- 冗余，方便按会话拉流
agent_run_id TEXT NULL,                    -- M5 多 Agent 预留位
attempt_no INT,
type TEXT,                                 -- 事件类型枚举（见 §3）
payload JSON,
created_at
```

### 2.2 投影表（随事件写入同步更新；可由重放器全量重建）

**steps**：`id PK, task_run_id, ordinal, model_slot, input_tokens, output_tokens, latency_ms, status`

**llm_calls**：`id PK, step_id FK, model, prompt_tokens, completion_tokens, latency_ms, retry_no, error TEXT NULL`

**tool_calls**（副作用三态账本，恢复的关键）
```
id PK, call_id TEXT UNIQUE,        -- 每次调用的唯一身份（v1.1：合法重复调用不冲突）
task_run_id, step_id, tool_name,
side_effect_class CHECK IN (verifiable, external_idempotency, outcome_unknown),
input_hash TEXT,                  -- 完整参数哈希：审批与对账绑定不可变内容
status CHECK IN (prepared, dispatched, completed, outcome_unknown, pending_verification, failed),
risk_level, input JSON, output_summary TEXT, artifact_path TEXT NULL,  -- 大输出外部化指针
error TEXT NULL,
prepared_at, dispatched_at, completed_at
```

**messages**（聊天记录投影，Cowork 页直接读）
```
id PK, conversation_id, task_run_id NULL,
role CHECK IN (user, assistant),
content TEXT,                    -- 小文最终回复全文（流式块不落库，完成时一次性写入）
created_at
```

---

### 2.3 持久化契约（v1.2 新增——建表前的事实源分级协议）

| 层级 | 内容 | 规则 |
|---|---|---|
| **执行事件**（run_events） | 任务执行过程的一切事实 | append-only；**事件载荷必须携带重建模型上下文所需的全文**：`LLM_REQUEST_DONE` 含完整回复文本；`TOOL_COMPLETED` 含完整工具输出（内联上限 32KB，超出落 artifact 文件并在事件中留指针——**工件文件随任务存活，不得删除**，重建时按需读取）；用户指令原文入 `RUN_QUEUED` 载荷 |
| **投影表**（messages/steps/llm_calls/tool_calls） | 查询加速视图 | 全部可从执行事件 + 工件重建；messages 只存用户消息与最终回复属于**投影裁剪**，不是事实源 |
| **业务状态表**（grant/规则/角色/记忆/cron/连接器） | 各领域权威状态 | **不从 run_events 重建**，各有自己的账本（memory_ledger、审计链、cron_job_runs）；事件流里只有它们的变更事件 |
| **上下文恢复** | resume 的重建依据 | = 执行事件（全文 + 工件）；M1 压缩生效后，`CONTEXT_COMPACTED` 的摘要成为替代旧消息的持久内容 |

**会话级游标**：`global_seq`（事件表自增主键）跨任务稳定有序，供会话聚合 SSE 使用；任务内 `seq` 保持不变。

---

## 3. 事件类型枚举（M0 全集）

```python
class RunEventType(StrEnum):
    # 任务生命周期
    RUN_QUEUED          = "run.queued"            # 进入排队（D5）
    RUN_STARTED         = "run.started"           # 出队开始执行，attempt 创建
    RUN_COMPLETED       = "run.completed"
    RUN_FAILED          = "run.failed"
    RUN_CANCELLED       = "run.cancelled"
    RUN_INTERRUPTED     = "run.interrupted"       # 启动对账写入（D3）
    RUN_RESUMED         = "run.resumed"           # 恢复 = 新 attempt

    # 回合（Step）
    STEP_STARTED        = "step.started"
    STEP_COMPLETED      = "step.completed"

    # 模型交互
    LLM_REQUEST_STARTED = "llm.request_started"
    LLM_REQUEST_DONE    = "llm.request_done"      # 含 usage（token/耗时）；payload 有完整回复全文
    LLM_REQUEST_FAILED  = "llm.request_failed"    # 含错误分类、retry_no

    # 工具（三态账本，D3 恢复依据）
    TOOL_PREPARED       = "tool.prepared"         # 已生成幂等钥匙，尚未执行
    TOOL_DISPATCHED     = "tool.dispatched"       # 已开始执行（此后崩溃 = outcome_unknown）
    TOOL_COMPLETED      = "tool.completed"        # 含输出摘要 / artifact 指针
    TOOL_FAILED         = "tool.failed"
    TOOL_SKIPPED_IDEMPOTENT = "tool.skipped_idempotent"   # 外部幂等键命中：供应商侧确认已执行（v1.2 语义）
    TOOL_PENDING_VERIFICATION = "tool.pending_verification"  # 结果不明，暂停自动重试待人工核验（v1.1）

    # 权限与人机交互（喂 D4 的等待计数器）
    PERMISSION_REQUESTED  = "permission.requested"   # payload: tool_call_id, risk, options 四选项
    PERMISSION_RESOLVED   = "permission.resolved"    # payload: decision(allow_once/allow_always/reject_once/reject_always)
    QUESTION_REQUESTED    = "question.requested"     # 向用户提问（挂起任务等回答）
    QUESTION_ANSWERED     = "question.answered"

    # 上下文管理（M1 起启用，枚举先占位）
    CONTEXT_COMPACTED   = "context.compacted"
    TOOL_RESULT_EXTERNALIZED = "tool.result_externalized"
```

**纪律**：新增机制先加事件类型再写实现；投影表和前端都从事件推导，不改历史事件（append-only）。

---

## 4. 会话状态机（D4，AionCore 模式）

```python
class ConversationState:
    state: Literal["idle", "starting", "running", "error"]
    # 等待计数器：只在 running 下有意义，清零自动回普通 running
    waiting_approvals: int      # 挂起的审批数
    waiting_questions: int     # 挂起的提问数
    current_task_run_id: str | None
```

**迁移规则（纯函数 reducer，唯一入口 `reduce(state, event) -> state`）**

| 事件 | 迁移 |
|---|---|
| 指令出队 RUN_STARTED | idle → starting → running |
| PERMISSION_REQUESTED | running: waiting_approvals += 1 |
| PERMISSION_RESOLVED | running: waiting_approvals -= 1 |
| QUESTION_REQUESTED / ANSWERED | 同上，计数器增减 |
| RUN_COMPLETED/FAILED/CANCELLED | → idle（若队列非空自动出队下一个 → starting） |
| 致命错误（运行时崩溃） | → error（重启对账后由 INTERRUPTED 收敛） |

**能力真值表（前端直接调用）**

| 函数 | 条件 |
|---|---|
| `can_send` | state == idle |
| `can_queue` | state == running 且 waiting_approvals == 0 |
| `can_cancel` | state in (starting, running) |

排队语义（D5）：`can_send` 时指令立即开跑；`can_queue` 时进 `pending_queue`，当前任务终态后自动出队；`waiting_approvals > 0` 时两者皆否（先处理审批）。

---

## 5. Harness 主循环（ReAct）

```python
async def run_task(task: TaskRun, attempt: RunAttempt, ctx: RunContext):
    emit(RUN_STARTED)
    while True:
        emit(STEP_STARTED)
        resp = provider.stream(slot=MAIN, messages=ctx.messages, tools=registry.schemas())
            # LLM_REQUEST_STARTED / DONE / FAILED 事件在 provider 适配层发
        if resp.tool_calls:
            results = scheduler.execute(resp.tool_calls)   # 并发规则见 §6
            ctx.append_tool_results(results)
            emit(STEP_COMPLETED)
            continue                                          # 下一轮模型请求
        ctx.append_assistant(resp.text)                       # 同时写 messages 投影
        emit(STEP_COMPLETED); emit(RUN_COMPLETED)
        return
```

要点：
- **循环属于 agent，机制属于 harness**：权限门、幂等、外部化、压缩都是循环里的挂钩点，不改循环结构
- 回合上限（默认 40）与 token 预算守门，超限走 RUN_FAILED（带原因）
- 流式文本块经内存总线直推 SSE，**不落库**；LLM_REQUEST_DONE 事件 payload 携带全文
- M1 在循环中插入：compact 检查（模型请求前）、工具结果外部化（入上下文前）、记忆提炼 fork（回合结束后）

---

## 6. 工具系统

### 6.1 ToolMetadata（安全元数据，注册时声明）

```python
@dataclass(frozen=True)
class ToolMetadata:
    name: str
    description: str
    parameters: dict                    # JSON Schema
    read_only: bool
    destructive: bool
    risk_level: Literal["low", "medium", "high"]
    needs_approval: bool                # 默认 = not read_only（写操作审批，Q4）
    concurrent_safe: bool
    timeout_ms: int = 60_000
    max_output_bytes: int = 100_000     # 超限外部化（M1）
```

M0 内置工具：`read_file` / `write_file` / `list_dir` / `bash` / `http_request`。

**判定纪律（v1.2 收紧）**：
- bash 只读判定需**同时满足两条**：① 命令首词 ∈ 固定白名单 `ls / cat / head / tail / grep / wc / pwd / file / stat / du / diff`（**v1.2 移除 `find`——其 `-exec` 可执行任意命令**）；② **不含任何元字符或重定向**（`;` `&&` `||` `|` 反引号 `$( )` `>` `<` 换行）——任一命中即丧失只读资格，走审批。`python`、`awk`、`sed` 等可执行任意代码的一律不算只读
- 路径类匹配一律先 `realpath()` 规范化（解析符号链接）再比前缀
- `http_request` 目标域名受连接器配置 `allowed_hosts` 约束（M0 默认为空 = 全部需审批）

### 6.2 调度与并发规则

- 排序：destructive → 串行；read_only 或 concurrent_safe → 可并行；默认并发上限 4
- 每次调用流程：**生成幂等钥匙 → TOOL_PREPARED（落库）→ 权限门（needs_approval 则 PERMISSION_REQUESTED 挂起等待）→ TOOL_DISPATCHED → 执行 → TOOL_COMPLETED/FAILED**
- **调用身份与副作用三分类（v1.1 修订）**：每次调用有唯一 `call_id`；工具注册时声明 `side_effect_class`：
  - `verifiable`（可核验，如写文件——存在性/内容可检查）：恢复后自动核验，能确认则补 completed
  - `external_idempotency`（支持外部幂等键，如带 Idempotency-Key 的 HTTP API）：重试安全
  - `outcome_unknown`（结果不可核验，如发通知）：崩溃后 → `pending_verification`，**暂停自动重试**，Run Center 标"待核验"，人工确认后才继续（定时任务重试同样遵守）
  **恢复后的重发语义（v1.2 修订）**：resume 把历史调用及其结果重放进上下文（模型"看得见"已做过什么）；此后**模型新发出的任何调用都是新决定**——新 `call_id`、正常过闸门执行，**绝不因参数相同而自动跳过**（否则两次有意执行会被错误合并）。防重复副作用不靠跳过，靠分类语义：`external_idempotency` 工具的外部幂等键由 `(task_run_id, call_id)` 派生（v1.3 修订）——**同一次逻辑调用**的重试与核验后重执行复用同键（供应商侧去重兜底，命中发 TOOL_SKIPPED_IDEMPOTENT），而**同任务中两次合法的相同请求各有 call_id、各得各键，绝不合并**；`verifiable` 重执行天然安全且事后核验；`outcome_unknown` 反正处于待核验暂停态

---

## 7. 中断恢复协议（D3）

**启动对账**（FastAPI 启动时执行一次）：
1. 扫 `task_runs` 中 status ∈ (running, waiting_user) 的行
2. 全部写 `RUN_INTERRUPTED` 事件，status → interrupted（**不隐式重启任何任务**）
3. `tool_calls` 中 status = dispatched 但无终态的：`verifiable` 类先自动核验（可确认则补 completed）；无法核验的 → `pending_verification` **暂停任务自动重试**，待人工确认（§6.2 三分类，v1.1 修订）；副作用账本照常注入供模型参考"结果不明"清单

**用户点"恢复"**：
1. 新建 `RunAttempt(kind=resume, resume_reason=...)`
2. 从 `run_events` 重放重建完整消息上下文（事件载荷含回复全文与工具输出，超限部分读工件——见 §2.3 持久化契约）
3. 注入两段系统提示：副作用账本（"你已执行：整理了 1-30 号发票写入 half.xlsx；结果不明：调用过 send_mail"）+ 恢复指令（"任务中断于第 31 张，请决定如何继续"）
4. 幂等钥匙自动拦截重复副作用（§6.2）
5. 发 `RUN_RESUMED`，进入正常循环

---

## 8. Provider 适配层（自研薄层）

```python
class Provider(Protocol):
    async def stream(self, slot: Literal["main", "aux"], messages, tools) -> AsyncIterator[StreamEvent]
```

- M0 实现 GLM（主力 + flash 各一个配置）；`StreamEvent` 归一为：`text_delta / tool_call / usage / done / error`
- 工具调用格式归一为内部 `ToolCall{id, name, input}`（GLM/OpenAI/Anthropic 的差异封在本层——面试讲述点）
- aux 槽（flash）服务：压缩摘要、记忆提炼、轨迹审计、标题生成（M1+）
- 测试纪律（v1.3，用户确认**禁止 mock 与假测试**）：不设假 Provider——所有测试调用真实 GLM（控成本走 aux 槽），断言产物文件与数据库状态，不断言模型措辞
- 接入验证纪律（v1.2）：GLM 与 DeepSeek 的**流式 tool calls、usage 返回、错误码必须用真实 key 在接入首周验证**；DeepSeek 思考模式的 `reasoning_content` 按官方要求在后续请求中保留回传

---

## 9. Python 包结构（pi 式分层纪律）

```
agentcrew/
  backend/
    agentcrew_core/                 # 纯库：不 import FastAPI / sqlite3，全部依赖走 Port 接口
      events/        # RunEventType、payload 类型、reducer（会话 FSM 纯函数）
      loop/          # run_task 主循环、回合守门
      tools/         # ToolMetadata、registry、scheduler、幂等钥匙
      provider/      # Provider 协议、StreamEvent、GLM 适配、FauxProvider
      recovery/      # 对账、重放器、resume 组装
      ports/         # EventStore / Clock / AuditWriter 等接口定义
    agentcrew_server/               # FastAPI 应用：实现 core 的 Port
      db/            # SQLite 落盘（事件表 + 投影表 + 迁移）
      run_manager/   # asyncio 任务管理、事件总线（内存扇出 + 落盘）
      api/           # REST + SSE 路由
      seed/          # 种子组织数据
    tests/
  desktop/                          # Electron 壳 + React（AI 生成维护）
  docs/
```

**纪律**：core 单测不碰网络不碰盘（FauxProvider + 内存 EventStore）；server 只做装配。

## 10. API 面（M0）

```
POST /api/conversations                      创建会话（绑数字员工快照）
GET  /api/conversations                      列表
GET  /api/conversations/:id/messages         聊天记录（分页）
POST /api/conversations/:id/instructions     发指令（can_send 直接跑 / can_queue 入队）
POST /api/task-runs/:id/cancel               取消
POST /api/task-runs/:id/resume               恢复（D3）
GET  /api/task-runs/:id/events?from=seq      SSE 事件流（from=0 即重放）
POST /api/tool-approvals/:id                 审批决定（四选项）
GET  /api/conversations/:id/state            FSM 快照（can_send/can_queue/can_cancel）
```

## 11. 验收与测试

- **M0 验收**：能对话调工具、过程实时可见、刷新页面事件重放恢复界面、杀进程重启后会话可恢复、同会话指令排队自动接续
- 测试四类：reducer 等自有纯函数的参数化单测（状态迁移表全覆盖——构造输入测自有代码，不属于 mock）；真实 GLM 端到端（aux 槽控成本，断言库与产物状态）；故障注入（真实 kill 进程于第 N 条事件后 → 恢复 → 断言终态与幂等）；投影重建一致性（真实事件序列 → 重放 → 比对投影表）
- **测试哲学（v1.3 修订，覆盖 v1.1 双层方案——用户最终确认禁止 mock/假测试）**：外部系统一律真实——真实 GLM、真实文件产物、真实进程中断；自有纯函数（reducer/调度计算/权限判定）的参数化单测正常保留

## 12. 参考

- 事件三件套与对账：`workMate/eigent/backend/app/run_journal/`（store.py / models.py）
- 四态 FSM 与计数器：`workMate/AionCore/crates/aionui-session/src/state.rs`、`reducer.rs`
- 排队语义与恢复即接口：`workMate/AionUi/packages/desktop/src/common/adapter/ipcBridge.ts`（ensureRuntime / queue）
- 工具元数据与调度：`workMate/ZCode/apps/zcode-cli/packages/core/src/tool/`（types.ts / scheduler.ts）
- 离线确定性测试：`workMate/learn-workbuddy/mini_workbuddy/providers.py`
