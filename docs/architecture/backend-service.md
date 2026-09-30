# 后端服务层详设（FastAPI sidecar）

> 维护：后端 Agent ｜ 版本：v1.3（2026-09-29，C2 落地：诊断端点定为 `/api/diagnostics`，契约已同步；v1.2=C1 错误码表补 4 项）｜ 上游：[harness-design](./harness-design.md)、[desktop-shell](./desktop-shell.md)、[../contracts/openapi.yaml](../contracts/openapi.yaml)
> 本文收拢**服务层**设计：启动/关闭、写通道、SSE 边界、配置、CORS、日志、错误码、并发、数据目录。全部技术决策。

---

## 1. 启动序列（固定顺序）

```
读启动参数与环境（--port/--data-dir/--parent-pid, AGENTCREW_TOKEN）
→ 获取 instance.lock（已锁=另一实例在跑，退出码 2）
→ 打开 SQLite（WAL + busy_timeout=5s）
→ 升级前快照（v1.9 修正：目标版本 > 当前版本时，用 `VACUUM INTO backups/db-v<旧>-<时间戳>.sqlite`
   生成自洽快照——**含 WAL 中已提交事务**；禁止裸复制主库文件，WAL 模式下已提交事务可能仍在
   -wal 文件中，单复制 agentcrew.db 会丢事务（SQLite 官方要求）；保留最近 3 份）
→ 执行迁移（每条迁移单独事务，SQLite DDL 可回滚；版本冲突[目标<当前] → 拒绝启动报两版本号；
   迁移中途失败 → 该事务回滚 + 进入只读诊断模式，界面提示从 backups/ 还原）
→ 审计链校验（失败 → 只读诊断模式：仅 /api/health 与 /api/diagnostics，业务端点 503 DIAGNOSTIC_MODE）
→ 种子检查（M2 幂等 seed）
→ 启动对账（running→interrupted；dispatched 无终态→核验/待核验）
→ 起事件总线 + RunManager + 定时调度（P5）
→ uvicorn 监听 127.0.0.1:port → stdout AGENTCREW_READY {"port":N}
```

恢复语义分两层（v1.9 澄清）：**迁移回滚** = 只还原 db 快照（schema 迁移不触碰工件，升级快照无需含工件）；**灾备** = 用户整目录复制 data/（db 与工件成对，诊断界面写明）。

## 2. 写通道（v1.1 重写——全部库变更的唯一通道）

**所有数据库变更**（事件追加、投影更新、审批/队列/规则/审计/核验/产物状态/配置持久化）一律经过**同一个串行写通道**：单一写 executor（`asyncio.to_thread` 上的全局写锁）。

- **事件与投影同一事务**：append run_events + 更新对应投影表在一个 SQLite 事务内提交，要么都生效要么都不
- **提交后才发布**：事务提交成功后才向事件总线/SSE 扇出——订阅者永远看不到未提交的事件
- **发布顺序 = 提交顺序（v1.9）**：发布动作在写通道内、事务提交之后**同步入队**（内存入队非阻塞，不违反"禁止跨外部等待持有事务"）——全部写入经同一串行通道，SSE 各订阅队列按 global_seq 严格递增；杜绝"任务 A 提交 seq 10、任务 B 提交 seq 11，却按 11→10 发布导致客户端游标跳过 10"的乱序窗口
- **禁止跨外部等待持有事务**：事务内不得等待模型、工具、HTTP、SSE；工具执行 = 先写 prepared（事务一）→ 执行（无事务）→ 写结果（事务二）
- 读操作走只读连接（WAL 并发读不阻塞写通道）

## 3. SSE 边界（v1.1 新增——补播与实时的交接协议）

- **游标语义**：`from` / `after_seq` 一律**排他**（返回 seq > 游标的事件）；`from=0` 重放全部；事件信封 `{global_seq, ...}` 单调递增
- **无遗漏交接**（服务端单进程保证）：订阅建立时，SSE 端点**先注册实时监听（带缓冲）→ 再从库读历史至当前已提交 head → 转入实时**；缓冲中 `global_seq ≤ 历史 head` 的帧丢弃——两段之间不存在窗口
- **FSM 快照配套游标**：`GET /conversations/:id/state` 返回 `at_global_seq`（快照反映到的位置）；客户端规则：先取快照，再从 `at_global_seq` 续播事件，`≤ at_global_seq` 的事件已被快照吸收、直接跳过
- **慢消费者**：每连接独立队列上限 1000 条；溢出 = **服务端终止该订阅**并发送最后一帧 `event: resync`（data 含最后连续 `global_seq`），客户端从该游标重连补读——不静默丢帧
- **连接上限**：并发 SSE 连接 ≤ 32（单用户桌面足够）；超限 503
- 优雅关闭时向所有订阅发 `event: shutdown`（客户端保存游标，见 §7）

## 4. 配置体系（v1.1 版本化重写）

**优先级**：环境变量 > `data/config.json` > 代码默认值。

```jsonc
{ "models": {...}, "gates": {...}, "limits": {...}, "log_level": "info" }  // 同 v1.0
```

- **配置版本化（v1.1）**：每次修改配置生成新 `config_version`；**每个任务与 aux 执行开始时绑定当时的配置版本（不可变快照，记入 attempt 的 context_fingerprint）**；`PATCH /api/settings` 只切换**后续执行**使用的版本——运行中的 main 流与 aux 任务继续用各自绑定版，跑完即止
- **环境变量覆盖字段**：PATCH 写入被 env 覆盖的字段 → 200 + `ignored_fields` 列表（文件已存但非有效值；GET 返回 `effective_source: env|file`）
- **需重启生效字段（C8 二轮回稿）**：`gates.global_concurrency`（全局并发信号量在进程启动期构造）——PATCH 该字段正常落盘与版本化，响应带 `restart_required: ["gates.global_concurrency"]` 明示重启后生效；GET 始终显示当前生效值
- **密钥语义**：GET 永不返回完整 key（只回尾 4 位 + 是否已配置）；PATCH 中 `api_key` 字段**缺省 = 不变**；清空须显式 `api_key_clear: true`
- **写入失败**：先写文件（临时+rename），成功后才切换内存生效版本；文件写失败 → 500 CONFIG_WRITE_FAILED，内存配置不变
- 一切配置变更入审计链（含 config_version）

