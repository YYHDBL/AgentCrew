# M4 · macOS 发布、产品打磨与演示任务卡

> 日期：2026-10-10，Asia/Shanghai ｜ 状态：任务规划完成，实施未开始 ｜ 分支：desktop-shell ｜ 当前目标平台：macOS arm64

依据：[阶段规划](../decisions/alignment-record.md)、[桌面架构](../architecture/desktop-shell.md)、[桌面前端计划](../plans/2026-09-29-desktop-frontend.md)、[F010 桌面运行与设置](../features/F010-desktop-lifecycle.md)、[ADR-010 模型适配](../decisions/ADR-010-openai-compatible-provider.md)。执行安排见 [M4 发布实施计划](../plans/2026-10-10-m4-release.md)。

## 目标与当前条件

交付一个能通过 DMG 安装、从 Finder 启动、通过界面配置模型、执行真实任务并安全退出的 AgentCrew macOS 应用。应用携带 Python sidecar、依赖和经校验的模型计数资源，用户无需准备开发工具即可使用。工作台、运行中心、员工管理和组织管理保持中文、清楚的任务状态、键盘操作和统一设计。发布材料包含安装指南、能力边界、真实办公与数据分析演示、机制说明和可核查证据。

M3 完整验收按所有者要求暂停，[M3 报告](../acceptance/M3.md)与 [M3-14 报告](../acceptance/M3-14.md)继续保留真实状态。M4 发布检查使用实际安装包核查发布涉及的流程，不能将其结果改写为 M3 全量验收通过。M3 全量回归、系统通知实际点击、真实休眠唤醒和完整技能复用贯穿的未完成证据进入本阶段风险清单。

当前 `npm run build` 仅生成开发构建输出；`Sidecar.start()` 始终调用 `uv run python` 并依赖仓库的 `backend/` 路径，尚无生产可执行文件路径。已有 PNG/ICNS 图标可以直接用于应用包。模型配置 GET/PATCH 已存在，renderer 尚无 main/aux 配置界面。DeepSeek Chat Completions 和 GLM Anthropic 协议适配均有实现；当前 `load_counter()` 仅允许固定 DeepSeek 模型、端点与编码，双供应商完整可用仍需 M4-09 核查和实施。

## 领取、证据与发布纪律

每张卡完成实现、适用检查和 `docs/acceptance/M4-XX.md` 后才判定通过，公开证据保存到 `docs/acceptance/assets/M4/XX/`。独立数据、模型凭据、运行数据库、构建中间结果使用已忽略的 `desktop/.artifacts/`、`data/m4-intermediate/` 和 `desktop/release/`；禁止使用 `/tmp`。发布清单必须保存源代码提交、依赖锁文件、架构、实际系统版本、应用版本、包大小及 SHA-256。

全部业务核查使用真实 Electron、真实 sidecar、当前有效模型、SQLite、文件、网络服务和进程。使用成熟库处理 CSV、JSON、DMG、归档与模型协议；不能生成模拟响应、伪造电源事件或跳过审批。UI 证据默认采用 DOM、控件边界与实际焦点记录，视觉检查在用户明确要求时执行。不得削弱安全与数据检查来缩短发布流程。

构建和本地验证由任务授权覆盖。签名身份、Apple 公证材料与实际模型凭据作为执行时的外部依赖记录；凭据缺失只阻挡对应卡片。公开上传、发布下载链接及发布版本说明需要所有者明确授权，授权前准备好完整、可审阅的包与报告。任务卡不要求自动提交其他人的改动；M4-01 先确认变更归属与可追溯的构建基准，后续按卡片保存独立提交。

## 顺序与依赖

