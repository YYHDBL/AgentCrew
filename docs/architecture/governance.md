# 04 · 权限治理详设

> 模块深潜 #3 ｜ 日期：2026-09-28 ｜ 状态：**已定稿（G1–G2 对齐，其余直接设计）**
> 上游依据：[01-设计对齐纪要](../decisions/alignment-record.md)（Q14 B 档 / Q4 真实执行）、[02-Harness与Session事件模型](./harness-session.md)（审批事件、工具三态）、[03-Memory系统详设](./memory-system.md)（skill 账本）
> 参考源码：`workMate/openwork/ee/packages/den-db/src/schema/sharables/`（config_object + grant 表族）、`workMate/ZCode/apps/zcode-cli/packages/core/src/permission/`、`workMate/AionUi`（四选项契约）、`workMate/learn-workbuddy/s23_audit_sandbox/`
> 实施任务：[M2 安全与治理实施卡](../tasks/M2-cards.md)，13 张卡均未开始，依赖 M1 收口确认。

---

## 0. 本模块对齐记录（2 问）

| # | 决策点 | 结论 |
|---|---|---|
| G1 | "总是允许"的粒度 | **按"工具 + 资源范围"记，挂员工名下**；路径规范化后按目录范围匹配，bash 按完整命令等值匹配。工作区外但已加入任务 scope 的目录可审批，scope 外直接拒绝；管理页提供已授权清单和回收操作 |
| G2 | 审计链校验失败 | **fail-closed 进入只读诊断模式**，定位断点并提供导出、校验和受控备份恢复；任务、工具和治理写入禁用 |

---

## 1. 资源模型（建表）

```
organizations(id PK, name, slug UNIQUE, created_at)
workspaces(id PK, org_id FK, name, data_dir, created_at)
users(id PK, name, email UNIQUE, password_hash, created_at)
memberships(id PK, org_id FK, user_id FK, role CHECK IN (owner, admin, member),
            UNIQUE(org_id, user_id), created_at)

agents(id PK, workspace_id FK, name, spec JSON,          # 岗位描述/模型槽/skill 引用
       status CHECK IN (active, disabled), created_by_user_id, created_at)
  # spec 快照在会话创建时复制（docs/02 conversations.agent_spec_snapshot）

skills(id PK, workspace_id FK, name UNIQUE(ws,name), description(≤60字),
       current_version_id NULL, source CHECK IN (user, agent), status, created_at)
skill_versions(id PK, skill_id FK, version_no, UNIQUE(skill_id, version_no),
               content TEXT, created_by CHECK IN (user, agent), created_at)   # 不可变
connectors(id PK, workspace_id FK, name, type CHECK IN (mcp, http),
           config JSON, status, created_at)
```

**纪律**：skill 版本不可变（只新增版本号，不改历史版本）；数字员工创建的 skill `source=agent`，与用户创建的同台管理（演化走 docs/03 的 skill 账本）。

### 1.1 本地请求认证与身份语义（v1.1 修订）

- sidecar 启动握手时生成一次性随机 `AGENTCREW_TOKEN`，传给 Electron 主进程，渲染层经 preload 获取，每个请求携带 `Authorization: Bearer`；缺少或错误凭证的调用被拒绝，凭据不得进入日志或公开材料（机制见 docs/06 §1）
- 认证身份 = 本地登录的 owner（真实人）；王明/李蕾等虚拟成员是**演示身份切换**：切换后 API 按该身份真实执行 RBAC 规则（403 是规则代码的真实输出），但进程凭证始终是 owner
- 审批者身份 = 当前认证用户；审批记录落审计链时带 actor
- 表述纪律：对内对外都说"RBAC 真实实现 + 演示身份切换"——规则是真的，第二个真人是演示

## 2. 授权模型：RBAC × Grant × 规则表，三层各管一件事

### 2.1 人的权限 = 角色（RBAC，API 层中间件）

| 角色 | 能干什么 |
|---|---|
| owner | 一切：改角色、删组织、验证审计 |
| admin | 管工作区资源：建/改数字员工、skill、连接器、grant、权限规则 |
| member | 只能用：与数字员工对话、查看自己的会话与 Run Center |

