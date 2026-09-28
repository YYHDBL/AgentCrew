# ADR-002 · Agent Harness 全自研

状态：已接受（Q8）

**背景**：可选 litellm / LangGraph / CrewAI / fork Eigent。
**决策**：循环、工具注册、权限门、事件发射、多供应商薄适配层全部自写；不引入 agent 框架与 LLM 聚合库。
**理由**：简历核心是"自研 Harness"；框架会让叙事失效；learn-workbuddy 提供教学兜底，Eigent 提供 Run 模型参考。
**影响**：GLM 走 OpenAI 兼容端点自封装；DeepSeek 为第二个适配器（M4）；多供应商归一化只在双供应商落地后才可如此表述。