| 卡片 | 负责内容 | 前置条件 | 负责范围 | 状态 |
|---|---|---|---|---|
| M4-01 | 发布范围、版本与构建基准 | 所有者允许进入 M4；M3 验收暂停 | 文档、构建清单 | 未开始 |
| M4-02 | 自包含 sidecar 与计数资源 | M4-01 | 后端、构建 | 未开始 |
| M4-03 | 生产启动、监管与数据路径 | M4-02 | Electron 主进程 | 未开始 |
| M4-04 | 首次使用与模型配置界面 | M4-01、M4-03 | renderer、配置接口 | 未开始 |
| M4-05 | 正式应用包、图标与 DMG | M4-02 至 M4-04 | 桌面构建 | 未开始 |
| M4-06 | 安装、升级、备份与数据保护 | M4-05 | 安装包、迁移、恢复 | 未开始 |
| M4-07 | 页面视觉与交互统一 | M4-04、M4-05 | renderer | 未开始 |
| M4-08 | 办公与数据分析真实演示 | M4-06、M4-07 | 材料、任务、连接器 | 未开始 |
| M4-09 | 双供应商与完整计数预算 | M4-02、M4-04 | Provider、计数、设置 | 未开始 |
| M4-10 | 签名、公证与发布安全核查 | M4-05；最终内容来自 M4-06 至 M4-09 | 打包、签名、安全 | 未开始 |
| M4-11 | 使用指南、机制说明与演示脚本 | M4-06 至 M4-09 | 文档 | 未开始 |
| M4-12 | 最终安装包验收与发布交付 | M4-01 至 M4-11 | 前后端共同交付 | 未开始 |

M4-02/M4-03/M4-04/M4-05 优先形成可安装应用。M4-09 的官方计数资源需要纳入最终包，完成后重新执行资源构建与安装包检查。M4-07 与 M4-09 在资源和文件归属明确时可以独立推进。正式发布以 M4-12 所核验的同一包为准。

## M4-01 · 发布范围、版本与构建基准

**目的与范围。** 明确本次交付内容和证据，建立可重建的版本基准。M3 验收保持暂停，现有功能与未完成检查均按实际状态记录。

**文件。** 新增 `docs/release/release-manifest.schema.json`、`docs/release/M4-release-scope.md`；维护 `desktop/package.json`、`backend/pyproject.toml` 的应用版本与锁文件；验收为 `docs/acceptance/M4-01.md`。版本号在本卡确定，全程使用同一个版本值，不在构建脚本中随意生成另一个产品版本。

**实施。** 核对工作区的已跟踪/未跟踪文件与归属，保留全部已授权修改；完成适用的静态检查后保存发布构建基准。定义应用名称 AgentCrew、稳定 appId、arm64 架构、实际支持的 macOS 系统范围、数据目录、包名、产物路径及构建清单字段。最低系统版本由可用测试环境的实际安装结果和依赖要求共同确定，不从当前开发机版本直接推断。

清单记录 `M3_15=paused`、系统通知点击和真实电源事件的当前证据，以及本轮检查范围。发布说明只宣称已验证能力。明确手动安装升级、数据备份与删除政策；历史任务、审计与工件保持保留，自动删除不属于本阶段交付。

**核验与失败。** 在 `desktop/` 执行 `npm run check:api && npm run typecheck && npm run build`，在 `backend/` 执行 OpenAPI 检查。核对版本、清单 schema、锁文件和源提交可以相互关联。身份 token、真实模型密钥、用户库、开发环境数据进入发布输入，或者清单将暂停验收写成已通过，均判失败。

## M4-02 · 自包含 Python sidecar 与模型计数资源

**目的与范围。** 将现有后端变为生产可执行文件，并携带运行所需的依赖和模型计数资产。生产运行无需 `uv`、外部 Python、项目源码或首次访问模型资源仓库。

**文件。** 新增 `backend/packaging/sidecar_entry.py`、`backend/packaging/agentcrew-sidecar.spec`、`scripts/release/build_sidecar.py`、`scripts/release/verify_sidecar.py`；修改 `backend/pyproject.toml`、`backend/uv.lock`、`backend/agentcrew_server/memory/tokenizer.py`；新增 `backend/tests/test_packaged_resources.py`。构建资产位于 `desktop/.artifacts/m4-build/`，生产资源进入 `desktop/resources/sidecar/darwin-arm64/` 与 `desktop/resources/tokenizers/`。这些生成目录在本卡加入 `.gitignore`，构建脚本和资源清单进入版本控制。

