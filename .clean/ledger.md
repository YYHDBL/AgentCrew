# Audit Ledger — agentcrew-desktop

Commit `60f20a2` (desktop-shell) · 2026-09-30 · clean-code skill audit
覆盖:inventoried 156 / reviewed 156 / sweeps 2 / new findings per sweep: 22, 0

## Coverage checklist

- backend/agentcrew_core(16)与 backend/agentcrew_server(29)全部源码:逐行读审
- desktop/src(7 ts + 配置):子代理读审,关键发现已抽查核实(sidecar.ts/index.ts 本人复读)
- backend/tests(20)+ desktop/tests(2):子代理读审,test_approvals/test_sse_api/test_review_fixes 关键点抽查核实
- scripts(5):子代理读审,c6_demo/c3/make_mess 三项声明本人核实
- docs(58 md/json):结构性略读(architecture.md 全文);docs/acceptance/assets 为验收证据,按"generated, skipped"跳过

## Findings(修复批次计划,Critical→Low)

无 Critical。

### H1 sidecar 就绪后误杀健康进程 — desktop/src/main/sidecar.ts:104,109
`actualPort !== null` 后再出现 `AGENTCREW_READY` 前缀行(:104)或残留 stdout 缓冲 >8192(:109)仍走 fail()→terminate→重启。当前后端 stdout 纪律下触发概率低,但属监管契约脆弱点。修:就绪后两条路径直接忽略(stderr 侧 :121-125 已有同款处理可照抄)。

### M 批(每改一次都在付成本/行为缺口)
- M1 main 进程无 unhandledRejection/uncaughtException 兜底;`BrowserWindow.getAllWindows()[0]`(:162)未判空,窗口销毁后 TypeError 即未捕获异常;appendFileSync(:158)磁盘异常同路径。修:判空 + 进程级兜底日志。
- M2 renderer 无 CSP(index.html 无 meta,main 无注入)。修:加 `default-src 'self'` 类 meta。
- M3 `open-path` IPC 接受任意绝对路径即 shell.openPath(index.ts:89-92),macOS 上可启动 .app——窄 API 中唯一超最小必要的特权面。修:改 showItemInFolder 或限定 data/workspace 前缀。
- M4 c6_demo.py 全程零断言(仅 :95 超时 AssertionError):HTTP 500 / 审计校验失败仍 exit 0,与 c3(有 check()→SystemExit)、verify(assert_clean_run)纪律不齐。修:补 ③2xx、④409+APPROVAL_STALE、⑦verify.ok 三处断言。
- M5 test_tools_builtin.py 三处真实外网依赖(example.com):离线即红/因错误原因绿;:156 旧版与 :312 本地对照版重复覆盖同一命题。修:删外网版,保留本地监听对照版;/tmp 固定路径改 tmp_path。
- M6 断言完整性:test_sse_api.py:350 shutdown_wins_over_overflow 未证明 overflowed 前提(对比 :289 有自校验),可空转通过;test_approvals.py:255 注释宣称 tool.failed 被记录但断言只查 permission.resolved。修:各补一行前提断言。
- M7 App.tsx:11,26 JS 断点 1100 与 styles.css:83,94 断点 979/820 不一致,980–1099px 区间布局错位。修:JS 改 matchMedia('(max-width: 979px)')。
- M8 组合根(Database→migrations→WriteChannel→EventBus→EventStore→RuntimeState→uvicorn)4 份拷贝:cli.py、c3_acceptance.py、c6_demo.py、test_sse_api.py LiveServer。现状可容忍;新增第 5 处前抽 `create_runtime(data_dir, token)` 共享工厂。

### L 批(顺手修/记录在案)
- L1 测试死代码:test_approvals.py:67-71 asm fixture 未用、:86 `if False else None` 残迹、:96-97 `_sync` 未调用;c3_acceptance.py:349-354 `count=` 赋值未用。
- L2 desktop/package.json 无 `test` 脚本,detect_stack 建议的 `npm test` 会失败;c10 两脚本为外部驱动的验收 harness,非独立 runner。
- L3 命名:approvals.py `ih`(:107,280,300)、audit.py `ts`、sse.py `data/item` 等短名/泛名 ~55 处(map 计数,脚本与局部循环变量居多)。
- L4 真重复仅两处:`_is_busy`(write_channel.py:23 / migrations.py:74 同函数)、`_find_resolution`/`_resolution_row`(approvals.py:364/374 同 SQL)。queries.py/projections.py 的同形函数属不同属主(不同表/不同游标语义),不合并。
- L5 无循环原因的函数内 import:judgment.py(shutil/shlex)、projections.py(json×2)、builtin.py(ToolMetadata)、runtime.py(verify_with_anchor)、c6_demo(socket __import__)。
- L6 agentcrew_core/__init__.py 声明"外部系统访问一律经 ports"强于现实(GLM 适配器 httpx 与工具磁盘 IO 在 core);fastapi/sqlite3 零 import 的字面声明成立。
- L7 scheduler.run 串行/并行两支重复 emit+execute(:156-167);sibling-variant schema_v1.py 是迁移版本化惯例,非变体。
- L8 check_openapi.py 自称 CI 守卫但仓库无 CI;scripts/seed/ 放的是验收驱动(命名错位,文档规则妥协);verify.py:206 访问 provider._slots;c3:401 绕过 RuntimeState.close();c5 /tmp 残留无清理。
- docs/tasks/M0-cards.md:115 make_mess.py 为 C12 未交付计划项,非悬空引用(已核实,撤销)。

## Campaign contract(拟,批淮后生效)

深度:仅上表逐项,不做行为变更;广度:H1+M1-M7 为第一批,M8/L 批第二批;行为策略:测试补强不算行为变更,sidecar 守卫属 bug 修复;检查点:每批后 backend pytest + desktop typecheck + c10-startup-stop。
