"""配置 API（M0-C7）：GET 脱敏 + PATCH 版本化（契约 v0.3 settings 段）。"""

from __future__ import annotations

import asyncio

from fastapi import Body

from ..settings import SettingsWriteFailed
from .errors import ApiError, ErrorCode


def install_settings_routes(app, runtime) -> None:
    service = runtime.settings

    @app.get("/api/settings")
    async def get_settings():
        return await asyncio.to_thread(service.get_view)

    @app.patch("/api/settings")
    async def patch_settings(body: dict = Body(...)):
        try:
            return await service.patch(body)
        except ValueError as e:  # 形状/取值非法 → 422（文件与内存均未动）
            raise ApiError(ErrorCode.VALIDATION_ERROR, f"配置非法：{e}") from None
        except SettingsWriteFailed as e:
            raise ApiError(ErrorCode.CONFIG_WRITE_FAILED, str(e)) from None
