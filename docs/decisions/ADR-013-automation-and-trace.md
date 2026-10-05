# ADR-013 · M3 自动化与轨迹审查边界

状态：已接受实施，2026-10-05。依据为所有者 M3 开工授权和 M3-01 至 M3-15，M1/M2 人工核验与收口确认继续待完成。运行契约使用 OpenAPI v0.6。

## 时间与发生身份

`at` 使用 UTC Unix 毫秒并保留 IANA 时区；`every` 最小间隔为 1000 毫秒，以创建或修改 schedule 时的持久 anchor 为节奏；`cron` 使用成熟 croniter 库，允许标准五字段与末尾秒字段的六字段表达式，拒绝宏与年度字段。时区由标准 ZoneInfo 校验。cron 的当地时间候选经过 UTC 往返校验，不存在的夏令时时刻跳过，重复当地时刻选择第一次绝对时刻；计算函数接收时刻参数，集成运行使用真实时钟。

正常运行的派发宽限为 2000 毫秒。启动恢复、系统 resume、实际进程暂停超过宽限时，既存到期时间登记 missed，不补跑。every 通过 anchor 算出严格晚于当前时间的下一次；cron 使用同一库计算。连续错过合并为一个记录，保存首个 scheduled_at、missed_through 和 missed_count，唯一范围不得与后续发生重叠。过期 at 保存 missed，next_run_at 置空。时间倒退不重复已登记发生。

定时发生以 `(job_id, scheduled_at)` 唯一；手动发生的 scheduled_at 为 null，以 `(job_id, actor_id, client_request_id)` 唯一，保存独立 triggered_at。occurrence 与实际 TaskRun、attempt_no、retry_no 分别关联。run_count 只累计初始派发次数。同计划未结束发生，以及 existing 会话运行、等待人工、队列暂停、排队或待核验，均登记 skipped。发生登记、任务关联、事件、审计与通知通过既有串行写通道提交后派发。

## 重试与当前授权

可重试失败在失败结束后 30 秒重试，初始执行之后最多额外三次，仍归属原 occurrence。优先通过正常恢复接续原 TaskRun 和已保存上下文，保留原账本与调用身份。每次重试复查 enabled、revision、当前成员、员工、Grant、deny、连接器、scope/protected 和冲突。unknown outcome、pending_verification、权限拒绝、停用或修改后的旧 revision 禁止自动重试。已经完成副作用而无法证明安全接续时保留人工处理状态。重启先执行 M2 对账，再恢复调度；已登记但缺派发或终态的记录必须先查明真实任务状态。

所有定时及手动运行均使用无人值守上下文。当前角色、Grant、scope/protected、连接器与 deny 构成强制边界；需审批调用只有同时命中员工 allow 和计划 pre_authorized 才执行。ask_user、schedule_task 及其他要求人工交互的调用直接拒绝，不能产生等待审批或永久 waiting_user。计划预授权不改变员工能力和范围。

## 员工提案与技能发布

员工 schedule_task 持久化不可变提案，强制真人确认，不接受永久决定。参数 input_hash、活动 task/attempt/call_id、执行身份、revision、合法候选与最终所选范围绑定；审批事务重新核查当前授权，拒绝扩大候选。相同决定和选择返回首次结果；不同内容、已中断或失去执行方返回 APPROVAL_STALE。批准、唯一计划创建、审计与来源同事务，不能先批准后丢失创建。API 不接受 created_by、owner 或角色伪装字段。

轨迹审查复用 M1 aux 作业、预算和前台两秒取消，只允许 trace_read/session_search。失败终态立即登记高优先级审查，每五个 completed 触发批次，触发 task/attempt/水位去重。模型报告与 M2 哈希审计链分别保存；报告只能引用真实存在且属于输入任务/尝试和水位的事件。没有报告时保存 skipped 原因，模型错误保存 failed，旧尝试报告标明适用性。

有效 skill_proposal 经真人确认进入现有 Skill 提炼与统一版本服务，保存 read-before-write、change_id、正文、机制说明、账本、版本及报告来源。无提案或模型未生成正文时如实结束作业。固化不创建 Grant，管理员单独授予后才进入新任务有效索引。启用计划的真实技能引用阻止 curator 归档，停用或删除后释放引用。

## 查询、通知与生命周期

运行查询遵守当前身份和私有会话可见性，记录与 at_global_seq 在同一 SQLite 读取快照取得。分页游标绑定身份、范围、过滤及固定排序；事件按 task seq 排他分页，through_global_seq 固定历史上界，跨任务 global_seq 间隔合法。模型明细只返回允许展示的摘要和工具声明，移除凭据及隐藏推理。指标分别聚合各表后合并，缺失 usage 显示未知，完成率分母为可见筛选任务总数。历史回放只读取保存事件。

通知身份持久唯一，read 与 presented 分别记录；桌面弹出前通过 compare-and-set 认领，只有 claimed=true 才显示，重放和重启不会重复弹出。成功用徽标，失败、拒绝及错过使用系统提醒并保留应用内状态。系统权限不足时如实记录。

退出影响读取真实活动任务、待核验、辅助作业及未来 24 小时计划；确认后在既有 10 秒预算内停止调度、流和子进程，写入链头并退出。关窗驻留继续调度；关窗即退出与防休眠由受控设置及窄 preload 管理。真实 Electron powerMonitor suspend/resume 事件持久化，resume 重新核查到期时间。SIGSTOP/SIGCONT 和实际 macOS 电源事件分别留证；未发生电源事件不能判定该项通过。
