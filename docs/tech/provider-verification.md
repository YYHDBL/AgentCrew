# Provider 接入验证记录（GLM · Anthropic 兼容端点）

> 维护：后端 Agent ｜ 验证日期：2026-09-29（M0-C4，接入首周纪律 harness-session §8）
> 环境：glm-5.3-flash @ `https://open.bigmodel.cn/api/anthropic`（ADR-009）
> 纪律：全部真实 key 真实请求（脚本 `scripts/provider/verify.py`，命令可复现）；key 存 `backend/data/config.json`（git 忽略），**本记录不含 key**。

## ① 无工具纯文本流式

```
$ cd backend && uv run python ../scripts/provider/verify.py plain
  text_delta: '重' / '放' / '这些' / '事件' / …（36 块）
  usage: input=21 output=38 cache_read=0
  done: stop_reason=end_turn
[结论] 文本长度=66；thinking 块数=0（disabled 生效应为 0）
[结论] 事件计数={'text_delta': 36, 'usage': 1, 'done': 1}
[PASS] ①
```

## ② 带工具定义 → 流式 tool_use 拼装 → 多轮回传

```
$ uv run python ../scripts/provider/verify.py tooluse
-- 第一轮：模型决定调用工具
  tool_call_started: get_weather（进行中，不带参数）
  tool_call: id=call_7bf8c7ae16fb481da66dd03b name=get_weather input={"city": "北京"}
  usage: input=169 output=12 cache_read=0
  done: stop_reason=tool_use
-- 第二轮：回传 tool_result → 最终文本
  usage: input=197 output=20 cache_read=0
  done: stop_reason=end_turn
[结论] 第二轮文本：北京现在天气晴朗，气温25°C，南风2级，天气不错！
[PASS] ②
```

要点（curl 探测原文，碎片会切在 token 中间——适配层缓冲拼装的直接依据）：

```
data: {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use",
       "id": "call_ca7c2b96…", "name": "get_weather", "input": {}}}
data: {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\""}}
data: {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "city"}}
data: {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "\":\""}}
data: {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "北京\""}}
data: {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "}"}}
```

多轮回传：assistant 回传 `tool_use` + user 回传 `tool_result` 正常；**不带 thinking 块回传同样被接受（HTTP 200）**——比 Anthropic 官方（强制 signature 回传）宽松；C8 仍按保守姿态（带 signature 回传）。

## ③ 坏 key → 错误分类

```
$ uv run python ../scripts/provider/verify.py badkey
  error: class=auth retryable=False status=401 msg=令牌已过期或验证不正确
[结论] 分类=auth retryable=False status=401（请求只发出一次——无 retry 日志即未重试）
[PASS] ③
```

上游错误体原文：`{"error":{"message":"令牌已过期或验证不正确","type":"401"}}`。另记录：同 key 走 OpenAI 兼容端点报 `{"code":"1113","message":"余额不足或无可用资源包"}`（归 UNKNOWN 不重试——非瞬态账户状态）。

## ④ 缓存与思考参数

```
$ uv run python ../scripts/provider/verify.py cache
  第1次 input=1460 cache_read=0
  第2次 input=1460 cache_read=0
  第3次 input=1460 cache_read=0
-- thinking 参数对比
  thinking={'type': 'disabled'} → thinking_delta 块数=14（本次；另一次实测 290）
  thinking={'type': 'enabled', 'budget_tokens': 512} → thinking_delta 块数=114
[注] 上游不一致：流式响应下 disabled 仍可能输出 thinking 块（非流式才生效，
     curl 双路实测确认）；消费侧将 thinking_delta 视为可忽略事件即可，
     多轮回传不带 thinking 块亦被接受（已实测）
[结论] 缓存命中字段存在但实测恒 0（ADR-009：不做缓存中断检测）
[PASS] ④
```

缓存结论（v1.5 悬案）：`usage.cache_read_input_tokens` 字段存在，但同前缀（~1460 token）重复请求隐式命中恒 0；显式 `cache_control: {"type":"ephemeral"}` 断点同样为 0（curl 双路实测）。**不做缓存中断检测**；system prompt 尾部注入等前缀稳定姿态保留（无害，为将来命中做准备）。

## 对 C8 的接口约定（本层结论）

- 消息格式为 Anthropic 块式：`content` 为块数组（text / thinking / tool_use），工具结果用 `tool_result` 块（`tool_use_id` 配对）；
- `thinking` 参数：非流式下 `{"type":"disabled"}` 可关；流式下不确定（可能仍出思考块）——循环层忽略 `thinking_delta` 事件即可，不影响正文与工具调用；
- `max_tokens` 必须 > `budget_tokens`（enabled 时）；
- 归一事件全集：`text_delta / thinking_delta / tool_call_started / tool_call / usage / done / error`；`done.stop_reason ∈ end_turn / tool_use / max_tokens`；
- usage 在 `message_delta` 给终值；缺失时适配层记 0 并告警（实测均有）；
- 中断语义：已向下游产出事件后的流中断 → `error(network, retryable=true)` 上交（上游无续传原语，自动重发会重复输出——如实声明）；未产出任何事件前的失败按分类退避重试（≤3 次）。