**实施。** 使用锁定版本的 PyInstaller，沿用桌面架构规定的 onefile sidecar。打包配置收集 FastAPI、uvicorn、Pydantic、SQLite FTS5、MCP、croniter、tokenizers、证书与必要动态模块，完整 import 检查使用真实打包程序。处理 onefile 的子进程与信号行为、受控解包目录权限和签名要求，禁止写入应用资源目录或 `/tmp`；核对启动时间符合现有监管预算。

模型 tokenizer、官方消息编码文件和许可证在构建时按固定 revision 下载并核对 SHA-256。生产从应用只读资源路径加载，经明确配置传入；可写缓存使用应用数据目录。开发模式保持可复现资源取得流程。缺文件、错误哈希或未知编码在模型请求前明确失败，不使用估算计数或自动替换模型。M4-09 新增的正式资源也进入相同清单和路径规则。

**核验命令。** 在仓库根目录执行 `backend/.venv/bin/python scripts/release/build_sidecar.py --arch arm64 --output-dir desktop/resources/sidecar/darwin-arm64`，再执行 `backend/.venv/bin/python scripts/release/verify_sidecar.py --binary desktop/resources/sidecar/darwin-arm64/agentcrew-sidecar --data-dir desktop/.artifacts/m4-sidecar/data`。脚本只在本卡实施后成为可执行命令。

**通过条件。** 使用受限 PATH，从独立工作目录启动真实 sidecar，取得就绪标记与健康响应；完成数据库初始化、FTS5 查询、迁移、审计校验、计数资源读取和一次实际模型工具调用。运行期间对 tokenizer 来源站点的访问次数为零。明确验证 SIGTERM、父进程终止后的所有打包子进程清理；完成模型通信使用真实外部服务。产物清单保存模块、资源、依赖许可与 SHA-256。缺失动态模块、依赖开发机缓存、读写应用包或出现残留进程均判失败。

## M4-03 · 生产启动、监管与数据路径

**目的与范围。** 使用正式 sidecar 资源启动应用，保留现有本地认证、健康握手、重启上限与退出预算。

**文件。** 修改 `desktop/src/main/sidecar.ts`、`desktop/src/main/index.ts`，必要时修改 `backend/agentcrew_server/cli.py`；新增 `desktop/tests/m4-sidecar.mjs`。如资源路径需要内部环境字段，后端只接收明确的资源目录，不扩充 preload 的进程/文件权限。

**实施。** `app.isPackaged` 分支通过 `process.resourcesPath` 定位当前架构可执行文件；开发分支保持当前仓库启动方式。生产绝对路径启动，校验资源存在和权限；缺失资源立即提供具体错误与日志位置，不转向开发命令。数据统一位于稳定 appId 对应的 userData/data，日志、tokenizer 工作目录和备份路径均在可写目录。通过 Finder 启动时的工作目录、PATH 和环境差异不能影响启动。

令牌由主进程每次启动生成；握手仅含端口。保持 contextIsolation、sandbox、禁止 nodeIntegration、导航和新窗口边界。生产仅加载包内 renderer，不能因外部 `ELECTRON_RENDERER_URL` 环境变量加载任意网页。检查启动/重启请求去重、旧令牌失效、真正退出的模型流和子进程清理；PyInstaller 的父/子进程应全部纳入当前监管。

**核验。** 执行 `cd desktop && npm run typecheck && npm run build && node tests/m4-sidecar.mjs`。用真实打包 sidecar 验证首次就绪、端口占用、SIGKILL 后重启、超过预算的启动失败、token 轮换、父进程终止、正常退出和二次启动聚焦。保存所有实际 PID、端口与脱敏日志，保护其他应用进程。

