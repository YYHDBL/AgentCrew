# 事件契约（全量汇总）

> 维护者：后端 Agent ｜ 权威定义在各详设；新增机制**先加事件类型再实现**（纪律）。SSE 载荷 = `{global_seq, task_run_id?, seq?, type, payload, ts}`。

## 任务生命周期（harness-session §3）
`run.queued / run.started / run.completed / run.failed / run.cancelled / run.interrupted / run.resumed`

## 回合与模型（harness-session §3）
`step.started / step.completed / llm.request_started / llm.request_done（含全文+usage）/ llm.request_failed（含错误分类、retry_no）`

## 工具（harness-session §3，v1.2/v1.3 语义）
`tool.prepared / tool.dispatched / tool.completed / tool.failed / tool.skipped_idempotent（供应商侧去重确认）/ tool.pending_verification（结果不明暂停待人工）`

## 权限与人机（harness-session §3 + governance）
`permission.requested（四选项 + input_hash）/ permission.resolved / question.requested / question.answered`

## 上下文（M1，harness-session 占位）
`context.compacted / tool.result_externalized`

## 记忆与技能（M1，memory-system §11）
`memory.updated / memory.archived / skill.patched`

## 治理（M2，governance §6）
`governance.grant_changed / governance.rule_changed`

## 定时与审计（M3，cron-and-audit §3）
`cron.job_fired / cron.job_missed / cron.job_skipped / cron.job_failed / audit.reported`
