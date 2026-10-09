# M3-13 公开证据检查

日期：2026-10-09。最终真实 Electron 来源目录为 `desktop/.artifacts/m3-automation-1791530590038/`。四张截图逐张检查，界面中显示身份角色、计划参数、文件范围、任务及发生标识，没有 key/token。

| 截图 | SHA256 | 检查结果 |
|---|---|---|
| automation-wide.png | `d09182aba40fd3fc3545f46527dd4168a218c57a0abc5063beb618dcb4aeee24` | 未发现凭据 |
| automation-approval-wide.png | `1e43bef90f34b1a83ba5078e830d21cc9701cc77198bd5c3ee934dc506ebc5b2` | 未发现凭据 |
| automation-narrow.png | `ded2662371d910364c83a85787074ab50ae556c23434a8fe5986ad06ff7006a7` | 未发现凭据 |
| automation-pending-verification.png | `05bc60f7890b01221f1258085d26387ba4db55ad731f04633e0cb46192143b3d` | 未发现凭据 |

`automation-electron-reviewed.json` SHA256 为 `f4e94bf40b9892ae6411e36def4da8c1f1a4b182ffd0eb494044f647df713488`，实际配置密钥匹配数为 0。保留任务、尝试、提案、发生、作业、事件与审计标识及文件 SHA/mtime；认证 token 和机密字段已脱敏。

完整后端回归及技能固化失败日志中的测试认证 token 已清理，原始运行日志继续保存在已忽略的中间目录。失败状态、断言、数量及实际输出保留。运行中心 JSON 公开前也通过当前配置密钥扫描。
