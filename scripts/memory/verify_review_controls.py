"""真实后台审批拒绝、前台取消及 SIGKILL 后旧审批失效。"""

import argparse
import asyncio
import json
import hashlib
import shutil
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from verify_summaries import query, start, until
from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.db.database import Database


def files(root):
    paths = sorted(set(root.rglob("*.md")) | set(root.rglob("*.meta.json")) | set(root.rglob("memory-snapshot.json")))
    return [{"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "mtime_ns": path.stat().st_mtime_ns} for path in paths]


async def verify(seed, root, output):
    shutil.copytree(seed, root, ignore=shutil.ignore_patterns("requests.jsonl", "streams.jsonl", "main-streams.jsonl", "service.log", "instance.lock"))
    conversation = query(root, "SELECT conversation_id FROM memory_trigger_state ORDER BY user_turns DESC LIMIT 1")[0]["conversation_id"]
    token = uuid.uuid4().hex
    process, log, port = await start(root, token)
    records, controls, error = [], [], None
    before_files = files(root)
    client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30, headers={"Authorization": f"Bearer {token}"})
    try:
        async def request(method, path, body=None, expected=(200, 202)):
            response = await client.request(method, path, json=body)
            records.append({"method": method, "path": path, "body": body, "status": response.status_code,
                "response": response.json(), "observed_at": datetime.now(timezone.utc).isoformat()})
            assert response.status_code in expected, records[-1]
            return response.json()
        async def send(text):
            result = await request("POST", f"/api/conversations/{conversation}/instructions", {
                "text": text, "client_request_id": uuid.uuid4().hex})
            task_id = result["data"]["task_run_id"]
            row = await until(root, "SELECT status FROM task_runs WHERE id=? AND status IN ('completed','failed','cancelled')", (task_id,))
            assert row[0]["status"] == "completed"
            await until(root, "SELECT id FROM memory_jobs WHERE task_run_id=? AND kind='summary' AND status IN ('completed','failed','cancelled','interrupted')", (task_id,))
            return task_id
        await request("PATCH", "/api/settings", {"memory": {"write_approval": True}})
        for target, mode in ((30, "reject"), (40, "foreground_cancel"), (50, "sigkill")):
            marker = uuid.uuid4().hex[:12]
            while query(root, "SELECT user_turns FROM memory_trigger_state WHERE conversation_id=?", (conversation,))[0]["user_turns"] < target:
                task_id = await send(f"长期偏好更新：我的汇报标题固定使用核验标记 {marker}，偏好正文使用简洁中文。"
                    "这项信息需要由任务结束后的后台保存到 USER。本次前台只用一句话确认已经收到这一偏好，直接文字回复。")
            job = (await until(root, "SELECT id,status FROM memory_jobs WHERE kind='memory_review' AND trigger_key=?", (f"{conversation}:{target}",)))[0]
            deadline = time.monotonic() + 240
            pending = []
            while time.monotonic() < deadline:
                pending = query(root, "SELECT * FROM memory_job_approvals WHERE job_id=? AND status='pending'", (job["id"],))
                if pending:
                    break
                terminal = query(root, "SELECT status,error,report FROM memory_jobs WHERE id=? AND status IN ('completed','failed','cancelled','interrupted')", (job["id"],))
                if terminal:
                    controls.append({"mode": mode, "job_id": job["id"], "actual_model_outcome": terminal})
                    break
                await asyncio.sleep(0.01)
            assert pending, f"真实模型没有发起 {mode} 验收需要的后台写入审批；实际结果已记录"
            approval = pending[0]
            path = f"/api/memory/jobs/{job['id']}/approvals/{approval['id']}"
            ledger_before = query(root, "SELECT COUNT(*) AS count FROM memory_ledger WHERE json_extract(source,'$.job_id')=?", (job["id"],))[0]["count"]
            if mode == "reject":
                rejected = []
                while time.monotonic() < deadline:
                    for item in query(root, "SELECT * FROM memory_job_approvals WHERE job_id=? AND status='pending'", (job["id"],)):
                        decision_path = f"/api/memory/jobs/{job['id']}/approvals/{item['id']}"
                        body = {"decision": "reject_once", "input_hash": item["input_hash"]}
                        await request("POST", decision_path, body)
                        await request("POST", decision_path, body)
                        rejected.append(item["id"])
                    terminal = query(root, "SELECT status,error FROM memory_jobs WHERE id=? AND status IN ('completed','failed','cancelled','interrupted')", (job["id"],))
                    if terminal:
                        break
                    await asyncio.sleep(0.02)
                assert terminal and terminal[0]["status"] == "completed"
                controls.append({"mode": mode, "job_id": job["id"], "rejected_approvals": rejected, "terminal": terminal})
            elif mode == "foreground_cancel":
                started = time.monotonic()
                response = await request("POST", f"/api/conversations/{conversation}/instructions", {
                    "text": "新的前台指令：请直接确认已收到新的任务。", "client_request_id": uuid.uuid4().hex})
                duration = time.monotonic() - started
                assert duration < 2
                state = query(root, "SELECT status FROM memory_jobs WHERE id=?", (job["id"],))[0]
                assert state["status"] == "cancelled"
                await request("POST", path, {"decision": "allow_once", "input_hash": approval["input_hash"]}, expected=(409,))
                new_task = response["data"]["task_run_id"]
                await until(root, "SELECT id FROM task_runs WHERE id=? AND status='completed'", (new_task,))
                await until(root, "SELECT id FROM memory_jobs WHERE task_run_id=? AND kind='summary' AND status IN ('completed','failed','cancelled','interrupted')", (new_task,))
                controls.append({"mode": mode, "job_id": job["id"], "approval_id": approval["id"],
                    "request_seconds": duration, "new_task_run_id": new_task, "terminal": state})
            else:
                process.send_signal(signal.SIGKILL)
                assert await process.wait() == -9
                log.close()
                await client.aclose()
                for restart in range(2):
                    process, log, port = await start(root, token)
                    client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30,
                        headers={"Authorization": f"Bearer {token}"})
                    await request("POST", path, {"decision": "allow_once", "input_hash": approval["input_hash"]}, expected=(409,))
                    assert query(root, "SELECT status FROM memory_jobs WHERE id=?", (job["id"],))[0]["status"] == "interrupted"
                    if restart == 0:
                        process.send_signal(signal.SIGTERM)
                        assert await process.wait() == 0
                        log.close()
                        await client.aclose()
                controls.append({"mode": mode, "job_id": job["id"], "approval_id": approval["id"], "sigkill_exit": -9,
                    "restart_count": 2, "terminal": "interrupted"})
            ledger_after = query(root, "SELECT COUNT(*) AS count FROM memory_ledger WHERE json_extract(source,'$.job_id')=?", (job["id"],))[0]["count"]
            assert ledger_before == ledger_after == 0
            controls[-1].update(ledger_before=ledger_before, ledger_after=ledger_after,
                source_task_run_id=task_id, source_task_status=query(root, "SELECT status FROM task_runs WHERE id=?", (task_id,))[0]["status"])
            print(json.dumps(controls[-1], ensure_ascii=False), flush=True)
    finally:
        exc = sys.exception()
        if exc is not None:
            error = f"{type(exc).__name__}: {exc}"
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
        exit_code = await asyncio.wait_for(process.wait(), 15)
        log.close()
        await client.aclose()
        output.parent.mkdir(parents=True, exist_ok=True)
        after_files = files(root)
        db = Database(root / "agentcrew.db")
        audit = verify_with_anchor(db.read_conn, root / "chain-head.txt")
        db.close()
        output.write_text(json.dumps({"observed_at": datetime.now(timezone.utc).isoformat(), "conversation_id": conversation,
            "controls": controls, "http": records, "error": error, "service_exit": exit_code,
            "files_before": before_files, "files_after": after_files, "files_unchanged": before_files == after_files,
            "audit": {"ok": audit.ok, "checked_count": audit.checked_count}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-data-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.seed_data_dir.absolute(), args.data_dir.absolute(), args.output.absolute()))
