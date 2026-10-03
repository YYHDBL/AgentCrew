"""服务运行时上下文：装配层共享句柄与优雅关闭步骤（backend-service.md §7）。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .db.audit import snapshot_chain_head
from .db.database import Database
from .db.write_channel import WriteChannel

if TYPE_CHECKING:  # 避免运行时循环导入（bus 导入 core.events 而已，防御性）
    from agentcrew_core.tools import ToolScheduler

    from .approvals import ApprovalService
    from .bus import EventBus
    from .db.event_store import EventStore
    from .questions import QuestionService
    from .providers import ConfiguredProvider
    from .recovery import RecoveryService
    from .run_manager import RunManager
    from .sessions import SessionService
    from .settings import SettingsService
    from .memory.store import MemoryStore
    from .memory.snapshots import MemorySnapshots
    from .memory.search import MemorySearch
    from .memory.jobs import MemoryJobs


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
    token: str = ""
    bus: "EventBus | None" = None
    event_store: "EventStore | None" = None
    provider: ConfiguredProvider | None = None
    approvals: "ApprovalService | None" = None  # M0-C6 起装配
    scheduler: "ToolScheduler | None" = None
    settings: "SettingsService | None" = None  # M0-C7 起装配
    sessions: "SessionService | None" = None  # M0-C7 起装配
    questions: "QuestionService | None" = None  # M0-C8 起装配
    run_manager: "RunManager | None" = None  # M0-C8 起装配
    recovery: "RecoveryService | None" = None  # M0-C9 起装配
    memory: "MemoryStore | None" = None
    snapshots: "MemorySnapshots | None" = None
    memory_search: "MemorySearch | None" = None
    memory_jobs: "MemoryJobs | None" = None
    governance: object | None = None
    identities: object | None = None
    skill_versions: object | None = None

    async def shutdown(self) -> None:
        """优雅关闭（§7 顺序；任务取消不写终态——run_manager.shutdown 在
        总线停收之前执行，被取消任务不落 run.* 终态，C9 对账收敛）。"""
        self.log.info("shutdown.begin 优雅关闭（总预算 10s；停收新请求由 uvicorn 完成）")
        if self.memory_jobs is not None:
            await self.memory_jobs.shutdown()
        if self.run_manager is not None:
            try:
                await self.run_manager.shutdown()
                self.log.info("shutdown.run_manager stopped")
            except Exception as e:  # noqa: BLE001
                self.log.warning("shutdown.run_manager 关闭异常：%s", e)
        # 第 3 步：结束全部 SSE 订阅（≤1s）——shutdown 控制帧入队后留出冲刷窗口
        if self.bus is not None:
            self.bus.shutdown_all()
            await asyncio.sleep(0.3)
        if self.provider is not None:
            try:
                await self.provider.aclose()
                self.log.info("shutdown.provider closed")
            except Exception as e:  # noqa: BLE001
                self.log.warning("shutdown.provider 关闭异常：%s", e)
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
            # 诊断模式绝不快照（外审回稿 F10）：库已判定不可信，把当前链头写进
            # 锚点文件等于认可篡改——原锚点被覆盖后校验反而"通过"。
            # 正常模式快照前先两级校验：链或锚点已失守时快照同样会掩盖篡改。
            if self.diagnostic is not None:
                self.log.warning(
                    "shutdown.chain_head 诊断模式：跳过链头快照（不可信库不得覆盖锚点）")
            else:
                try:
                    from .db.audit import verify_with_anchor

                    check = verify_with_anchor(
                        self.db.write_conn, self.data_dir / "chain-head.txt")
                    if not check.ok:
                        self.log.warning(
                            "shutdown.chain_head 审计链校验失败（seq=%s %s），"
                            "跳过快照以防覆盖可信锚点", check.broken_at_seq,
                            check.reason)
                    else:
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
