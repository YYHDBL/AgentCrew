# 事件契约（全量汇总）

> 维护者：后端 Agent ｜ 权威定义在各详设；新增机制**先加事件类型再实现**（纪律）。
> **SSE 语义（v1.1）**：载荷 = `{global_seq, task_run_id?, seq?, type, payload, ts}`；游标（from/after_seq）**排他**；控制帧：`event:resync`（溢出终止，data 含最后连续 global_seq）、`event:shutdown`（优雅关闭，客户端存游标）、`event:ping` 心跳；FSM 快照带 `at_global_seq` 配对续播。

## M0 帧与 payload schema

以下字段定义对应 C8 运行时。`?` 表示字段可以省略；允许 JSON `null` 的字段明确写出 `null`，省略与 `null` 分别处理。`integer` 为整数，`object` 为 JSON 对象，`T[]` 为数组。未知附加字段允许保留，已列字段的名称和类型保持兼容。事件由执行层发射，投影表消费同一载荷。

普通帧的必填字段为 `global_seq: integer`、`type: string`、`payload: object`、`ts: string`。`global_seq` 为已提交事件的全局递增位置，`ts` 为 ISO 8601 时间。任务事件携带 `task_run_id: string` 与 `seq: integer`；会话域事件省略这两个字段。`attempt_no?: integer` 为运行尝试编号。任务内 `seq` 和会话级 `global_seq` 分别用于对应端点，不能互换。

| SSE 控制帧 | data schema | 客户端行为 |
|---|---|---|
| `resync` | `{last_continuous_global_seq: integer, reason: "slow_consumer", task_run_id?: string, seq?: integer}` | 服务端结束当前流；客户端立即重新获取 state 与完整历史，完成后从新水位排他续播。恢复请求失败后按连接失败规则退避。 |
| `shutdown` | `{last_continuous_global_seq: integer, reason: "server_shutdown", task_run_id?: string, seq?: integer}` | 保存已连续消费的游标，结束当前订阅；重新连接时先获取快照并配对历史。 |
| `ping` | 无业务载荷 | 只维持连接，不修改游标或业务状态。当前服务使用 SSE 注释心跳；客户端同样忽略命名 `ping` 帧。 |

首次加载、普通断线重连及 resync 恢复均先获取 state 的 `at_global_seq`。当前 state 只含 FSM 与队列，聊天和过程由会话历史补齐至该水位，包含无 `task_run_id` 的会话域事件；历史恢复完成后，以该水位订阅实时流并跳过 `global_seq <= at_global_seq` 的帧。恢复期间保留已有画面。连接失败按 1、2、4、8、16、30 秒退避，每次重新经 preload 获取端口与 token。

### 任务、队列与模型

