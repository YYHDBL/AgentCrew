"""当前请求者及审查目标范围内的事件快照。"""

import asyncio

from agentcrew_core.governance import RequestIdentity
from agentcrew_core.events.run_projection import project_run_state, project_attempts
from ..governance.resources import GovernanceError
from ..runs import Runs, display_value, event_frame
from ..db.projections import _row_to_event


class TraceReader:
    def __init__(self, runtime):
        self.runtime, self.db = runtime, runtime.db

    def read(self, invocation, context):
        conn = self.db.read_conn
        if context.job_id:
            row = conn.execute("SELECT credential_owner_id,effective_user_id FROM job_governance WHERE job_id=?", (context.job_id,)).fetchone()
            if row is None:
                raise GovernanceError("OUT_OF_SCOPE", "审查作业缺少来源身份", 403)
            actor = RequestIdentity(row[0], row[1], row[0] != row[1])
            self.runtime.identities.job(actor, context.job_id)
        else:
            actor = self.runtime.identities.task(context.task_run_id)
        task_id = invocation.input["task_run_id"]
        runs = Runs(self.runtime)
        with runs.snapshot(actor) as snapshot:
            runs.require_task(snapshot, actor, task_id)
            source = snapshot.execute("SELECT c.workspace_id,c.agent_id,g.effective_user_id,g.credential_owner_id FROM task_runs t JOIN conversations c ON c.id=t.conversation_id JOIN task_governance g ON g.task_run_id=t.id WHERE t.id=?", (context.task_run_id,)).fetchone()
            target = snapshot.execute("SELECT c.workspace_id,c.agent_id,g.effective_user_id,g.credential_owner_id FROM task_runs t JOIN conversations c ON c.id=t.conversation_id JOIN task_governance g ON g.task_run_id=t.id WHERE t.id=?", (task_id,)).fetchone()
            if tuple(source) != tuple(target):
                raise GovernanceError("OUT_OF_SCOPE", "轨迹不属于当前员工、工作区及请求者", 403)
            head = runs.head(snapshot)
            if context.job_id:
                bound = snapshot.execute("SELECT source_global_seq FROM trace_targets WHERE job_id=? AND task_run_id=?", (context.job_id, task_id)).fetchone()
                if bound is None:
                    raise GovernanceError("OUT_OF_SCOPE", "任务不属于该审查的冻结目标", 403)
                head = min(head, bound[0])
            requested = invocation.input.get("through_global_seq", head)
            if requested > head:
                raise GovernanceError("OUT_OF_SCOPE", "读取水位超出审查绑定", 403)
            head = requested
            limit, after = invocation.input.get("limit", 50), invocation.input.get("after_seq", 0)
            rows = snapshot.execute("SELECT * FROM run_events WHERE task_run_id=? AND seq>? AND global_seq<=? ORDER BY seq LIMIT ?", (task_id, after, head, limit + 1)).fetchall()
            task = dict(snapshot.execute("SELECT * FROM task_runs WHERE id=?", (task_id,)).fetchone())
            all_events = [_row_to_event(row) for row in snapshot.execute("SELECT * FROM run_events WHERE task_run_id=? AND global_seq<=? ORDER BY seq", (task_id, head))]
            task.update(project_run_state(all_events))
            attempts = project_attempts(all_events)
            summaries = [dict(row) for row in snapshot.execute("SELECT s.id,s.text FROM session_summaries s JOIN memory_jobs j ON j.id=s.job_id WHERE s.task_run_id=? AND j.trigger_global_seq<=?", (task_id, head))]
            result = display_value({"task": task, "attempts": attempts, "summaries": summaries,
                "items": [event_frame(_row_to_event(row)) for row in rows[:limit]], "has_more": len(rows) > limit,
                "next_after_seq": rows[limit - 1]["seq"] if len(rows) > limit else None, "at_global_seq": head})
        runs.require_task(self.db.read_conn, actor, task_id)
        return result

    async def run_tool(self, invocation, context):
        return await asyncio.to_thread(self.read, invocation, context)
