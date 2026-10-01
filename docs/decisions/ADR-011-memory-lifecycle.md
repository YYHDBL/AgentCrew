# ADR-011 · M1 记忆持久化、后台生命周期与上下文恢复

状态：已接受。日期：2026-10-01，Asia/Shanghai。依据为 M1-01、Memory 详设和所有者的完整实施要求；M0 收口记录见 [所有者确认](../acceptance/M0-owner-confirmation.md)。本决策定义后续卡片共用的契约，业务实现随各卡片交付。

## 条目、身份与权限

统一库身份为 `(store_type, store_id)`，`store_type` 为 `user/workspace/soul/skill`。USER 的 `store_id=owner`，工作区 MEMORY 使用真实工作区标识，soul 使用员工标识，Skill 使用服务分配的标识并记录工作区、员工和名称。M1 按现有 owner 身份、会话工作区及员工核对范围；M2 的角色、Grant 和不可变 Skill 版本依照 M2 卡片实施。

条目的稳定身份为服务生成的 `entry_id`，同时暴露 `entry_hash=SHA-256(首个非空正文行的 UTF-8)`。首行不包含分隔符，不修剪正文空格，换行统一为 LF；同库重复哈希返回 `ENTRY_HASH_CONFLICT`。编辑首行时保留 `entry_id` 并更新哈希和 metadata 映射。`revision` 为整库单调整数，每次正文、支撑文件或业务 metadata 变更递增；命中统计单独存储，不改变正文修订号。接口使用当前哈希定位和 `expected_revision` 校验，旧哈希返回对象不存在，旧修订返回 `REVISION_CONFLICT`。

正文使用 Markdown 库识别独立段落 `§` 作为条目分隔符，分隔符处于代码块时保留正文语义。字符配额以最终规范化 Markdown 文本的 Python `len()` 为准，包含分隔符和换行，归档条目不占用活动库配额。默认 USER/MEMORY/soul 配额为 1400/2200/2700。soul 由 aux 依据真实员工岗位生成，目标为配额的 45%–55%，生成失败直接报告，不生成固定成功正文。

source 保存 `actor_type/actor_id`、真实 `conversation_id/task_run_id/job_id` 或人工编辑标识；basis 为非空依据摘录。写入身份与来源工作区、员工须相符。财务、凭据、法律等高风险事实必须保持 `needs_review=true`，只有当前人类用户提交人工审核决定才可解除；模型不能更改审核状态。凭据正文拒绝保存。注入和检索均执行审核及投毒防护，管理读取保留原文以供审核。

## 文件与数据库的一致性

每库维护 `.md`、`.meta.json`，归档保留正文、metadata、Skill 支撑文件及原位置清单。归档放入 `data/agents/<actor-agent>/archive/<store_type>/<store_id>/<entry_id>/`；人工操作使用 `data/archive/<store_type>/<store_id>/<entry_id>/`。路径由服务派生，客户端不能提供任意绝对路径。Skill 支撑文件限定在技能目录，校验路径穿越和符号链接。

持久化意图 `memory_changes` 包含唯一 `change_id`、规范化输入哈希、身份、预期修订、所有受影响文件的前后字节及 SHA-256、正文和 metadata 的前后状态、动作、来源和状态。处理顺序为取得按库互斥，校验预期修订和文件校验值，在串行准备事务中再次校验修订并持久化 `prepared` 意图，再依次使用同目录临时文件写入、flush/fsync、原子替换、目录 fsync，随后经既有串行写通道在一个事务中提交账本、审计、事件、业务修订及 `committed` 标记，提交后发布事件。数据库唯一约束保证每库最多一条未完成意图，互斥覆盖准备到提交的完整过程。整个操作完成前该库读取返回 `STORE_RECOVERING`，禁止提供正文与 metadata 的混合版本。

启动先恢复未完成意图，再接收任务。每个文件只能处于意图中的旧状态或新状态；核对成功则完成剩余替换和唯一提交。出现任何第三种状态返回 `EXTERNAL_MODIFICATION`，保存原文件并禁止该库写入。已提交意图重放只读取首次结果。相同 `change_id` 的不同输入返回 `IDEMPOTENCY_CONFLICT`。取消到达后未准备的操作终止；已准备操作按该协议完成一致性提交，后台作业终止不会删除已提交内容。SQL 事务使用连接的事务上下文保障失败时撤销未提交事务，错误继续向调用方传播。

