"""M1-07：核对真实 HTTP/SQL/文件证据并生成可发布的无正文记录。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def check(input_path: Path, output_path: Path) -> None:
    observed = json.loads(input_path.read_text(encoding="utf-8"))
    first, repeat, rejected = observed["queries"][0], observed["queries"][4], observed["queries"][5]
    assert len(observed["queries"][2]["rows"]) == 1
    root = Path(observed["queries"][2]["rows"][0]["path"]).resolve().parents[2]
    source = observed["files"][0]
    source_sha = source["sha256"]
    source_path = root / source["path"]
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_sha
    assert source_path.stat().st_mtime_ns == source["mtime_ns"]
    artifacts = []
    extra_queries = []
    database = root / "agentcrew.db"
    for query in (first, repeat):
        dispatched = [row for row in query["rows"] if row["type"] == "tool.dispatched"]
        externalized = [row for row in query["rows"] if row["type"] == "tool.result_externalized"]
        assert len(externalized) == 1
        event = externalized[0]
        payload = json.loads(event["payload"])
        if not dispatched:
            statement = "SELECT global_seq,task_run_id,type,payload FROM run_events WHERE task_run_id=? AND type='tool.dispatched' ORDER BY global_seq"
            with sqlite3.connect(database) as connection:
                connection.row_factory = sqlite3.Row
                dispatched = [dict(row) for row in connection.execute(statement, (event["task_run_id"],))]
            extra_queries.append({"sql": statement, "parameters": [event["task_run_id"]],
                                  "rows": dispatched})
        assert len(dispatched) == 1
        assert payload["sha256"] == source_sha and payload["size_bytes"] == source_path.stat().st_size
        assert 0 < payload["prefix_bytes"] <= 2048
        assert hashlib.sha256(Path(payload["artifact_path"]).read_bytes()).hexdigest() == source_sha
        assert payload["source_global_seq"] == dispatched[0]["global_seq"]
        artifacts.append({"event_global_seq": event["global_seq"], **payload})
    job_statement = "SELECT id,task_run_id,status,usage FROM memory_jobs WHERE task_run_id IN (?,?) ORDER BY created_at,id"
    call_statement = ("SELECT c.job_id,c.ordinal,c.type,"
                      "json_extract(c.payload,'$.total_input_tokens') AS calculated_tokens,"
                      "json_extract(c.payload,'$.prompt_tokens') AS provider_tokens "
                      "FROM memory_job_calls c JOIN memory_jobs j ON j.id=c.job_id "
                      "WHERE j.task_run_id IN (?,?) AND c.type IN ('context.budget_checked','llm.request_done') "
                      "ORDER BY c.job_id,c.ordinal")
    task_ids = [observed["task_run_id"], observed["repeat_task_run_id"]]
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        for statement in (job_statement, call_statement):
            extra_queries.append({"sql": statement, "parameters": task_ids,
                                  "rows": [dict(row) for row in connection.execute(statement, task_ids)]})
    assert [row["type"] for row in rejected["rows"]] == ["llm.request_failed", "run.failed"]
    assert "TOKENIZER_UNAVAILABLE" in json.loads(rejected["rows"][-1]["payload"])["reason"]
    requests = observed["request_headers_and_bodies"]
    assert all(request["body"]["model"] == "deepseek-v4.1-flash" for request in requests)
    request_checks = [{"body_sha256": request["body_sha256"],
        "model": request["body"]["model"], "tool_count": len(request["body"].get("tools", [])),
        "session_id": request["session_id"],
        "contains_artifact_sha": source_sha in json.dumps(request["body"], ensure_ascii=False),
        "contains_full_file": "真实资料：第七阶段预算和工件核验。" * 1000 in json.dumps(request["body"], ensure_ascii=False)}
        for request in requests]
    assert sum(row["contains_artifact_sha"] for row in request_checks) >= 2
    assert not any(row["contains_full_file"] for row in request_checks)
    assert observed["audit"]["ok"] and observed["service_exit"] == 0 and observed["error"] is None
    published = {"observed_at": observed["observed_at"],
        "conversation_id": observed["conversation_id"],
        "task_run_ids": {"first": observed["task_run_id"],
                         "repeat": observed["repeat_task_run_id"],
                         "rejected": observed["rejected_task_run_id"]},
        "http": observed["http"], "queries": observed["queries"] + extra_queries,
        "files": observed["files"], "artifacts": artifacts,
        "outgoing_request_checks": request_checks,
        "audit": observed["audit"], "service_exit": observed["service_exit"]}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(published, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"requests": len(request_checks), "artifacts": len(artifacts),
                      "audit": published["audit"], "service_exit": published["service_exit"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    check(args.input.absolute(), args.output.absolute())
