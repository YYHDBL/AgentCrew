"""实际提炼模型流返回文本后由新前台指令取消；保留已提交变更。"""

import argparse
import asyncio
import json
import shutil
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from verify_summaries import lines, query, start, until


async def verify(seed, root, output):
    shutil.copytree(seed, root, ignore=shutil.ignore_patterns("requests.jsonl", "streams.jsonl", "main-streams.jsonl", "service.log", "instance.lock"))
    state = query(root, "SELECT conversation_id,user_turns FROM memory_trigger_state ORDER BY user_turns DESC LIMIT 1")[0]
    conversation, target = state["conversation_id"], (state["user_turns"] // 10 + 1) * 10
    token = uuid.uuid4().hex
    process, log, port = await start(root, token)
    error, observations, measured = None, [], None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30,
                headers={"Authorization": f"Bearer {token}"}) as client:
            async def send(text):
                response = await client.post(f"/api/conversations/{conversation}/instructions", json={
                    "text": text, "client_request_id": uuid.uuid4().hex})
                assert response.status_code == 202
                return response.json()["data"]["task_run_id"]
            for attempt in range(3):
                marker = uuid.uuid4().hex[:12]
                while query(root, "SELECT user_turns FROM memory_trigger_state WHERE conversation_id=?", (conversation,))[0]["user_turns"] < target:
                    task_id = await send(f"长期偏好：每份汇报末尾保留来源标识 {marker}。前台只用一句话确认收到，记忆交给任务结束后的后台检查。")
                    assert (await until(root, "SELECT status FROM task_runs WHERE id=? AND status IN ('completed','failed')", (task_id,)))[0]["status"] == "completed"
                    await until(root, "SELECT id FROM memory_jobs WHERE task_run_id=? AND kind='summary' AND status IN ('completed','failed','cancelled','interrupted')", (task_id,))
                job = (await until(root, "SELECT id,status FROM memory_jobs WHERE kind='memory_review' AND trigger_key=?", (f"{conversation}:{target}",)))[0]
                deadline, stream = time.monotonic() + 240, None
                while time.monotonic() < deadline:
                    stream = next((line for line in lines(root / "streams.jsonl") if line["job_id"] == job["id"] and line["event"] == "actual_text_delta"), None)
                    if stream:
                        break
                    if query(root, "SELECT id FROM memory_jobs WHERE id=? AND status IN ('completed','failed','cancelled','interrupted')", (job["id"],)):
                        break
                    await asyncio.sleep(0.001)
                if stream is None:
                    observations.append({"job_id": job["id"], "result": "真实作业没有可观测文本增量", "state": query(root, "SELECT status,error FROM memory_jobs WHERE id=?", (job["id"],))})
                    target += 10
                    continue
                writes_before = query(root, "SELECT change_id,status FROM memory_changes WHERE json_extract(plan,'$.identity.job_id')=?", (job["id"],))
                started = time.monotonic()
                new_task = await send("新的前台任务：请直接确认收到新指令，后台提炼停止继续执行。")
                duration = time.monotonic() - started
                actual = query(root, "SELECT status,error FROM memory_jobs WHERE id=?", (job["id"],))[0]
                await until(root, "SELECT id FROM task_runs WHERE id=? AND status='completed'", (new_task,))
                if actual["status"] == "completed":
                    observations.append({"job_id": job["id"], "result": "模型流在新请求接收前已经完成", "stream": stream})
                    target += 10
                    continue
                assert actual["status"] == "cancelled" and duration < 2
                closed = [line for line in lines(root / "streams.jsonl") if line["job_id"] == job["id"] and line["event"] == "response_closed"]
                assert closed and max(line["monotonic"] for line in closed) <= started + duration
                writes_after = query(root, "SELECT change_id,status FROM memory_changes WHERE json_extract(plan,'$.identity.job_id')=?", (job["id"],))
                assert all(row["status"] == "committed" for row in writes_after)
                measured = {"job_id": job["id"], "stream": stream, "response_closed": closed, "request_seconds": duration,
                    "new_task_run_id": new_task, "state": actual, "changes_before": writes_before, "changes_after": writes_after,
                    "observed_at": datetime.now(timezone.utc).isoformat()}
                break
            assert measured is not None, "实际模型流取消未形成完整证据；已记录每次真实结果"
    finally:
        exc = sys.exception()
        if exc is not None:
            error = f"{type(exc).__name__}: {exc}"
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
        exit_code = await asyncio.wait_for(process.wait(), 15)
        log.close()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"conversation_id": conversation, "measured": measured, "observations": observations,
            "error": error, "service_exit": exit_code}, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-data-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.seed_data_dir.absolute(), args.data_dir.absolute(), args.output.absolute()))
