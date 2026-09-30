"""启动序列中仍属后置卡片的步骤挂钩（backend-service.md §1）。

C2 起迁移/升级前快照/审计链校验已是真实实现（migrations.py / audit.py，
由 cli 调用）；启动对账自 C9 起在 lifespan 内执行（agentcrew_server.
recovery.RecoveryService.reconcile，需事件总线与 RunManager 装配完成）。
此处只剩：种子检查（M2）。
"""

from __future__ import annotations

import logging


def run_startup_hooks(log: logging.Logger) -> None:
    log.debug("startup.step 种子检查：M2 交付（跳过）")
    log.info("startup.hooks 后置步骤完成（种子 M2）")
