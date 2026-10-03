"""M1-07：真实 HTTP、main/aux、SQLite 与上下文预算事件贯穿验收。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.memory.tokenizer import DEEPSEEK_V41
from verify_summaries import lines, query, start, until


async def verify(seed: Path, root: Path, output: Path) -> None:
    shutil.copytree(seed, root, ignore=shutil.ignore_patterns(
        "requests.jsonl", "streams.jsonl", "service.log", "instance.lock"))
    token = uuid.uuid4().hex
    process, log, port = await start(root, token)
    exchanges = []
    task_id = conversation_id = None
    repeat_task_id = rejected_task_id = None
    source = root / "workspaces" / "default" / "M1-07-资料.txt"
    error = None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                                     headers={"Authorization": f"Bearer {token}"}) as client:
            settings = await client.patch("/api/settings", json={"models": {
                "main": DEEPSEEK_V41, "aux": DEEPSEEK_V41}})
            assert settings.status_code == 200, settings.text
            exchanges.append({"method": "PATCH", "path": "/api/settings",
                "status": settings.status_code, "config_version": settings.json()["data"]["settings"]["config_version"]})
            source.parent.mkdir(parents=True, exist_ok=True)
            body = ("真实资料：第七阶段预算和工件核验。" * 8_000).encode("utf-8")
            source.write_bytes(body)
            request = {"instruction": f"请使用 read_file 读取这个真实资料文件：{source}。核对开头内容，随后用一句话回复。",
                       "agent_id": "skill-validator", "client_request_id": uuid.uuid4().hex}
            response = await client.post("/api/conversations", json=request)
            assert response.status_code == 201, response.text
            created = response.json()["data"]
            task_id, conversation_id = created["task_run_id"], created["conversation"]["id"]
            exchanges.append({"method": "POST", "path": "/api/conversations",
                "status": response.status_code, "task_run_id": task_id,
                "conversation_id": conversation_id, "client_request_id": request["client_request_id"]})
            terminal = await until(root, "SELECT status FROM task_runs WHERE id=? AND status IN ('completed','failed','cancelled')",
                                   (task_id,), seconds=240)
            assert terminal[0]["status"] == "completed", terminal
            detail_path = f"/api/conversations/{conversation_id}/task-runs"
            detail = await client.get(detail_path)
            assert detail.status_code == 200 and any(
                row["id"] == task_id and row["status"] == "completed"
                for row in detail.json()["data"])
            exchanges.append({"method": "GET", "path": detail_path,
                "status": detail.status_code})
            stored = query(root, "SELECT path FROM artifacts WHERE task_run_id=? AND status='ready' ORDER BY id",
                           (task_id,))
            if stored:
                repeat = await client.post(f"/api/conversations/{conversation_id}/instructions",
                    json={"text": f"请使用 read_file 再次读取刚才保存的大型工具工件：{stored[0]['path']}。核对内容开头，并用一句话回复。",
                          "client_request_id": uuid.uuid4().hex})
                assert repeat.status_code in (201, 202), repeat.text
                repeat_task_id = repeat.json()["data"]["task_run_id"]
                repeated = await until(root, "SELECT status FROM task_runs WHERE id=? AND status IN ('completed','failed','cancelled')",
                                       (repeat_task_id,), seconds=240)
                assert repeated[0]["status"] == "completed", repeated
                exchanges.append({"method": "POST", "path": f"/api/conversations/{conversation_id}/instructions",
                                  "status": repeat.status_code, "task_run_id": repeat_task_id})
            rejected_patch = await client.patch("/api/settings", json={"models": {
                "main": {"model": "model-without-counting-basis"}}})
            assert rejected_patch.status_code == 200, rejected_patch.text
            rejected = await client.post(f"/api/conversations/{conversation_id}/instructions",
                json={"text": "请确认模型计数配置状态。", "client_request_id": uuid.uuid4().hex})
            assert rejected.status_code in (201, 202), rejected.text
            rejected_task_id = rejected.json()["data"]["task_run_id"]
            rejected_state = await until(root, "SELECT status FROM task_runs WHERE id=? AND status='failed'",
                                         (rejected_task_id,), seconds=60)
            assert rejected_state[0]["status"] == "failed"
            failure = query(root, "SELECT json_extract(payload,'$.reason') AS reason FROM run_events "
                                 "WHERE task_run_id=? AND type='run.failed'", (rejected_task_id,))
            assert len(failure) == 1 and "TOKENIZER_UNAVAILABLE" in failure[0]["reason"], failure
            restored = await client.patch("/api/settings", json={"models": {
                "main": {"model": "deepseek-v4.1-flash"}}})
            assert restored.status_code == 200, restored.text
            exchanges.extend([
                {"method": "PATCH", "path": "/api/settings", "status": rejected_patch.status_code,
                 "model": "model-without-counting-basis"},
                {"method": "POST", "path": f"/api/conversations/{conversation_id}/instructions",
                 "status": rejected.status_code, "task_run_id": rejected_task_id,
                 "result": failure[0]["reason"]},
                {"method": "PATCH", "path": "/api/settings", "status": restored.status_code,
                 "model": "deepseek-v4.1-flash"},
            ])
    finally:
        error = sys.exception()
        if process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 20)
        log.close()
        sqls = [
            ("SELECT global_seq,task_run_id,type,payload FROM run_events WHERE task_run_id=? AND type IN ('context.budget_checked','llm.request_done','tool.dispatched','tool.result_externalized','tool.completed','tool.failed','run.completed','run.failed') ORDER BY global_seq", (task_id,)),
            ("SELECT id,status,finished_at FROM task_runs WHERE id=?", (task_id,)),
            ("SELECT a.id,a.path,a.size_bytes,a.status FROM artifacts a WHERE a.task_run_id=? ORDER BY a.id", (task_id,)),
            ("SELECT id,status,usage FROM memory_jobs WHERE task_run_id=?", (task_id,)),
            ("SELECT global_seq,task_run_id,type,payload FROM run_events WHERE task_run_id=? "
             "AND type IN ('context.budget_checked','llm.request_done','tool.result_externalized','tool.completed','run.completed') ORDER BY global_seq", (repeat_task_id,)),
            ("SELECT global_seq,task_run_id,type,payload FROM run_events WHERE task_run_id=? "
             "AND type IN ('context.budget_checked','llm.request_failed','run.failed') ORDER BY global_seq", (rejected_task_id,)),
        ]
        sql = [{"sql": statement, "parameters": parameters, "rows": query(root, statement, parameters)}
               for statement, parameters in sqls]
        with sqlite3.connect(root / "agentcrew.db") as conn:
            audit = verify_with_anchor(conn, root / "chain-head.txt")
        snapshot = root / "conversations" / conversation_id / "memory-snapshot.json" if conversation_id else None
        files = [{"path": str(path.relative_to(root)),
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "mtime_ns": path.stat().st_mtime_ns}
                 for path in ([source, snapshot] if conversation_id else []) if path.exists()]
        evidence = {"observed_at": datetime.now(timezone.utc).isoformat(),
            "conversation_id": conversation_id, "task_run_id": task_id,
            "repeat_task_run_id": repeat_task_id, "rejected_task_run_id": rejected_task_id,
            "http": exchanges, "queries": sql, "files": files,
            "request_headers_and_bodies": lines(root / "requests.jsonl"),
            "audit": {"ok": audit.ok, "checked_count": audit.checked_count,
                      "reason": audit.reason}, "service_exit": process.returncode,
            "error": f"{type(error).__name__}: {error}" if error else None}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert audit.ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-data-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.seed_data_dir.absolute(), args.data_dir.absolute(),
                       args.output.absolute()))