## 5. CORS 与请求/资源上限（分开定义，v1.1）

- **CORS allow all origins**：安全由 Bearer 承担（显式头、无 cookie、无浏览器自动携带——无 CSRF 面）；第四轮审查确认保留
- **请求体上限 60MB**（仅约束 JSON 本体）；**资源上限在服务端执行时另查**：材料导入逐文件 ≤50MB、单任务 ≤20 文件/5 文件夹（超限逐文件拒绝带原因）——传路径不传字节，体积控制必须在复制时强制
- SSE：连接 ≤32、每连接队列 ≤1000（§3）

## 6. 错误响应信封与错误码表

统一：成功 = `{"data": ...}`；错误 = `{"error": {"code","message","detail"}}`（204 与 SSE 流不套信封）。

| code | HTTP | 场景 |
|---|---|---|
| INVALID_PATH / NOT_FOUND | 400/404 | 路径非法 / 资源不存在 |
| OUT_OF_SCOPE / PROTECTED_PATH | 403 | 超出任务 scope / 受保护路径 |
| UNAUTHORIZED | 401 | Bearer 缺失/错误（C3 鉴权；C1 起保留映射，v1.2 补） |
| METHOD_NOT_ALLOWED | 405 | 方法不支持（框架 405 统一信封，v1.2 补） |
| VALIDATION_ERROR | 422 | 请求体校验失败（框架校验异常统一信封，v1.2 补） |
| APPROVAL_PENDING | 409 | 等待审批时发送指令 |
| APPROVAL_STALE | 409 | 审批已被**不同决定**处理或 input_hash 不符（同决定重试 → 200 幂等返回首次结果，v1.1 明确） |
| PENDING_VERIFICATION | 409 | resume/继续队列被待核验阻塞（detail 含清单） |
| QUEUE_EMPTY / QUEUE_PAUSED | 409 | 继续队列时无指令 / 状态不符 |
| INVALID_TRANSITION | 409 | FSM 非法迁移（detail 返回当前合法动作） |
| QUESTION_STALE | 409 | 提问已回答/已取消后再次提交 |
| CONFIG_WRITE_FAILED | 500 | 配置文件写入失败（内存未变） |
| INTERNAL_ERROR | 500 | 未捕获异常统一信封（v1.2 补） |
| SSE_LIMIT | 503 | SSE 连接超上限 |
| DIAGNOSTIC_MODE | 503 | 只读诊断模式 |

新增错误码纪律：先进本表再写实现。

## 7. 日志与优雅关闭

日志：`data/logs/sidecar.log`，轮转 5MB×3，`LOG_LEVEL` 可配；token/api_key 永不落日志；日志（运维可删）与审计链（司法永在）分界。

**优雅关闭（v1.1 定完成条件与上限，总预算 10s）**：
```
SIGTERM/SIGINT →
1) 停止接受新请求与新任务创建；向所有 SSE 订阅发 event:shutdown（客户端保存游标）
2) 限时取消与收割（≤8s）：取消模型流、杀工具子进程组并 wait、审批等待任务保持 waiting_user
   （诚实留态，下次启动对账）；运行中任务不写终态
3) 结束全部 SSE 订阅（≤1s）
4) 关闭数据库：尝试 PASSIVE checkpoint（结果记日志；被读事务阻塞则跳过——WAL 文件留存，
   恢复路径本就依赖重放，不为 checkpoint 无限等待）
5) 写审计链头快照 → 释放 instance.lock → exit 0；超预算强制作业全部放弃按序退出
```

## 8. 并发与调度

- 业务任务（TaskRun）：全局并发 8（config 可调），超出 FIFO 排队；aux 任务（压缩/提炼/审计/标题）并发 2，前台优先 2 秒让路
- **客户端幂等（v1.1）**：`POST /conversations` 与 `POST instructions` 接受可选 `client_request_id`——服务端按其唯一去重（网络重试不产生重复任务/重复指令）
- 乐观锁 `task_runs.version` 用于服务端内部状态迁移的并发保护（不进 API 请求）

## 9. 数据目录布局

```
data/
  agentcrew.db            # 全部表（WAL）
  backups/                # v1.1：升级前 db 快照（保留 3 份）
  config.json / chain-head.txt / instance.lock
  logs/sidecar.log(.1/.2/.3)
  conversations/<id>/materials/
  artifacts/<task_run_id>/
  # M1+：USER.md、workspaces/<id>/MEMORY.md、agents/<id>/soul.md
```

## 10. 备份与保留

- 备份 = 退出后整目录复制 `data/`（db+工件成对）；自动备份/归档策略 → M4；API 不版本化（壳与 sidecar 同发，但 db 迁移独立版本化）

## 11. 与任务卡的映射

C1：config 链/CORS/错误信封/轮转/优雅关闭（**验收仅限 C1 已有能力**）；C2：写通道+同事务投影+升级快照；C3：SSE 边界全套（排他游标/交接/resync/连接与队列上限）+ 慢客户端真实验收；C5：ask_user 全链（含 POST /questions/:id/answer 与取消）；C7：materials+队列 API 级验收（**运行依赖 C8 的行为移至 C8 验收**）；C8：审批/排队/停止的完整交互验收 + 绑定配置版本。
