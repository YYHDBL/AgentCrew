"""启动序列中仍属后置卡片的步骤挂钩（backend-service.md §1）。

C2 起迁移/升级前快照/审计链校验已是真实实现（migrations.py / audit.py，
由 cli 调用）；此处只剩：种子检查（M2）与启动对账（C9）。
"""

from __future__ import annotations

import logging


def run_startup_hooks(log: logging.Logger) -> None:
    log.debug("startup.step 种子检查：M2 交付（跳过）")
    log.debug("startup.step 启动对账：C9 交付（无 task_runs 在跑，跳过）")
    log.info("startup.hooks 后置步骤完成（种子 M2 / 对账 C9）")
