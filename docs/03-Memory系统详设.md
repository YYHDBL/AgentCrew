# 03 · Memory 系统详设

> 模块深潜 #2 ｜ 日期：2026-09-28 ｜ 状态：**已定稿（M1–M3 对齐，其余技术细节直接设计）**
> 上游依据：[01-设计对齐纪要](./01-设计对齐纪要.md)（Q12 全项 Memory + hermes 蓝图）、[02-Harness与Session事件模型](./02-Harness与Session事件模型详设.md)（事件与循环挂钩点）
> 参考源码：`workMate/hermes-agent/`（Python，源码级）

---

## 0. 本模块对齐记录（3 问）

| # | 决策点 | 结论 |
|---|---|---|
| M1 | 记忆归属 | **三库制**：USER.md（全局画像）+ 工作区事实库（区内共享）+ **soul.md（员工私有灵魂**，入职生成、随经历成长） |
| M2 | 提炼信任边界 | **自动写 + 通知 + 管理页可干预 + 账本可回滚**；审批门为可选开关（默认关） |
| M3 | 遗忘规则 | **按使用遗忘**：命中计数 + 最后使用时间；14 天未用→陈旧，30 天→归档（永不真删）；确定性规则零成本；skill 同规则 |

---

## 1. 存储布局

```
data/
  USER.md                        # 全局用户画像（所有员工共享）
  workspaces/<ws-id>/MEMORY.md   # 工作区事实库（区内员工共享）
  agents/<agent-id>/soul.md      # 员工灵魂（私有）
  agents/<agent-id>/archive/     # 归档区（员工维度收纳本员工触发的归档）
  sidecar（与 md 同目录）:
  USER.meta.json / MEMORY.meta.json / soul.meta.json
    # 每条目: {entry_hash, hits, last_hit_at, state(active|stale|archived|pinned), created_at,
    #           source(task_run_id/conversation_id), basis(提炼依据摘录), needs_review(bool)}
    # v1.1：来源与依据必填——管理页能回答"这条记忆从哪来"；高风险事实 needs_review=true
```

- **条目格式**：markdown 文件内用 `§` 分隔条目（hermes 同款）；sidecar 按条目首行哈希对齐元数据
- **限额（默认，可配置）**：USER ≈ 1,400 字符；工作区 MEMORY ≈ 2,200 字符；soul ≈ 2,700 字符（入职生成的首版约占一半，留成长空间）
- **超限纪律**：`memory_write` 工具在超限时**拒绝写入并返回现有条目清单**，逼模型当场合并/删减再重试；每回合最多 3 次失败后返回"本次跳过保存"——记忆永不阻塞用户回复

## 2. 注入（冻结快照）

会话开始时把三库**冻结成快照**注入 system prompt（中途写盘不改 prompt，保 prefix cache）：

```
[USER（你的用户画像） 32% — 450/1,400 字]
…条目…

[WORKSPACE MEMORY（本工作区已知事实） 61% — 1,340/2,200 字]
…条目…

[SOUL（你是谁·小文的自我认知） 48% — 1,300/2,700 字]
…条目…
```

- 注入前过**防投毒扫描**：指令性模式检测（"ignore previous"/要求执行动作的句式），命中条目替换为 `[BLOCKED: 疑似注入]` 后注入，原文保留在盘上供用户处理
- **来源核验（v1.1）**：扫描挡不住"从错误文件/网页里提炼出的错误事实"——每条记忆带 source + basis（哪次任务、依据什么写入）；财务/凭据/法律等高风险模式的事实写入时标 `needs_review=true`，管理页显著提示，**人工过目后才参与注入**
- `session_search` 工具返回的检索结果**不冻结**（按需实时查，见 §6）

## 3. 提炼 fork（background review）

**触发**（确定性计数器，不做内容判断）：每 **10 个用户回合** → 记忆提炼；每 **15 次工具迭代** → skill 审查。回合结束、回复已交付后执行；**前台优先**——用户发新消息 2 秒内取消 fork。

**执行**：fork 一个 aux 槽（GLM flash）agent，重放本回合对话快照，只给白名单工具：`memory_write`（三库）/ `skill_patch` / `session_search`（只读）/ `read_file`（只读）。无 terminal、无写文件。

**三库路由**（写进 fork 的提示词）：

| 内容 | 去处 |
|---|---|
| 用户是谁、偏好、习惯（"喜欢简洁汇报"） | USER.md |
| 业务事实（"发票在 D:\download"、"报销走飞书"） | 工作区 MEMORY |
| 员工自我认知、风格、个人教训（"我做表格要先确认列顺序"） | soul.md |
| 可泛化的操作程序（"处理这批发票的流程是…"） | skill（程序性记忆） |

- 允许回答"Nothing to save"（不许硬凑）
- 写入成功 → 发事件 `memory.updated`（payload: store、条目摘要）→ 前端通知"💾 小文记住了：…"；改 skill → `skill.patched`
- 成本控制：fork 重放近期 24 条原文 + 更早回合的单行摘要；输入预算上限 75% 窗口

## 4. 会话记忆（Session 层）

- **任务摘要**：每个任务 RUN_COMPLETED 后，aux 模型生成 ≤200 字任务摘要，入 `session_summaries` 表（conversation_id, task_run_id, summary, created_at）
- **注入**：新任务开始时注入"本会话近期工作记录"（最近 5 条任务摘要）——这是 Working Memory 的一部分，计入预算
- 会话归档时摘要随会话可检索（§6），不进三库

## 5. Working Memory 与上下文管理

**预算表**（Context Budget，按模型窗口百分比）：