`memory_ledger` 保存不可修改的 before/after 正文、metadata 和支撑文件清单，`change_id` 唯一。恢复历史内容产生新的 `change_id` 和 `rollback` 动作，引用旧账本标识并保存新的前后状态。数据库触发器禁止更新或删除账本。审计、变更事件各携带 `change_id/ledger_id`，真实 SIGKILL 验收逐项核对恰好一次提交。

## 持久化对象及唯一约束

| 对象 | 字段与唯一性 | 交付卡片 |
|---|---|---|
| `memory_stores`、`memory_entries`、`memory_changes`、`memory_ledger` | 库身份及范围、修订、正文/metadata 校验值；条目稳定身份及首行哈希、来源、依据、审核、状态和使用统计；意图唯一 change_id；账本唯一 change_id，前后完整状态 | M1-02 |
| `memory_snapshots` | 唯一 conversation_id、snapshot_id、owner/workspace/agent、三库原文及过滤后的注入正文、各库修订与 SHA、Skill 索引及校验值、created_at；快照内容不可修改 | M1-03、M1-05 |
| `messages_fts`、`memory_fts` | trigram；关联消息 id 或 entry_id、来源修订；可重建索引，查询先限制身份范围 | M1-04 |
| `memory_skills`、`skill_reads`、`skill_references` | 工作区、员工、名称唯一；Skill 描述≤60 字符、正文修订和支撑文件清单；读取唯一执行身份/用户回合/Skill，保存读取修订；引用包含真实资源类型/标识、Skill、有效状态 | M1-05 |
| `memory_jobs`、`memory_job_calls`、`memory_job_approvals`、`session_summaries` | job_id、kind、来源事件水位、配置快照、状态、错误、usage、模型请求/工具记录；唯一触发键；审批绑定独立 job_id/call_id/input_hash；摘要 task_run_id 唯一、≤200 字符 | M1-06、M1-09 |
| `context_checkpoints` | checkpoint_id、conversation/task/attempt、source_global_seq、snapshot_id、摘要、保留消息及原事件映射、替换范围、工件 SHA、token 前后值、模板版本、内容 SHA；唯一压缩事件 global_seq | M1-08 |
| `memory_trigger_state`、`memory_counted_events`、`memory_governance` | 会话用户回合/工具迭代计数、已触发水位；事件 global_seq 唯一；last_curate_at、治理作业和完整报告 | M1-09、M1-10 |

数据库按实际交付逐张迁移，M1-01 只交付契约。执行事件继续进入 `run_events`；后台及人工管理事件可以没有 task_run_id，跨会话管理事件允许 conversation_id 为空，身份范围保存在 payload.scope，业务事件不会改变前台任务投影。全局自增水位保序，历史事件不修改。事件订阅在读取 head 前注册，补齐到 head 后排他续播，沿用已有背压和 shutdown 协议。

## 用户回合与后台作业

一个用户回合对应一次真实发送的指令。直发以不含 queue_item_id 的 `run.queued` 计数，排队以 `queue.item_enqueued` 计数，出队的 `run.queued` 不重复计数；相同 client_request_id 的重试沿用首次事件。每个含非空 tool_uses 的 `llm.request_done` 为一次工具迭代，同一模型响应的多个工具只计一次。水位与去重记录同事务持久化。每达到 10 个用户回合或 15 次工具迭代只创建一个相应提炼作业；任务结束、回复交付且会话空闲后启动，恢复和重复事件不能重复触发。

后台状态为 `queued/running/waiting_approval/completed/cancelled/interrupted/failed`。作业绑定真实会话和触发任务、独立 job_id、配置版本及 aux 模型。任务摘要以 run.completed 的任务标识唯一触发；新任务读取本会话最近五条已完成摘要，按创建时间和标识确定顺序，摘要占用历史预算。

后台提炼输入保留近期 24 条原始上下文消息和更早任务的单行摘要，完整 system/工具/输入计数≤aux 窗口 75%，其余预留输出。缺少早期摘要时记录缺失，不伪造模型摘要。唯一工具白名单为 `memory_write/skill_patch/session_search/read_file`；`skill_view` 是 skill_patch 的受控读取操作，由其 read 动作记录正文修订，保持白名单。read_file 沿用任务 scope 和内部文件保护，不注册 terminal/bash/write_file/http_request、治理或授权工具。

可选写入审批默认关闭；开启后 memory_write/skill_patch 在提交意图前请求后台专用审批，参数哈希与 job_id 绑定，只允许本次允许或本次拒绝。后台审批使用独立端点及事件，不复用前台 tool_calls 外键，不修改前台 waiting_approvals。接收前台指令（包括排队发送）时，立即停止发起新后台动作，取消模型流和等待审批，在两秒内等待取消完成；持久化意图按一致性协议完成恢复。作业失败保留错误和 usage，不改变已经 completed 的前台任务。重启将未终结作业转 interrupted，已提交摘要、记忆及触发水位保留。

