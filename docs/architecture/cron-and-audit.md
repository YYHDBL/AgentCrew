# 05 · 定时任务与轨迹审计详设

> 模块深潜 #4 ｜ 日期：2026-09-28 ｜ 状态：**已定稿（C1–C2 对齐，其余直接设计；C2 按用户指示参考 hermes）**
> 上游依据：[01-设计对齐纪要](../decisions/alignment-record.md)（Q17 cron 语义）、[02-Harness与Session事件模型](./harness-session.md)（闸门/事件/排队）、[03-Memory系统详设](./memory-system.md)（fork 基础设施、skill 账本）、[04-权限治理详设](./governance.md)（pattern 规则、审计链）
> 参考源码：`workMate/AionCore/crates/aionui-cron/`（四段式/错过语义）、`workMate/AionUi/packages/desktop/src/common/adapter/ipcBridge.ts`（ICronJob 契约）、`workMate/hermes-agent/agent/background_review.py`（fork 机制）
> 实施任务：[M3 监控回放与自动化实施卡](../tasks/M3-cards.md)，依据[所有者开工授权](../acceptance/M3-start-authorization.md)进入实施；M1/M2 人工核验与收口确认继续待完成。

---

## 0. 本模块对齐记录（2 问）

| # | 决策点 | 结论 |
|---|---|---|
| C1 | 无人值守的审批 | 当前权限与 scope/protected 校验通过后，需审批调用必须同时满足员工 allow 与计划预授权；任何 deny 或范围外操作直接拒绝、审计并通知，不等待人类 |
| C2 | 审查触发 | 复用 M1 辅助模型作业、白名单及前台两秒取消；失败任务立即登记高优先级审查，每五个完成任务批量审查。允许无需报告，并保留实际作业状态与原因 |

---

## 1. 定时任务

M3实施的具体时间窗口、DST、身份、重试和生命周期使用[ADR-013](../decisions/ADR-013-automation-and-trace.md)，完整请求与响应使用OpenAPI v0.6，操作归属见[M3操作矩阵](../contracts/m3-operations.md)。模型报告与安全审计链分别保存；报告字段使用TraceReport，包含适用尝试、真实事件引用和输入水位。

### 1.1 数据模型（四段式，AionUi 契约裁剪）

```
cron_jobs(id PK, workspace_id FK, name,
  schedule JSON:   {kind: at|every|cron, at_ms?|every_ms?|expr?, tz?}
  target JSON:     {instruction TEXT,
                   execution_mode: existing|new_conversation,
                   conversation_id NULL}          -- existing 模式下的挂靠会话
  metadata JSON:   {agent_id, agent_spec_snapshot,        -- 创建时快照，员工后续改动不影响已建任务
                   pre_authorized: [{tool, pattern}],     -- C1 预授权范围（G1 pattern 语法）
                   created_by: user|agent,
                   created_via_task_run_id NULL}
  state JSON:      {next_run_at, last_run_at,
                   last_status: ok|error|skipped|missed,
                   run_count, retry_count, max_retries=3, enabled}
  created_at, updated_at

cron_job_runs(id PK, job_id FK, task_run_id FK, scheduled_at,
              status CHECK IN (fired, missed, skipped, failed), note,
              UNIQUE(job_id, scheduled_at))   -- 定时发生记录防重复；手动触发使用独立请求身份
```

一次计划发生与初始执行、重试执行分别关联，保留每次实际 TaskRun、重试次数和错误；不得用多条 fired 记录冒充新到期。missed/skipped 可以没有 TaskRun，通过作业域持久事件与独立游标查询，不伪造会话。关联任务的 completed/interrupted/待核验状态按实际执行记录显示。

### 1.2 调度器（单进程，简化自 AionCore）

- 每个 job 一个 asyncio 定时任务（`at` 一次性 / `every` interval / `cron` 表达式）；配置全在库里，**重启 = 重读表重建定时器**
- **错过语义**：启动或唤醒时已错过的到期点 → 写 `missed` 记录 + 通知，不补跑，重排下次；运行中正常定时延迟与错过窗口的界限由调度契约明确。连续错过需保存覆盖时间和次数，过期 at 结束该一次性计划
- **冲突**：到点时上一轮还没跑完 → 记 `skipped` + 重排（不排队堆积）
- **重试**：可重试失败在失败后 30 秒重试，初始执行后最多额外三次，归属同一计划发生记录。停用、权限拒绝、未知副作用及待核验禁止自动重试；存在已完成副作用时按原事件与账本验证安全接续，无法证明时停止自动重试并保留人工处理状态
- 单机单进程调度，沿用 instance.lock、串行写入与发生记录唯一约束
- 已有会话忙碌、队列已暂停或待核验时记录 skipped，不替用户继续队列，不改变会话模式

### 1.3 执行流（与 Harness 的接缝）

```
到点 → 校验 job/agent 仍存在且 enabled
     → 创建/复用会话（new: 按快照建会话；existing: 挂靠既有会话）
     → 创建 task_run（cron_job_id 回链）→ 发 cron.job_fired
     → 进入正常 Harness 循环，但第三闸换判定源（v1.1 修订为交集语义）：
         无人值守闸门序：元数据分级 → deny（员工规则或任务预授权任一 deny 即拒）
           → 员工 allow 规则 ∩ 任务预授权范围（**两层同时允许才放行**）
           同时命中 → 视同批准（audit: pre_authorized_pass）
           任一未命中 → 直接拒绝（tool.failed reason=pre_auth_exceeded
                            + 审计 + 桌面通知"凌晨 3:14 小文试图写 ~/Documents 被拒"）
         触发时复查（不信任创建时快照的权限部分）：grant 仍有效、员工未禁用、范围仍匹配
     → 终态写 cron_job_runs + 更新 job.state + 通知（成功仅徽标，失败/被拒/错过强提醒）
```

