# 后端服务层详设（FastAPI sidecar）

> 维护：后端 Agent ｜ 版本：v1.0（2026-09-30）｜ 上游：[harness-design](./harness-design.md)（引擎）、[desktop-shell](./desktop-shell.md)（进程监管）、[../contracts/openapi.yaml](../contracts/openapi.yaml)（接口契约）
> 本文收拢**服务层**（非引擎）的设计：启动/关闭、配置、CORS、日志、错误码、并发、数据目录。全部为技术决策（后端 Agent 权限内），无产品分叉。

---

## 1. 启动序列（固定顺序）

```
读启动参数与环境（--port/--data-dir/--parent-pid, AGENTCREW_TOKEN）
→ 获取 instance.lock（已锁=另一实例在跑，退出码 2）
→ 打开 SQLite（WAL + busy_timeout=5s）
→ 执行迁移（版本冲突 → 拒绝启动报版本号）
→ 审计链校验（失败 → 进入【只读诊断模式】：仅挂 /api/health 与诊断端点，
   业务端点全部 503 + 断点信息；不执行任何 agent/工具操作）
→ 种子检查（M2：组织/工作空间/小文 幂等 seed）
→ 启动对账（running→interrupted；dispatched 无终态→核验/待核验，见 harness §8）
→ 起事件总线 + RunManager + 定时调度（P5）
→ uvicorn 监听 127.0.0.1:port
→ stdout 打 AGENTCREW_READY {"port":N}（无 token）
```

## 2. 配置体系

**优先级**：环境变量 > `data/config.json` > 代码默认值。

```jsonc
// data/config.json（运行时可改；0600 权限；data/ 不入 git）
{
  "models": { "main": {...GLM}, "aux": {...GLM-flash} },   // 含 api_key（单用户本机文件存储，界面只回显尾 4 位）
  "gates":  { "max_steps": 40, "stall_seconds": 600, "repeat_limit": 3, "global_concurrency": 8 },
  "limits": { "max_files": 20, "max_file_mb": 50, "max_folders": 5 },
  "log_level": "info"
}
```

- 桌面端设置页改"关窗行为/数据目录"存 Electron 侧（desktop-shell 范畴）；**模型与守门参数**走后端：`GET /api/settings`（key 脱敏只回尾 4 位）、`PATCH /api/settings`（改 gates/limits 即时生效；改 models 重建 provider 实例）
- 任何配置变更写审计链（actor=owner）

## 3. CORS 与请求边界

- **CORS：allow all origins**——安全性由 Bearer token 承担而非 Origin：token 是显式请求头、无 cookie、无浏览器自动携带，wildcard origin 不构成 CSRF 面；Electron renderer（file:// 或 app:// 源）与 localhost 调试都免配置
- 请求体上限 60MB（材料 50MB + 余量，超限 413）；SSE 连接数本地场景不设上限（单用户桌面）

## 4. 错误响应信封与错误码表

统一：`{ "error": { "code": "...", "message": "...", "detail": {...} } }`（HTTP 状态码由 code 映射）。

| code | HTTP | 场景 |
|---|---|---|
| INVALID_PATH / NOT_FOUND | 400/404 | 路径非法 / 资源不存在 |
| OUT_OF_SCOPE | 403 | 工具目标超出任务 scope（含原因与合法范围） |
| APPROVAL_PENDING | 409 | 等待审批时发送指令 |
| APPROVAL_STALE | 409 | 审批已处理或 input_hash 不符 |
| PENDING_VERIFICATION | 409 | resume/继续队列被待核验调用阻塞（detail 含清单） |
| QUEUE_EMPTY / QUEUE_PAUSED | 409 | 继续队列时无指令 / 状态不符 |
| INVALID_TRANSITION | 409 | FSM 非法迁移（detail 返回当前合法动作） |
| DIAGNOSTIC_MODE | 503 | 只读诊断模式下访问业务端点 |

新增错误码纪律：先进本表再写实现（与事件枚举同款纪律）。

## 5. 日志

- `data/logs/sidecar.log`：文本格式，`LOG_LEVEL` 可配（默认 info）；**轮转 5MB × 3 份**；token 与 api_key 永不落日志（写入前过滤）
- 日志（运维）与审计链（司法）边界：log 排查问题可删；audit_log 不可篡改永在

## 6. 优雅关闭（SIGTERM/SIGINT）

```
停收新请求 → 取消运行中任务（状态留 running，不写终态——下次启动对账为 interrupted，
诚实优于伪造完成）→ flush 事件与日志 → 写审计链头快照 chain-head.txt → 关 DB（checkpoint WAL）
→ 释放 instance.lock → 退出码 0
```

强杀（kill -9）路径：什么都不做——启动对账与两级链校验就是为它设计的。

## 7. 并发与调度

- 业务任务（TaskRun）：全局并发 8（config 可调），超出排队 FIFO，跨会话公平
- **aux 任务**（压缩/提炼/审计/标题）：并发上限 2，队列 FIFO；前台优先——新用户指令到达时 2 秒内取消运行中的 aux fork（hermes 纪律）
- SQLite 单写者：EventStore 写入经 executor 串行化 + busy_timeout 重试（C2 已定）

## 8. 数据目录布局

```
data/
  agentcrew.db            # 全部 SQLite 表（WAL）
  config.json             # §2
  chain-head.txt          # 审计链头外置快照（每 100 条 + 退出时）
  instance.lock
  logs/sidecar.log(.1/.2/.3)
  conversations/<id>/materials/     # 任务导入材料
  artifacts/<task_run_id>/          # 大输出工件与产物
  # M1+：USER.md、workspaces/<id>/MEMORY.md、agents/<id>/soul.md（见 memory-system）
```

## 9. 备份与保留（M0 口径）

- 备份 = 退出后整目录复制 `data/`（文档写明）；自动备份/归档策略 → M4 打磨期定（对齐纪要 v1.6 已记）
- API 不做版本化（/api 无版本号）：壳与 sidecar 同包同发，一起升级，无跨版本兼容负担

## 10. 与任务卡的映射

C1 增：config.json 加载链（env>file>默认）、CORS 中间件、日志轮转、SIGTERM 优雅关闭；C3 增：错误信封中间件与错误码表落地；C7 增：settings GET/PATCH（脱敏）；C8 增：aux 并发 2 与前台优先。
