"""真实模型报告、审查队列及授权取消接口。"""

import asyncio

from fastapi import Request, Query
from pydantic import BaseModel, ConfigDict, Field

from agentcrew_core.memory.pagination import memory_page


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=1, max_length=128)


def install_review_routes(app, runtime):
    from ..reviews.trace_audit import TraceAudit
    service = runtime.trace_audit = TraceAudit(runtime)
    runtime.memory_jobs.trace = service
    runtime.run_manager.trace_reader = service.reader

    @app.post("/api/task-runs/{task_run_id}/review", status_code=202)
    async def start_review(task_run_id: str, body: ReviewRequest, request: Request):
        return await service.enqueue(request.state.identity, task_run_id, body.client_request_id)

    @app.get("/api/task-runs/{task_run_id}/audit-report")
    async def task_report(task_run_id: str, request: Request):
        report = await asyncio.to_thread(service.report, request.state.identity, task_run_id)
        row = runtime.db.read_conn.execute("SELECT j.id FROM memory_jobs j JOIN trace_targets t ON t.job_id=j.id WHERE t.task_run_id=? ORDER BY j.created_at DESC,j.id DESC LIMIT 1", (task_run_id,)).fetchone()
        job = service.view(request.state.identity, row[0]) if row else None
        task = runtime.db.read_conn.execute("SELECT current_attempt_no FROM task_runs WHERE id=?", (task_run_id,)).fetchone()
        head = runtime.db.read_conn.execute("SELECT max(global_seq) FROM run_events WHERE task_run_id=?", (task_run_id,)).fetchone()[0]
        applicable = bool(report and report["attempt_no"] == (task[0] or None) and head <= report["source_global_seq"])
        return {"report": report, "job": job, "applicable": applicable}

    @app.get("/api/reviews/jobs/{job_id}")
    async def read_job(job_id: str, request: Request):
        return await asyncio.to_thread(service.view, request.state.identity, job_id)

    @app.get("/api/reviews/jobs")
    async def list_jobs(request: Request, workspace_id: str | None = None, agent_id: str | None = None,
                        limit: int = Query(50, ge=1, le=200), after: str | None = None):
        actor = request.state.identity
        runtime.identities.require(actor, "use", workspace_id)
        if agent_id:
            runtime.identities.agent(actor, agent_id, workspace_id)
        rows = runtime.db.read_conn.execute("SELECT j.id,j.conversation_id,g.effective_user_id FROM memory_jobs j JOIN job_governance g ON g.job_id=j.id WHERE j.kind='trace_audit' AND (? IS NULL OR j.workspace_id=?) AND (? IS NULL OR j.agent_id=?) ORDER BY j.created_at DESC,j.id DESC", (workspace_id, workspace_id, agent_id, agent_id)).fetchall()
        visible = [service.view(actor, row[0]) for row in rows if row[2] == actor.effective_user_id and runtime.identities.visible_conversation(actor, row[1])]
        return memory_page(visible, {"actor": actor.effective_user_id, "workspace": workspace_id, "agent": agent_id}, limit, after, key="id")

    @app.post("/api/reviews/jobs/{job_id}/cancel", status_code=202)
    async def cancel_job(job_id: str, request: Request):
        service.view(request.state.identity, job_id)
        await runtime.memory_jobs.review.cancel(job_id, reason="真人取消轨迹审查")
        return service.view(request.state.identity, job_id)
