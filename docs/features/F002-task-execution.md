# F002 · 执行过程展示与审批

- **目标**：任务执行全程实时可见；高危操作经审批（UF2/UF3）
- **主流程**：SSE 订阅会话流 → 步骤/模型请求/工具调用事件实时渲染（进行中/完成/失败三态）→ needs_approval 的调用弹审批卡（操作+参数+input_hash）→ 四选一 → 继续/拒绝
- **边界**：审批期间任务暂停（FSM 等待计数）；allow_always 写规则表并提示记录范围；断流重连后从 global_seq 续播、UI 自动恢复
- **契约**：SSE `/conversations/:id/stream`、`POST /tool-approvals/:callId`；事件 `step.* / llm.* / tool.* / permission.*`
- **验收**：写文件操作必弹审批；批准后继续；"始终允许"后同类放行；拒绝后该调用失败但任务继续由模型决定
