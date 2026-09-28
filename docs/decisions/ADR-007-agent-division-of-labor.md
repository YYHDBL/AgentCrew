# ADR-007 · 多 Agent 分工与目录所有权

状态：已接受（2026-09-29，用户裁定）

**决策**：docs/ 按领域分目录，所有权如下——产品：product/（用户主导）；设计：design/（**专门的设计 Agent**，用户审美把关；后端 AI 不做设计决策）；后端架构与契约：architecture/ + contracts/（**后端 Agent = 本 AI**）；功能规格：features/（产品线）；决策：decisions/（跨领域必须落 ADR）；任务卡与验收：tasks/ + acceptance/（领卡者）。前端实现由 AI 依 ui-spec 生成维护。
**理由**：多 Agent 协作需要明确"我的领域在哪、别人的决策在哪"；审美与设计非后端 AI 所长（用户明示）。
**影响**：跨目录改动先读对方 ADR；契约冻结后修改需新 ADR；旧编号文档映射见 docs/README.md。
