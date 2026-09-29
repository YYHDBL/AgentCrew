# ADR-009 · GLM 适配走 Anthropic 兼容端点

状态：已接受（2026-09-29，C4 实测裁定）

**背景**：M0-cards §3 原选型"GLM 走 OpenAI 兼容端点"（依据：官方兼容层成熟、DeepSeek 同协议）。用户提供的 key（glm-5.3-flash 资源）实测：OpenAI 兼容端点 `/api/paas/v4` 返回 `{"code":"1113","message":"余额不足或无可用资源包"}`；同一 key 在 Anthropic 兼容端点 `https://open.bigmodel.cn/api/anthropic/v1/messages` 流式/非流式/工具调用全部可用。

**决策**：
1. GLM 适配器（`agentcrew_core/provider/glm_anthropic.py`）走 **Anthropic Messages 协议**（`/v1/messages`，`x-api-key` + `anthropic-version`）。
2. 协议差异封在 Provider 适配层内（harness-session §8 本来就要求各家 tool_use 差异封在本层）；DeepSeek 适配器（M4）仍走 OpenAI 兼容协议——双适配器格局与"多供应商归一化"的简历叙事一致。
3. 上游流式响应的 SSE 消费用 `httpx.aiter_bytes` + 本层约 50 行的确定性行解析器（分片跨 chunk/CRLF/多行 data/注释行，单测覆盖）。**v1.4 裁定"SSE 用现成库"针对的是前后端之间的事件流传输**（后端 sse-starlette、前端 fetch-event-source，不变）；上游模型 API 是客户端角色，不引 anthropic SDK（保持薄自研、避免第二棵依赖树），也不引第三方 SSE 客户端库。

**实测依据（2026-09-29，glm-5.3-flash）**：
- 流式事件序列：`message_start → ping → content_block_start(thinking) → thinking_delta* → content_block_start(tool_use) → input_json_delta(partial_json)* → content_block_stop → message_delta(stop_reason+最终 usage) → message_stop`；`partial_json` 碎片会切在 token 中间（`{"` / `city` / `":"` / `北京"` / `}`），适配层必须缓冲拼装。
- 多轮工具回合：assistant 回传 thinking 块（带 signature）+ tool_use 可行；**只回传 tool_use 不带 thinking 块同样 200**（比 Anthropic 官方严格模式宽松，C8 组装历史时仍按带 signature 回传的保守姿态）。
- thinking 控制：默认开启；请求级 `thinking: {"type":"disabled"}` 关闭；`{"type":"enabled","budget_tokens":N}` 开启。
- 缓存：`usage.cache_read_input_tokens` 字段存在，但同前缀重复请求（1460 token 前缀，隐式与显式 cache_control 各测）命中恒为 0——**当前无缓存命中信号，v1.5 悬案"是否做缓存中断检测"结论：不做**；system prompt 尾部注入等前缀稳定姿态保留（无害且为将来命中做准备）。
- 坏 key：HTTP 401 `{"error":{"message":"令牌已过期或验证不正确","type":"401"}}` → 分类 AUTH、不重试。

**影响**：config.models 的 `base_url` 指向 Anthropic 端点（`backend/data/config.json`，key 落 git 忽略目录）；M0-cards §3 的"GLM 走 OpenAI 兼容端点"表述由本 ADR 修订；C8 消息组装需按 Anthropic 块格式（text/thinking/tool_use + tool_result）。