| 事件 | payload schema | 消费语义 |
|---|---|---|
| `run.queued` | `{instruction: string, client_request_id?: string \| null, cron_job_id?: string \| null, queue_item_id?: string}` | 用户指令全文。存在 `queue_item_id` 时，该消息已由入队事件建立，避免重复呈现。 |
| `run.started` | `{attempt_no: integer, attempt_id: string, kind?: "initial", context_fingerprint?: object}` | 开始首次运行尝试。fingerprint 包含 `system_prompt_hash`、`tools_schema_hash` 和 `model_config`。 |
| `run.completed` | `{final_text?: string, outcome?: string}` | 任务成功终态；C8 发射最终回复全文。 |
| `run.failed` | `{reason: string}` | 任务失败终态，与单次模型请求失败分别呈现。 |
| `run.cancelled` | `{}` | 用户停止任务的终态。 |
| `run.interrupted` | `{reason?: string}` | 中断终态，关闭本次运行的待操作卡片。 |
| `run.resumed` | `{attempt_no: integer, attempt_id: string, resume_reason: string}` | 恢复尝试，执行与核验界面由 C9 负责。 |
| `queue.item_enqueued` | `{item_id: string, text: string, client_request_id?: string \| null}` | 会话域事件，保存用户发送记录；state 查询将队列条目标识映射为 `queue[].id`。 |
| `queue.item_cancelled` | `{item_ids: string[]}` | 删除等待执行的条目，用户发送记录保留。 |
| `queue.paused` / `queue.resumed` | `{}` | 暂停或继续队列；合法动作读取 state 的能力字段。 |
| `step.started` | `{step_id: string, ordinal: integer, model_slot?: string}` | 建立步骤，`step_id` 用于关联同一步骤中的请求重试。 |
| `step.completed` | `{step_id: string, input_tokens?: integer, output_tokens?: integer, latency_ms?: integer}` | 模型回合完成；后续工具事件独立表示工具执行状态。 |
| `llm.request_started` | `{llm_call_id: string, step_id: string, model: string, model_slot?: string, retry_no?: integer, tool_declarations?: object[]}` | 每次请求尝试有独立 `llm_call_id`；同一步骤重试保留 `step_id`，model_slot记录实际使用的main或aux配置槽。M3起保存本次实际工具声明，历史缺失单独标明。 |
| `llm.request_done` | `{llm_call_id: string, step_id: string, text: string, tool_uses: ToolUse[], thinking_blocks: ThinkingBlock[], prompt_tokens: integer|null, completion_tokens: integer|null, usage_received?: boolean, latency_ms: integer, stop_reason: string \| null}` | C8 完整回复。`tool_uses=[]` 表示最终文本回合；包含工具调用的回合继续执行工具，文本保留在事件中。M3起缺失usage保存null。 |
| `llm.request_failed` | `{llm_call_id: string, step_id: string, error: string, retry_no: integer}` | `error` 含错误分类与说明。该请求结束，后续允许同一步骤重试；后续成功应显示已恢复。 |

`ToolUse = {id: string, name: string, input: object}`；`ThinkingBlock = {text: string, signature: string}`。C8 的 `llm.request_done` 总是携带数组，空数组和空字符串也保留。文本 delta 当前未公开为会话事件；D2 逐字呈现读取持久化全文，历史恢复立即呈现全文。

### 工具、审批、提问与材料

| 事件 | payload schema | 消费语义 |
|---|---|---|
| `tool.prepared` | `{call_id: string, tool_name: string, side_effect_class: string, input_hash: string, risk_level: string, input: object, read_only_verdict?: boolean, verdict_reason?: string, content_sha256?: string, step_id?: string, tool_call_id?: string}` | 建立调用记录；`side_effect_class` 为 `verifiable`、`external_idempotency` 或 `outcome_unknown`。`write_file` 正常准备时附带内容哈希，参数校验失败时可缺少判定附加字段。 |
| `tool.dispatched` | `{call_id: string}` | 工具实际开始执行。 |
| `tool.completed` | `{call_id: string, output: string, output_summary: string, details?: object, artifact_path?: string}` | 执行成功；`output` 为完整内联输出，超限输出由工件指针恢复，`output_summary` 用于界面摘要。 |
| `tool.failed` | `{call_id: string, error: string, output_summary: string, output?: string, details?: object, artifact_path?: string}` | 校验、权限或执行失败。执行前失败可省略 `output`，摘要为空字符串。 |
| `tool.skipped_idempotent` | `{call_id: string, note?: string}` | 供应商确认幂等命中。 |
| `tool.pending_verification` | `{call_id: string}` | 调用结果需要核验，执行恢复由 C9 负责。 |
| `tool.verification_submitted` | `{call_id: string, verdict: "confirmed_executed" \| "confirmed_not_executed", note?: string, actor?: string}` | 保存核验决定。 |
| `permission.requested` | `{tool_call_id: string, tool: string, risk: string, options: Decision[], input_hash: string, target: string, always_scope_preview: string}` | 使用 `tool_call_id` 关联工具 `call_id`；呈现持续授权范围，决定请求携带原始 `input_hash`。 |
| `permission.resolved` | `{tool_call_id: string, decision: Decision}` | 关闭对应审批卡；任务终态同样关闭过期卡片。 |
| `question.requested` | `{request_id: string, question: string, options?: string[]}` | 建立待回答卡片，关联键为 `request_id`。 |
| `question.answered` | `{request_id: string, answer: string \| null}` | 关闭对应卡片；`null` 表示用户取消回答。 |
| `materials.imported` | `{files: ImportedFile[], folders?: ImportedFolder[]}` | 保存逐项导入与授权结果，部分失败仍保留成功项。 |
| `conversation.updated` | `{title?: string}` | 更新会话元数据。 |
| `artifact.created` | `{artifact_id: string, task_run_id: string, tool_call_id?: string, path: string, name: string, ext?: string}` | 产物进入生成状态。 |
| `artifact.ready` | `{artifact_id: string, size_bytes?: integer}` | 产物生成完成。 |
| `artifact.failed` | `{artifact_id: string, error?: string}` | 产物生成失败。 |
| `artifact.missing_detected` | `{artifact_id: string}` | 产物查询发现文件缺失。 |