### 2.2 资源的授权 = grant 表（默认拒绝，OpenWork 模式）

```
grants(id PK,
       resource_type CHECK IN (skill, connector, agent),
       resource_id,
       grantee_type CHECK IN (agent, user),
       grantee_id,
       granted_by_user_id, created_at, revoked_at NULL)
```

- **grantee=agent** → 能力隔离（核心）：决定哪个数字员工能用哪些 skill / 连接器。无 grant 的 skill 不进有效索引，无 grant 的连接器工具不注册；直接调用也由执行器检查并拒绝
- **grantee=user** → member 可见性：决定"哪个成员能和哪个数字员工对话"（演示用）
- 撤销 = 填 `revoked_at`（不删行，留痕）；每次任务组装、模型请求、工具派发、审批提交和恢复检查当前有效授权。未派发调用被拒绝，已派发调用取消后按实际副作用核验；历史快照不能授予已撤销能力

### 2.3 工具执行的三级闸门（运行时，接 docs/02 §6）

```
第 1 闸：元数据分级（ToolMetadata）
   read_only=true            → 直接执行
   needs_approval=true       → 进入第 2 闸
   risk=high & destructive   → 直接拒绝（audit: permission.denied）

第 2 闸：员工权限规则表（G1 的"总是允许/总是拒绝"）
   agent_permission_rules(id, agent_id, tool_name, pattern, effect CHECK(allow,deny),
                          created_by_user_id, created_at, revoked_at)
   匹配顺序：deny 命中 → 直接拒（不再问人）→ allow 命中 → 视同批准 → 都不中 → 第 3 闸
   pattern 语义：write_file → 先 realpath() 规范化并解析符号链接，再按目录范围匹配；bash → 完整命令等值匹配；http_request → 域名通配（*.example.com），受连接器 allowed_hosts 硬约束

第 3 闸：人工审批（四选项）
   PERMISSION_REQUESTED 挂起（D4 计数器 +1）→ 用户决定（**审批卡绑定该次调用完整参数的 input_hash——批准的是不可变内容，参数一变即失效**，v1.1）：
   allow_once    → 本次执行
   allow_always  → 本次执行 + 写入 allow 规则（带 pattern，G1）
   reject_once   → 本次拒绝
   reject_always → 本次拒绝 + 写入 deny 规则
```

所有闸门决定（放行/拒绝/审批/规则写入）全部进审计链（§3）与事件流。

## 3. 审计链（哈希链，fail-closed）

```
audit_log(seq INTEGER PK, ts, actor_type CHECK IN (user, agent, system, curator),
          actor_id, action, resource_type, resource_id, detail JSON,
          prev_hash, hash)
# hash = sha256("v2|" + JSON数组[seq, ts, actor, action, resource, detail, prev_hash])，链式向前
# （v2，C6 外审回稿 S10：v1 用 "|" 直接拼接存在字段边界歧义——actor_id 含 "|" 时可与相邻字段互换而哈希不变；JSON 数组规范化无歧义）
```

**必入链的动作**：审批（请求与四种决定）、三级闸门的所有拒绝、grant 授予/撤销、权限规则的写入与回收、工具执行（risk≥medium 的完成与失败）、角色变更、登录、审计验证本身。

**校验**：启动时顺链重算哈希比对（几万条毫秒级）；管理页"验证审计链"按钮随时手动跑（现场篡改演示用）。

**防整库重写（v1.2 加强；v1.7 断言诚实化）**：**每追加 100 条及每次正常退出**都把链头哈希原子写入库外文件 `data/chain-head.txt`（临时文件 + rename）。校验分两级：① 内部链一致性（全量重算）；② 快照锚点比对（覆盖至最近一次快照，其后新条目由①保护）。**能力边界如实声明**：两级校验防的是"仅改数据库不改文件系统"的篡改（最常见场景）；对拥有整个 data/ 目录写权限的攻击者，库与快照可被同时重写，链条无法自卫——**真正的锚点是用户将 chain-head.txt 备份到数据目录之外**（界面与文档提示此操作）。

