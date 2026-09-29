"""服务运行时上下文：装配层共享的句柄与优雅关闭步骤（backend-service.md §7）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .db.database import Database
from .lifecycle import snapshot_audit_chain_head


@dataclass
class RuntimeState:
    log: logging.Logger
    data_dir: Path
    db: Database | None = None

    async def shutdown(self) -> None:
        """优雅关闭（C1 子集；SSE 订阅关闭随 C3、任务取消/不写终态随 C8/C9 填充）。

        顺序即 §7：停收（uvicorn 先行）→ [C8/C9：取消任务不写终态] → [C3：关 SSE]
        → DB PASSIVE checkpoint + 关闭 → [C2：链头快照]。
        """
        self.log.info("shutdown.begin 优雅关闭（总预算 10s；停收新请求由 uvicorn 完成）")
        if self.db is not None and not self.db.closed:
            try:
                busy, wal_frames, checkpointed = self.db.checkpoint_passive()
                self.log.info(
                    "shutdown.checkpoint wal_checkpoint(PASSIVE) busy=%s wal_frames=%s checkpointed=%s",
                    busy,
                    wal_frames,
                    checkpointed,
                )
            except Exception as e:  # noqa: BLE001 —— checkpoint 失败不阻断关闭序列
                self.log.warning("shutdown.checkpoint 失败（跳过，WAL 文件留存）：%s", e)
            self.db.close()
            self.log.info("shutdown.db closed")
        snapshot_audit_chain_head(self.log)
        self.log.info("shutdown.done")
