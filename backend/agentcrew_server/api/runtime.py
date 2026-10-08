"""当前真实退出影响的SQL快照。"""

import asyncio
from datetime import datetime, timezone

from fastapi import Request

from ..cron.store import now_ms
from ..runs import Runs, display_value


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


def install_runtime_routes(app, runtime):
    @app.get("/api/runtime/exit-impact")
    async def read_exit_impact(request: Request):
        return await asyncio.to_thread(exit_impact, runtime, request.state.identity)
