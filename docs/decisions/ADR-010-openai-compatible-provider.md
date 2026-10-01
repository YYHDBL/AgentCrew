# ADR-010 · Chat Completions 模型适配

状态：已接受（2026-10-01，用户指定 OpenCode Go 的 DeepSeek V4.1 Flash）

模型槽使用既有 `provider` 字段选择请求协议。`glm` 使用 Anthropic Messages；`openai-compatible` 使用 Chat Completions。主模型槽与辅助模型槽分别绑定，任务开始和恢复时将协议、模型、请求地址及密钥摘要写入 context_fingerprint。运行中的任务保持本次尝试绑定的配置。

Chat Completions 适配使用 OpenAI Python SDK 的请求客户端及 `AsyncChatCompletionStream`，由 SDK 解析 SSE 和拼装工具调用参数。内部 text/thinking/tool_use/tool_result 消息转换为原生聊天消息、reasoning_content、function tool_calls 和配对的 tool 消息。工具调用参数通过标准 JSON 库读取，再由既有工具调度器校验。非正常结束和无效参数直接产生异常，由任务监督层记录失败。

OpenCode Go 配置为 `provider=openai-compatible`、`model=deepseek-v4.1-flash`、`base_url=https://opencode.ai/zen/go/v1`；SDK 请求地址为 `https://opencode.ai/zen/go/v1/chat/completions`。请求携带 `User-Agent: agentcrew/0.1.0` 和以 conversation_id 为值的 `x-opencode-session`，恢复后沿用同一会话标识，符合 [OpenCode Go 官方文档](https://opencode.ai/docs/go/#where-can-i-use-it)。密钥仅写入受 Git 忽略的本地运行配置。

真实 Electron 回归任务 `386d7034f964420683bbec047803388a` 通过 DeepSeek 发起工具调用，在未审批状态收到真实 SIGKILL，经自动重启、界面恢复、新审批批准后完成文件写入和读取，最终 completed。历史失效审批无操作按钮，刷新后保持此行为。证据见 [deepseek-recovery-validation.json](../acceptance/assets/C12/deepseek-recovery-validation.json)。