**通过条件。** 从不依赖仓库的目录启动成功；一次应用会话只有一个有效任务服务，关闭与重启没有重复 sidecar。资源错误可定位，30 秒启动预算及已有清理预算有真实计时证据。禁止扩大超时或关闭监管来掩盖打包失败。

## M4-04 · 首次使用与模型配置

**目的与范围。** 全新安装后用户通过设置界面配置 main/aux，完成真实任务；模型凭据始终属于本机用户。

**文件。** 新增 `desktop/src/renderer/src/ModelSettings.tsx`、`desktop/tests/m4-onboarding.mjs`；修改 `desktop/src/renderer/src/App.tsx`、`styles.css`，复用 `backend/agentcrew_server/settings.py` 与已有 `/api/settings`。如需额外配置核查接口，先更新 `docs/contracts/openapi.yaml`、后端实现与生成类型，再接界面。

**实施。** 设置新增“模型连接”分页，提供 main/aux 的协议、模型、地址、密钥配置状态和已验证计数配置。已知模型的窗口、编码 revision/hash 使用服务端受控元数据；不让用户手填哈希来声称模型可用。密码输入不回填完整密钥，空值保持现有密钥，清除使用明确操作；GET 与保存响应仅返回脱敏状态。owner/admin 修改，member 只读。

全新空配置仍可启动桌面与设置页，工作台说明需要完成配置，发送按钮按实际服务状态启用。连接验证实际检查鉴权、模型请求及计数资产，区分“配置已保存”与“连接验证通过”；说明验证产生的真实请求。已有配置启动不重复引导，保存冲突保留草稿和真实原因。环境变量覆盖的字段说明实际生效来源，运行中尝试保持原绑定配置，下一次尝试读取新配置。

**核验。** 执行 `cd desktop && npm run check:api && npm run typecheck && npm run build && node tests/m4-onboarding.mjs`，后端执行 `uv run pytest -q tests/test_config.py tests/test_settings.py tests/test_context_budget.py`。独立 userData 首次启动，不拷贝开发 config；从界面输入真实凭据后完成新会话、文件审批/写入/读取和一次 aux 作业。再验证真实无效密钥、不可达地址、缺计数资产、重新启动、更新与清除密钥、member 只读、保存失败和草稿保持。

**通过条件。** 用户无需终端编辑配置即可使用。真实密钥不出现在 API GET、DOM 属性、事件、日志、公开检查 JSON 或应用包；密钥配置文件按现有安全契约限制权限。身份切换取消旧请求并清理敏感草稿；协议错误保持明确失败。

## M4-05 · 正式应用包、图标与 DMG

**目的与范围。** 生成包含 renderer、preload、main、sidecar 和计数资源的 AgentCrew.app 与 DMG。

**文件。** 新增 `desktop/electron-builder.yml`、`desktop/tests/m4-package.mjs`；修改 `desktop/package.json`、`desktop/package-lock.json`、`desktop/assets/brand/README.md`。构建使用锁定的 electron-builder；图标复用 `desktop/assets/brand/agentcrew.icns`。

**实施。** 配置稳定 appId、productName、版本、arm64 架构和最低系统要求，准确列出 `out/`、sidecar、tokenizer 及许可资源。应用代码进入 ASAR，sidecar 作为 external resource，并保持可执行权限。应用包、Finder、Dock、窗口和 DMG 的名称与图标一致。定义 `npm run package:mac` 和 `npm run dist:mac`，DMG 支持拖入 Applications，资源中不含开发数据库、`.env`、config 密钥、运行日志或测试材料。

**核验。** 执行 `cd desktop && npm run check:api && npm run build && npm run package:mac && node tests/m4-package.mjs && npm run dist:mac`。检查 .app 布局、ASAR 文件清单、Mach-O 架构、资源许可、PNG/ICNS 原生解码和可执行权限；从生成 .app 真正启动，并通过 UI 完成一项真实任务。打包脚本扫描登记的真实凭据值和不允许的资源路径，生成应用与 DMG SHA-256 清单。

