# AgentCrew Agent Harness 设计（送审版）

> 维护：后端 Agent ｜ 版本：v1.7（2026-09-30，审查回稿修订版）｜ 状态：**已按第三轮外部审查修订**
> 本文自包含，审者无需先读其他文档；需要深挖时按 §10 的映射去查详设。
> 项目背景一句话：macOS 桌面端**通用 Agent 基座**（数字员工平台，办公为后续专项），单用户本地运行，Python FastAPI sidecar 单进程 + Electron 壳，全自研 Harness（不用 LangGraph/litellm），模型 GLM 双槽。

---

## 1. 设计参考与总体取舍

**原则**：循环属于 agent，机制属于 harness——所有机制都是循环上的挂钩点，循环结构永不改。

| 参考 | 借了什么 | 为什么是它 |
|---|---|---|
| pi（earendil-works） | 极简循环骨架（898 行）、"概念少而非代码少"纪律、双通道工具结果（content 给模型 / details 给 UI） | 证明自研循环可以很小；分层纪律直接学 |
| ZCode | ToolMetadata 安全元数据 + 调度器并发规则、TurnMachine 挂钩点思想、三级 compact 分层 | 工具安全语义声明化的最佳实现；M1 压缩的分层参照 |
| AionCore | 四态会话 FSM + 等待计数器 + can_send/queue/cancel 真值表 | 七仓库中最值得搬的抽象，纯函数可测 |
| Eigent | run_journal 三件套（runs/attempts/events）、恢复哲学（不隐式重启、显式 resume）、停滞看门狗（滑动窗口非累计） | 事件溯源 + 恢复的完整范本 |
| hermes-agent | M1 记忆蓝图（限额冻结快照、提炼 fork、压缩模板）——Python 源码级参考 | 语言同构；机制最完整 |
| Claude Code（那本 harness 书 + 泄露源码研究） | 主题清单对照（循环/压缩三件套/权限/fail-closed/缓存）；**反面教材**：YOLO 自动分类器、modes 我们不做 | 用它的地图，不走它的重路 |
| EasyMint | 事件传输（序列号+环形缓冲+快照补播）、沙盒分层（判定层/强制层）、ask_user 挂起四招 | 同为桌面壳+引擎的落地实例 |

**刻意不做（附理由，供审者检验）**：YOLO 式命令危险度自动分类器（静态元数据+规则+人审已覆盖；自动分类不可解释且演示翻车）；plan modes（与 question 工具重叠）；提示词缓存深度优化（GLM 缓存行为未知，C4 实测后再定）；bash 只读静态分析器（CC/ZCode 有二十个文件的静态分析，我们用"固定白名单 + 元字符一票否决"近似）；hooks/插件拦截点（后置到二次开发专项）；多 Agent（M5 加时赛，subagent-as-tool）。

---

## 2. Agent 循环

```
async def run_task(task, attempt, ctx):
    emit(RUN_STARTED)
    while True:
        resp = provider.stream(slot=MAIN, messages=ctx.messages, tools=registry.schemas())
        if resp.tool_calls:
            results = scheduler.execute(resp.tool_calls)     # §4：闸门/幂等/并发都在这里
            ctx.append_tool_results(results)                  # >32KB 外部化（M1 前即生效）
            continue
        ctx.append_assistant(resp.text)                        # messages 投影 + 事件全文
        emit(RUN_COMPLETED); return
```

- **system prompt 尾部注入环境块**（v1.7 位置修订）：日期时间、macOS、工作目录、资料目录、授权文件夹——agent 必须知道"自己在哪、今天几号"；放尾部保住静态身份/记忆前缀的缓存稳定性，动态字段每个任务开始时刷新
- 挂钩点（不改循环结构）：权限门（工具执行前）、幂等账本（执行前后）、输出外部化（入上下文前）、压缩检查（模型请求前，M1）、记忆提炼 fork（回合结束后，M1）
- 回合上限 40、token 预算守门（历史预算 80% 触发压缩，硬上限拒绝请求）

## 3. 循环稳定性三招

