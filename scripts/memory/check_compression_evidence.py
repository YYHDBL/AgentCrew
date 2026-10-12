"""核对真实压缩检查点、事件、恢复请求并输出脱敏验收证据。"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.memory.checkpoints import ContextCheckpoints
from agentcrew_server.db.database import Database
from agentcrew_core.provider.openai_compatible import chat_messages


def check(root: Path, report_path: Path, output: Path):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["error"] is None and report["service_exit"] == 0
    assert report["sigkill"]["exit_code"] == -9
    assert report["sigkill"]["stream"]["event"] == "actual_main_text_delta"
    assert report["resume_http"]["status"] == 202
    conversation = report["conversation_id"]
    task = report["resumed_task_run_id"]
    requests = [json.loads(line) for line in (root / "requests.jsonl").read_text(encoding="utf-8").splitlines()]
    main_requests = [request for request in requests if request["body"].get("tools")]
    db = Database(root / "agentcrew.db")
    try:
        latest = ContextCheckpoints(db, None, root).latest(conversation)
        assert latest is not None
        statement = "SELECT * FROM context_checkpoints WHERE conversation_id=? ORDER BY event_global_seq"
        rows = [dict(row) for row in db.read_conn.execute(statement, (conversation,))]
        checkpoints = []
        for row in rows:
            assert ContextCheckpoints._sha(ContextCheckpoints._body(row)) == row["sha256"]
            summary = json.loads(row["summary_json"])
            messages = json.loads(row["retained_messages_json"])
            wire_messages = chat_messages(messages)
            matching = [request for request in main_requests if request["body"]["messages"][1:] == wire_messages]
            assert matching, f"检查点 {row['id']} 缺少对应的真实供应商请求"
            checkpoints.append({key: row[key] for key in (
                "id", "conversation_id", "task_run_id", "source_global_seq", "event_global_seq",
                "snapshot_id", "before_tokens", "after_tokens", "summarized_messages",
                "template_version", "sha256", "aux_model", "aux_usage_json")})
            checkpoints[-1].update(summary_fields=list(summary),
                summary=summary, retained_messages=len(messages),
                message_sources=[message.get("_event_global_seqs", []) for message in messages],
                instruction_ids=[message["_task_instruction_id"] for message in messages if "_task_instruction_id" in message],
                retained_sha256=hashlib.sha256(row["retained_messages_json"].encode()).hexdigest(),
                actual_requests=[{"body_sha256": request["body_sha256"], "observed_at": request["observed_at"],
                                  "messages_equal_checkpoint": True} for request in matching],
                artifacts=json.loads(row["artifacts_json"]))
        assert len(checkpoints) >= 2
        assert all(row["after_tokens"] < row["before_tokens"] for row in checkpoints)
        run = dict(db.read_conn.execute("SELECT id,status,current_attempt_no FROM task_runs WHERE id=?", (task,)).fetchone())
        assert run["status"] == "completed" and run["current_attempt_no"] == 2
        instruction = db.read_conn.execute("SELECT instruction FROM task_runs WHERE id=?", (task,)).fetchone()[0]
        resumed_requests = [request for request in main_requests if
            "【中断恢复·副作用账本】" in request["body"]["messages"][0]["content"] and
            any(message.get("content") == instruction for message in request["body"]["messages"])]
        assert resumed_requests, "恢复任务缺少带副作用账本的真实请求"
        checkpoint_wire = chat_messages(latest["messages"])
        resume_checks = []
        for request in resumed_requests:
            actual = request["body"]["messages"]
            starts = [index for index in range(len(actual) - len(checkpoint_wire) + 1)
                      if actual[index:index + len(checkpoint_wire)] == checkpoint_wire]
            assert starts, "恢复请求没有完整使用最近检查点"
            resume_checks.append({"body_sha256": request["body_sha256"], "observed_at": request["observed_at"],
                                  "checkpoint_message_start": starts[0], "checkpoint_messages": len(checkpoint_wire),
                                  "actual_messages": len(actual), "side_effect_ledger_present": True})
        event_query = ("SELECT global_seq,task_run_id,type,payload,attempt_no FROM run_events "
                       "WHERE conversation_id=? AND type IN ('context.compacted','context.budget_checked',"
                       "'run.interrupted','run.resumed','run.completed','run.failed') ORDER BY global_seq")
        events = [dict(row) for row in db.read_conn.execute(event_query, (conversation,))]
        for row in events:
            payload = json.loads(row["payload"])
            if row["type"] == "run.completed":
                payload = {"outcome": payload.get("outcome")}
            row["payload"] = payload
        audit = verify_with_anchor(db.read_conn, root / "chain-head.txt")
        assert audit.ok
        snapshot = root / "conversations" / conversation / "memory-snapshot.json"
        file = {"path": str(snapshot.relative_to(root)),
                "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                "mtime_ns": snapshot.stat().st_mtime_ns}
        assert {key: file[key] for key in ("sha256", "mtime_ns")} == report["snapshot_initial"]
        published = {"conversation_id": conversation, "resumed_task": run,
            "observed_at": report["observed_at"], "corpus": report["corpus"],
            "tasks": report["tasks"], "sigkill": report["sigkill"],
            "resume_http": report["resume_http"], "checkpoint_query": statement,
            "resume_requests": resume_checks,
            "checkpoints": checkpoints, "event_query": event_query, "events": events,
            "snapshot": file, "audit": {"ok": audit.ok, "checked_count": audit.checked_count},
            "request_count": report["request_count"], "service_exit": report["service_exit"]}
    finally:
        db.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(published, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checkpoints": len(checkpoints), "resumed_task": run,
                      "audit": published["audit"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    check(args.data_dir.absolute(), args.report.absolute(), args.output.absolute())
