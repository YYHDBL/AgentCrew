"""Bearer 鉴权中间件（M0-C3）：无/错 token → 401 UNAUTHORIZED。

- 豁免：仅 /api/health（契约 security: []；CORS 预检由最外层 CORS 短路，
  到不了本层）；
- 比较用 hmac.compare_digest（防时序侧信道）；token 值绝不进日志/响应。
"""

from __future__ import annotations

import hmac

from .errors import ErrorCode, error_response

_EXEMPT_PATHS = frozenset({"/api/health"})


class BearerAuthMiddleware:
    def __init__(self, app, token: str):
        self.app = app
        self._token = token

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/api"):
            if scope["path"] not in _EXEMPT_PATHS:
                headers = dict(scope.get("headers") or [])
                provided = headers.get(b"authorization", b"").decode("latin-1")
                expected = f"Bearer {self._token}"
                if not hmac.compare_digest(provided, expected):
                    response = error_response(
                        ErrorCode.UNAUTHORIZED,
                        "缺少或错误的 Bearer 凭证",
                        detail={"hint": "Authorization: Bearer <AGENTCREW_TOKEN>"},
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