## Token 依据、预算及检查点

模型槽新增 `context_window/context_window_source/tokenizer_repository/tokenizer_revision/tokenizer_sha256/prompt_format/max_tokens`。窗口来自供应商能力或官方模型配置，token 计数使用对应的成熟 tokenizer 与供应商消息模板，覆盖角色、system、工具 schema、参数、thinking、请求和结果结构。模型名称或协议变更必须重新核对来源，缺少确定依据返回 `TOKENIZER_UNAVAILABLE/MODEL_WINDOW_UNKNOWN`，拒绝请求。公开证据只包含模型、计数方法、版本及哈希。

当前 DeepSeek V4.1 Flash 的官方 [模型配置](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/config.json) 给出 max_position_embeddings=1048576；实际服务窗口还需核对 [OpenCode Go 模型 metadata](https://opencode.ai/zen/go/v1/models)。官方 [编码说明](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/encoding/README.md) 定义消息和工具模板；M1-07 使用发布的 tokenizer 及编码实现，验证完整请求与供应商 usage 的差异并记录确定性计数依据。模型仓库版本及文件 SHA 固定在配置，运行时不执行未经核对的远程代码。接口使用 [OpenCode Go 要求](https://opencode.ai/docs/go/#where-can-i-use-it) 的 User-Agent 及按会话稳定的 x-opencode-session，后台作业保留所属会话标识。

system 与工具声明最多占窗口 15%，历史含摘要分配窗口 60%，最新工具结果和输出保留至少 25%。工具结果从历史单独计数，历史工具请求/结果配对不拆散；所有输入加 max_tokens 必须≤实际窗口。输出预算超出保留区、system 超限或保护内容无法容纳时报告 `CONTEXT_BUDGET_EXCEEDED`，不调用模型。

大型工具结果超过工具 metadata 限额或现有 32KiB 内联阈值时，裁剪前完整写入工件并持久化 SHA-256、大小、来源 call_id，上下文保留 UTF-8 安全的前 2048 字节与可校验指针。禁止先进行 100000 字符裁剪再写入工件。工件跟随真实任务保留。

历史预算占用≥80% 时压缩，保护 system、最近任务指令、最近20条消息及完整工具请求/结果组合，保护边界跨越组合时向前扩展。旧结果先以已有完整工件替换；仍超过阈值时由 aux 生成历史任务、目标、约束偏好、已完成动作及工具名、当前状态五个字段的结构化摘要，已有摘要作为输入迭代更新。摘要、保留内容、来源水位、快照身份及 SHA 与 context.compacted 同事务提交。恢复校验最新有效检查点、冻结快照和工件，再读取后续事件及副作用账本，保留未回答与未执行调用的明确状态；不得重新填入全部压缩前历史。损坏的最新检查点明确报错，禁止悄悄使用未经验证的旧上下文。

## 遗忘与管理接口

确定性治理使用真实 UTC 时间：active 未使用14天转 stale，active/stale 未使用30天转 archived；没有使用记录时按 created_at 计算。pinned 和存在有效 Skill 引用的条目豁免；skill_references 由服务读取真实资源记录，M3 定时实现必须登记、撤销并复验引用。M1 的引用判定测试使用自有真实引用记录，不宣称存在定时服务。常驻快照和索引展示不更新 last_hit_at；显式检索和正文读取才增加 hits，注入另存独立统计。启动距成功治理≥7天且所有前台任务及辅助作业空闲时创建治理作业，前台到达后停止新写入，只有整个治理成功才更新 last_curate_at。

OpenAPI M1 操作均标注 `x-implementation-card=M1-11` 和实际领域卡片；三库与已有 Skill 使用 `/api/memory/stores/{store_type}/{store_id}`，条目操作使用稳定哈希和预期修订，管理页可查询原文、审核状态、来源、归档、历史和后台审批。新建 Skill 使用 `POST /api/memory/skills`，请求带真实 workspace_id/agent_id/name，服务分配 store_id 并在首次幂等结果中保存；已有 Skill 不能通过库端点重新创建。统一响应为 data/error 信封，分页上限200、游标排他并绑定作用域，写入使用唯一 change_id 保证幂等，治理返回202和真实 job_id。M1-11 对全部操作执行真实 HTTP 验收，M1-12 用真实 Electron 验证交互和刷新重放。
