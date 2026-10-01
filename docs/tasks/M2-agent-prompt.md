# M2 实施 Agent 提示词

将以下内容完整交给实施 Agent。详细要求以 [M2 实施卡](./M2-cards.md) 为准。

```text
你是 AgentCrew 的 M2 实施 Agent，负责安全与治理的后端、桌面前端、回归测试和真实验收。按 docs/tasks/M2-cards.md 完整实施 M2-01 至 M2-13。

【仓库与开工检查】
唯一交付仓库：https://github.com/YYHDBL/AgentCrew.git。主分支 main，桌面开发分支 desktop-shell。AgentCrew-dev-archive 仅供查询历史。所有提交推送和 Pull Request 都进入主仓库，保留 legacy-mycodeagent，不强制推送，不自动合并 main。
本机工作区：/Users/yyhdbl/Desktop/workMate/agentcrew-desktop；其他环境先确认实际工作区。读取适用的 AGENTS.md，检查工作区改动、远端、当前分支和最新提交；保留已有修改。获取 origin 最新状态，检查 main 与 desktop-shell 的关系，沿用已有实施分支和项目结构，不能凭旧提交号判断当前状态。
开工前确认 M1 功能已经实现、八项真实验收通过，并有所有者收口确认。核查 docs/tasks/M1-cards.md、对应代码及验收记录。前置条件缺失时，报告具体缺失内容并停止依赖它的 M2 实施；不能擅自实施 M1，不能用空接口、模拟数据或固定返回值代替 M1。

【设计与参考】
完整阅读 docs/tasks/M2-cards.md、docs/architecture/governance.md、harness-session.md、memory-system.md、docs/contracts/openapi.yaml、events.md、ADR-006、ADR-007、ADR-008，以及 F009、frontend-cards.md 和相关 UI 规格。
按 M2 卡片中的路径阅读同级 OpenWork、ZCode、AionUi、EasyMint、learn-workbuddy 参考源码。参考源码用于理解设计，运行代码使用 AgentCrew 的领域服务和成熟依赖。复用已有审批、串行写入、副作用账本、恢复、审计、Seatbelt，以及 M1 三库、技能、变更账本和后台作业。

【实施顺序】
依次完成 M2-01 契约与安全语义、02 资源迁移与种子组织、03 身份与 RBAC、04 Skill 不可变版本、05 Grant 与实时撤销、06 HTTP/MCP 连接器、07 规则与审批、08 完整 Seatbelt、09 当前权限下恢复、10 审计与只读诊断、11 管理 API、12 治理界面、13 贯穿验收。
逐张完成实现、运行、针对性回归和真实验收，更新卡片实际状态，提交并推送后继续下一张。每张记录 docs/acceptance/M2-XX.md，公开证据进入 docs/acceptance/assets/M2/XX/，最终报告为 docs/acceptance/M2.md。连续推进全部已具备依赖的卡片；失败定位到具体卡片，修复并重新验证，报告保留真实验收结果。

【必须保持的安全语义】
scope、protected、角色和 grant 均为强制边界，deny 优先；bash 规则按完整命令等值匹配，bash 网络全部禁止。工作区外且已经获准加入 scope 的目录可以审批，scope 外直接拒绝。Seatbelt 实际限制普通文件读取、写入和子进程，加载失败立即拒绝执行。
会话记忆冻结、任务技能版本和当前授权分别保持各自语义。任务组装、模型请求、工具派发、审批提交及恢复均复查当前授权；授权检查与 dispatched 登记采用同一串行写入顺序。撤销先提交时禁止派发；已派发调用取消后核对实际效果。模型和后台工具不能修改角色、grant、规则或连接器配置。
历史 Skill 版本不可改写。旧审批失去执行方后无法批准或新增规则，恢复后展示并处理新尝试的有效审批。SIGKILL 恢复保留任务、版本、记忆和副作用账本；未知效果等待人工核验。外部幂等只能用于实际支持去重的端点，不能按参数相同跳过新的调用。
HTTP/MCP 真实执行并遵守连接器目标范围、grant、凭据保护及子进程限制。治理变更、审计与事件同事务。审计同时执行内部全量重算和独立链头验证；失败进入只读诊断，经受控备份恢复、重启和完整验证后恢复业务。

【测试与交付】
遵循 ADR-006，使用真实 Electron、当前有效配置的 main/aux 模型、真实文件、SQLite、HTTP/MCP 服务及实际 SIGKILL。先验证模型配置可用，绝不公开 key/token，不固定要求使用 GLM。禁止 mock、编造工具成功或跳过真实上游；不断言模型必定表达某句话，核对实际工具声明、请求、事件和副作用。
完成卡片规定的七项治理验收，并完成额外沙盒、连接器、不可变版本和中断恢复检查；回归 M1 八项能力及 M0 材料、审批、队列、刷新和恢复。保存实际任务/尝试标识、SQL 查询与结果、事件摘录、文件 SHA/mtime 和界面证据。截图发布前逐张确认没有凭据，运行数据和中间结果存入 gitignore 覆盖的工作目录，禁止使用 /tmp。
最终在 backend/ 执行 uv sync、uv run pytest -q、uv run --with pyyaml python ../scripts/check_openapi.py；在 desktop/ 执行 npm run typecheck、npm run build、node tests/m2-governance.mjs。使用实际测试数量和退出结果，任何失败均如实记录并完成返工。
代码、测试、文档和脱敏证据提交推送到主仓库实施分支，确认远端提交与本地一致。汇报七项治理验收结果及证据指针、额外安全检查、M0/M1 回归、实际缺口和提交号。完成后停止，等待所有者最终人工核验与 M2 收口确认。
```
