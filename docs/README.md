# AgentCrew 文档地图（多 Agent 协作规范）

> 每个领域一个目录、一个负责者。**跨领域决策必须落 ADR（decisions/）**，别人才知道你定了什么。契约（contracts/）由后端维护，其他角色只读引用。

## 主仓库与提交目标

主仓库为 [YYHDBL/AgentCrew](https://github.com/YYHDBL/AgentCrew)，`origin` 使用 `https://github.com/YYHDBL/AgentCrew.git`。后续实施分支、提交推送和 Pull Request 统一使用该仓库；主分支为 `main`，桌面开发分支为 `desktop-shell`。合并主分支需要所有者授权，保留 `legacy-mycodeagent` 历史分支。

`archive` 指向 `YYHDBL/AgentCrew-dev-archive`，用于查询原开发历史；只有所有者明确指定时才向归档仓库提交。

## 分工与目录所有权

| 目录 | 领域 | 负责 | 说明 |
|---|---|---|---|
| `product/` | 产品 | 产品 Agent（用户决定产品方向） | 产品定义与用户流程 |
| `design/` | 设计 | **设计 Agent**（用户审美把关） | 输入包在此；design-system / ui-spec 由它产出 |
| `architecture/` | 后端架构 | 后端 Agent | 总架构 + 五份模块详设 |
| `contracts/` | 接口契约 | **后端 Agent** | 事件枚举、openapi.yaml——冻结后他人只读 |
| `features/` | 功能规格 | 产品 Agent | F 编号功能规格（供设计与后端实现） |
| `decisions/` | 决策记录 | 各自落 ADR | ADR + 完整对齐历史档案 |
| `tasks/` | 实施任务卡 | 后端 Agent（后端卡）/ 前端 Agent（前端卡） | 一次领一张，验收记录进 `acceptance/` |
| `acceptance/` | 验收证据 | 领卡者 | 真实运行的输出粘贴 |
| `assets/` | 资产 | 共享 | 概念图等（参考性质） |

## 产品与设计入口

[PRODUCT.md](./product/PRODUCT.md) 定义用户、产品边界和页面职责；[USER-FLOWS.md](./product/USER-FLOWS.md) 定义完整用户流程；[features](./features/) 定义每项具体功能及验收；[design-input.md](./design/design-input.md) 汇总交给 UX Agent 的页面状态与视觉方向。接口与事件契约由后端维护，设计和实现应核对其能否支撑产品状态。代码位置约定为 `backend/` 与 `desktop/`。

[M1 Memory 实施任务卡](./tasks/M1-cards.md) 将 Memory 详设拆分为 13 张卡，明确实施依赖、后端与前端职责、文件范围和真实验收条件；功能实施等待 M0 收口确认。

[M2 安全与治理实施任务卡](./tasks/M2-cards.md) 将资源、角色、授权、连接器、规则、完整沙盒、恢复和审计拆分为 13 张卡，包含参考源码与七项治理验收映射；功能实施等待 M1 收口确认。

[M2 执行提示词](./tasks/M2-agent-prompt.md) 可交给实施 Agent，包含主仓库要求、前置检查、实施顺序、真实验收及提交规则。

[M3 监控回放与自动化实施任务卡](./tasks/M3-cards.md) 拆分为 15 张卡，覆盖运行中心、定时任务、轨迹审查、技能固化、员工与管理页面、通知及桌面生命周期；功能实施等待 M2 收口确认。

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
| 08 产品定义 | product/PRODUCT.md |
| 09 设计输入包 | design/design-input.md |
