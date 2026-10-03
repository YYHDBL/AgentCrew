"""M1-09：真实 HTTP、main/aux、回合及工具阈值验收。"""

import argparse
import asyncio
import json
import shutil
import signal
import sys
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.db.database import Database
from verify_summaries import lines, query, start, until


async def verify(seed, root, output, *, reuse_conversation=False):
    shutil.copytree(seed, root, ignore=shutil.ignore_patterns("requests.jsonl", "streams.jsonl", "main-streams.jsonl", "service.log", "instance.lock"))
    token = uuid.uuid4().hex
    process, log, port = await start(root, token)
    records, conversation, tasks, error = [], None, [], None
    initial_turns = 0
    if reuse_conversation:
        state = query(root, "SELECT conversation_id,user_turns FROM memory_trigger_state ORDER BY user_turns DESC LIMIT 1")[0]
        conversation, initial_turns = state["conversation_id"], state["user_turns"]
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30,
                headers={"Authorization": f"Bearer {token}"}) as client:
            async def request(method, path, body=None):
                started = time.monotonic()
                response = await client.request(method, path, json=body)
                records.append({"method": method, "path": path, "status": response.status_code,
                                "body": body, "response": response.json(), "request_seconds": time.monotonic() - started,
                                "observed_at": datetime.now(timezone.utc).isoformat()})
                assert response.status_code in {200, 201, 202}, records[-1]
                return response.json()["data"]
            await request("PATCH", "/api/settings", {"memory": {"write_approval": False}})
            file = root / "workspaces/default/m1-09-source.txt"
            file.write_text("来源核验步骤：读取原始材料，确认编号，登记来源，核查文件哈希。\n" +
                "\n".join(f"编号{index}：本机材料核查验收记录。" for index in range(1, 21)) + "\n", encoding="utf-8")
            for index in range(1, 21):
                instruction = (f"第{index}次材料核查。请调用 read_file 读取工作空间 m1-09-source.txt，"
                    f"然后直接用一句话确认编号{index}和来源登记方法。我的长期汇报偏好为简洁中文，"
                    "本工作区材料保存在 m1-09-source.txt；员工核验材料时需要先确认编号再登记来源。"
                    "本次前台只完成读取与确认，记忆和技能交给任务完成后的后台检查。")
                result = await request("POST", f"/api/conversations/{conversation}/instructions" if conversation else "/api/conversations",
                    {"text": instruction, "client_request_id": uuid.uuid4().hex} if conversation else
                    {"instruction": instruction, "agent_id": "skill-validator", "client_request_id": uuid.uuid4().hex})
                if conversation is None:
                    conversation = result["conversation"]["id"]
                task = result["task_run_id"]
                tasks.append(task)
                deadline = time.monotonic() + 240
                terminal = []
                while time.monotonic() < deadline:
                    terminal = query(root, "SELECT status FROM task_runs WHERE id=? AND status IN ('completed','failed','cancelled')", (task,))
                    if terminal:
                        break
                    approvals = await request("GET", f"/api/task-runs/{task}/approvals?status=pending")
                    for approval in approvals:
                        await request("POST", f"/api/tool-approvals/{approval['call_id']}", {
                            "decision": "reject_once", "input_hash": approval["input_hash"]})
                    questions = await request("GET", f"/api/conversations/{conversation}/questions")
                    for question in questions:
                        await request("POST", f"/api/questions/{question['request_id']}/answer", {"answer":
                            "编号来自当前用户指令；请完成本次编号和原文来源登记方法的核查。"})
                    await asyncio.sleep(0.2)
                assert terminal, "前台任务未在期限内完成"
                assert terminal[0]["status"] == "completed"
                await until(root, "SELECT id FROM memory_jobs WHERE task_run_id=? AND kind='summary' AND status IN ('completed','failed','cancelled','interrupted')", (task,))
                if (initial_turns + index) % 10 == 0:
                    review = await until(root, "SELECT id,status FROM memory_jobs WHERE conversation_id=? AND kind='memory_review' AND trigger_key=?",
                        (conversation, f"{conversation}:{initial_turns + index}"))
                    await until(root, "SELECT id FROM memory_jobs WHERE id=? AND status IN ('completed','failed','cancelled','interrupted')", (review[0]["id"],), seconds=240)
                    await request("GET", f"/api/memory/jobs/{review[0]['id']}")
                print(json.dumps({"completed_task": index, "task_run_id": task}, ensure_ascii=False), flush=True)
            state = query(root, "SELECT * FROM memory_trigger_state WHERE conversation_id=?", (conversation,))[0]
            assert state["user_turns"] == initial_turns + 20 and state["memory_watermark"] == (initial_turns + 20) // 10 * 10
            assert state["tool_iterations"] >= 15
            skill_jobs = await until(root, "SELECT id,status FROM memory_jobs WHERE conversation_id=? AND kind='skill_review'", (conversation,))
            for job in skill_jobs:
                await until(root, "SELECT id FROM memory_jobs WHERE id=? AND status IN ('completed','failed','cancelled','interrupted')", (job["id"],), seconds=240)
                await request("GET", f"/api/memory/jobs/{job['id']}")
            reviews = query(root, "SELECT id,kind,status,report,error FROM memory_jobs WHERE conversation_id=? AND kind<>'summary' "
                "AND created_at>=(SELECT created_at FROM task_runs WHERE id=?)", (conversation, tasks[0]))
            assert all(job["status"] in {"completed", "cancelled"} for job in reviews), reviews
            assert sum(job["kind"] == "memory_review" and job["status"] == "completed" for job in reviews) == 2
    finally:
        exc = sys.exception()
        if exc is not None:
            error = f"{type(exc).__name__}: {exc}"
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
        exit_code = await asyncio.wait_for(process.wait(), 15)
        log.close()
        db = Database(root / "agentcrew.db")
        audit = verify_with_anchor(db.read_conn, root / "chain-head.txt")
        db.close()
        sqls = ["SELECT * FROM memory_trigger_state", "SELECT * FROM memory_counted_events ORDER BY global_seq",
            "SELECT * FROM memory_jobs ORDER BY created_at,id", "SELECT * FROM memory_job_calls ORDER BY job_id,ordinal",
            "SELECT * FROM memory_job_approvals", "SELECT * FROM memory_ledger ORDER BY id",
            "SELECT global_seq,type,payload FROM run_events WHERE type LIKE 'memory.%' OR type='skill.patched' ORDER BY global_seq"]
        report = {"observed_at": datetime.now(timezone.utc).isoformat(), "conversation_id": conversation,
            "task_run_ids": tasks, "http": records, "error": error, "service_exit": exit_code,
            "queries": [{"sql": sql, "rows": query(root, sql)} for sql in sqls],
            "audit": {"ok": audit.ok, "checked_count": audit.checked_count}, "requests": lines(root / "requests.jsonl")}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-data-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-conversation", action="store_true")
    args = parser.parse_args()
    asyncio.run(verify(args.seed_data_dir.absolute(), args.data_dir.absolute(), args.output.absolute(), reuse_conversation=args.reuse_conversation))
