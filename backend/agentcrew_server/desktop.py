"""桌面行为偏好（M3-14）：关窗即退出与阻止休眠的持久读写。

桌面设置是整台机器单一壳层行为，不属于任何资源修订序列；读写走串行写
通道，变更入审计链（与 /api/settings 一致——一切配置变更入审计）。
"""

from __future__ import annotations

from .db.audit import append_audit
from .governance.resources import canonical, now


class DesktopSettings:
    def __init__(self, runtime):
        self.runtime = runtime

    def get(self, conn=None) -> dict:
        connection = conn if conn is not None else self.runtime.db.read_conn
        row = connection.execute("SELECT close_to_quit,prevent_sleep FROM desktop_preferences WHERE id=1").fetchone()
        return {"close_to_quit": bool(row[0]), "prevent_sleep": bool(row[1])}

    async def patch(self, identity, request) -> dict:
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                self.runtime.identities.require(identity, "manage", conn=conn)
                conn.execute("UPDATE desktop_preferences SET close_to_quit=?,prevent_sleep=?,updated_at=? WHERE id=1",
                             (int(request["close_to_quit"]), int(request["prevent_sleep"]), now()))
                append_audit(conn, ts=now(), actor_type="user", actor_id=identity.effective_user_id,
                    action="settings.desktop_updated", resource_type="config", resource_id="desktop",
                    detail=canonical({"close_to_quit": bool(request["close_to_quit"]), "prevent_sleep": bool(request["prevent_sleep"])}))
                return self.get(conn)
        return await self.runtime.event_store.channel.execute(tx)
