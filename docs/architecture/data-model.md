# 数据模型汇总

> 维护者：后端 Agent ｜ **M0 建表以 [harness-session §2](./harness-session.md) 为唯一权威**；本文件是全库表清单与归属索引。

## 执行域（M0，定义：harness-session §2）

| 表 | 用途 |
|---|---|
| conversations | 会话/任务线程容器（AgentSpec 快照、pending_queue） |
| task_runs | 任务（每条指令一次；状态机 + 乐观锁） |
| run_attempts | 执行尝试（恢复 = 新 attempt） |
| run_events | **执行过程唯一事实源**（global_seq 全局游标 + 任务内 seq；载荷含全文） |
| steps / llm_calls / tool_calls / messages | 投影表（tool_calls 含 call_id、副作用三分类、input_hash、pending_verification） |
| task_materials | 任务导入材料（original_path/stored_name/逐文件 error）——v1.4 |
| artifacts | 产物投影（generating/ready/failed/missing + 惰性探测）——v1.4 |
| agent_permission_rules | 员工规则（effect allow/deny + pattern）——M0 建好，M2 全面使用 |
| audit_log | 审计哈希链（链头周期快照 chain-head.txt）——M0 起步 |
| schema_migrations | 迁移版本表（v1.1 补录；升级前 db 快照存 backups/） |

**M0 建表合计 13 张**，C2 验收逐表核对以本清单为准。

## 治理域（M2，定义：governance §1–3）

organizations / workspaces / users / memberships（固定三角色）/ agents / skills + skill_versions（不可变版本）/ connectors / grants（默认拒绝，agent/user 两类授予方）

## 记忆域（M1，定义：memory-system §1、§9）

markdown 三库（USER.md / workspace MEMORY.md / soul.md）+ sidecar 元数据（命中/状态/来源/needs_review）+ session_summaries（任务摘要）+ memory_ledger（append-only 可回滚）+ FTS5 检索表（trigram）

## 定时与审计域（M3，定义：cron-and-audit §1–2）

cron_jobs（四段式：schedule/target/metadata 含预授权/state）+ cron_job_runs（UNIQUE(job_id, scheduled_at)）+ audit_reports（轨迹审计报告）

## 持久化契约（必读）

事件 vs 投影 vs 业务状态表的边界、上下文重建依据（事件全文 + 工件文件）——见 harness-session §2.3。
