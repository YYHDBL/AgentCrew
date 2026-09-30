"""agentcrew_server 入口：启动序列（backend-service.md §1）与退出码约定。

顺序：读参数与环境（AGENTCREW_TOKEN）→ data-dir 准备（§9 布局）→ 配置链
（env > config.json > 默认）→ 日志 → instance.lock → SQLite(WAL) → 后置挂钩
（C2/C9 占位）→ 端口探测（被占换端口重试 3 次）→ uvicorn 监听 127.0.0.1
→ stdout AGENTCREW_READY {"port":N}（就绪标记只含端口，绝不含 token）。

退出码：0 = 正常（含 SIGTERM/SIGINT 优雅关闭）；1 = 启动失败 / 未知异常；
2 = instance.lock 已被另一实例持有。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
import sys
from pathlib import Path

import uvicorn

from agentcrew_core.provider.glm_anthropic import DEFAULT_BASE_URL
from agentcrew_core.tools import ToolScheduler, build_default_registry

from .api.app import create_app
from .approvals import ApprovalService
from .bus import EventBus
from .config import ConfigError, load_config
from .db.audit import verify_with_anchor
from .db.database import Database
from .db.event_store import EventStore
from .db.migrations import MigrationFailedError, run_migrations
from .db.write_channel import WriteChannel
from .providers import build_provider
from .sessions import SessionService
from .settings import SettingsService
from .instance_lock import (
    DataDirNotWritable,
    InstanceLock,
    InstanceLockError,
    prepare_data_dir,
)
from .lifecycle import run_startup_hooks
from .logging_setup import setup_logging, shutdown_logging
from .runtime import DiagnosticInfo, RuntimeState
from .secrets import register_secret

READY_MARKER = "AGENTCREW_READY"
DEFAULT_PORT = 8710
_PORT_RETRIES = 3  # 端口被占：换端口重试 3 次后报错退出


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m agentcrew_server",
        description="AgentCrew sidecar（FastAPI，仅监听本地回环）",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="监听端口（被占时依次 +1 重试 3 次）")
    parser.add_argument("--data-dir", default="data",
                        help="数据目录（backend-service.md §9 布局）")
    parser.add_argument("--parent-pid", type=int, default=None,
                        help="父进程 pid：父进程退出时本进程优雅自杀（不孤儿）")
    return parser.parse_args(argv)


def pick_port(requested: int) -> int | None:
    """在 127.0.0.1 上探测可用端口：requested 起，被占则 +1，共试 4 次。"""
    for candidate in range(requested, requested + _PORT_RETRIES + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
        return candidate
    return None


async def _parent_watchdog(
    parent_pid: int, schedule_graceful, log: logging.Logger, interval: float = 2.0
) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            log.warning("parent.watchdog 父进程 %s 已退出 → 触发优雅关闭", parent_pid)
            schedule_graceful("parent-exit")
            return
        except PermissionError:
            continue  # 进程存在但属其他用户（罕见）：继续观察


async def _overflow_sweeper(bus, interval: float = 1.0) -> None:
    """每秒清理溢出超宽限的死订阅（外审回稿：不依赖新写入触发）。"""
    while True:
        await asyncio.sleep(interval)
        bus.sweep()


class _GracefulServer(uvicorn.Server):
    """在 uvicorn 处理退出信号的第一时间投递 SSE shutdown 帧。

    顺序是硬约束（§7）：uvicorn 的关闭流程"先等连接结束、后跑 lifespan"——
    若 shutdown 帧留到 lifespan 才发，SSE 长连接与关闭流程互等，帧永远送
    不到（实测教训）。挂进 handle_exit 首行使帧先于连接等待入队，SSE 生成
    器自行 return，连接自然结束。
    """

    def __init__(self, config: uvicorn.Config, on_exit_signal) -> None:
        super().__init__(config)
        self._on_exit_signal = on_exit_signal
        self._exit_signalled = False

    def handle_exit(self, sig, frame) -> None:
        if not self._exit_signalled:
            self._exit_signalled = True
            try:
                self._on_exit_signal(f"signal {sig}")
            except Exception:  # noqa: BLE001 —— 投帧失败不阻断退出
                logging.getLogger("agentcrew.server").exception(
                    "shutdown.begin 投递 SSE shutdown 帧失败"
                )
        super().handle_exit(sig, frame)


async def serve(
    app, port: int, parent_pid: int | None, log: logging.Logger, bus=None
) -> None:
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_config=None,  # 日志交给根 logger（轮转文件 + stderr），stdout 保持干净
        access_log=False,
        lifespan="on",
        timeout_graceful_shutdown=8,  # §7 第 2 步预算 ≤8s
    )
    loop = asyncio.get_running_loop()

    def _begin_graceful(source: str) -> None:
        """§7 第 1/3 步：先向全部 SSE 订阅投递 shutdown 帧，再停收。幂等。"""
        if bus is not None:
            bus.shutdown_all()
        server.should_exit = True
        log.info("shutdown.begin 触发源=%s（SSE shutdown 帧已投递）", source)

    def _schedule_graceful(source: str) -> None:
        loop.call_soon_threadsafe(_begin_graceful, source)

    server = _GracefulServer(config, _schedule_graceful)

    # 信号语义（§7：信号 → 优雅关闭 → exit 0）：uvicorn 0.54 在优雅关闭完成后
    # 会恢复"原 handler"并重抛捕获的信号（默认处置即 128+N 退出）。我们先安装
    # 自己的记录型 handler——uvicorn 会把它当"原 handler"恢复，重抛时被吞掉，
    # serve() 正常返回、exit 0；启动早期（uvicorn 未装 handler 前）同样兜底。
    captured_signals: list[int] = []

    def _request_shutdown(signum: int, _frame: object) -> None:
        captured_signals.append(signum)
        _schedule_graceful(f"signal {signum}")

    installed: dict[int, object] = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            installed[sig] = signal.signal(sig, _request_shutdown)
        except ValueError:  # 非主线程（理论路径）：交给 uvicorn 自己的 handler
            pass

    server_task = asyncio.create_task(server.serve(), name="uvicorn")
    watchdog = None
    sweeper = None
    if parent_pid:
        watchdog = asyncio.create_task(
            _parent_watchdog(parent_pid, _schedule_graceful, log), name="parent-watchdog"
        )
    if bus is not None:
        # 溢出死订阅的独立计时检查：写入停止（无 publish 可蹭）后也能强断
        sweeper = asyncio.create_task(_overflow_sweeper(bus), name="bus-overflow-sweeper")
    try:
        while not server.started and not server_task.done():
            await asyncio.sleep(0.05)
        if server_task.done():
            server_task.result()  # 启动失败（如绑定异常）：抛出真实错误
        print(f'{READY_MARKER} {{"port": {port}}}', flush=True)
        log.info("http.ready listening on 127.0.0.1:%s", port)
        await server_task
    finally:
        if watchdog is not None:
            watchdog.cancel()
        if sweeper is not None:
            sweeper.cancel()
        for sig, handler in installed.items():
            try:
                signal.signal(sig, handler)
            except ValueError:
                pass
        if captured_signals:
            log.info(
                "shutdown.signal 收到信号 %s，已优雅关闭",
                [int(s) for s in captured_signals],
            )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    token = os.environ.get("AGENTCREW_TOKEN", "").strip()
    if not token:
        print(
            "启动失败：环境变量 AGENTCREW_TOKEN 未设置（token 由桌面壳生成并经"
            "环境变量注入，绝不经 stdout）",
            file=sys.stderr,
        )
        return 1

    data_dir = Path(args.data_dir).expanduser().absolute()
    try:
        prepare_data_dir(data_dir)
    except DataDirNotWritable as e:
        print(f"启动失败：{e}", file=sys.stderr)
        return 1

    try:
        config = load_config(data_dir)
    except ConfigError as e:
        print(f"启动失败：{e}", file=sys.stderr)
        return 1

    register_secret(token)
    for key in config.api_keys():
        register_secret(key)
    setup_logging(data_dir / "logs", config.log_level)
    log = logging.getLogger("agentcrew.server")
    log.info(
        "startup.begin data_dir=%s log_level=%s env覆盖字段=%s",
        data_dir,
        config.log_level,
        config.env_fields or "无",
    )

    lock = InstanceLock(data_dir)
    db: Database | None = None
    channel: WriteChannel | None = None
    try:
        try:
            lock.acquire()
        except InstanceLockError as e:
            log.error("startup.fail %s", e)
            print(f"启动失败：{e}", file=sys.stderr)
            return 2

        try:
            db = Database(data_dir / "agentcrew.db")
        except Exception as e:  # noqa: BLE001 —— SQLite 打开失败按启动失败处理
            log.exception("startup.fail SQLite 打开失败：%s", e)
            return 1
        log.info(
            "startup.db opened journal_mode=%s busy_timeout=%sms",
            db.journal_mode, db.busy_timeout_ms,
        )

        # §1 第 3-4 步：升级前快照 + 版本化迁移
        diagnostic: DiagnosticInfo | None = None
        try:
            migration = run_migrations(db.write_conn, data_dir / "backups")
            if migration.status == "conflict":
                log.error("startup.fail %s", migration.error)
                print(f"启动失败：{migration.error}", file=sys.stderr)
                return 1
            if migration.status == "applied":
                log.info(
                    "startup.migrations applied v%s→v%s 已应用=%s 升级前快照=%s",
                    migration.current_version, migration.target_version,
                    migration.applied_versions, migration.snapshot_path,
                )
            else:
                log.info("startup.migrations up_to_date v%s", migration.target_version)
        except MigrationFailedError as e:
            log.error("startup.migrations failed：%s", e)
            diagnostic = DiagnosticInfo(reason=f"迁移失败：{e}")

        # §1 第 5 步：审计链两级校验（空链 = 通过；C6 起有写入方）
        if diagnostic is None:
            verification = verify_with_anchor(
                db.write_conn, data_dir / "chain-head.txt"
            )
            if not verification.ok:
                log.error(
                    "startup.audit 审计链校验失败 seq=%s：%s",
                    verification.broken_at_seq, verification.reason,
                )
                diagnostic = DiagnosticInfo(
                    reason=(
                        f"审计链校验失败（seq={verification.broken_at_seq}："
                        f"{verification.reason}）"
                    ),
                    hint="审计账本不可信，系统拒绝进入正常模式；"
                    "可从 backups/ 快照还原后重启",
                )
            else:
                log.info("startup.audit 审计链校验通过（%s 条）", verification.checked_count)

        run_startup_hooks(log)
        if diagnostic is not None:
            log.warning("startup.diagnostic 只读诊断模式：%s", diagnostic.reason)

        channel = WriteChannel(db.write_conn)
        # 事件总线 + 常驻 EventStore（M0-C3 起）：发布在写通道线程内、提交之后
        # 同步入队——发布顺序 = 提交顺序（backend-service §2 v1.9）
        bus = EventBus()
        event_store = EventStore(channel, publisher=bus.publish)
        # Provider 双槽（M0-C4）：main/aux 从配置链构建（ADR-009：Anthropic 端点）
        provider = build_provider(config.values.get("models", {}))
        # 审批闸门 + 带闸门调度器（M0-C6）：事件经 EventStore 落库并扇出 SSE
        approvals = ApprovalService(db, event_store, data_dir / "chain-head.txt")
        scheduler = ToolScheduler(build_default_registry(), gate=approvals.gate)
        approvals.scheduler = scheduler
        # 配置 API + 会话装配（M0-C7）：settings 是 config 的运行时持有者，
        # PATCH 成功后 sessions 读到的 limits 即为新值
        settings = SettingsService(config, data_dir, channel,
                                   data_dir / "chain-head.txt")
        sessions = SessionService(db, event_store, data_dir, settings)
        runtime = RuntimeState(
            log=log, data_dir=data_dir, db=db,
            write_channel=channel, diagnostic=diagnostic,
            token=token, bus=bus, event_store=event_store, provider=provider,
            approvals=approvals, scheduler=scheduler,
            settings=settings, sessions=sessions,
        )
        log.info("startup.bus 事件总线就绪（队列上限 1000，SSE 连接上限 32）")
        log.info(
            "startup.provider main=%s aux=%s @ %s",
            config.values.get("models", {}).get("main", {}).get("model", "?"),
            config.values.get("models", {}).get("aux", {}).get("model", "?"),
            DEFAULT_BASE_URL,
        )
        app = create_app(runtime)

        port = pick_port(args.port)
        if port is None:
            log.error(
                "startup.fail 端口 %s-%s 全部被占（已换端口重试 %s 次）",
                args.port,
                args.port + _PORT_RETRIES,
                _PORT_RETRIES,
            )
            return 1
        if port != args.port:
            log.warning("startup.port %s 被占，改用 %s", args.port, port)

        try:
            asyncio.run(serve(app, port, args.parent_pid, log, bus=bus))
        except KeyboardInterrupt:
            log.info("shutdown.signal 启动阶段收到中断，退出")
            return 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            log.error("startup.fail uvicorn 异常退出 code=%s", code)
            return code
        except Exception:
            log.exception("startup.fail 未知异常")
            return 1
        log.info("shutdown.exit 正常退出（exit 0）")
        return 0
    finally:
        # lifespan 未跑或中途失败时兜底；正常路径 channel/db 已在 shutdown() 关闭
        if channel is not None and not channel.closed:
            channel.close()
        if db is not None and not db.closed:
            try:
                db.checkpoint_passive()
            except Exception:  # noqa: BLE001
                pass
            db.close()
        lock.release()
        shutdown_logging()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
