"""读取真实验收数据库，核对事件投影、记忆事务、文件及两级审计。"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.db.database import Database
from verify_summaries import query


def verify(root, output):
    sqls = [
        "SELECT t.id,t.status,t.current_attempt_no,COUNT(DISTINCT s.id) AS projected_steps,"
        "(SELECT COUNT(*) FROM run_events e WHERE e.task_run_id=t.id AND e.type='step.started') AS started_steps "
        "FROM task_runs t LEFT JOIN steps s ON s.task_run_id=t.id GROUP BY t.id ORDER BY t.created_at,t.id",
        "SELECT t.id,COUNT(DISTINCT l.id) AS projected_calls,"
        "(SELECT COUNT(*) FROM run_events e WHERE e.task_run_id=t.id AND e.type='llm.request_started') AS started_calls "
        "FROM task_runs t LEFT JOIN steps s ON s.task_run_id=t.id LEFT JOIN llm_calls l ON l.step_id=s.id GROUP BY t.id ORDER BY t.created_at,t.id",
        "SELECT change_id,status,(SELECT COUNT(*) FROM memory_ledger l WHERE l.change_id=m.change_id) AS ledgers,"
        "(SELECT COUNT(*) FROM run_events e WHERE e.type IN ('memory.updated','memory.archived','skill.patched') "
        "AND json_extract(e.payload,'$.change_id')=m.change_id) AS memory_events FROM memory_changes m ORDER BY created_at,change_id",
        "SELECT id,change_id,store_type,store_id,action,revision,source,audit_seq,global_seq FROM memory_ledger ORDER BY id",
        "SELECT id,conversation_id,status,current_attempt_no FROM task_runs ORDER BY created_at,id",
        "SELECT conversation_id,sha256,global_seq FROM memory_snapshots ORDER BY created_at",
        "SELECT id,conversation_id,event_global_seq,source_global_seq,sha256,before_tokens,after_tokens FROM context_checkpoints ORDER BY event_global_seq",
        "SELECT MAX(global_seq) AS event_watermark FROM run_events", "PRAGMA foreign_key_check"]
    result = {"queries": [{"sql": sql, "rows": query(root, sql)} for sql in sqls]}
    assert all(row["projected_steps"] == row["started_steps"] for row in result["queries"][0]["rows"])
    assert all(row["projected_calls"] == row["started_calls"] for row in result["queries"][1]["rows"])
    assert all(row["status"] == "committed" and row["ledgers"] == row["memory_events"] == 1
        for row in result["queries"][2]["rows"])
    assert result["queries"][-1]["rows"] == []
    db = Database(root / "agentcrew.db")
    audit = verify_with_anchor(db.read_conn, root / "chain-head.txt")
    db.close()
    assert audit.ok
    result["audit"] = {"ok": audit.ok, "checked_count": audit.checked_count, "reason": audit.reason}
    result["files"] = [{"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "mtime_ns": str(path.stat().st_mtime_ns)} for path in sorted(root.rglob("*.md"))
        + sorted(root.rglob("*.meta.json")) + sorted(root.rglob("memory-snapshot.json"))
        + sorted((root / "workspaces").rglob("*.txt"))]
    result["all_checks_passed"] = True
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(output), "audit": result["audit"], "all_checks_passed": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.data_dir.absolute(), args.output.absolute())