**校验失败的行为**：进入只读诊断模式，界面提供断点定位、审计导出、链校验和受控备份恢复入口；任务、工具和治理写入全部禁用。使用已验证备份恢复后，经重启和完整校验恢复业务操作。

## 4. 提权防线（写死的设计约束）

- grant / 权限规则 / 角色**只能由人类用户经 API 修改**（角色门控：owner 或 admin）
- 数字员工的工具面、提炼 fork 的白名单（docs/03 §3）**均不包含任何治理工具**——agent 永远无法给自己加权限
- skill 由 agent 演化可以，但 skill 内容只是知识，不改变任何权限边界

## 5. 种子组织（演示数据，幂等 seeder）

```
组织：演示科技有限公司
工作区：日常办公 ／ 数据分析
用户：你(owner) · 王明(admin) · 李蕾(member)
数字员工：
  小文（办公助理 @日常办公）
    grants: skill[Excel处理, 文档模板] + connector[HTTP]
    内置文件能力: 按任务 scope、保护路径及审批规则执行
    预置规则: allow write_file 指向日常办公数据目录的规范化真实路径
    bash 只读命令按 ToolMetadata 与动态判定执行；需审批的命令规则按完整命令等值匹配
  小刚（数据分析 @数据分析）
    grants: skill[SQL查询]，不授 HTTP（演示默认拒绝）
    内置文件能力: 按任务 scope、保护路径及审批规则执行
```

文件系统表示受控内置文件能力，按实际工具体系映射，不标记未运行的 MCP 服务为已连接。演示流程：李蕾管理操作返回 403；小刚任务的工具声明无 HTTP，直接调用也被拒绝；小文工作区目录规则允许后续写入，已加入 scope 的外部目录另行审批，scope 外拒绝。模型回答按实际内容记录。

## 6. API 增量

```
# 管理面（角色门控）
GET/POST/PATCH   /api/workspaces | /api/agents | /api/skills | /api/connectors
POST /api/skills/:id/versions                      # 发新版本（不可变）
GET/POST/DELETE   /api/grants                      # 授予/撤销
GET/POST/DELETE   /api/agents/:id/permission-rules # 规则表（已授权清单）
PATCH /api/memberships/:id/role                    # 仅 owner
# 审计
GET  /api/audit?actor=&action=&resource=           # 过滤查询
POST /api/audit/verify                             # → {ok, broken_at?}
# 审批（docs/02 已定义）：POST /api/tool-approvals/:id
```

事件枚举扩展：`governance.grant_changed` / `governance.rule_changed`（遵循"先加事件再实现"纪律）。

## 7. 验收（里程碑 M2）

1. 小文写入真实文件 → 弹审批；选"总是允许"后同目录写入不再弹，已加入 scope 的独立外部目录仍弹审批，scope 外直接拒绝
2. deny 规则命中 → 不弹窗直接拒 + 入审计链
3. 小刚没有 HTTP grant → 实际工具声明中无 HTTP，直接调用被拒绝；核对 bash 与 MCP 无法绕过能力边界，模型回答如实记录
4. 李蕾（member）访问管理面 API → 403
5. grant 撤销后，小文下一次任务的 skill 索引立即少了该项
6. **篡改演示**：修改独立验收库中的一条 audit 记录 → 重启进入只读诊断并指出断点；业务操作禁用，使用已验证备份恢复并重新校验后正常（G2）
7. 审批链验证按钮：绿 ✓；全链查询按 actor/action 过滤可用

## 8. 测试

- 三级闸门真值表单测（元数据 × 规则 × 审批的全组合矩阵）
- 规则匹配：规范化目录范围 / 完整命令等值 / 撤销后失效 / deny 优先于 allow
- 链校验：正常通过、篡改中间一条 → 断点定位、删行 → 断点定位
- grant 解析：skill 索引过滤、连接器工具注册过滤、user 可见性
- 角色中间件 × 种子数据的幂等性
