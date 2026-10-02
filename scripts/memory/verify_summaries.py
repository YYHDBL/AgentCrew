"""M1-06：真实 main/aux、HTTP 连续任务、前台取消及 SIGKILL 验收。"""

import argparse
import asyncio
import json
import os
import shutil
import signal
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_core.memory import sha256
from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.db.database import Database
from agentcrew_server.memory.summaries import SessionSummaries


def query(root, sql, parameters=()):
    with sqlite3.connect(root / "agentcrew.db") as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, parameters)]


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def start(root, token):
    log = (root / "service.log").open("ab")
    process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).with_name("observed_server.py")),
        "--data-dir", str(root), "--port", "18986", env={**os.environ, "AGENTCREW_TOKEN": token,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "backend"),
        "MEMORY_REQUEST_EVIDENCE": str(root / "requests.jsonl"),
        "MEMORY_JOB_STREAM_EVIDENCE": str(root / "streams.jsonl")},
        stdout=asyncio.subprocess.PIPE, stderr=log)
    ready = await asyncio.wait_for(process.stdout.readline(), 30)
    assert ready.startswith(b"AGENTCREW_READY "), "服务启动失败，请检查忽略目录日志"
    return process, log, json.loads(ready.split(b" ", 1)[1])["port"]


async def until(root, sql, parameters=(), *, seconds=180, interval=0.05):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        rows = query(root, sql, parameters)
        if rows:
            return rows
        await asyncio.sleep(interval)
    raise TimeoutError(f"实际状态等待超时：{sql}")


