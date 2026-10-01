# AgentCrew 桌面工程

在本目录执行 `npm ci` 安装依赖，然后执行 `npm run dev` 同时启动 Electron 窗口与真实 Python sidecar。开发环境需要 `uv` 可执行文件在 `PATH` 中，并在 `../backend/` 执行 `uv sync`。默认开发数据目录为 `../backend/data`，真实模型配置放在其中的 `config.json`，该目录不提交。指定 Electron `--user-data-dir` 或运行安装应用时，后端使用相应应用数据目录下的 `data`。main 进程负责监管重启。`npm run typecheck` 检查 TypeScript；`npm run build` 完成类型检查并构建 main、preload 和渲染层。

`src/main/index.ts` 管理窗口、托盘和原生能力，`src/main/sidecar.ts` 管理后端进程。`src/preload/index.ts` 暴露端口、临时凭证、材料选择、通知、定位本地路径与休眠控制窄接口。渲染层的 `session.ts` 管理真实 HTTP 和会话 SSE，`Conversation.tsx` 展示聊天、步骤、工具、审批与提问，`App.tsx` 组合会话列表、输入和队列操作。

执行 `npm run build && node tests/c11-real.mjs` 进行真实 Electron/GLM 验收，需有效的 `../backend/data/config.json`。脚本使用 `playwright-core` 和项目 Electron，在 `.artifacts/` 中建立隔离数据目录，真实调用模型并写入三个验收文件；截图与不含凭证的结果进入 `../docs/acceptance/assets/C11/`。

执行 `node tests/c11-real.mjs --text-only` 进行相同操作与断言，文本结果保存在本次 `.artifacts/c11-*/` 目录，不进行截图操作。执行 `node tests/c11-stream.mjs` 检查提问中刷新、重启后的快照配对与新增事件、真实模型文本传输中断后的重试呈现、会话流背压产生的 resync，以及优雅关闭的 shutdown 游标。该检查通过本地代理转发真实响应并施加传输故障，排队压力指令全部取消，运行数据与文本结果保存在 `.artifacts/c11-stream-*/`。