1. **重复调用守门**：连续 3 次相同 (tool, input_hash) 且结果相同 → 注入纠偏消息（"你已连续三次得到相同结果，换思路或汇报障碍"）；再犯 → RUN_FAILED(doom_loop)
2. **停滞看门狗**：每任务记 `last_progress_at`（模型 delta / 工具完成 / 审批决定都刷新）；超 600s 无进展且非等待用户 → RUN_FAILED(stalled)。**不做累计超时**——长任务合法，只看"最近一次进展"（Eigent 教训）
3. **输出解析重试**：tool_use 参数非法（JSON 坏/schema 不符）→ 错误作为 tool_result 回填让模型重说，上限 2 次 → RUN_FAILED(unparseable)；重试的坏响应保留但可被压缩层清理，不污染长期上下文
4. **终态时序契约（v1.7）**：模型 delta 只证明连接存活、不证明任务有进展；RUN_COMPLETED/FAILED 发出前必须——① 模型流已关闭；② 所有工具子进程已收割（杀+wait，无孤儿）；③ dispatched 无结果的调用已转待核验落库。**终态事件 = "执行真正结束"的证据，不是"模型说完话"**

## 4. 工具系统

### 4.1 准入纪律（less is more）

bash 是万能工具；专用工具必须满足以下之一才准入：
- **承担治理语义**：`http_request` 是网络唯一受治入口（域名白名单 + 外部幂等键）；bash 里的 curl/wget 走审批且无域名豁免
- **上下文/投影友好度不可替代**：`read_file` 分页读（cat 大文件对模型是灾难）；`write_file` 是 verifiable 类与 **artifacts 产物投影的唯一来源**

**M0 五件套**：`read_file` / `write_file` / `bash` / `http_request` / `ask_user`（交互原语：向用户提问挂起任务，触发 question.* 事件——不占资源类准入坑位）。**不设** grep/glob/edit/list_dir（bash+read 覆盖；find 经参数否决重入只读白名单，见 §4.3）；禁止 read_word 式碎片工具；专项工具按增量片按需加。产物口径：bash 重定向写的文件不进产物卡。

### 4.2 ToolMetadata（声明式安全元数据）

```python
name, description, parameters(JSON Schema),
read_only, destructive, risk_level(low|medium|high),
needs_approval(默认 = not read_only), concurrent_safe,
side_effect_class(verifiable | external_idempotency | outcome_unknown),
timeout_ms=60_000, max_output_bytes=100_000
```

### 4.3 bash 判定纪律

- 只读判定 = 首词 ∈ 固定白名单（ls/cat/head/tail/grep/**find**/wc/pwd/file/stat/du/diff）**且**不含任何元字符/重定向（`;` `&&` `||` `|` 反引号 `$( )` `>` `<` 换行，一票否决）**且**（v1.7，find 重入）find 的参数不含 `-exec/-execdir/-delete/-ok/-okdir/-fprintf/-fprint/-fls` 任一 token；python/awk/sed 一律非只读
- **强制层 v1.7：M0 即给 bash 套最小 macOS Seatbelt profile**（用户裁定）——写限制在任务 scope 内、**网络全禁**（http_request 恢复为唯一网络入口的强承诺）、凭据路径禁读；越界 = 内核级拒绝；M2 升级完整 profile（read 范围收紧）
- **子进程环境变量白名单（安全关键）**：仅 PATH/HOME/LANG/TZ/TERM；**绝不传 AGENTCREW_TOKEN / API key**——否则 bash 里一句 echo 就能读 token 反打本地接口，权限体系整体击穿
- 路径判定先 `realpath()`（解析符号链接）再比前缀；合法范围 = 任务 scope（工作区目录 ∪ 任务资料目录 ∪ 授权文件夹），范围外 OUT_OF_SCOPE 直接拒
- **受保护路径（v1.7）**：即使在 scope 内，工具读写双向硬禁平台内部数据与凭据——agentcrew.db / config.json / chain-head.txt / logs / USER.md / soul.md / MEMORY.md / ~/.ssh 等（"持久化提示词载体与凭据"品类，改一次即绕过判定层；读即泄密）——拒绝码 PROTECTED_PATH
- **内容信任姿态（v1.7 写明）**：文件/网页内容进入上下文不可能靠扫描防住注入（行业未解）；我们的防线 = 强制边界（Seatbelt+scope+受保护路径）+ 三级闸门 + 人审——**内容是数据，不是权限**

