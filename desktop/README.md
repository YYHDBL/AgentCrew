# AgentCrew 桌面工程

在本目录执行 `npm ci` 安装依赖，然后执行 `npm run dev` 同时启动 Electron 窗口与真实 Python sidecar。开发环境需要 `uv` 可执行文件在 `PATH` 中；main 进程会在应用数据目录启动后端并监管重启。`npm run typecheck` 检查 TypeScript；`npm run build` 完成类型检查并构建 main、preload 和渲染层。依赖及运行程序均位于本目录的 `node_modules`，仓库根目录无需安装前端依赖。

`src/main/index.ts` 管理窗口、托盘和原生能力，`src/main/sidecar.ts` 管理后端进程。`src/preload/index.ts` 只暴露端口、临时凭证、通知、打开本地路径与休眠控制接口。`src/renderer/src/App.tsx` 使用带 Bearer 的健康检查显示连接状态；任务与审批功能仍由后续任务卡接入。
