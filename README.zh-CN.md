<p align="center">
  <img src="desktop/assets/brand/agentcrew-icon.png" width="96" alt="AgentCrew 应用图标" />
</p>

<h1 align="center">AgentCrew</h1>

<p align="center">让 AI 员工在本地桌面持续处理任务，执行过程清楚可查。</p>

<p align="center"><strong>macOS · Apple Silicon · Electron + Python</strong></p>

<p align="center"><a href="README.md">English</a> · 简体中文</p>

AgentCrew 将任务执行、人工审批、持久记忆和定时任务整合到一个桌面应用。你可以交办任务、添加文件与文件夹材料，查看实时回复、执行步骤、工具调用和结果。新的指令可以进入队列，中断的任务可以恢复，运行中心保留完整的执行记录供你检查。

工作区、聊天记录、记忆、模型凭据和审计记录保存在本机。模型请求发送到你配置的服务商，HTTP 与 MCP 连接器按照明确授予的权限访问外部服务。

## 功能

| 功能范围 | 具体能力 |
| --- | --- |
| 任务工作台 | 创建会话、添加文件与文件夹、查看流式回复、排队执行指令，以及取消任务。 |
| 工具与审批 | 按照访问规则读取与写入文件、执行命令、发起 HTTP 请求，并由用户审核需要审批的操作。 |
| 持久记忆 | 管理用户知识、工作区知识、员工身份、可复用技能，以及可检索的会话上下文。 |
| 恢复与运行记录 | 恢复中断任务，在运行中心查看步骤、工具结果、审批和审计事件。 |
| 定时任务 | 创建与管理重复执行的任务，审核模型提出的计划，并接收本地通知。 |
| 员工与权限管理 | 管理员工、工作区、角色、资源访问范围、连接器与审批规则。 |

## 从源码运行

桌面构建面向 **Apple Silicon 架构的 macOS**。开发环境需要 **Python 3.12 及以上版本**、**uv**、**Node.js 22.12 及以上版本**和 **npm**。请确保 `uv` 位于 `PATH` 中，Electron 会在开发模式下自动启动 Python 任务服务。

```sh
git clone https://github.com/YYHDBL/AgentCrew.git
cd AgentCrew

cd backend
uv sync --locked --python 3.12

cd ../desktop
npm ci
npm run dev
```

应用打开后，进入“模型连接”，配置主模型和辅助模型，保存配置并选择“验证连接”。主模型负责执行任务，辅助模型负责摘要与记忆处理。连接验证会向服务商发送少量真实请求。

当前模型目录中的 **DeepSeek V4.1 Flash（OpenCode Go）**具备经过核查的上下文窗口和固定版本的 tokenizer 资源，可以用于任务执行。为两个模型配置选择该目录项，填写服务商的 API 密钥。源码运行首次使用时会下载固定版本的 tokenizer 文件并核验哈希；任务执行需要通过上下文计数检查并能够访问模型服务。

选择工作区与员工，添加任务所需材料并发送指令。在执行过程中处理审批请求，完成后检查实际结果与运行记录。应用界面使用简体中文。

## 数据与运行条件

开发模式的数据目录为 `backend/data/`。安装应用使用 Electron 的用户数据目录，“桌面设置”页面可以显示确切位置并在 Finder 中打开。模型凭据保存在本机配置文件中，分享备份或诊断材料时请保护数据目录。

定时任务需要应用与电脑保持运行。关闭窗口时可以让应用驻留托盘；退出应用或关闭电脑会停止执行。应用重新启动后会登记期间错过的计划，这些计划不会自动补跑。

## 构建 macOS 安装包

安装依赖后，在仓库根目录执行：

```sh
backend/.venv/bin/python scripts/release/build_sidecar.py --arch arm64
cd desktop
npm run dist:mac
```

构建会携带 Python 任务服务、依赖、固定版本的模型计数资源和依赖许可文件。产物写入 `desktop/release/`，其中包括 `AgentCrew-0.4.0-arm64.dmg`。打包配置声明最低系统版本为 macOS 13。Developer ID 签名和公证需要相应的 Apple 凭据，上面的命令本身不保证产物已经签名或公证。

## 开发与贡献

安装接口生成工具的锁定依赖，然后检查接口契约与桌面构建：

```sh
cd desktop
npm ci --prefix tools/api-generator
npm run check:api
npm run build
node tests/m3-contract-types.mjs
```

后端检查与打包说明见[开发指南](docs/development.md)。报告问题和提交修改前，可以阅读[贡献指南](CONTRIBUTING.md)。

| 目录 | 内容 |
| --- | --- |
| `backend/agentcrew_core/` | Agent 执行、模型适配、工具、记忆策略与恢复逻辑。 |
| `backend/agentcrew_server/` | 本地接口、持久化、运行服务、定时调度与权限管理。 |
| `desktop/` | Electron 应用、React 界面、生成的接口类型与桌面测试。 |
| `docs/contracts/` | 用于验证与代码生成的 HTTP 和事件契约。 |
| `scripts/` | 验证脚本、数据准备工具和发布构建工具。 |