`Decision` 为 `allow_once`、`allow_always`、`reject_once` 或 `reject_always`。`ImportedFile = {original_path: string, stored_name: string, size_bytes: integer | null, error: string | null}`；`ImportedFolder = {path: string, error: string | null}`。审批事件的 `tool`、工具准备事件的 `tool_name` 分别使用自己的字段名；身份关联使用上述标识字段。

核对实现：`agentcrew_core/events/__init__.py` 的 `Event.as_frame`、`agentcrew_core/loop/__init__.py`、`agentcrew_core/tools/scheduler.py` 与 `builtin/`、`agentcrew_server/approvals.py`、`questions.py`、`sessions.py`，投影消费规则见 `agentcrew_server/db/projections.py`。新增或修改字段时同步维护此契约。

## 任务生命周期（harness-session §3）
`run.queued / run.started / run.completed / run.failed / run.cancelled / run.interrupted / run.resumed`

## 排队控制（v1.4，F006）
`queue.paused`（用户停止后队列进入暂停，不自动接续）/ `queue.resumed`（用户点继续，队首启动）/ `queue.item_enqueued`（payload: item_id/text/client_request_id?——入队即写 messages 发送记录，C7 落地补位）/ `queue.item_cancelled`（取消排队指令，payload 含 item_ids，发送记录保留）。queue.* 是**会话域**事件：task_run_id 可空（无任务锚点，帧中省略 task_run_id/seq）。

## 回合与模型（harness-session §3）
`step.started / step.completed / llm.request_started / llm.request_done（含全文+usage）/ llm.request_failed（含错误分类、retry_no）`

## 工具（harness-session §3，v1.2/v1.3 语义）
`tool.prepared / tool.dispatched / tool.completed / tool.failed / tool.skipped_idempotent（供应商侧去重确认）/ tool.pending_verification（结果不明暂停待人工）`

## 核验提交（v1.4，F003）
`tool.verification_submitted`（payload: verdict=confirmed_executed|confirmed_not_executed、note、actor；写审计链）

## 权限与人机（harness-session §3 + governance）
`permission.requested（四选项 + input_hash + always_scope_preview——"始终允许"将保存的范围预览，F005）/ permission.resolved / question.requested / question.answered`

## 产物（v1.4，F004）
`artifact.created（generating）/ artifact.ready（含 path/size）/ artifact.failed / artifact.missing_detected（探测发现文件缺失，卡片转缺失态）`

## 材料与任务（v1.4，F001）
`materials.imported`（payload: 逐文件结果 original_path/stored_name/error + folders 逐项结果 path/error——外审回稿：逐项结果随事件持久化，创建响应与幂等重放同源读回；部分失败仍创建任务）/ `conversation.updated`（标题由首条指令生成等元数据变化）

## M1 记忆、技能、后台与上下文

M1 的会话后台事件没有前台 `task_run_id/seq`；来源任务放入 payload 的 `source_task_run_id`。人工管理及启动治理事件允许没有 conversation_id，使用 payload.scope 定义当前 owner/workspace/agent 范围；会话事件流只返回所属会话，记忆管理流只返回当前身份可读范围。所有事件沿用提交后的全局水位和排他续播，不修改前台任务状态及等待计数。M1-02 持久化与事件写入同事务，M1-11 实际验证 HTTP/SSE，M1-12 验证界面重放。

`GET /api/memory/stream` 使用实际已登记的 workspace_id/agent_id：USER 变更属于当前所有者共享范围，workspace 变更向同工作区发送，soul、Skill、后台作业及会话上下文事件同时匹配工作区和员工。订阅注册后固定已提交 head，补播到 head 后按排他 global_seq 接收实时事件；范围过滤之前不向客户端发送正文或依据。新上下文预算与压缩事件由 EventStore 从真实会话记录补充 scope；旧上下文事件补播读取关联会话范围，不修改历史载荷。

