# AgentCrew 桌面工程

在本目录执行 `npm ci` 安装依赖，然后执行 `npm run dev` 启动 Electron 窗口。`npm run typecheck` 检查 TypeScript；`npm run build` 完成类型检查并构建 main、preload 和渲染层。依赖及运行程序均位于本目录的 `node_modules`，仓库根目录无需安装前端依赖。

`src/main/index.ts` 负责 Electron 窗口与进程权限边界；`src/preload/index.ts` 当前不向渲染层暴露接口；`src/renderer/src/App.tsx` 只管理本地布局状态。接入 Python sidecar 时，由 main 管理其启动和退出，通过 preload 向渲染层暴露限定的端口与凭证接口，渲染层再按架构文档通过本机 HTTP/SSE 呈现真实任务。当前没有启动 sidecar，也没有任务或审批功能。
