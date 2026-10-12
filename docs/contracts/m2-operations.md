# M2 操作与验收矩阵

所有接口沿用 Bearer、data/error 信封，演示身份标识为 X-AgentCrew-Identity。每项操作在指定卡片启动真实 HTTP 服务核查，契约定义不会计为接口实现通过。管理写入要求 change_id；修改、禁用与撤销要求 expected_revision。服务端生成身份、归属、修订、时间和审计信息。GET 集合使用 limit≤200 和绑定身份及过滤条件的排他 after 游标。

| HTTP 操作 | 权限与范围 | 领域卡片 | HTTP 验收 |
|---|---|---|---|
| GET /api/identity | 当前有效身份及真实 owner，读取当前角色 | M2-03 | M2-03、M2-11 |
| POST /api/identity/demo | 真实 owner 签发有效成员演示标识，诊断禁用 | M2-03 | M2-03、M2-11 |
| GET、PATCH /api/organization | 本组织读取，owner 修改及禁用 | M2-02、M2-03 | M2-11 |
| GET /api/memberships | owner 查询全组织，其他角色仅自身 | M2-03 | M2-03、M2-11 |
| PATCH /api/memberships/{id}/role | owner，最后有效 owner 保护 | M2-03 | M2-03、M2-11 |
| PUT /api/workspaces/{id}/members/{user_id} | owner 设置访问或回收 | M2-03 | M2-03、M2-11 |
| GET、POST /api/workspaces；GET、PATCH、DELETE /api/workspaces/{id} | 可见范围读取；owner/admin 创建及管理，admin 限登记范围 | M2-02、M2-03 | M2-11 |
| GET、POST /api/agents；GET、PATCH、DELETE /api/agents/{id} | member 需有效员工 Grant；owner/admin 管理 | M2-02、M2-05 | M2-11 |
| GET、POST /api/skills；GET、PATCH、DELETE /api/skills/{id} | 管理资源 owner/admin；member 仅授权员工有效 Skill | M2-04、M2-05 | M2-04、M2-11 |
| GET、POST /api/skills/{id}/versions；GET /api/skills/{id}/versions/{version_id} | 历史不可变；发布及恢复正文 owner/admin，版本读取需当前授权 | M2-04 | M2-04、M2-11 |
| GET、POST /api/connectors；GET、PATCH、DELETE /api/connectors/{id} | owner/admin 管理；凭据只返回配置状态 | M2-06 | M2-06、M2-11 |
| POST /api/connectors/{id}/validate | owner/admin，真实发现，凭据脱敏及当前目标边界 | M2-06 | M2-06、M2-11 |
| GET、POST /api/grants；DELETE /api/grants/{id} | owner/admin 所属工作区，成员只能查询自身有效可见性 | M2-05 | M2-05、M2-11 |
| GET、POST /api/agents/{id}/permission-rules；DELETE /api/agents/{id}/permission-rules/{rule_id} | owner/admin 管理，member 查询授权员工规则；回收保留历史 | M2-07 | M2-07、M2-11 |
| GET /api/governance/stream | 当前有效身份的组织/工作区事件；撤权关闭订阅 | M2-05、M2-11 | M2-11 |
| GET /api/audit；GET /api/audit/export | owner 本组织、admin 登记工作区、member 自身 actor | M2-10 | M2-10、M2-11 |
| POST /api/audit/verify | owner，内部全量重算及锚点分别报告 | M2-10 | M2-10、M2-11 |
| GET、POST /api/diagnostics/backups | owner，服务验证和登记备份，诊断期间仅查询 | M2-10 | M2-10、M2-11 |
| POST /api/diagnostics/restore | owner，已验证备份标识，保留损坏材料并要求重启 | M2-10 | M2-10、M2-11 |
| 既有 conversations、messages、materials、artifacts、runs、SSE、queue、cancel、questions | 当前身份、工作区、员工 Grant 及本人会话 | M2-03 | M2-03、M2-11、M2-13 |
| 既有 tool-approvals 决定 | 本人会话与当前执行权限、活动执行方；永久规则需owner/admin | M2-07 | M2-07、M2-11、M2-13 |
| 既有 verification、resume | 当前权限及真实效果核验，未知效果阻止派发 | M2-09 | M2-09、M2-11、M2-13 |
| 既有 memory/skills 管理及账本恢复、后台查询和独立审批 | 当前身份范围、角色及 Skill Grant，统一版本服务 | M2-03、M2-04、M2-05 | M2-11、M2-13 |

每项 HTTP 验收同时核对成功 schema、缺少或错误认证401、无权身份403、跨范围403、不存在404、陈旧修订及冲突409、非法输入422和诊断503。写操作核对相同 change_id 返回首次结果、不同内容不能复用；分页核对稳定排序和身份/过滤变化后拒绝旧游标。连接器、审批与恢复还保存实际工具声明、调用副作用次数、事件水位和审计序号。

七项治理由 M2-07/08/12 的工作区及外部目录审批、M2-07/10 的 deny、M2-05/06/08 的能力隔离、M2-03/11/12 的角色、M2-04/05/09 的撤销、M2-09/10/12 的诊断恢复和 M2-10/11/12 的两级审计覆盖。完整读取沙盒、不可变版本、HTTP/MCP 边界及 SIGKILL 恢复另行核查，M0/M1 回归维持原能力和真实证据。