`MemoryScope = {owner_id: string, workspace_id: string | null, agent_id: string | null}`。`ChangePayload = {change_id: string, ledger_id: integer, store_type: "user" | "workspace" | "soul" | "skill", store_id: string, entry_id: string | null, entry_hash: string | null, revision: integer, action: string, summary: string, scope: MemoryScope, source_task_run_id: string | null, job_id: string | null}`。摘要最多200字符并执行凭据脱敏；正文及 metadata 的完整前后状态由账本保存。

| 事件 | 必填 payload | 实施与消费 |
|---|---|---|
| `memory.updated` | ChangePayload | M1-02 的创建、编辑、固定、恢复、审核和账本恢复；通知引用 change_id，重播不重复显示同一变更 |
| `memory.archived` | ChangePayload，加 `archive_path: string` | M1-02/M1-10 的完整归档，保持原材料及恢复身份 |
| `skill.patched` | ChangePayload，加 `name: string, description: string, files: string[]` | M1-05，description≤60字符，索引及正文使用分别处理 |
| `memory.snapshot_created` | `{snapshot_id: string, conversation_id: string, snapshot_sha256: string, stores: object[], scope: MemoryScope}` | M1-03，同会话首次执行唯一；stores 保存身份、修订和校验值 |
| `memory.job_status` | `{job_id: string, kind: string, status: string, scope: MemoryScope, trigger_global_seq: integer, source_task_run_id: string \| null, model: string \| null, config_version: string, usage: {input_tokens: integer, output_tokens: integer}, error: string \| null}` | M1-06/M1-09，状态使用 OpenAPI MemoryJob 枚举；取消完成才提交 cancelled，错误先脱敏 |
| `memory.summary_created` | `{job_id: string, source_task_run_id: string, summary_id: string, summary: string, scope: MemoryScope}` | M1-06，summary≤200字符，与唯一任务摘要同事务 |
| `memory.approval_requested` | `{job_id: string, approval_id: string, call_id: string, tool: string, input: object, input_hash: string, scope: MemoryScope}` | M1-09，独立作业身份；不创建前台等待计数 |
| `memory.approval_resolved` | `{job_id: string, approval_id: string, decision: "allow_once" \| "reject_once" \| "expired", actor: string, scope: MemoryScope}` | M1-09，终止作业先让旧审批失效；同决定重试读取首次结果 |
| `memory.curated` | `{job_id: string, report: object, last_curate_at: string, scope: MemoryScope}` | M1-10，完整成功后更新治理水位，报告包含检查/陈旧/归档/豁免标识及原因 |
| `context.budget_checked` | `{model: string, context_window: integer, counting_method: string, tokenizer_revision: string, system_tokens: integer, history_tokens: integer, tool_result_tokens: integer, output_reserved: integer, total_input_tokens: integer}` | M1-07，模型请求前记录完整请求计数和输出预算 |
| `tool.result_externalized` | `{call_id: string, artifact_path: string, sha256: string, size_bytes: integer, prefix_bytes: integer, source_global_seq: integer}` | M1-07/M1-08，完整结果裁剪前保存，prefix_bytes≤2048，工件随任务保留 |
| `context.compacted` | `{checkpoint_id: string, snapshot_id: string, source_global_seq: integer, replaced_from_global_seq: integer, replaced_to_global_seq: integer, before_tokens: integer, after_tokens: integer, summarized_messages: integer, template_version: string, checkpoint_sha256: string}` | M1-08，事件与摘要/保留消息检查点同事务；恢复只重放检查点后续事件 |

后台模型请求和工具明细保存为独立作业记录，使用 job_id/call_id 和来源 global_seq 查询。新前台消息两秒内取消 aux、审批和流，已准备变更遵循 [ADR-011](../decisions/ADR-011-memory-lifecycle.md) 的恢复协议。M1-13 核对记录数量、文件 SHA/mtime、副作用次数和两级审计。

## 治理（M2，governance §6）