### 4.4 调用身份与副作用分类

- 每次调用唯一 `call_id`；审批与账本绑定完整参数 `input_hash`（参数一变审批作废）
- **三层语义分开（v1.7 精化）**：① 同一次调用的传输重试（provider 重试/超时重发）复用同 call_id 与外部幂等键；② 中断调用的核验：dispatched 无终态 → verifiable 自动核验 / 其余 pending_verification **挡住任务直到人工核验**；③ 恢复后模型的新动作 = 新 call_id 正常过闸门——重复外部效果被核验闸挡住（账本可见已完成的、明确告知 not_executed 的、pending_verification 挡在 resume 前）
- 分类判定（v1.7 收紧）：`verifiable` 仅限**原子写 + 内容哈希核验**（write_file = tmp+rename+fsync，prepared 记内容 sha256，核验 = 存在**且哈希一致**，不一致转待核验）；`external_idempotency` **仅当连接器显式声明端点承诺幂等键去重**（默认一切 HTTP 归 outcome_unknown——发键头不构成保证）；`outcome_unknown` 为外部副作用默认类
- 并发调度：destructive 串行；read_only/concurrent_safe 并行；上限 4；全局并发任务上限 8；每次 attempt 记 context_fingerprint（system_prompt/tools_schema/model 配置哈希——跨版本恢复可解释，v1.7）

### 4.5 守门参数总表（所有刹车集中一处）

| 守门 | 默认 | 动作 |
|---|---|---|
| 回合上限 | 40/任务 | RUN_FAILED(max_steps) |
| 重复调用 | 3 次同参同果 | 纠偏注入 → doom_loop 失败 |
| 停滞看门狗 | 600s 滑动窗口 | RUN_FAILED(stalled) |
| 解析重试 | 2 次 | RUN_FAILED(unparseable) |
| 工具超时 | 60s 可配 | 杀进程组，工具级 error |
| Provider 重试 | 限流/网络退避 3 次 | 冒泡 RUN_FAILED |
| 工具输出 | >32KB | 工件落盘留指针 |
| 全局并发 | 8 任务 | 新任务等待 |

## 5. 权限闸门（三级，概要）

1. **元数据分级**：read_only 直接执行；risk=high 且 destructive 直接拒；其余进第 2 级
2. **员工规则表**：deny 命中直接拒；allow 命中视同批准（pattern：路径前缀 realpath 规范化 / bash 只读白名单前缀 / 域名通配；挂员工名下可回收）
3. **人工审批四选项**：allow_once / allow_always（按预览的"工具+资源范围"写规则表）/ reject_once / reject_always；审批卡绑定 input_hash，已处理或参数变更 → 409 APPROVAL_STALE

无人值守（定时任务）= 员工规则 ∩ 任务预授权**交集**，任一未命中即拒（绝不 ASK、绝不免审批）。所有决定入哈希链审计。**提权防线**：agent 工具面永远不含治理工具。fail-closed 点：未知工具默认拒、审计链断拒启动（只读诊断模式）、pending_verification 挡 resume、http 白名单默认空=全审批。

## 6. 沙箱（M2，macOS Seatbelt）

- 分层照 CC/EasyMint：**三级闸门 = 判定层**（拦截前给可读理由）；**Seatbelt = 强制层**——v1.7 起 M0 即上最小 profile（bash：写限 scope 内 + 网络全禁 + 凭据禁读），M2 升级完整 profile（read 范围收紧）；越界 = 内核级拒绝（M0 验收即含"bash 尝试写 scope 外/连网 → 系统拦截"演示）
- 实现：调系统自带 `sandbox-exec` 生成 profile，零新依赖（CC 的 sandbox-runtime 底层同源）
- env 白名单（§4.3）独立于沙盒，M0 即生效
- 判定层与强制层的规则**同源编译**（EasyMint 纪律：两处不一致 = 同一操作两套语义）

