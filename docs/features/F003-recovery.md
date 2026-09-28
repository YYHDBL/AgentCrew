# F003 · 中断恢复

- **目标**：强杀应用后任务可恢复且无重复副作用（UF5）
- **主流程**：启动对账（running→interrupted；dispatched 无终态：可核验自动核验补齐，否则标待核验）→ 界面显示"已中断"+恢复入口 → resume（新 attempt：事件全文+工件重建上下文、副作用账本注入）→ 继续执行 → 完成
- **边界**：存在 pending_verification 时拒绝 resume（先人工核验）；工件缺失降级占位继续；恢复后模型重发调用 = 新决定（同参不跳过，防合并两次有意执行）
- **契约**：`POST /task-runs/:id/resume`（202/409）；事件 `run.interrupted/resumed`、`tool.pending_verification/skipped_idempotent`
- **验收**：kill -9 后重启恢复完成，目标文件内容无重复写入痕迹
