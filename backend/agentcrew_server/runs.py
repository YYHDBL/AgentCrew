"""运行中心只读查询：权限、SQLite 快照、事件定位和分别聚合的指标。"""

import hashlib
import json
import re
from contextlib import contextmanager
from pathlib import Path

from agentcrew_core.events.run_projection import project_run_state

from .db.projections import _row_to_event
from .governance.resources import GovernanceError
from .secrets import redact


_HIDDEN = frozenset({"thinking_blocks", "reasoning_content", "thinking", "signature",
                     "api_key", "credential", "authorization", "cookie", "identity_token"})


def display_value(value):
    if isinstance(value, dict):
        return {key: display_value(item) for key, item in value.items()
                if key.lower() not in _HIDDEN}
    if isinstance(value, list):
        return [display_value(item) for item in value]
    return redact(value) if isinstance(value, str) else value


def event_frame(event):
    frame = display_value(event.as_frame())
    frame["attempt_no"] = event.attempt_no
    if event.conversation_id is not None:
        frame["conversation_id"] = event.conversation_id
    return frame


class Runs:
    def __init__(self, runtime):
        self.db, self.identities = runtime.db, runtime.identities

    @contextmanager
    def snapshot(self, identity):
        conn = self.db.read_conn
        conn.execute("BEGIN")
        try:
            self.identities.current(identity, conn)
            yield conn
        finally:
            conn.rollback()
        self.identities.current(identity)

    @staticmethod
    def head(conn):
        return conn.execute("SELECT coalesce(max(global_seq),0) FROM run_events").fetchone()[0]

    def require_task(self, conn, identity, task_id):
        row = conn.execute("SELECT conversation_id FROM task_runs WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "任务不存在", 404)
        self.identities.conversation(identity, row[0], conn)
        return row[0]

    def filtered(self, conn, identity, filters, *, head=None):
        current = self.identities.current(identity, conn)
        if filters.get("workspace_id"):
            self.identities.require(identity, "use", filters["workspace_id"], conn)
        if filters.get("agent_id"):
            self.identities.agent(identity, filters["agent_id"], filters.get("workspace_id"), conn)
        if filters.get("conversation_id"):
            self.identities.conversation(identity, filters["conversation_id"], conn)
        clauses = ["w.org_id=?", "w.status='active'", "a.status='active'"]
        values = [current["org_id"]]
        if current["role"] != "owner":
            clauses += ["gc.effective_user_id=?", "EXISTS(SELECT 1 FROM workspace_members wm WHERE wm.workspace_id=c.workspace_id AND wm.user_id=? AND wm.enabled=1)"]
            values += [identity.effective_user_id, identity.effective_user_id]
        if current["role"] == "member":
            clauses.append("EXISTS(SELECT 1 FROM grants gr WHERE gr.resource_type='agent' AND gr.resource_id=c.agent_id AND gr.grantee_type='user' AND gr.grantee_id=? AND gr.revoked_at IS NULL)")
            values.append(identity.effective_user_id)
        for name, column in (("workspace_id", "c.workspace_id"), ("agent_id", "c.agent_id"),
                             ("conversation_id", "t.conversation_id")):
            if filters.get(name):
                clauses.append(f"{column}=?")
                values.append(filters[name])
        for name, comparison in (("since", ">="), ("until", "<=")):
            if filters.get(name):
                clauses.append(f"t.created_at{comparison}?")
                values.append(filters[name])
        source = "CASE WHEN t.cron_job_id IS NULL THEN 'user' WHEN json_extract(q.payload,'$.trigger')='manual' THEN 'manual' ELSE 'cron' END"
        if filters.get("source"):
            clauses.append(f"({source})=?")
            values.append(filters["source"])
        if head is not None:
            clauses.append("q.global_seq<=?")
            values.append(head)
        query = f"""SELECT t.*,c.workspace_id,c.agent_id,g.effective_user_id AS owner_id,
            g.agent_spec AS agent_spec_snapshot,g.skill_versions,{source} AS source,
            json_extract(q.payload,'$.occurrence_id') AS occurrence_id,
            (SELECT json_extract(e.payload,'$.reason') FROM run_events e
             WHERE e.task_run_id=t.id AND e.type='run.failed' ORDER BY e.seq DESC LIMIT 1) AS error
            FROM task_runs t JOIN conversations c ON c.id=t.conversation_id
            JOIN governance_conversations gc ON gc.conversation_id=c.id
            JOIN task_governance g ON g.task_run_id=t.id
            JOIN workspaces w ON w.id=c.workspace_id JOIN agents a ON a.id=c.agent_id
            JOIN run_events q ON q.task_run_id=t.id AND q.type='run.queued'
            WHERE {' AND '.join(clauses)} ORDER BY t.created_at DESC,t.id DESC"""
        rows = [dict(row) for row in conn.execute(query, values)]
        if head is not None and head < self.head(conn):
            for row in rows:
                events = [_row_to_event(event) for event in conn.execute(
                    "SELECT * FROM run_events WHERE task_run_id=? AND global_seq<=? ORDER BY seq", (row["id"], head))]
                row.update(project_run_state(events))
        if filters.get("status"):
            rows = [row for row in rows if row["status"] == filters["status"]]
        return rows

    def recheck(self, identity, conversation_ids):
        for conversation_id in set(conversation_ids):
            self.identities.conversation(identity, conversation_id)

    @staticmethod
    def record(row, head):
        return display_value({**row, "agent_spec_snapshot": json.loads(row["agent_spec_snapshot"]),
                              "skill_versions": json.loads(row["skill_versions"]), "at_global_seq": head})

    def list(self, identity, filters, limit, after):
        binding = hashlib.sha256(json.dumps({"credential": identity.credential_owner_id,
            "actor": identity.effective_user_id, "filters": filters}, sort_keys=True).encode()).hexdigest()
        with self.snapshot(identity) as conn:
            head = self.head(conn)
            cursor_id = None
            if after is not None:
                match = re.fullmatch(r"([a-f0-9]{64}):([0-9]+):([a-f0-9]{32})", after)
                if match is None or match[1] != binding or int(match[2]) > head:
                    raise GovernanceError("VALIDATION_ERROR", "游标与当前身份或筛选范围不一致", 422)
                head, cursor_id = int(match[2]), match[3]
            rows = self.filtered(conn, identity, filters, head=head)
            if cursor_id is not None:
                index = next((i for i, row in enumerate(rows) if row["id"] == cursor_id), None)
                if index is None:
                    raise GovernanceError("VALIDATION_ERROR", "游标对应记录已失去可见性", 422)
                rows = rows[index + 1:]
            selected = rows[:limit]
            next_after = f'{binding}:{head}:{selected[-1]["id"]}' if selected and len(rows) > limit else None
            result = {"items": [self.record(row, head) for row in selected],
                      "next_after": next_after, "at_global_seq": head}
        self.recheck(identity, [row["conversation_id"] for row in selected])
        return result

    def read(self, identity, task_id):
        with self.snapshot(identity) as conn:
            conversation_id = self.require_task(conn, identity, task_id)
            head = self.head(conn)
            rows = self.filtered(conn, identity, {"conversation_id": conversation_id}, head=head)
            row = next((row for row in rows if row["id"] == task_id), None)
            if row is None:
                raise GovernanceError("REPLAY_CORRUPT", "运行身份或起始事件投影缺失", 409)
            result = self.record(row, head)
        self.require_task(self.db.read_conn, identity, task_id)
        return result

    def events(self, identity, task_id, after, limit, through=None, attempt=None):
        with self.snapshot(identity) as conn:
            self.require_task(conn, identity, task_id)
            head = self.head(conn)
            if through is not None:
                if through > head:
                    raise GovernanceError("VALIDATION_ERROR", "事件水位超过当前已提交记录", 422)
                head = through
            condition, values = "", [task_id, after, head]
            if attempt is not None:
                condition = " AND attempt_no=?"
                values.append(attempt)
            values.append(limit + 1)
            rows = conn.execute("SELECT * FROM run_events WHERE task_run_id=? AND seq>? AND global_seq<=?"
                                + condition + " ORDER BY seq LIMIT ?", values).fetchall()
            selected = rows[:limit]
            more = len(rows) > limit
            result = {"items": [event_frame(_row_to_event(row)) for row in selected],
                      "next_after_seq": selected[-1]["seq"] if more else None,
                      "at_global_seq": head, "has_more": more}
        self.require_task(self.db.read_conn, identity, task_id)
        return result

    def locate(self, identity, task_id, seq, attempt=None):
        with self.snapshot(identity) as conn:
            self.require_task(conn, identity, task_id)
            row = conn.execute("SELECT * FROM run_events WHERE task_run_id=? AND seq=?", (task_id, seq)).fetchone()
            if row is None or attempt is not None and row["attempt_no"] != attempt:
                raise GovernanceError("NOT_FOUND", "指定任务与尝试的事件不存在", 404)
            result = event_frame(_row_to_event(row))
        self.require_task(self.db.read_conn, identity, task_id)
        return result

    def call(self, identity, task_id, call_id):
        with self.snapshot(identity) as conn:
            self.require_task(conn, identity, task_id)
            tool = conn.execute("SELECT * FROM tool_calls WHERE task_run_id=? AND call_id=?", (task_id, call_id)).fetchone()
            model = conn.execute("SELECT l.* FROM llm_calls l JOIN steps s ON s.id=l.step_id WHERE s.task_run_id=? AND l.id=?", (task_id, call_id)).fetchone()
            if tool is None and model is None:
                raise GovernanceError("NOT_FOUND", "任务调用投影不存在", 404)
            kind = "tool" if tool is not None else "model"
            summary = dict(tool if tool is not None else model)
            if kind == "tool":
                summary["input"] = json.loads(summary["input"])
            rows = conn.execute("SELECT * FROM run_events WHERE task_run_id=? AND (json_extract(payload,'$.call_id')=? OR json_extract(payload,'$.tool_call_id')=? OR json_extract(payload,'$.llm_call_id')=?) ORDER BY seq", (task_id, call_id, call_id, call_id)).fetchall()
            if not rows:
                raise GovernanceError("REPLAY_CORRUPT", "调用缺少对应原始事件", 409)
            summary["tool_declarations"] = None
            missing = []
            if kind == "model":
                request_payload = json.loads(rows[0]["payload"])
                summary["tool_declarations"] = request_payload.get("tool_declarations")
                if summary["tool_declarations"] is None:
                    missing.append("tool_declarations")
                next_request = conn.execute("SELECT min(seq) FROM run_events WHERE task_run_id=? AND seq>? AND type='llm.request_started'", (task_id, rows[0]["seq"])).fetchone()[0]
                end = rows[-1]["seq"] if rows[-1]["type"] != "llm.request_started" else conn.execute("SELECT max(seq) FROM run_events WHERE task_run_id=?", (task_id,)).fetchone()[0]
                budget = conn.execute("SELECT payload FROM run_events WHERE task_run_id=? AND seq>? AND seq<=? AND type='context.budget_checked' AND attempt_no IS ? ORDER BY seq LIMIT 1",
                    (task_id, rows[0]["seq"], min(end, next_request - 1) if next_request is not None else end, rows[0]["attempt_no"])).fetchone()
                if budget:
                    summary["request_summary"] = display_value(json.loads(budget[0]))
                else:
                    missing.append("request_summary")
                declarations = conn.execute("SELECT context_fingerprint FROM run_attempts WHERE task_run_id=? AND attempt_no=?", (task_id, rows[0]["attempt_no"])).fetchone()
                if declarations and declarations[0]:
                    summary["context_fingerprint"] = display_value(json.loads(declarations[0]))
            path = summary.get("artifact_path")
            if path:
                if not Path(path).is_file():
                    missing.append("artifact")
            result = {"id": call_id, "kind": kind, "task_run_id": task_id,
                "attempt_no": rows[0]["attempt_no"], "seq": rows[0]["seq"], "step_id": summary.get("step_id"),
                "status": summary.get("status") if kind == "tool" else "failed" if summary["error"] else "completed" if summary["latency_ms"] is not None else "running",
                "summary": display_value(summary), "events": [event_frame(_row_to_event(row)) for row in rows], "missing": missing}
        self.require_task(self.db.read_conn, identity, task_id)
        return result

    def metrics(self, identity, filters):
        with self.snapshot(identity) as conn:
            head = self.head(conn)
            rows = self.filtered(conn, identity, filters, head=head)
            ids = [row["id"] for row in rows]
            status = {}
            for row in rows:
                status[row["status"]] = status.get(row["status"], 0) + 1
            placeholders = ",".join("?" for _ in ids) or "NULL"
            llm = conn.execute(f"""SELECT count(*),coalesce(sum(l.prompt_tokens),0),coalesce(sum(l.completion_tokens),0),
                coalesce(sum(l.latency_ms),0),coalesce(sum(CASE WHEN l.prompt_tokens IS NULL OR l.completion_tokens IS NULL
                OR l.prompt_tokens=0 AND l.completion_tokens=0 THEN 1 ELSE 0 END),0),count(l.latency_ms)
                FROM llm_calls l JOIN steps s ON s.id=l.step_id WHERE s.task_run_id IN ({placeholders})""", ids).fetchone()
            step_count = conn.execute(f"SELECT count(*) FROM steps WHERE task_run_id IN ({placeholders})", ids).fetchone()[0]
            tools = dict(conn.execute(f"SELECT status,count(*) FROM tool_calls WHERE task_run_id IN ({placeholders}) GROUP BY status", ids))
            completed = status.get("completed", 0)
            result = {"task_count": len(rows), "completed_count": completed,
                "completion_rate": completed / len(rows) if rows else None, "status_counts": status,
                "step_count": step_count, "llm_call_count": llm[0], "tool_call_count": sum(tools.values()),
                "tool_status_counts": tools, "prompt_tokens": llm[1] if not llm[4] else None,
                "completion_tokens": llm[2] if not llm[4] else None, "known_prompt_tokens": llm[1],
                "known_completion_tokens": llm[2], "usage_complete": not bool(llm[4]),
                "missing_usage_calls": llm[4], "latency_ms": llm[3] if llm[5] == llm[0] else None, "at_global_seq": head}
        self.recheck(identity, [row["conversation_id"] for row in rows])
        return result