治理事件由业务服务与审计在同一写通道事务追加，提交后按 global_seq 发布。组织事件没有 task_run_id/seq；其 resource_id、change_id 与 audit_seq 直接关联权威业务记录。`GovernanceScope = {org_id: string, workspace_id: string | null, agent_id: string | null, owner_id: string | null}`。scope 在写入时由真实关联生成。owner 可查看本组织，admin 限登记工作区，member 限自身可见员工及自身 actor 记录；组织角色事件仅 owner 和受影响成员可读。过滤在发送正文之前执行，补播和实时推送逐次核查当前权限，撤权后的 SSE 在心跳复查或新事件前关闭。

`GovernanceChange = {change_id: string, resource_type: string, resource_id: string, revision: integer, actor_id: string, credential_owner_id: string, audit_seq: integer, scope: GovernanceScope}`。载荷不包含凭据、identity_token、连接器认证头或 Skill 正文；正文通过已鉴权资源 API 读取。新增字段先按本契约登记，再实施；客户端忽略未知事件类型。

| 事件 | payload schema | 实施及影响 |
|---|---|---|
| `governance.resource_changed` | GovernanceChange，加 `status: "active" \| "disabled" \| "archived"` | M2-02/11，更新真实资源与修订，禁用时取消相关调用 |
| `governance.identity_changed` | GovernanceChange，加 `effective_user_id: string` | M2-03，演示标识签发审计；不改变其他请求身份 |
| `governance.role_changed` | GovernanceChange，加 `user_id: string, role: "owner" \| "admin" \| "member", status: "active" \| "disabled"` | M2-03，重新查询当前权限并结束无权订阅 |
| `governance.grant_changed` | GovernanceChange，加 `grantee_type: "user" \| "agent", grantee_id: string, revoked_at: string \| null, capability_type: string, capability_id: string, revocation_changed?: boolean` | M2-05，实际撤销后重新组装工具与索引，已派发调用取消并核验；重复撤销旧记录的revocation_changed=false保留审计，不触发新Grant的执行取消 |
| `governance.rule_changed` | GovernanceChange，加 `agent_id: string, tool_name: string, effect: "allow" \| "deny", revoked_at: string \| null`；撤销带 `revocation_changed: boolean`，永久审批带 `source_task_run_id/source_call_id` | M2-07，员工规则查询刷新，deny 优先；无实际撤销变化保持任务状态，永久拒绝决定保留当前审批任务的正常收尾 |
| `governance.skill_version_published` | GovernanceChange，加 `version_id: string, version_no: integer, ledger_id: integer, sha256: string, status: string` | M2-04/11，change_id关联M1 skill.patched；恢复正文发布新版本，当前禁用或归档状态触发执行取消及副作用核验 |
| `governance.authorization_checked` | `{scope: GovernanceScope, actor_id: string, agent_id: string, allowed: boolean, reason: string, authorization_sha256: string, call_id?: string}` | M2-05/09，任务或尝试关联时保留其 task_run_id，明确当前有效权限 |
| `governance.audit_verified` | GovernanceChange，加 `internal: object, anchor: object` | M2-10，仅可信链可追加成功验证；失败切入只读诊断，原断点保留 |
| `governance.backup_created` | GovernanceChange，加 `kind: "database" \| "directory"` | M2-10，自洽快照和文件校验完成后，与审计及幂等记录同事务提交；恢复请求保存独立持久化计划，诊断期间禁止追加损坏审计链 |
| `governance.backup_restored` | GovernanceChange，加 `preserved_path: string` | M2-10，启动恢复完成且完整审计校验通过后，按恢复change_id幂等追加真实操作者与保留材料位置；诊断期间不写入损坏链 |
| `governance.access_denied` | GovernanceChange，加 `method: string, code: string, reason: string` | M2-10，已认证请求的角色或范围拒绝与审计同事务，包含真实凭证身份及有效身份，禁止记录认证头和请求正文 |

治理拒绝使用现有工具失败事件及审计 action=permission.denied。审批请求/决定增加有效 actor、真实 credential_owner_id、agent_id、resource_id 和 authorization_sha256；相同决定重试返回首次记录，失去活动执行方的旧卡返回409并展示失效。run.started/resumed 的 context_fingerprint 增加任务配置修订、skill_versions 和 authorization_sha256，恢复不改写历史尝试。

