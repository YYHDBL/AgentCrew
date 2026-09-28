# AgentCrew 文档地图（多 Agent 协作规范）

> 每个领域一个目录、一个负责者。**跨领域决策必须落 ADR（decisions/）**，别人才知道你定了什么。契约（contracts/）由后端维护，其他角色只读引用。

## 分工与目录所有权

| 目录 | 领域 | 负责 | 说明 |
|---|---|---|---|
| `product/` | 产品 | 产品线（用户主导） | 产品简报、用户流程、屏幕地图 |
| `design/` | 设计 | **设计 Agent**（用户审美把关） | 输入包在此；design-system / ui-spec 由它产出 |
| `architecture/` | 后端架构 | **后端 Agent（本 AI）** | 总架构 + 五份模块详设 |
| `contracts/` | 接口契约 | **后端 Agent** | 事件枚举、openapi.yaml——冻结后他人只读 |
| `features/` | 功能规格 | 产品线 | F 编号功能规格（供设计与后端对齐） |
| `decisions/` | 决策记录 | 各自落 ADR | ADR + 完整对齐历史档案 |
| `tasks/` | 实施任务卡 | 后端 Agent（后端卡）/ 前端 Agent（前端卡） | 一次领一张，验收记录进 `acceptance/` |
| `acceptance/` | 验收证据 | 领卡者 | 真实运行的输出粘贴 |
| `assets/` | 资产 | 共享 | 概念图等（参考性质） |

## 当前状态（2026-09-29）

- 设计：输入包已备（design/design-input.md），待设计 Agent 产出线框→高保真
- 后端：明日开工，按 tasks/M0-cards.md C1→C9 顺序领卡（引擎层，不依赖设计）
- 前端：等设计冻结后由后端 Agent 重写任务卡，交前端实现（AI 生成维护）
- 代码位置约定：`backend/`（后端 Agent）、`desktop/`（前端）

## 旧编号对照（正文里"docs/0X"文字引用按此解析）

| 旧 | 新 |
|---|---|
| 01 对齐纪要 | decisions/alignment-record.md |
| 02 Harness/Session | architecture/harness-session.md |
| 03 Memory | architecture/memory-system.md |
| 04 权限治理 | architecture/governance.md |
| 05 定时/审计 | architecture/cron-and-audit.md |
| 06 桌面壳 | architecture/desktop-shell.md |
| 07 M0 任务卡 | tasks/M0-cards.md |
| 08 MVP 切片 | product/product-brief.md |
| 09 设计输入包 | design/design-input.md |
