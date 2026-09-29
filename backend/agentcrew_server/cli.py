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

from .api.app import create_app
from .config import ConfigError, load_config
from .db.database import Database
from .instance_lock import (
    DataDirNotWritable,
    InstanceLock,
    InstanceLockError,
    prepare_data_dir,
)
from .lifecycle import run_startup_hooks
from .logging_setup import setup_logging, shutdown_logging
from .runtime import RuntimeState
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
    parent_pid: int, server: uvicorn.Server, log: logging.Logger, interval: float = 2.0
) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            log.warning("parent.watchdog 父进程 %s 已退出 → 触发优雅关闭", parent_pid)
            server.should_exit = True
            return
        except PermissionError:
            continue  # 进程存在但属其他用户（罕见）：继续观察


async def serve(app, port: int, parent_pid: int | None, log: logging.Logger) -> None:
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_config=None,  # 日志交给根 logger（轮转文件 + stderr），stdout 保持干净
        access_log=False,
        lifespan="on",
        timeout_graceful_shutdown=8,  # §7 第 2 步预算 ≤8s
    )
    server = uvicorn.Server(config)
    # 信号语义（§7：信号 → 优雅关闭 → exit 0）：uvicorn 0.54 在优雅关闭完成后
    # 会恢复"原 handler"并重抛捕获的信号（默认处置即 128+N 退出）。我们先安装
    # 自己的记录型 handler——uvicorn 会把它当"原 handler"恢复，重抛时被吞掉，
    # serve() 正常返回、exit 0；启动早期（uvicorn 未装 handler 前）同样兜底。
    captured_signals: list[int] = []

    def _request_shutdown(signum: int, _frame: object) -> None:
        captured_signals.append(signum)
        server.should_exit = True

    installed: dict[int, object] = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            installed[sig] = signal.signal(sig, _request_shutdown)
        except ValueError:  # 非主线程（理论路径）：交给 uvicorn 自己的 handler
            pass

    server_task = asyncio.create_task(server.serve(), name="uvicorn")
    watchdog = None
    if parent_pid:
        watchdog = asyncio.create_task(
            _parent_watchdog(parent_pid, server, log), name="parent-watchdog"
        )
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
        log.info("startup.db opened journal_mode=%s busy_timeout=5000", db.journal_mode)
        run_startup_hooks(db, log)

        runtime = RuntimeState(log=log, data_dir=data_dir, db=db)
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
            asyncio.run(serve(app, port, args.parent_pid, log))
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
        # lifespan 未跑或中途失败时兜底；正常路径 db 已在 shutdown() 关闭
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
