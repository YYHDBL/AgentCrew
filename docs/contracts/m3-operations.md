# M3 操作与实施归属

OpenAPI v0.6 为请求和响应依据，ADR-013 规定时间、权限和生命周期。所有 JSON 使用 data/error 信封，分页上限200，事件分页上限500；SSE 保持现有控制帧和鉴权。写入幂等身份绑定真实凭证身份及有效身份；异步202仅表示受理。

| 操作 | 领域卡片 | HTTP卡片 | 页面及验收 |
|---|---|---|---|
| GET task-runs、单项、runs/metrics、attempts、events分页及seq定位、calls明细 | M3-02 | M3-02 | M3-11运行中心，回放、指标、尝试与错误定位 |
| cron/jobs列表、创建、读取、修改、删除、启停、runs历史、authorization-preview | M3-03 | M3-03 | M3-13自动化，M3-12员工档案 |
| cron/jobs/{id}/run-now | M3-05 | M3-03/05 | M3-13，独立手动身份和无人值守边界 |
| cron/proposals/{id}查询与人工决定 | M3-07 | M3-07 | M3-13审批卡与范围选择，永久规则不能免除 |
| cron/stream | M3-03 | M3-10 | 作业域无任务事件，持久global_seq补播 |
| task-runs/{id}/audit-report、review、reviews/jobs查询及取消 | M3-08 | M3-10 | M3-11真实报告或实际无报告状态 |
| audit-reports/{id}/promote-skill | M3-09 | M3-10 | M3-11确认，M3-12版本和独立授权 |
| notifications列表与read/presented标记 | M3-14 | M3-14 | 应用徽标、系统提醒、导航与去重 |
| runtime/exit-impact、power-event、settings/desktop | M3-14 | M3-14 | 设置、托盘、退出清理与真实休眠记录 |
| M1 Memory及M2资源、版本、Grant、规则、身份、角色、审计与诊断 | 既有M1/M2 | 既有M1/M2 | M3-12整合Agent Studio/Admin Center |

owner 使用本组织范围；admin 使用登记工作区，私人任务仍限本人；member 读取本人可见员工及会话。计划管理限 owner/admin，员工提案审批还须具有来源任务可见性。member 不管理计划、固化技能或桌面设置。资源或权限撤销后立即拒绝无权请求并停止订阅，页面清除对应正文与缓存。

七项自动化分别覆盖三种触发、错过和冲突、两种会话模式、预授权、报告定位、技能发布复用、员工强制人工审批；四屏使用同一真实任务关联。检查和证据归属见 M3-15，不以契约检查替代真实执行验收。