**通过条件。** DMG 内应用可安装且资源完整，原生应用身份稳定，图标随包存在，启动没有开发路径依赖。壳与 sidecar 架构一致。仅运行 Vite build、只检查按钮可见或只验证文件存在不能判定本卡通过。

## M4-06 · 安装、升级、备份与数据保护

**目的与范围。** 在安装后的应用中核对首次运行与覆盖升级，保护现有任务、工件、记忆、计划、授权及审计。复用已有迁移与治理备份恢复。

**文件。** 新增 `desktop/tests/m4-install-upgrade.mjs`、`docs/release/data-and-upgrade.md`；必要时修改 `backend/agentcrew_server/db/migrations.py`、`governance/backups.py` 与 `desktop/src/main/sidecar.ts`。新增迁移必须按既有版本方式实施，不能编辑旧迁移伪装兼容。

**实施。** 使用独立 macOS 用户目录或独立虚拟机安装 DMG 中的真实 .app；Finder 启动时不提供仓库、全局 Python、uv 或 Node 运行条件。记录宿主机系统、架构和实际外部程序可用性。沙盒依赖的系统程序和可选办公软件依赖在说明中清楚列出。

为覆盖升级准备带真实任务、审批、工件、记忆、技能版本、授权、计划和审计链头的旧版本数据。迁移前使用 SQLite backup 与完整文件/链头一起保存一致性备份，不复制运行中的单独主 DB。替换 .app 后，数据路径稳定、迁移可查、历史身份和关联保持；中断任务与待核验调用使用现有恢复语义，过期计划登记 missed。退出后核对工件 SHA/mtime、数据库、链头和权限。降级不兼容时明确拒绝写入，恢复测试只使用独立数据与已有受控入口。

**核验。** 执行 `cd desktop && node tests/m4-install-upgrade.mjs --app <已安装的AgentCrew.app绝对路径>`。全新配置、覆盖安装、重新启动、迁移失败、低磁盘/不可写目录、审计异常只读诊断和备份恢复分别保存实际证据；故障注入只作用于测试拥有的文件。用户数据不会因卸载 .app 自动删除。

**通过条件。** 不依赖开发环境即可安装运行；升级前后关键行和文件校验可关联，迁移失败保留可恢复数据；恢复后审计两级验证通过，无未知副作用自动重试。无真实旧版安装包时，本卡的覆盖安装可核验，旧版本升级兼容项必须保持未验证，禁止伪造旧版包。

## M4-07 · 页面视觉与交互统一

**目的与范围。** 完成现有浅色桌面设计的全页面可用性，保持用户已确认的导航、工作台和设置布局。

**文件。** 修改 `desktop/src/renderer/src/styles.css`、`main.tsx`、`App.tsx`、`Conversation.tsx`、`run-center/`、`agents/`、`admin/`、`automation/`、`DesktopSettings.tsx`、`ModelSettings.tsx`、`Notifications.tsx`；维护 `desktop/src/renderer/INTERFACE.md`；新增 `desktop/tests/m4-ui.mjs`，复用现有 Markdown、对比度与 workbench 检查。

**实施。** 统一标题、正文、辅助文字、间距、图标尺寸、控件高度、按钮主次、状态与错误显示；设置与治理操作放入对应设置分页，工作台聚焦当前任务。图标按钮保留中文 tooltip、无障碍名称和焦点状态，审批与破坏性决定保留明确文字。代码、Markdown、长文件路径、中文换行和大量历史均可阅读；真实产物卡关联实际文件状态。

逐页覆盖空数据、加载、失连、无权限、成功、失败与长内容。760/960/1100/1440 宽度检查内容、输入框与弹窗；大字号、键盘导航、Escape、焦点恢复、减少动态效果和菜单定位必须有效。身份切换后，设置、通知、历史和敏感内容清理一致。运行回放仍只读，待核验和暂停队列的入口保持原安全语义。

