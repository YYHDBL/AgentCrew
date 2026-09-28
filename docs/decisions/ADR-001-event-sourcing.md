# ADR-001 · 执行过程采用事件溯源

状态：已接受（Q10，v1.1/v1.2 修订）

**背景**：需要持久化 Session、中断恢复、Trace/Replay、前端实时状态四件事。
**决策**：`run_events`（append-only，global_seq + 任务内 seq）是**执行过程**的唯一事实源；steps/llm_calls/tool_calls/messages 是投影；grant/记忆/定时是业务状态表（各有账本，不从事件重建）；上下文恢复 = 事件全文 + 工件文件（持久化契约 harness-session §2.3）。
**理由**：恢复/重放/Trace/前端状态是同一份事件的四个视图；双写有一致性坑，终会退化成事件溯源。
**影响**：事件载荷必须带全文（工具输出 ≤32KB 内联，超出落工件）；新增机制先加事件类型。
