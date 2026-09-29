"""统一错误信封与错误码表（backend-service.md §6）。

错误 = {"error": {"code", "message", "detail?"}}；成功 = {"data": ...}
（由 envelope 中间件统一包裹）；204 与 SSE 流不套信封。
新增错误码纪律：先改 backend-service.md §6 再写实现——
VALIDATION_ERROR / METHOD_NOT_ALLOWED / INTERNAL_ERROR / UNAUTHORIZED
为 v1.2 补录（C1 统一信封所需，已同步进该表）。
"""

from __future__ import annotations

import logging
from enum import Enum

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("agentcrew.api.errors")


class ErrorCode(str, Enum):
    INVALID_PATH = "INVALID_PATH"                      # 400 路径非法
    NOT_FOUND = "NOT_FOUND"                            # 404 资源不存在
    OUT_OF_SCOPE = "OUT_OF_SCOPE"                      # 403 超出任务 scope
    PROTECTED_PATH = "PROTECTED_PATH"                  # 403 受保护路径
    UNAUTHORIZED = "UNAUTHORIZED"                      # 401 Bearer 缺失/错误（C3）
    APPROVAL_PENDING = "APPROVAL_PENDING"              # 409 等待审批时发指令
    APPROVAL_STALE = "APPROVAL_STALE"                  # 409 审批已被不同决定处理
    PENDING_VERIFICATION = "PENDING_VERIFICATION"      # 409 resume 被待核验阻塞
    QUEUE_EMPTY = "QUEUE_EMPTY"                        # 409 继续队列时无指令
    QUEUE_PAUSED = "QUEUE_PAUSED"                      # 409 队列状态不符
    INVALID_TRANSITION = "INVALID_TRANSITION"          # 409 FSM 非法迁移
    QUESTION_STALE = "QUESTION_STALE"                  # 409 提问已回答/取消
    VALIDATION_ERROR = "VALIDATION_ERROR"              # 422 请求体校验失败
    CONFIG_WRITE_FAILED = "CONFIG_WRITE_FAILED"        # 500 配置文件写入失败
    INTERNAL_ERROR = "INTERNAL_ERROR"                  # 500 未捕获异常
    SSE_LIMIT = "SSE_LIMIT"                            # 503 SSE 连接超上限
    DIAGNOSTIC_MODE = "DIAGNOSTIC_MODE"                # 503 只读诊断模式
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"          # 405


DEFAULT_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_PATH: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.OUT_OF_SCOPE: 403,
    ErrorCode.PROTECTED_PATH: 403,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.APPROVAL_PENDING: 409,
    ErrorCode.APPROVAL_STALE: 409,
    ErrorCode.PENDING_VERIFICATION: 409,
    ErrorCode.QUEUE_EMPTY: 409,
    ErrorCode.QUEUE_PAUSED: 409,
    ErrorCode.INVALID_TRANSITION: 409,
    ErrorCode.QUESTION_STALE: 409,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.CONFIG_WRITE_FAILED: 500,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.SSE_LIMIT: 503,
    ErrorCode.DIAGNOSTIC_MODE: 503,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
}

# 框架自身抛出的 HTTPException（404 路由不存在 / 405 等）→ 错误码；
# 未映射的状态码走 INTERNAL_ERROR 兜底并保留原状态与告警日志。
_STATUS_TO_CODE: dict[int, ErrorCode] = {
    401: ErrorCode.UNAUTHORIZED,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    422: ErrorCode.VALIDATION_ERROR,
}


class ApiError(Exception):
    """带错误码的业务异常——路由/服务层抛出，统一转错误信封。"""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str | None = None,
        *,
        detail: object = None,
        status: int | None = None,
    ) -> None:
        self.code = ErrorCode(code)
        self.message = message or self.code.value
        self.detail = detail
        self.status = status or DEFAULT_STATUS[self.code]
        super().__init__(self.message)


def error_body(code: ErrorCode, message: str, detail: object = None) -> dict:
    error: dict = {"code": code.value, "message": message}
    if detail is not None:
        error["detail"] = detail
    return {"error": error}


def error_response(
    code: ErrorCode, message: str, *, detail: object = None, status: int | None = None
) -> JSONResponse:
    return JSONResponse(
        error_body(code, message, detail), status_code=status or DEFAULT_STATUS[code]
    )


def install_error_handlers(app) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.code, exc.message, detail=exc.detail, status=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code)
        if code is None:
            code = ErrorCode.INTERNAL_ERROR
            log.warning("errors.unmapped_status %s → INTERNAL_ERROR 兜底", exc.status_code)
        if isinstance(exc.detail, str):
            message = exc.detail
        elif isinstance(exc.detail, dict):
            message = str(exc.detail.get("message") or code.value)
        else:
            message = code.value
        detail = {"http_status": exc.status_code} if code is ErrorCode.INTERNAL_ERROR and exc.status_code != 500 else None
        return error_response(code, message, detail=detail, status=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "请求体校验失败",
            detail={"errors": jsonable_encoder(exc.errors())[:50]},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("api.unhandled %s: %s", type(exc).__name__, exc)
        return error_response(ErrorCode.INTERNAL_ERROR, "服务内部错误")