**核验。** 执行 `cd desktop && npm run typecheck && npm run build && node tests/markdown.check.mjs && node tests/contrast.check.mjs && node tests/workbench.integration.mjs && node tests/m4-ui.mjs`。使用安装包重复关键界面操作，保存 DOM 结构、边界、焦点和实际错误。包含真实模型回复、真实审批、产物、服务重启与 member 身份，不能设置组件内部状态形成测试画面。

**通过条件。** 无横向溢出、被遮挡的主要控件、离屏菜单、失去焦点的弹窗或只以颜色表示的状态；布局调整后旧业务操作仍能执行。记录发现与修复的实际问题，确认后文档描述最终设计。

## M4-08 · 办公、表格与数据分析真实演示

**目的与范围。** 以安装包完成文档整理、表格计算、受控 HTTP 和数据分析，核验实际结果质量。

**文件。** 新增 `scripts/seed/m4_demo_data.py`、`desktop/tests/m4-demo.mjs`、`docs/release/demo-guide.md`；复用 `scripts/seed/m3_automation_data.py` 与现有工具、连接器、审批和产物 API。需要修改产品时限定于真实缺陷，不新增未经需求定义的专用工具。

**实施。** 生成独立 UTF-8 文档、Markdown 材料和 CSV 表格，保留原始订单/记录编号、日期、空值、重复记录和计算预期；输出材料清单与 SHA-256。办公流程读取多份材料并输出汇总文档与 CSV 核对表；HTTP 流程连接实际运行、持久化请求的本地服务，通过已授予连接器完成一次受审批约束的操作。数据分析流程输出类别汇总、时间变化、异常说明与依据，并产生真实 CSV 和 Markdown 报告。

预期计算由成熟 CSV 库和 Decimal 基于原始文件独立完成，不把正确答案交给模型。核对数量、金额、分类、空值/重复记录处理与引用来源，保留已批准调用的参数哈希、请求、任务、事件水位和文件 SHA/mtime。模型输出错误必须真实失败并调查，不能只凭 completed 状态宣称正确。发布演示材料明确所用文件格式和外部程序需求，能力说明与实际包内容一致。

**核验。** 在仓库根目录执行 `backend/.venv/bin/python scripts/seed/m4_demo_data.py --output-dir desktop/.artifacts/m4-demo/materials`；执行 `cd desktop && node tests/m4-demo.mjs --app <已安装应用绝对路径> --materials .artifacts/m4-demo/materials`。覆盖批准、拒绝、越界、HTTP 响应前中断后的待核验、刷新和产物打开。输入文件与真实上游副作用保持可校验。

**通过条件。** 两项主线演示均从安装应用界面完成，输出计算与来源正确，所有外部副作用可追溯；输入不被静默改写，拒绝后无目标写入。所需文件可用原生默认应用打开，打开失败展示真实原因。

## M4-09 · 双供应商与完整上下文计数

**目的与范围。** 核对并补齐 GLM Anthropic 与 DeepSeek Chat Completions 的 main/aux 配置、计数、压缩、工具、恢复和切换能力。

**文件。** 修改 `backend/agentcrew_server/memory/tokenizer.py`、`backend/agentcrew_core/memory/budget.py`、必要的 `provider/`、`providers.py`、`settings.py`、`config.py`；维护 `docs/tech/provider-verification.md` 与实际协议 ADR；新增 `backend/tests/test_provider_switching.py`、`desktop/tests/m4-providers.mjs`。官方资源通过 M4-02 构建清单携带，设置复用 M4-04。

**实施。** 先核查每个供应商的官方模型窗口、正式计数接口或 tokenizer、消息/工具编码与许可证，记录完整来源、revision、hash 和验证结果。DeepSeek 已有实现继续复用；GLM 必须有与实际协议对应的系统、历史、工具结果和输出预算计数，不能套用 DeepSeek 模板或字符估算。已有供应商代码存在不等于当前完整任务链可执行。