### 1.4 数字员工自建定时任务（created_by=agent）

- 对话里"每天早上 9 点把昨日报表发我" → 小文调 `schedule_task` 工具
- 创建批准具有不可免除的人工确认语义，员工 allow 或 allow_always 不能代替授权人类批准
- 审批卡片展示不可变计划提案与预授权范围选择器，用户只能缩小当前合法候选范围；最终配置绑定提案 input_hash、批准身份和修订号，重复提交不重复创建
- 员工建议的预授权范围原样保存在提案内，候选由当前员工allow、Grant与目标scope计算；计划只保存真人选择且通过复查的范围
- 任务列表页与员工档案页都能看到"谁建的定时任务"，owner/admin 可停用任何 job

## 2. 轨迹审计 Agent（hermes 式）

### 2.1 机制：审查 fork 的第三个消费者

复用 docs/03 的 fork 基础设施（同一执行器、aux 槽、前台 2 秒取消、成本纪律：近期原文 + 早期摘要）。与记忆提炼 fork 的差别只在**提示词、白名单、产物**：

| | 记忆提炼 fork | 轨迹审计 fork |
|---|---|---|
| 触发 | 10 回合 / 15 工具迭代 | 失败任务立即 + 每 5 个完成任务批量 |
| 白名单 | memory_write / skill_patch / session_search | `trace_read`（只读事件流投影）/ session_search |
| 产物 | 三库写入 + skill 补丁 | 结构化审计报告 |
| 允许 | "Nothing to save" | "Nothing to report" |

审查触发与输入水位持久化，重复事件不重复发起。审查作业独立于前台 TaskRun，失败分析仍服从前台优先；没有报告时保留 skipped 及原因，不修改原任务终态。

### 2.2 输入与报告

输入：任务指令 + 事件流投影（步骤、每次 LLM 调用 token/延迟、每次工具调用参数与结果摘要、错误、重试、审批记录）。

报告 schema（`audit_reports` 表）：

```
audit_reports(id PK, task_run_id FK UNIQUE, created_at,
  report JSON: {
    summary TEXT,                        -- 一句话结论
    verdict: good|needs_attention|failed_analyzed
    root_cause TEXT NULL,                -- 失败任务：死在哪一步、为什么
    root_cause_event_seq INT NULL,       -- 指向事件流断点（Run Center 可跳转）
    improvement_suggestions [TEXT],
    skill_proposal NULL {name, description(≤60字), outline},   -- "流程已稳定可固化"时
    anomalies [TEXT]                     -- token 异常/耗时异常/重试风暴
  })
```

事件：有有效报告时提交 `audit.reported`。报告保留模型、时间、适用尝试和输入水位，事件引用必须存在且属于目标任务。Run Center 展示报告与原事件链接；未生成、失败或无报告分别显示真实状态。

### 2.3 闭环：报告 → skill

审计报告含有效 `skill_proposal` 时，Run Center 提供固化为技能操作。授权人类确认后进入 M1 Skill 提炼分支，按 M2 服务发布不可变新版本，source=agent，并写入记忆账本。固化不自动授予 grant；管理员授权后，下一任务有效索引及读取才可复用。不存在提案或发布失败时显示实际状态。

## 3. API 增量

```
GET/POST/PATCH/DELETE  /api/cron/jobs            # CRUD（enabled 开关）
POST /api/cron/jobs/:id/run-now                  # 手动触发（演示用，仍走预授权闸门）
GET  /api/cron/jobs/:id/runs                     # 运行历史
GET  /api/task-runs/:id/audit-report             # 报告
POST /api/audit-reports/:id/promote-skill        # 固化为技能
```

事件枚举扩展：`cron.job_fired / cron.job_missed / cron.job_skipped / cron.job_failed / audit.reported`

## 4. 验收（里程碑 M3 组成部分）

1. 自有时间计算参数化测试通过，真机验证 at/every/cron 三种计划准确触发
2. 错过不补跑：真实暂停/恢复进程、终止重启或系统休眠跨过窗口 → 记录 missed + 通知，无重复执行
3. 复用会话与新建会话两种模式都可演示（同一员工每天在同一个会话里接着干）
4. **预授权演示**：范围外写路径被拒 + 审计留痕 + 通知可见（C1 的现场戏）
5. 失败任务自动登记审查，实际报告引用可跳转原事件；无报告或审查失败如实展示
6. 实际报告提案经真人确认固化为新技能版本，按 M2 授权后在下一任务读取和复用
7. 数字员工建定时任务必弹审批（含范围选择器）

## 5. 测试

- 调度计算：时间参数化纯函数单测（传入固定时刻值——非全局 fake clock，v1.3）；触发/错过/冲突用秒级 `every` 计划 + 真实杀起进程验证
- 无人值守闸门序单测（预授权命中/未命中/deny 优先矩阵——纯函数参数化）
- 审计 fork：真实 aux 模型审真实事件流 → 断言报告 schema 与 root_cause 定位
- promote-skill 流：报告 → skill 版本 → 账本一致性
