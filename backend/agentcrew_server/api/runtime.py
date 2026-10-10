"""桌面生命周期 API（M3-14）：退出影响、通知、电源事件与桌面偏好。"""

import asyncio
from datetime import datetime, timezone
from typing import Literal

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..cron.notifications import Notifications
from ..cron.store import now_ms
from ..desktop import DesktopSettings
from ..governance.resources import GovernanceError
from ..runs import Runs, display_value
from agentcrew_core.events import RunEventType


def exit_impact(runtime, actor):
    runtime.identities.require(actor, "owner")
    runs = Runs(runtime)
    observed = datetime.now(timezone.utc).isoformat()
    clock = now_ms()
    with runs.snapshot(actor) as conn:
        organization = runtime.identities.current(actor, conn)["org_id"]
        head = runs.head(conn)
        records = runs.filtered(conn, actor, {}, head=head)
        active = [runs.record(row, head) for row in records if row["status"] in {"queued", "running", "waiting_user", "waiting_verification"}]
        pending = []
        conversations = {row["conversation_id"] for row in records}
        for conversation in conversations:
            pending.extend(runtime.recovery.pending_verifications(conversation))
        jobs = [runtime.cron_store.get(actor, row[0], conn) for row in conn.execute("SELECT j.id FROM cron_jobs j JOIN workspaces w ON w.id=j.workspace_id WHERE w.org_id=? AND j.enabled=1 AND j.deleted_at IS NULL AND j.next_run_at>=? AND j.next_run_at<=? ORDER BY j.next_run_at,j.id", (organization, clock, clock + 86400000))]
        background = [dict(row) for row in conn.execute("SELECT j.id,j.kind,j.status,j.task_run_id,j.workspace_id,j.agent_id,j.model,j.created_at FROM memory_jobs j JOIN workspaces w ON w.id=j.workspace_id WHERE w.org_id=? AND j.status IN ('queued','running','waiting_approval') ORDER BY j.priority DESC,j.created_at,j.id", (organization,))]
        result = display_value({"active_runs": active, "pending_verifications": pending, "upcoming_jobs": jobs,
            "background_jobs": background, "at_global_seq": head, "observed_at": observed})
    runtime.identities.require(actor, "owner")
    return result


class NotificationActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["read", "presented"]


class PowerEventBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: Literal["suspend", "resume"]
    observed_at: str = Field(min_length=20, max_length=40)


class DesktopPreferencesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    close_to_quit: StrictBool
    prevent_sleep: StrictBool


def install_runtime_routes(app, runtime):
    notifications = runtime.notifications = Notifications(runtime)
    preferences = runtime.desktop_preferences = DesktopSettings(runtime)

    @app.get("/api/runtime/exit-impact")
    async def read_exit_impact(request: Request):
        return await asyncio.to_thread(exit_impact, runtime, request.state.identity)

    @app.get("/api/notifications")
    async def list_notifications(request: Request, workspace_id: str | None = None,
                                 limit: int = Query(50, ge=1, le=200), after: str | None = None):
        return await asyncio.to_thread(notifications.list, request.state.identity, workspace_id, limit, after)

    @app.patch("/api/notifications/{notification_id}")
    async def acknowledge_notification(notification_id: str, body: NotificationActionBody, request: Request):
        return await notifications.mark(request.state.identity, notification_id, body.action)

    @app.post("/api/runtime/power-event")
    async def record_power_event(body: PowerEventBody, request: Request):
        try:
            observed = datetime.fromisoformat(body.observed_at)
        except ValueError:
            raise GovernanceError("VALIDATION_ERROR", "observed_at 不是有效的 ISO 时间", 422) from None
        if observed.tzinfo is None:
            raise GovernanceError("VALIDATION_ERROR", "observed_at 必须携带时区", 422)
        identity = request.state.identity

        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                current = runtime.identities.require(identity, "manage", conn=conn)
                event = runtime.event_store.append_in_tx(conn, task_run_id=None, conversation_id=None,
                    type=RunEventType.RUNTIME_POWER_CHANGED,
                    payload={"event": body.event, "observed_at": body.observed_at, "source": "electron_power_monitor",
                             "scope": {"org_id": current["org_id"], "workspace_id": None, "agent_id": None,
                                       "owner_id": identity.effective_user_id}})
            runtime.event_store.publish(event)
            return event.global_seq
        global_seq = await runtime.event_store.channel.execute(tx)
        if body.event == "resume":
            # resume 重新核查到期与重试（ADR-013：既存到期登记 missed，不补跑）
            if runtime.cron_scheduler is not None:
                await runtime.cron_scheduler.wake()
            if runtime.cron_recovery is not None:
                await runtime.cron_recovery.refresh()
        return {"global_seq": global_seq}

    @app.get("/api/settings/desktop")
    async def read_desktop_preferences(request: Request):
        def read():
            runtime.identities.require(request.state.identity, "use")
            return preferences.get()
        return await asyncio.to_thread(read)

    @app.patch("/api/settings/desktop")
    async def edit_desktop_preferences(body: DesktopPreferencesBody, request: Request):
        return await preferences.patch(request.state.identity, body.model_dump())
