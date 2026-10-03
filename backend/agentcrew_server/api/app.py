"""FastAPI 装配：/api/health、/api/diagnostics、CORS allow-all、统一错误信封。

契约：docs/contracts/openapi.yaml v0.3——M0-C2 交付 health + diagnostics
（只读诊断模式入口，backend-service §1）；其余路由随 C3/C5/C6/C7/C9 增补。
docs/redoc/openapi 端点关闭：契约以 yaml 文件为准。
中间件全部纯 ASGI 实现（envelope/诊断守卫），对 C3 的流式 SSE 透明。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..runtime import RuntimeState
from .approvals import install_approval_routes
from .auth import BearerAuthMiddleware
from .envelope import EnvelopeMiddleware
from .errors import ErrorCode, error_response, install_error_handlers
from .recovery import install_recovery_routes
from .runs import install_run_routes
from .sessions import install_session_routes
from .settings import install_settings_routes
from .memory_jobs import install_memory_job_routes
from .sse import install_sse_routes

# 诊断模式下仍然可用的端点（§1：仅 health 与诊断端点）
_DIAGNOSTIC_ALLOWED_PATHS = frozenset({"/api/health", "/api/diagnostics"})


class DiagnosticGuardMiddleware:
    """只读诊断模式守卫：业务端点 503 DIAGNOSTIC_MODE，health/diagnostics 放行。"""

    def __init__(self, app, runtime: RuntimeState):
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] == "http"
            and self.runtime.diagnostic is not None
            and scope["path"].startswith("/api")
            and scope["path"] not in _DIAGNOSTIC_ALLOWED_PATHS
        ):
            response = error_response(
                ErrorCode.DIAGNOSTIC_MODE,
                "只读诊断模式：业务端点不可用",
                detail={
                    "reason": self.runtime.diagnostic.reason,
                    "hint": self.runtime.diagnostic.hint,
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app(runtime: RuntimeState) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runtime.log.debug("http.lifespan startup")
        if runtime.memory is not None and runtime.diagnostic is None:
            results = await runtime.memory.recover()
            for result in results:
                if "error" in result:
                    raise RuntimeError(f"记忆启动恢复失败：{result}")
        if runtime.snapshots is not None and runtime.diagnostic is None:
            await runtime.snapshots.recover()
        if runtime.recovery is not None:
            # 启动对账（§7）：非终态任务收敛 interrupted、结清 dispatched
            # 调用——必须在 RunManager 派发协程之前完成
            try:
                summary = await runtime.recovery.reconcile()
                if summary.get("tasks"):
                    runtime.log.info("http.lifespan 启动对账：%s", summary)
            except Exception:  # noqa: BLE001 —— 对账失败如实记录，不拦启动
                runtime.log.exception("http.lifespan 启动对账失败")
        if runtime.memory_jobs is not None and runtime.diagnostic is None:
            await runtime.memory_jobs.recover()
            await runtime.memory_jobs.start()
        if runtime.run_manager is not None:
            await runtime.run_manager.start()  # 总线订阅 + 派发协程（C8）
        yield
        await runtime.shutdown()

    app = FastAPI(
        title="AgentCrew Sidecar",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_error_handlers(app)
    install_sse_routes(app, runtime)
    if runtime.memory_jobs is not None:
        install_memory_job_routes(app, runtime)
    if runtime.approvals is not None:
        install_approval_routes(app, runtime)
    if runtime.sessions is not None:
        install_session_routes(app, runtime)
    if runtime.settings is not None:
        install_settings_routes(app, runtime)
    if runtime.questions is not None and runtime.run_manager is not None:
        install_run_routes(app, runtime)
    if runtime.recovery is not None:
        install_recovery_routes(app, runtime)
    app.add_middleware(EnvelopeMiddleware)
    app.add_middleware(DiagnosticGuardMiddleware, runtime=runtime)
    # Bearer 鉴权（M0-C3）：无/错 token → 401；仅 /api/health 豁免。
    # 顺序：401 优先于诊断模式 503（认证先于业务状态）
    app.add_middleware(BearerAuthMiddleware, token=runtime.token)
    # CORS allow-all：安全由 Bearer 承担（显式头、无 cookie、无 CSRF 面）
    # ——backend-service.md §5，第四轮审查确认保留
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/diagnostics")
    async def diagnostics() -> dict:
        if runtime.diagnostic is None:
            return {"mode": "normal"}
        return {
            "mode": "diagnostic",
            "reason": runtime.diagnostic.reason,
            "hint": runtime.diagnostic.hint,
        }

    return app