连接器tool.prepared附加connector_id及connector_revision，工具目录绑定相同配置修订；审批和dispatched登记在同一写通道内核对当前修订，实际HTTP/MCP传输继续核对当前Grant与该次绑定修订。服务端认证头和凭据不进入输入、目录、事件或产物，HTTP响应正文及派生元数据执行凭据脱敏。明确承诺external_idempotency的写入端点禁止重定向；普通HTTP/MCP写入保持outcome_unknown。stdio传输使用官方SDK、固定环境白名单和Seatbelt，服务退出时清理整个进程组。

## M3 自动化、模型审查及生命周期

作业域事件使用既有 run_events 的 global_seq 和 scope，未关联任务时省略 task_run_id/seq/conversation_id。已关联任务时保留真实任务身份及任务内 seq，attempt_no来自实际尝试；通知通过独立 notification_id 关联。M2安全审计 audit_seq 与模型报告 report_id 分别保存。

`CronPayload = {job_id: string, occurrence_id: string, revision: integer, trigger: scheduled|manual, scheduled_at: integer|null, triggered_at: integer, source_task_run_id: string|null, retry_no: integer, status: string, reason: string|null, scope: GovernanceScope, notification_id: string|null, audit_seq: integer}`。时间为UTC Unix毫秒；手动发生保留client_request_id。各次重试保持同一occurrence_id。

| 事件 | 必填载荷与附加字段 | 归属及消费 |
|---|---|---|
| `cron.job_changed` | `{job_id, revision, change_id, enabled, deleted_at, scope, audit_seq}` | M3-03，修改与审计同事务，旧定时器复查revision |
| `cron.job_fired` | CronPayload | M3-04/05，提交发生及任务关联后派发，重试不重复初始计数 |
| `cron.job_missed` | CronPayload，加`missed_count: integer, missed_through: integer` | M3-04，覆盖错过范围，不创建执行任务 |
| `cron.job_skipped` | CronPayload | M3-04/05，冲突、暂停、等待人工或待核验，无排队副作用 |
| `cron.job_failed` | CronPayload，加`retry_at: integer|null` | M3-05/06，当前权限拒绝、未知副作用等不得设置重试 |
| `cron.job_status` | CronPayload，加`retry_at: integer|null` | M3-05/06，completed/interrupted/pending_verification/retry_wait/cancelled真实收敛 |
| `cron.proposal_requested` | `{proposal_id, call_id, input_hash, revision, source_task_run_id, scope}` | M3-07，不可变提案通过鉴权API读取，必经真人决定 |
| `cron.proposal_resolved` | `{proposal_id, call_id, decision, selected, input_hash, revision, actor_id, job_id, scope, audit_seq}` | M3-07，批准绑定选择与计划；过期decision=expired |
| `review.job_status` | `{job_id, kind, status, source_task_run_id, attempt_no, trigger_global_seq, model, config_version, reason, report_id, skill_id, usage, scope}` | M3-08/09，作业域事件，状态见TraceJob；无报告保存实际skipped原因 |
| `audit.reported` | `{report_id, job_id, source_task_run_id, attempt_no, source_global_seq, model, root_cause_event, scope}` | M3-08，有有效真实报告才发送，引用必须属于输入任务、尝试及水位 |
| `notification.created` | `{notification_id, source_global_seq, job_id, source_task_run_id, severity, scope}` | M3-14，与领域终态同事务创建，持久唯一 |
| `notification.updated` | `{notification_id, read_at, presented_at, scope}` | M3-14，独立已读与认领，重放不重复弹出 |
| `runtime.power_changed` | `{event: suspend|resume, observed_at: string, source: electron_power_monitor, scope}` | M3-14，记录实际macOS事件，resume重新核查到期 |

回放API返回展示投影，移除thinking_blocks、reasoning_content、认证值和隐藏推理；原始事实继续由服务器保留。事件精确引用为`{task_run_id, attempt_no, seq, global_seq}`，任务内seq和global_seq分别核对。报告水位之后的恢复或新事件不改变既有报告的适用尝试。