| 区块 | 预算 |
|---|---|
| system prompt（三库快照 + skill 索引 + 工具声明） | ≤ 15% |
| 对话历史（含任务摘要块） | 剩余的 60% |
| 工具结果 + 预留给模型输出 | ≥ 25% |

**压缩管线**（阈值：历史预算占用 ≥ 80% 触发，循环挂钩点=模型请求前）：
1. 保护头部（system + 最近 1 个任务指令）与尾部（最近 20 条消息）
2. 确定性裁剪先行：旧工具结果替换为占位符（`[工具输出已归档 artifact/xxx]`）→ 发 `tool.result_externalized`
3. 仍超预算 → aux 模型**结构化模板摘要**（历史任务/目标/约束偏好/已完成动作（含工具名）/当前状态），**迭代更新式**（已有摘要则改写而非重生成）
4. 发 `context.compacted`（payload: 压缩前后 token、被摘要条数）；原文全部保留在事件流，可回查

**工具结果外部化**（独立于压缩，工具完成时即判）：输出 > `max_output_bytes` → 落 `data/artifacts/<task_run_id>/` 文件，上下文只留前 2KB + 指针。

## 6. 检索：session_search 工具

- 只读工具，**零 LLM**：SQLite FTS5（`tokenize='trigram'`，中文子串可用，不自研分词）索引 messages 与三库条目
- 结果带来源（哪次任务/哪个库、时间），供小文"翻自己旧账"
- 表：`messages_fts`、`memory_fts`（写入时同步维护）

## 7. 遗忘治理（每周确定性 pass）

**触发**：后端启动时检查 `last_curate_at ≥ 7 天` 且系统空闲 → 执行（无需常驻定时器）。

规则（全确定性，零 LLM）：
1. `last_hit_at` 距今 ≥ 14 天且 state=active → **stale**（仍注入，标记待观察）
2. ≥ 30 天 → **archived**：条目移入对应 archive 区，**不再注入**；管理页可见、可恢复
3. `state=pinned`（用户钉住）永不迁移；被定时任务引用的 skill 免疫
4. 超限整合时优先淘汰：archived > stale > 低命中 active
5. 每次治理写 `memory.archived` 事件 + 治理报告入账本

## 8. Skill（程序性记忆，记忆侧）

- **三级渐进披露**：system prompt 常驻**只有索引**（名字 + ≤60 字符描述——超限部分永远不被路由）；`skill_view(name)` 载全文；`skill_view(name, file=references/…)` 载支撑文件
- **AI 自写/自改**：`skill_patch` 工具（create/patch/edit），**read-before-write 强制**（本回合必须先 skill_view 过）；变更走 append-only skill 账本
- **反熵增**（写进提示词）：skill 是"类级指令库"不是事件日志——必须可泛化 + 一句机理；禁止 PR 号/日期/一次性细节；同一教训只留一条；宁可扩展已有 skill 不建重复
- 遗忘同 §7；版本与授权（grant）归治理模块（docs/04）
- `/learn` 等价物：用户在会话里说"把这个流程存成技能"→ 走提炼 fork 的 skill 分支

## 9. 账本与回滚

表 `memory_ledger`（append-only，三库 + skill 共用）：
```
id PK, store_type(user|workspace|soul|skill), store_id,
action(create|update|archive|restore|pin|rollback),
before_text NULL, after_text NULL, actor(user|agent|curator|system),
task_run_id NULL, created_at
```

- 任何一次变更都可从账本回滚（恢复 before_text）；管理页提供时间线 + 回滚按钮
- soul 的每次演化都在账本里——"小文的成长记录"本身就是演示素材

## 10. 管理页（记忆管理，Cowork 侧栏 + 员工档案页）

三库分别的条目列表（含状态徽标：active/stale/archived/pinned、命中数、最后使用）；条目编辑/删除/钉住/恢复；归档区；账本时间线 + 回滚；soul 视图嵌在人事部（Agent Studio）员工档案里。

## 11. API 增量

```
GET    /api/memory/user | workspace/:id | agent/:id/soul     读库（含 sidecar 元数据）
PATCH  /api/memory/.../entries/:hash                        编辑条目
POST   /api/memory/.../pin | archive | restore              状态操作
DELETE /api/memory/.../entries/:hash                        删除（归档式）
GET    /api/memory/ledger?store=                            账本
POST   /api/memory/ledger/:id/rollback                      回滚
POST   /api/memory/curate/run                               手动触发治理（演示用）
```

事件枚举扩展（遵循 docs/02 纪律，先加类型再实现）：
`memory.updated` / `memory.archived` / `skill.patched`

## 12. 验收（里程碑 M1）

1. 长会话不爆上下文：连续任务触发压缩，`context.compacted` 事件可见，对话不中断
2. 隔天记忆生效：今天告诉小文"发票在 D:\download"，明天新会话它直接知道
3. 三库路由正确：偏好进 USER、事实进工作区库、自我教训进 soul（管理页可核）
4. 超限自整合：把工作区库写到超限，观察提炼 fork 自动合并条目
5. 自动通知与回滚：出现"💾 记住了"通知；账本可回滚一次变更
6. 遗忘可演示：手动触发治理，30 天未用条目进归档、钉住的不动
7. 检索可用：session_search 中文子串命中历史
8. 防投毒：注入含指令样式的条目被 BLOCKED 不进 prompt

## 13. 测试

- 限额三件套单测（写/拒/整合循环）；三库路由用 FauxProvider 固定对话断言去处
- 压缩管线：构造超长历史，断言保护头尾 + 摘要模板字段 + 事件
- 治理：造不同 last_hit_at 的条目跑 pass，断言状态迁移与 pinned 豁免
- 账本：变更-回滚-再读一致性
