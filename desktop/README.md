# AgentCrew 桌面工程

本目录包含 Electron 主进程、preload 接口、React 界面和桌面测试。开发环境要求与模型配置方法见[项目首页](../README.zh-CN.md)，接口生成与打包说明见[开发指南](../docs/development.md)。

## 运行与构建

在 `../backend/` 执行 `uv sync --locked --python 3.12` 后，在本目录执行：

```sh
npm ci
npm run dev
```

开发模式自动启动 Python 任务服务。默认数据目录为 `../backend/data/`；安装应用或指定 `--user-data-dir` 时，服务使用应用用户目录下的 `data/`。

执行 `npm run build` 完成类型检查以及 main、preload 和 renderer 的构建。执行 `npm ci --prefix tools/api-generator` 安装接口生成工具，然后执行 `npm run check:api` 检查生成文件是否与正式契约一致。

## 代码入口

`src/main/index.ts` 管理窗口、托盘、通知与原生能力，`src/main/sidecar.ts` 管理后端进程启动和监管。`src/preload/` 提供受限的原生接口。`src/renderer/src/session.ts` 管理 HTTP 和会话 SSE，`App.tsx` 组合工作台、运行中心、员工管理、自动化与设置页面。

## 回归检查

执行 `node tests/m3-contract-types.mjs`，使用 `tests/fixtures/actual-review-events.json` 中的真实事件检查类型和运行时校验器。其他集成测试位于 `tests/`，执行前检查各脚本需要的模型配置和文件操作范围。测试运行数据保存在已忽略的 `.artifacts/` 或 `../data/` 目录。
