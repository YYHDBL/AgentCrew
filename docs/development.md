# 开发指南

## 环境与安装

使用 Apple Silicon 架构的 macOS、Python 3.12 及以上版本、uv、Node.js 22.12 及以上版本和 npm。Python 测试使用 Python 3.12 提供的线程检查接口。运行桌面应用前，确认 `uv` 位于 `PATH` 中。

在仓库根目录执行：

```sh
cd backend
uv sync --locked --python 3.12
cd ../desktop
npm ci
npm ci --prefix tools/api-generator
```

在 `desktop/` 执行 `npm run dev`，Electron 会启动本地 Python 任务服务。模型配置方法见[项目首页](../README.zh-CN.md)。

## 后端验证

在 `backend/` 执行持久记忆、真实进程恢复和接口契约检查：

```sh
mkdir -p data
uv run pytest -q tests/test_memory_snapshots.py tests/test_memory_store.py tests/test_migrations.py tests/test_startup.py tests/test_audit.py --basetemp=data/test-output
uv run python ../scripts/check_openapi.py
```

这些检查使用真实文件、SQLite 和独立进程，不需要模型凭据。测试目录中还包含各模块的回归测试。修改数据库、审批或恢复逻辑时，应运行对应测试，并检查实际文件、数据库记录与进程退出状态。`backend/tests/fixtures/` 保存后端测试需要的真实模型记录与协议输入，`desktop/tests/fixtures/` 保存事件校验需要的真实执行记录。

需要调用模型的 HTTP 集成测试通过 `AGENTCREW_TEST_CONFIG_SOURCE` 读取本机的有效模型配置文件。部分运行中心和报告契约检查还要求准备包含真实执行、审查及技能发布记录的测试数据库。执行前阅读相应测试的 fixture 要求，使用隔离数据目录并保护配置文件中的密钥。

## 桌面与接口验证

在 `desktop/` 执行：

```sh
npm run check:api
npm run build
node tests/m3-contract-types.mjs
```

`npm run build` 包含 TypeScript 检查以及 main、preload 和 renderer 的构建。`npm run check:api` 在内存中重新生成接口文件并比较结果，存在差异时终止。

修改 `docs/contracts/openapi.yaml` 或 `docs/contracts/events.schema.json` 后，执行 `npm run gen:api` 更新 `src/renderer/src/api/generated/`，然后执行上面的验证命令。生成工具拥有独立的 `package.json` 与锁文件。

桌面集成测试位于 `desktop/tests/`。需要模型的测试必须配置真实服务与密钥，部分脚本还会执行文件写入、审批和应用重启。执行前检查脚本要求，测试产物应保存在已忽略的 `desktop/.artifacts/` 或 `data/` 中。

## 打包

在仓库根目录执行：

```sh
backend/.venv/bin/python scripts/release/build_sidecar.py --arch arm64
cd desktop
npm run package:mac
```

`build_sidecar.py` 使用 PyInstaller 构建独立的 Python 服务，并按照 `scripts/release/tokenizer_resources.json` 下载固定版本的官方计数资源、核验 SHA-256 和整理依赖许可。服务与计数资源写入 `desktop/resources/`，构建清单写入 `desktop/.artifacts/m4-build/`。

`npm run package:mac` 生成 `desktop/release/mac-arm64/AgentCrew.app`。执行 `npm run dist:mac` 生成 DMG。两条命令均读取 `desktop/electron-builder.yml`，应用版本来自 `desktop/package.json`。签名与公证需要对应的 Apple 凭据。

## 数据目录

开发模式将配置、SQLite、记忆、材料和日志保存到 `backend/data/`。安装应用使用 Electron 用户数据目录下的 `data/`，可以在“桌面设置”中定位。带有 `--user-data-dir` 的桌面测试会使用相应的隔离目录。

本地接口凭证由 Electron 为每次服务启动生成，并通过受限的 preload 接口交给 renderer。模型密钥保存在本机配置文件中。请在分享日志或备份之前检查内容，避免公开凭据和任务材料。