## 7. Provider 适配层（自研薄层）

- 双槽：main（对话/工具决策）+ aux（GLM flash：压缩摘要、记忆提炼、轨迹审计、标题）
- `StreamEvent` 归一：text_delta / tool_call / usage / done / error；各家 tool_use 格式差异封在本层（GLM 走 OpenAI 兼容端点；DeepSeek 第二适配器 M4）
- **流式 tool_call 拼装**：arguments 以 JSON 碎片到达，适配层缓冲拼装完整再校验——绝不把半成品交给循环
- 错误分类（网络/限流/鉴权/内容过滤/未知）+ 重试策略；usage 缺失记 0 不失败
- 验证纪律：真实 key 首周验证流式 tool calls / usage / 错误码 / 思考参数 / **缓存命中字段**（决定是否做缓存中断检测）
- 测试纪律：禁止 mock——全部真实 GLM（aux 槽控成本），断言库与产物状态不断言模型措辞

## 8. 事件与存储衔接（Harness 视角）

- 循环各环节发**里程碑事件**（run/step/llm/tool/permission/queue/artifact…约 30 种），append-only 落 `run_events`（global_seq 全局游标 + 任务内 seq）；**流式打字块只走内存不落库**；`llm.request_done` 载荷 = 回复全文 + **本次全部 tool_use 块（call_id/tool/input/同批关系）——恢复重建对话与结果配对的唯一依据（v1.7 明确）**；状态枚举已统一（tool_calls 含 not_executed；task_runs 含 waiting_verification）
- 事件载荷携带**重建上下文所需全文**（工具输出 ≤32KB 内联、超出落工件且工件随任务存活）；grant/记忆/cron 是业务状态表，不从事件重建
- 恢复协议：启动对账（running→interrupted；dispatched 无终态：verifiable 自动核验 / 否则 pending_verification 暂停待人工提交"确认已执行/未执行"）；resume = 新 attempt + 账本注入 + 事件全文重建上下文
- 会话 FSM：四态（idle/starting/running/error）+ 等待计数器；排队：completed/failed 自动接续、用户停止 → 队列暂停

## 9. 已知取舍与弱点（请审者重点攻击）

1. bash 只读判定是**白名单+元字符静态规则**，没有 CC/ZCode 的命令解析级静态分析——复合命令一律进审批是兜底，但"误报率高"（体验成本）
2. 产物 missing 是**查看时惰性探测**（GET 时 stat），不做文件系统监控——外部移走文件后状态滞后
3. **失败任务自动接续队列**（与完成同样自动跑下一条）——产品默认，可推翻为"失败暂停"
4. 文件夹授权默认**读写**（写仍过闸门）——默认可推翻为只读
5. GLM prompt cache 行为未知——设计遵守"追加不重写"，但不做缓存中断检测（等 C4 实测）
6. read 工具无检索型伴侣（无 grep/glob 工具）——模型用 bash find/grep，大输出靠 32KB 外部化兜底；模型是否会滥用 cat 大文件是实测风险
7. 无 todo/计划工具——长任务的自我步骤管理目前靠模型自觉
8. 数据保留/归档策略未定（事件表与工件的长期增长）→ M4
9. 全局并发 8、停滞 600s、重复 3 次等默认值——拍脑袋初值，待实测校准
10. system prompt 不做版本化（单机产品；换版导致的缓存失效与行为差异接受）

## 10. 映射（深挖入口）

循环/工具/稳定性/Provider 详设：[harness-session.md](./harness-session.md)（含建表与事件枚举）｜ 权限与审计详设：[governance.md](./governance.md)｜ 记忆（M1）：[memory-system.md](./memory-system.md)｜ 沙盒参考：[reference-easymint.md](./reference-easymint.md)｜ 接口契约：[../contracts/openapi.yaml](../contracts/openapi.yaml) + [events.md](../contracts/events.md)｜ 决策档案：[../decisions/](../decisions/)（ADR-001~008 + 31 项对齐记录）｜ 实施卡：[../tasks/M0-cards.md](../tasks/M0-cards.md)
