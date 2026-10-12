"""核查真实提炼水位、独立审批、文件校验与审计并发布有限证据。"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.db.database import Database
from verify_review_controls import files


def check(root, seed, controls_report, output):
    control = json.loads(controls_report.read_text())
    assert control["error"] is None and control["service_exit"] == 0
    conversation = control["conversation_id"]
    assert len(control["controls"]) == 3
    db = Database(root / "agentcrew.db")
    try:
        query = "SELECT * FROM memory_trigger_state WHERE conversation_id=?"
        state = dict(db.read_conn.execute(query, (conversation,)).fetchone())
        assert state["user_turns"] == 50 and state["memory_watermark"] == 50
        jobs_query = "SELECT * FROM memory_jobs WHERE conversation_id=? ORDER BY created_at,id"
        jobs = [dict(row) for row in db.read_conn.execute(jobs_query, (conversation,))]
        safe_jobs = []
        requests = [json.loads(line) for line in (root / "requests.jsonl").read_text().splitlines()]
        for job in jobs:
            if job["kind"] == "summary":
                continue
            calls = [dict(row) for row in db.read_conn.execute("SELECT ordinal,type,payload FROM memory_job_calls WHERE job_id=? ORDER BY ordinal", (job["id"],))]
            budgets = [json.loads(call["payload"]) for call in calls if call["type"] == "context.budget_checked"]
            assert all(budget["total_input_tokens"] <= budget["context_window"] * 75 // 100 for budget in budgets)
            prepared = [json.loads(call["payload"]) for call in calls if call["type"] == "tool.prepared"]
            assert all(call["tool_name"] in {"memory_write", "skill_patch", "session_search", "read_file"} for call in prepared)
            safe_jobs.append({key: job[key] for key in ("id", "kind", "trigger_key", "trigger_global_seq", "task_run_id", "status",
                "model", "config_version", "error", "created_at", "finished_at")})
            safe_jobs[-1].update(usage=json.loads(job["usage"]), report=json.loads(job["report"]) if job["report"] else None,
                budgets=budgets, tool_names=[call["tool_name"] for call in prepared])
        for item in control["controls"]:
            assert item["ledger_before"] == item["ledger_after"] == 0 and item["source_task_status"] == "completed"
            count = db.read_conn.execute("SELECT COUNT(*) FROM memory_ledger WHERE json_extract(source,'$.job_id')=?", (item["job_id"],)).fetchone()[0]
            assert count == 0
        before, after = files(seed), files(root)
        assert before == after
        approvals_query = "SELECT * FROM memory_job_approvals ORDER BY requested_at,id"
        approvals = [dict(row) for row in db.read_conn.execute(approvals_query)]
        for row in approvals:
            row["input"] = json.loads(row["input"])
        events_query = "SELECT global_seq,type,payload FROM run_events WHERE conversation_id=? AND type IN " \
            "('memory.job_status','memory.approval_requested','memory.approval_resolved','memory.updated','skill.patched') ORDER BY global_seq"
        events = [dict(row) for row in db.read_conn.execute(events_query, (conversation,))]
        for row in events:
            row["payload"] = json.loads(row["payload"])
        audit = verify_with_anchor(db.read_conn, root / "chain-head.txt")
        assert audit.ok
        published = {"conversation_id": conversation, "observed_at": control["observed_at"],
            "controls": control["controls"], "state_query": query, "state": state,
            "jobs_query": jobs_query, "jobs": safe_jobs, "approvals_query": approvals_query, "approvals": approvals,
            "events_query": events_query, "events": events, "files_before": before, "files_after": after,
            "files_unchanged": True, "audit": {"ok": audit.ok, "checked_count": audit.checked_count},
            "actual_aux_requests": [{"body_sha256": request["body_sha256"], "observed_at": request["observed_at"],
                "model": request["body"]["model"], "tools": [tool["function"]["name"] for tool in request["body"].get("tools", [])]}
                for request in requests if request["body"]["messages"][0]["content"].startswith("你负责检查已交付任务的持久化记忆")],
            "service_exit": control["service_exit"]}
    finally:
        db.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(published, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"user_turns": state["user_turns"], "controls": len(control["controls"]),
                      "files_unchanged": True, "audit": published["audit"]}))


def check_run(root, report_path, output):
    report = json.loads(report_path.read_text())
    assert report["error"] is None and report["service_exit"] == 0
    conversation = report["conversation_id"]
    db = Database(root / "agentcrew.db")
    try:
        state = dict(db.read_conn.execute("SELECT * FROM memory_trigger_state WHERE conversation_id=?", (conversation,)).fetchone())
        jobs_sql = "SELECT * FROM memory_jobs WHERE conversation_id=? AND kind<>'summary' ORDER BY created_at,id"
        jobs = [dict(row) for row in db.read_conn.execute(jobs_sql, (conversation,))]
        safe_jobs = []
        for job in jobs:
            calls = [dict(row) for row in db.read_conn.execute("SELECT ordinal,type,payload FROM memory_job_calls WHERE job_id=? ORDER BY ordinal", (job["id"],))]
            budgets = [json.loads(call["payload"]) for call in calls if call["type"] == "context.budget_checked"]
            names = [json.loads(call["payload"])["tool_name"] for call in calls if call["type"] == "tool.prepared"]
            assert set(names) <= {"memory_write", "skill_patch", "session_search", "read_file"}
            assert all(budget["total_input_tokens"] <= budget["context_window"] * 75 // 100 for budget in budgets)
            safe_jobs.append({key: job[key] for key in ("id", "kind", "trigger_key", "trigger_global_seq", "task_run_id", "status", "model", "config_version", "error")})
            safe_jobs[-1].update(usage=json.loads(job["usage"]), report=json.loads(job["report"]) if job["report"] else None,
                budgets=budgets, tool_names=names)
        ledger_sql = "SELECT * FROM memory_ledger WHERE json_extract(source,'$.conversation_id')=? AND json_extract(source,'$.job_id') IS NOT NULL ORDER BY id"
        ledger = [dict(row) for row in db.read_conn.execute(ledger_sql, (conversation,))]
        assert {row["store_type"] for row in ledger} >= {"user", "workspace", "soul", "skill"}
        requests = []
        for request in report["requests"]:
            body = request["body"]
            if not body["messages"][0]["content"].startswith("你负责检查已交付任务的持久化记忆"):
                continue
            material = json.loads(body["messages"][1]["content"])
            assert len(material["recent_raw_messages"]) <= 24
            assert all("\n" not in row["text"] for row in material["older_single_line_summaries"])
            requests.append({"body_sha256": request["body_sha256"], "observed_at": request["observed_at"],
                "model": body["model"], "source_task_run_id": material["source_task_run_id"], "kind": material["kind"],
                "raw_messages": len(material["recent_raw_messages"]), "older_summaries": len(material["older_single_line_summaries"]),
                "missing_summary_task_ids": material["missing_summary_task_ids"],
                "tools": [tool["function"]["name"] for tool in body.get("tools", [])],
                "skill_index_present": any("【当前范围的 Skill 索引" in (message.get("content") or "") for message in body["messages"])})
        assert requests and all(request["skill_index_present"] for request in requests)
        audit = verify_with_anchor(db.read_conn, root / "chain-head.txt")
        assert audit.ok
        published = {"conversation_id": conversation, "observed_at": report["observed_at"], "task_run_ids": report["task_run_ids"],
            "state_query": "SELECT * FROM memory_trigger_state WHERE conversation_id=?", "state": state,
            "jobs_query": jobs_sql, "jobs": safe_jobs, "ledger_query": ledger_sql, "ledger": ledger,
            "actual_requests": requests, "files": files(root), "service_exit": report["service_exit"],
            "http": [{"method": row["method"], "path": row["path"], "status": row["status"], "request_seconds": row["request_seconds"]} for row in report["http"]],
            "audit": {"ok": audit.ok, "checked_count": audit.checked_count}}
    finally:
        db.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(published, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"state": state, "actual_aux_requests": len(requests), "audit": published["audit"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--seed-data-dir", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.run:
        check_run(args.data_dir.absolute(), args.report.absolute(), args.output.absolute())
    else:
        check(args.data_dir.absolute(), args.seed_data_dir.absolute(), args.report.absolute(), args.output.absolute())