两槽各自绑定协议、模型、地址、预算与密钥摘要；运行中任务保持创建/尝试快照，设置变更只影响合法的新请求绑定。恢复校验 context_fingerprint；历史模型/usage 保持实际来源。校验工具调用参数、reasoning/thinking 回传、流中断与真实上游拒绝，不输出隐藏推理。无法取得官方计数依据或有效凭据时记录对应阻挡，保持未通过，不放宽预算。

**核验。** 通过 `/api/settings` 真正切换两个供应商；每个供应商分别验证纯文本、工具批准/拒绝、实际文件、aux 摘要/审查、接近预算的真实上下文、压缩、取消、重启和恢复。执行后端对应 Provider/context 测试与 `cd desktop && node tests/m4-providers.mjs --app <已安装应用绝对路径>`。保留模型请求/usage/预算、实际请求地址、配置版本、失败分类和文件证明，凭据脱敏。

**通过条件。** 两个供应商均有本次真实完整任务链证据，main/aux 与上下文计数可用，切换不改变历史快照，缺失计数资源在发请求前明确拒绝。新增正式模型资源后重新构建 sidecar 和 DMG，最终安装包验证包含这些资源。

## M4-10 · 签名、公证与发布安全核查

**目的与范围。** 使最终应用能够按 macOS 正常安装与信任流程使用，核查发布包的原生代码、凭据和权限边界。

**文件。** 新增 `desktop/build/entitlements.mac.plist`、`scripts/release/verify_package.py`；维护 `desktop/electron-builder.yml`、构建命令和 `docs/release/signing.md`。签名/公证凭据通过本机凭据设施或受控环境提供，不能进入源码、命令输出或构建清单。

**实施。** 正式发布使用 Developer ID 签名、hardened runtime、公证与票据；按实际 Electron/native module 要求配置最小 entitlement。顺序覆盖 sidecar 内嵌 Mach-O、onefile 资源、依赖模块、Electron 应用和 DMG；最终内容完成后签名，签名后不再改写资源。证书、Team ID 与公证记录必须绑定最终版本和 SHA-256。

核对 renderer 安全配置、限定 preload、本机 token、受控连接器与 Seatbelt；检查 bash 子进程环境不含模型 key 或壳 token。扫描应用包、资源、ASAR、DMG、日志及公开材料的真实凭据；包含第三方组件许可。生产启动错误不得通过开发服务器 URL、关闭沙盒、放宽文件/网络范围或要求用户关闭系统安全检查处理。

**核验。** 对实际 .app 执行 `codesign --verify --deep --strict --verbose=2 <应用路径>` 和 `spctl --assess --type execute --verbose=4 <应用路径>`，通过公证工具验证并检查票据；在独立用户/系统环境以实际下载隔离属性安装 DMG 并启动。安装包内执行真实受限文件访问、命令与连接器边界测试。所有检查记录命令、退出码与身份/架构/包哈希，隐藏凭据。

**通过条件。** 最终包签名完整、公证成功、正常 Gatekeeper 路径启动通过，安全测试真实拒绝越界。签名材料缺失时本地构建检查可记录，正式发布资格保持未通过；公证失败不能写成已发布。

## M4-11 · 使用指南、机制说明与演示脚本

**目的与范围。** 用户能安装使用、理解边界并恢复数据；演示者能在 15 分钟内说明核心机制和真实结果。

**文件。** 新增 `docs/release/install.md`、`docs/release/known-limitations.md`、`docs/how-it-works.md`、`docs/release/interview-qa.md`；完成 M4-08 的 `demo-guide.md`，更新 `docs/architecture/architecture.md` 与相关入口链接。

