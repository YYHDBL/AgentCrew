"""启动序列中后置卡片步骤的挂钩点（backend-service.md §1）。

C1 只交付骨架：下列步骤以诚实的占位日志出现，M0-C2（迁移 / 升级前快照 /
审计链校验）与 M0-C9（启动对账）落地时在此替换为真实实现。
"""

from __future__ import annotations

import logging

from .db.database import Database


def run_startup_hooks(db: Database, log: logging.Logger) -> None:
    log.debug("startup.step 升级前快照/迁移：C2 交付（无 schema_migrations，跳过）")
    log.debug("startup.step 审计链校验：C2 交付（无 audit_log，跳过）")
    log.debug("startup.step 种子检查：M2 交付（跳过）")
    log.debug("startup.step 启动对账：C9 交付（无 task_runs，跳过）")
    log.info("startup.hooks 后置步骤占位完成（C2/C9 填充）")


def snapshot_audit_chain_head(log: logging.Logger) -> None:
    """§7 第 5 步：链头哈希原子写 chain-head.txt——audit_log 建表后在 C2 交付。"""
    log.info("shutdown.step 链头快照：C2 交付（audit_log 未建，跳过）")
