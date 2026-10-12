# AgentCrew 文档

[项目首页](../README.zh-CN.md)介绍应用能力、环境要求、模型配置和从源码运行的方法。[开发指南](development.md)说明验证命令、接口生成、运行数据与 macOS 打包流程。[贡献指南](../CONTRIBUTING.md)说明问题报告与修改要求。

## 接口契约

| 文档 | 内容 |
| --- | --- |
| [HTTP API](contracts/openapi.yaml) | 请求、响应、错误结构与操作标识，供后端检查和桌面类型生成使用。 |
| [事件协议](contracts/events.md) | 事件信封、执行事件与流式订阅约定。 |
| [事件 JSON Schema](contracts/events.schema.json) | 用于生成事件类型和运行时校验器的正式 schema。 |
| [权限接口](contracts/m2-operations.md) | 身份、资源授权、连接器、规则和审计操作。 |
| [运行与调度接口](contracts/m3-operations.md) | 运行记录、任务计划与审查操作。 |
