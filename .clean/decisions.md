# Decisions — agentcrew-desktop

- 验证命令:`backend/.venv/bin/python -m pytest -q`(230 passed,107s);desktop `npm run typecheck` + `node desktop/tests/c10-startup-stop.mjs`;c10-auth.mjs 为两相位外驱动验收脚本,不独立跑。
- 分层:未声明 `.clean/architecture.md`。既有隐式纪律 agentcrew_core(纯库,不 import server/fastapi/sqlite3,已验证)→ agentcrew_server(装配)。若要机器强制可再声明并跑 check_boundaries.py——需用户确认层序后再建。
- no-go 区:docs/ 为卡片驱动文档,验收 assets 为证据,不参与代码清理;外审回稿注释(F/S 编号)是审计线索,清理时保留。
- 既有裁定(沿用,勿当发现):CORS allow-all 由 Bearer 承担(app.py:79 第四轮审查确认);schema_v1.py 命名为迁移版本化惯例;工具异常收敛为 ToolResult 是调度器契约;CORS/重试/审计哈希 v2 等外审修复不重开。
- 本审计(2026-09-30,60f20a2):无 Critical;H1 sidecar 就绪后误杀路径 + 8 项 Medium 见 ledger.md。组合根第 5 份拷贝出现前必须抽共享工厂。
