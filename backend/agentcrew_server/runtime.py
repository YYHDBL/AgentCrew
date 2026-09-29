"""服务运行时上下文：装配层共享句柄与优雅关闭步骤（backend-service.md §7）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .db.audit import snapshot_chain_head
from .db.database import Database
from .db.write_channel import WriteChannel


@dataclass
class DiagnosticInfo:
    """只读诊断模式成因（§1）：业务端点 503，仅 health 与 diagnostics 可用。"""

    reason: str
    hint: str = "数据目录 backups/ 内有升级前快照，可整目录复制还原后重启"


@dataclass
class RuntimeState:
    log: logging.Logger
    data_dir: Path
    db: Database | None = None
    write_channel: WriteChannel | None = None
    diagnostic: DiagnosticInfo | None = field(default=None)

    async def shutdown(self) -> None:
        """优雅关闭（§7 顺序；SSE 关闭/任务取消随 C3/C8 填充）。"""
        self.log.info("shutdown.begin 优雅关闭（总预算 10s；停收新请求由 uvicorn 完成）")
        if self.write_channel is not None and not self.write_channel.closed:
            self.write_channel.close()
        if self.db is not None and not self.db.closed:
            try:
                busy, wal_frames, checkpointed = self.db.checkpoint_passive()
                self.log.info(
                    "shutdown.checkpoint wal_checkpoint(PASSIVE) busy=%s wal_frames=%s"
                    " checkpointed=%s",
                    busy, wal_frames, checkpointed,
                )
            except Exception as e:  # noqa: BLE001 —— checkpoint 失败不阻断关闭序列
                self.log.warning("shutdown.checkpoint 失败（跳过，WAL 文件留存）：%s", e)
            # 链头快照（governance §3：每次正常退出写 chain-head.txt；空链跳过）
            try:
                head_seq = snapshot_chain_head(
                    self.db.write_conn, self.data_dir / "chain-head.txt"
                )
                if head_seq is not None:
                    self.log.info("shutdown.chain_head 快照至 seq=%s", head_seq)
                else:
                    self.log.info("shutdown.chain_head 审计链为空，跳过快照")
            except Exception as e:  # noqa: BLE001
                self.log.warning("shutdown.chain_head 快照失败（不阻断退出）：%s", e)
            self.db.close()
            self.log.info("shutdown.db closed")
        self.log.info("shutdown.done")