**实施。** 安装指南按最终 DMG 实际流程编写，覆盖架构/系统、拖入安装、首次模型设置、密钥状态、数据位置、手动升级、完整备份/恢复、错误日志和真正退出。能力说明准确区分本机任务、外部模型通信、授权/审批、自动化与系统生命周期；列明 M3 暂停验收和仍无证据的通知/电源项目。没有实际自动更新、跨架构或办公格式能力就不能宣称存在。

机制说明以真实代码路径和事件样例解释 Electron 三进程、狭窄 preload、本地认证、TaskRun/attempt、SSE/事件投影、工具与待核验、记忆/压缩/Skill、Grant/Seatbelt/审计、cron missed/skipped 和双供应商。结构图使用 Mermaid。演示脚本给出准备材料、具体指令、批准位置、预期可核查结果和实际失败处理，时间安排为 2 分钟安装配置、5 分钟办公执行、3 分钟运行回放、3 分钟治理/自动化、2 分钟数据分析和机制说明。

**核验。** 从全新独立环境按文档执行安装和首次任务，再按演示脚本操作。用真实计时记录是否可以在 15 分钟内完成，模型响应等待如实计入；文档的路径、控件、命令、链接与最终包一致。问答引用事实与证据，历史未完成项保持说明。

**通过条件。** 无隐藏的终端配置步骤、失效链接、开发路径依赖或未经验证的产品承诺。公开材料通过密钥扫描，演示输出与 M4-08 的真实任务和工件相互关联。

## M4-12 · 最终安装包验收与发布交付

**目的与范围。** 对将要交付的同一签名 DMG 完成最后核查、记录版本与遗留状态，再交付所有者审核。

**文件。** 新增 `desktop/tests/m4-release.mjs`、`docs/acceptance/M4.md`、`docs/release/CHANGELOG.md`；正式清单生成到 `desktop/release/`，脱敏证据公开到 `docs/acceptance/assets/M4/12/`。状态更新到本文件和阶段规划。

**实施。** 将最终代码与资源构建一次，记录提交、锁文件、资源清单、架构、版本、DMG/.app SHA-256、签名和公证标识。使用该包完成 DMG 安装、首次配置、办公/数据分析任务、审批、产物打开、Run Center 回放、治理拒绝、定时计划、隐藏窗口继续执行、退出快照、真正退出无残留及重新启动 missed。核查回放不增加模型/工具调用或副作用。

M3 历史暂停状态继续保留；本卡独立记录实际包中通过、失败、未验证项。系统通知或休眠相关行为若有可获得的真实证据就关联，否则保留未验证和产品边界。不能模拟系统点击或让电脑真实休眠来伪造证据。验收失败修复后，重建、重新签名/公证并重新核查新包，旧包证据不能直接继承。

**最终命令。** 在 `backend/` 执行 `uv sync --locked` 和 OpenAPI 检查；在 `desktop/` 执行 `npm ci`、`npm run check:api`、`npm run typecheck`、`npm run build`、`npm run dist:mac`、`node tests/m4-release.mjs --app <实际安装应用绝对路径> --dmg <最终DMG绝对路径>`。运行本阶段新增及改动所涉及的后端测试，公布实际退出码。M3 全量测试不会因执行本卡自动恢复，报告将未执行范围明确列出。

**发布条件。** 所有发布必需卡片通过，最终包可安装运行、配置可用、核心结果正确、数据与安全检查通过、签名公证完整，报告列出所有外部依赖和遗留风险。发布所需的包、清单、使用说明、许可和证据准备好后提交所有者批准；获得明确授权才上传或公开版本。完整 M4 通过还要求双供应商和演示证据齐全，M3 的完整验收仍按独立报告判定。

**硬阻挡。** 凭据泄漏、越权执行、数据损坏、审批绕过、重复外部副作用、安装后依赖开发环境、关键退出残留、签名/公证失败、真实结果错误，以及对未通过检查的成功宣称，都阻挡正式发布。未获得的通知点击/真实电源证据在报告中保持明确状态，不影响记录其他检查的实际结果。
