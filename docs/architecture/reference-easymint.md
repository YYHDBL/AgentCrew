# 参考借鉴 · EasyMint（桌面壳 + pi 引擎的完整落地）

> 后端 Agent 维护 ｜ 源码：`workMate/EasyMint`（v0.30.2，Electron + 内嵌 Pi SDK，MIT）
> 结论先行：**最值得吸收的三件事——事件流传输设计（序列号+快照+环形缓冲）、experience-service 记忆治理（M1 对照蓝本）、沙盒四张保护表的分层思想。**

## 按 AgentCrew 卡片/模块的借鉴映射

| 我们的模块 | 借鉴什么 | EasyMint 位置 |
|---|---|---|
| C3 SSE 事件流 | **序列号 + 双级缓冲重放**：每事件带全局 sequence；环形缓冲（全局 2000/会话 500）+ 游标补播；游标过期返回特殊码→前端走全量快照（"快照+补播合并"范式） | `app/main/services/app-event-bus.ts`、`agent-service.ts:2165` |
| C3/C8 流事件形态 | **累计全文快照式流事件**（替换而非拼接，乱序/丢帧幂等）；**磁盘与流式同构归一化**（tool_use/input 统一格式，历史解析同样归一） | `event-bridge.ts`、`chat-store.ts`、`session-service.ts:normalizeToolCallBlocks` |
| C6 审批 | ask_user 挂起工具四招：**abort 时取消挂起并广播关闭；先登记 pending 再广播**（新订阅者不漏）；选项 value/label 分离；recommended 徽章 | `agent-service.ts:548 createAskUserTool` |
| C8/C10 会话管理 | **同一会话只允许一个写者实例**（activationPromises）；"标记侧进行中回合"区分引擎 isStreaming 残留；历史会话最小模式打开、发消息时重建工具集 | `agent-service.ts`（竞态防御集中地） |
| C10 子进程清场 | trackChild/退出杀进程组/detached 树杀/Windows taskkill /T | `app/main/services/process-registry.ts` |
| M1 记忆系统 | **experience-service 全套**：索引/正文分离（注入只有索引，正文模型自己 read）；双通道触发门槛（fix 对 8 次/大轮 15 次）；**值不值得沉淀由模型判断**；按技术栈投递（本项目 4+通用 4+栈匹配 3）；送达与使用分开计量；容量 100 淘汰进 archive 带理由；**目录即作用域**（不落第二真相源）；损坏改名存证后重建 | `experience-service.ts` + `learn-gate.ts`（全仓最值得逐行读的文件） |
| 未来·执行边界 | 沙盒分层：判定层（启发式+可读理由）与强制层（OS 沙盒）分离；**规则单一来源**（判定层与沙盒 allowWrite 同源编译）；四张保护表——系统核心/裸设备/凭据/**持久化提示词载体**（skills/experiences/MCP 配置/白名单文件本身，"改一次就绕过判定层"，三档全禁）；域名白名单走用户态代理 + strictAllowlist；项目运行区依赖重定向（40+ 环境变量、安全 git config 播种不带凭据） | `permission/access-policy.ts`、`sandbox/manager.ts`、`development-runtime.ts` |
| M5 多 Agent | 委派 = 同引擎再开会话（子会话独立 jsonl 落盘）；父会话只存摘要注入；模型唯一来源收敛；深度=1；`list_agents/read_agent_log` 阶梯观察工具（先结论后细节） | `task/executor.ts`、`task/registry.ts` |
| MCP（未来） | **懒加载 broker**：只注册 search_mcp_tools/call_mcp_tool 两个稳定入口，命中才连 server（防全量 schema 挤爆上下文）；server 描述进 prompt 前必过 sanitizeForPrompt | `permission/mcp-broker.ts` |
| 工程纪律 | "规划定稿即落盘 docs/design/、实现即回收"；增量文档只追加（保 API 缓存前缀）；**CI 守卫脚本锚定规则**（源码落盘路径与保护清单交叉核对，漏登记即红） | `CLAUDE.md`、`scripts/check-em-home-paths.mjs` |

## 有意分歧（记录，不抄）

- **7 道 Gate**：EasyMint 刻意"纯提示词+skill 分载，代码只做下限"；我们是可审计的数字员工平台，**Gate 应显式落成 run_events 状态事件**——产品形态决定取舍。但"同一规则只存一份、按阶段按需 Read 子 skill"的防漂移纪律要抄
- **引擎内嵌 vs sidecar**：它语言同构所以进程内嵌零 IPC；我们 Python sidecar 维持原判，只抄它的竞态防御两招（见上表 C8/C10 行）

## 不适用（跳过）

手机端局域网遥控、Pi 扩展体系、供应商 OAuth/品牌矩阵、Pi 配置双向同步、auto-updater 等桌面周边。
