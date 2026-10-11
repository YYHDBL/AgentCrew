"""配置 API（M0-C7）：GET 脱敏 + PATCH 版本化（契约 v0.3 settings 段）。

M4-04 新增 POST /api/settings/models/verify：对已保存配置做真实验证——
计数资产检查（本地、不触网）+ 对配置端点的一次真实小请求。"配置已保存"
与"连接验证通过"是两件事，界面对应区分。
"""

from __future__ import annotations

import asyncio
import time

import httpx
from fastapi import Body

from agentcrew_core.loop import user_text_message
from agentcrew_core.memory.budget import ContextBudgetError

from ..memory.tokenizer import load_counter
from ..providers import ConfiguredProvider, bind_slot
from ..secrets import redact
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

    @app.post("/api/settings/models/verify")
    async def verify_models(body: dict = Body(default=None)):
        slots = (body or {}).get("slots") or ["main", "aux"]
        if not isinstance(slots, list) or not slots \
                or any(slot not in ("main", "aux") for slot in slots):
            raise ApiError(ErrorCode.VALIDATION_ERROR, "slots 需要 [main|aux] 的非空列表")
        results = {}
        for slot in slots:
            entry = service.config.values.get("models", {}).get(slot, {})
            cfg = bind_slot(entry)
            configured = bool(entry.get("model")) and bool(entry.get("api_key"))
            result: dict = {"configured": configured,
                            "counting": {"ok": False}, "request": {"ok": False}}
            try:
                await asyncio.to_thread(load_counter, cfg)
                result["counting"] = {
                    "ok": True, "context_window": cfg.context_window,
                    "counting_method": "官方 tokenizer + 消息编码（SHA-256 固定）",
                    "source": cfg.context_window_source,
                }
            except ContextBudgetError as e:
                result["counting"] = {"ok": False, "error": str(e)}
            except Exception as e:  # noqa: BLE001 —— 计数资源异常如实呈现（脱敏）
                result["counting"] = {"ok": False,
                                      "error": redact(f"{type(e).__name__}: {e}")}
            if configured:
                started = time.monotonic()
                client = httpx.AsyncClient(timeout=15)
                try:
                    # ADR-010：opencode 端点要求 x-opencode-session 路由头；
                    # 验证使用固定会话标识，不与任务会话混用
                    provider = ConfiguredProvider({slot: cfg}, client=client,
                                                  session_id="settings-verify")
                    messages = [user_text_message("连接验证：请只回复 ok。")]
                    stream = provider.stream(slot, messages, [], max_tokens=16,
                                             thinking={"type": "disabled"})
                    try:
                        async for _ in stream:
                            break  # 首个事件即证明鉴权与端点可用
                    finally:
                        await stream.aclose()
                    result["request"] = {
                        "ok": True,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    }
                except Exception as e:  # noqa: BLE001 —— 失败分类呈现（脱敏）
                    message = getattr(e, "message", None) or str(e)
                    error_class = getattr(e, "error_class", None)
                    result["request"] = {
                        "ok": False,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                        "error": redact(f"{error_class or type(e).__name__}：{message}"),
                    }
                finally:
                    await client.aclose()
            else:
                result["request"] = {"ok": False, "error": "槽位未配置模型或密钥"}
            results[slot] = result
        return {"results": results,
                "note": "验证会读取本地计数资源，并对配置的模型端点发起一次真实小请求"
                        "（约一个短回复的用量）；密钥不会出现在任何结果中。"}