async def verify(seed, root, output):
    shutil.copytree(seed, root, ignore=shutil.ignore_patterns("requests.jsonl", "streams.jsonl", "service.log", "instance.lock"))
    token = uuid.uuid4().hex
    process, log, port = await start(root, token)
    exchanges, task_ids, cancellations, kills = [], [], [], []
    conversation_id = None
    error = None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}) as client:
            async def send(text):
                path = f"/api/conversations/{conversation_id}/instructions" if conversation_id else "/api/conversations"
                body = {"text": text, "client_request_id": uuid.uuid4().hex} if conversation_id else {
                    "instruction": text, "agent_id": "skill-validator", "client_request_id": uuid.uuid4().hex}
                response = await client.post(path, json=body)
                exchanges.append({"method": "POST", "path": path, "status": response.status_code,
                                  "request": body, "response": response.json(), "observed_at": datetime.now(timezone.utc).isoformat()})
                assert response.status_code in {201, 202}
                result = response.json()["data"]
                task_ids.append(result["task_run_id"])
                return result
            for index, color in enumerate(["蓝色", "绿色", "黄色", "紫色", "橙色", "白色"], start=1):
                result = await send(f"本次材料编号为 SUMMARY-{index}，标签颜色为{color}。请只用一句话确认这两项本次任务资料，直接文字回复，省略工具调用和记忆保存。")
                if conversation_id is None:
                    conversation_id = result["conversation"]["id"]
                terminal = await until(root, "SELECT status FROM task_runs WHERE id=? AND status IN ('completed','failed','cancelled')", (result["task_run_id"],))
                assert terminal[0]["status"] == "completed"
                job = await until(root, "SELECT id,status FROM memory_jobs WHERE task_run_id=? AND kind='summary' AND status IN ('completed','failed','cancelled','interrupted')", (result["task_run_id"],))
                assert job[0]["status"] == "completed", job
            snapshot_path = root / "conversations" / conversation_id / "memory-snapshot.json"
            snapshot_before = {"sha256": sha256(snapshot_path.read_bytes()), "mtime_ns": snapshot_path.stat().st_mtime_ns}
            baseline = query(root, "SELECT * FROM session_summaries WHERE conversation_id=? ORDER BY created_at,id", (conversation_id,))
            assert len(baseline) == 6 and all(1 <= len(row["text"]) <= 200 for row in baseline)
            db = Database(root / "agentcrew.db")
            try:
                recent = SessionSummaries(db).recent(conversation_id)
                assert [row["id"] for row in recent] == [row["id"] for row in baseline[:0:-1]]
            finally:
                db.close()
            cancellation_observed = False
            for index in range(3):
                result = await send("请用约1200字符说明材料归档的目录命名、来源登记、版本标识和结果核验方法。直接文字回答，省略工具调用。")
                await until(root, "SELECT id FROM task_runs WHERE id=? AND status='completed'", (result["task_run_id"],))
                deadline = time.monotonic() + 180
                matched = None
                while time.monotonic() < deadline:
                    active = query(root, "SELECT id,status FROM memory_jobs WHERE task_run_id=? AND status='running'", (result["task_run_id"],))
                    if active:
                        matched = next((line for line in lines(root / "streams.jsonl")
                                        if line["job_id"] == active[0]["id"] and line["event"] == "actual_text_delta"), None)
                        if matched:
                            break
                    if query(root, "SELECT id FROM memory_jobs WHERE task_run_id=? AND status IN ('completed','failed')", (result["task_run_id"],)):
                        break
                    await asyncio.sleep(0.001)
                if matched is None:
                    cancellations.append({"source_task_run_id": result["task_run_id"], "result": "真实模型流在观测前已经结束"})
                    continue
                started = time.monotonic()
                new = await send("新的前台指令：请直接确认已经收到，并回复当前摘要验收仍然继续。")
                finished = time.monotonic()
                state = query(root, "SELECT status,finished_at,error FROM memory_jobs WHERE id=?", (matched["job_id"],))[0]
                closed = [line for line in lines(root / "streams.jsonl") if line["job_id"] == matched["job_id"] and line["event"] == "response_closed"]
                cancellation = {"source_task_run_id": result["task_run_id"], "new_task_run_id": new["task_run_id"],
                                "job_id": matched["job_id"], "stream": matched, "request_seconds": finished-started,
                                "started_monotonic": started, "finished_monotonic": finished, "state": state, "closed": closed}
                cancellations.append(cancellation)
                await until(root, "SELECT id FROM task_runs WHERE id=? AND status='completed'", (new["task_run_id"],))
                await until(root, "SELECT id FROM memory_jobs WHERE task_run_id=? AND status IN ('completed','failed')", (new["task_run_id"],))
                if state["status"] == "cancelled":
                    assert finished-started <= 2 and closed and closed[-1]["monotonic"] <= finished
                    cancellation_observed = True
                    break
            assert cancellation_observed, "未观察到实际模型流取消，保留实际尝试证据"
            result = await send("请用约1500字符说明长期材料整理中的来源、目录、摘要、版本检查和人工核验步骤，直接文字回复。")
            await until(root, "SELECT id FROM task_runs WHERE id=? AND status='completed'", (result["task_run_id"],))
            active = await until(root, "SELECT id FROM memory_jobs WHERE task_run_id=? AND status='running'", (result["task_run_id"],), interval=0.001)
            source_job = active[0]["id"]
            deadline = time.monotonic() + 180
            crash_stream = None
            while time.monotonic() < deadline:
                crash_stream = next((line for line in lines(root / "streams.jsonl")
                                     if line["job_id"] == source_job and line["event"] == "actual_text_delta"), None)
                if crash_stream is not None:
                    break
                if query(root, "SELECT id FROM memory_jobs WHERE id=? AND status IN ('completed','failed')", (source_job,)):
                    raise AssertionError("SIGKILL 前实际摘要流已经结束，请检查真实记录")
                await asyncio.sleep(0.001)
            assert crash_stream is not None, "SIGKILL 前尚未观察到真实摘要流"
            process.send_signal(signal.SIGKILL)
            await asyncio.wait_for(process.wait(), 15)
            log.close()
            kills.append({"job_id": source_job, "task_run_id": result["task_run_id"], "exit_code": process.returncode,
                          "stream": crash_stream, "observed_at": datetime.now(timezone.utc).isoformat()})
        process, log, port = await start(root, token)
        await until(root, "SELECT id FROM memory_jobs WHERE id=? AND status IN ('interrupted','completed')", (source_job,))
        state = query(root, "SELECT status FROM memory_jobs WHERE id=?", (source_job,))[0]
        assert state["status"] == "interrupted", state
        assert query(root, "SELECT status FROM task_runs WHERE id=?", (result["task_run_id"],))[0]["status"] == "completed"
        process.terminate()
        assert await asyncio.wait_for(process.wait(), 15) == 0
        log.close()
        process, log, port = await start(root, token)
        assert query(root, "SELECT status FROM memory_jobs WHERE id=?", (source_job,))[0]["status"] == "interrupted"
        assert {"sha256": sha256(snapshot_path.read_bytes()), "mtime_ns": snapshot_path.stat().st_mtime_ns} == snapshot_before
    finally:
        error = sys.exception()
        if process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 15)
        log.close()
        sqls = ["SELECT id,status,instruction,finished_at FROM task_runs ORDER BY created_at,id",
                "SELECT * FROM session_summaries ORDER BY created_at,id", "SELECT * FROM memory_jobs ORDER BY created_at,id",
                "SELECT * FROM memory_job_calls ORDER BY job_id,ordinal",
                "SELECT global_seq,task_run_id,type,payload FROM run_events ORDER BY global_seq",
                "SELECT conversation_id,sha256,global_seq FROM memory_snapshots", "SELECT COUNT(*) AS count FROM steps",
                "SELECT COUNT(*) AS count FROM llm_calls", "SELECT seq,action,resource_id,hash FROM audit_log ORDER BY seq"]
        with sqlite3.connect(root / "agentcrew.db") as conn:
            check = verify_with_anchor(conn, root / "chain-head.txt")
        evidence = {"validated_at": datetime.now(timezone.utc).isoformat(), "conversation_id": conversation_id,
                    "task_run_ids": task_ids, "http": exchanges, "cancellations": cancellations, "sigkill": kills,
                    "requests": lines(root / "requests.jsonl"), "streams": lines(root / "streams.jsonl"),
                    "queries": [{"sql": sql, "rows": query(root, sql)} for sql in sqls],
                    "audit": {"ok": check.ok, "checked_count": check.checked_count, "reason": check.reason},
                    "service_exit": process.returncode, "error": f"{type(error).__name__}: {error}" if error else None,
                    "files": [{"path": str(p.relative_to(root)), "sha256": sha256(p.read_bytes()), "mtime_ns": p.stat().st_mtime_ns}
                        for p in sorted(root.rglob("*.md")) + sorted(root.rglob("*.meta.json")) + sorted(root.rglob("memory-snapshot.json"))]}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"tasks": len(task_ids), "cancellations": cancellations, "sigkill": kills,
                          "audit": evidence["audit"], "service_exit": process.returncode, "error": evidence["error"]}, ensure_ascii=False))
    assert check.ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-data-dir", type=Path, required=True, help="已经由真实 aux 初始化的验收数据目录")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.seed_data_dir.absolute(), args.data_dir.absolute(), args.output.absolute()))
