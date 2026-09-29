"""FastAPI 装配：/api/health、CORS allow-all、统一错误信封、优雅关闭 lifespan。

契约：docs/contracts/openapi.yaml v0.3——M0-C1 只交付 /api/health（其余路由
随 C3/C5/C6/C7/C9 增补）。docs/redoc/openapi 端点关闭：契约以 yaml 文件为准。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..runtime import RuntimeState
from .envelope import EnvelopeMiddleware
from .errors import install_error_handlers


def create_app(runtime: RuntimeState) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runtime.log.debug("http.lifespan startup")
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
    app.add_middleware(EnvelopeMiddleware)
    # CORS allow-all：安全由 Bearer 承担（显式头、无 cookie、无 CSRF 面）
    # ——backend-service.md §5，第四轮审查确认保留
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app
