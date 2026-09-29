# ADR-008 · 通用基座定位、工具准入纪律与沙箱提前

状态：已接受（2026-09-30，用户裁定）

**背景**：用户明确三条——① AgentCrew 是**通用 Agent 基座**，办公只是后续专项优化，不预设场景绑定；② 工具 **less is more**，初期可只有 bash（参考 ZCode 基础增删改查集），拒绝 read_word/read_excel 式碎片工具；③ 沙箱用 **macOS 系统自带 Seatbelt**（参考 CC/ZCode），不做容器。

**决策**：
1. **M0 核心四件套**：`read_file` / `write_file` / `bash` / `http_request`（ZCode 的 read/write/bash 基础集 + http 的治理位）。**工具准入纪律**：bash 是万能工具，专用工具必须满足"承担治理语义"（http=域名白名单与外部幂等键的唯一网络入口）或"上下文/投影友好度不可替代"（read=分页读；write=verifiable 类与 artifacts 产物投影的唯一来源）之一。grep/glob/edit/list_dir 不设；专项工具按增量片按需加。
2. **产物口径**：只有 write_file 与外部化工件进 artifacts 投影；bash 重定向产生的文件不进产物卡（M0 靠非只读 bash 必审批兜底，M2 起靠 Seatbelt 限制 bash 写范围）。
3. **bash 子进程环境变量白名单**：仅 PATH/HOME/LANG/TZ/TERM；绝不传 AGENTCREW_TOKEN、模型 API key 或任何凭据——否则任意 bash 调用可读 token 反打本地接口，权限体系被整体击穿。
4. **沙箱**：macOS Seatbelt（`sandbox-exec`，系统自带）——**v1.7 修订（第三轮审查 + 用户裁定 F1=A）：M0 即上最小 profile**（bash：写限制在任务 scope、**网络全禁**、凭据路径禁读——"http_request 是唯一网络入口"恢复为强承诺）；M2 升级完整 profile（read 范围收紧）与越界拦截演示。强制层与判定层规则同源编译（EasyMint 纪律）。从"明确不做"清单移出。
5. **find 重入（v1.7，用户裁定⑥=B）**：find 以参数否决方式回到只读白名单——参数含 `-exec/-execdir/-delete/-ok/-okdir/-fprintf/-fprint/-fls` 任一即否决；解决"找文件要审批"的摩擦且不新增工具。

**理由**：工具数量是上下文与维护的双重成本；治理与投影是不可妥协的集中点；Seatbelt 零依赖且 CC 同源验证（sandbox-runtime 底层即它）。

**影响**：M0 卡 C5 已更新（四件套 + env 白名单验收：bash 内 `echo $AGENTCREW_TOKEN` 必须为空）；M2 增沙盒验收（越界写入被系统拦截）；守门参数总表与循环稳定性三招见 harness-session v1.5/v1.6。
