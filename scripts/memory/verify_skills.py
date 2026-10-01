"""M1-05：真实 HTTP 前台任务、模型读取修改与完整账本验收。"""

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_core.memory import sha256
from agentcrew_server.db.audit import verify_with_anchor
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.memory.skills import MemorySkills
from agentcrew_server.memory.store import MemoryIdentity, MemoryStore
from agentcrew_server.sessions import DEFAULT_WORKSPACE_ID


async def seed(root):
    db = Database(root / "agentcrew.db")
    run_migrations(db.write_conn, root / "backups")
    channel = WriteChannel(db.write_conn)
    skills = MemorySkills(MemoryStore(db, EventStore(channel), root))
    try:
        identity = MemoryIdentity(DEFAULT_WORKSPACE_ID, "skill-validator")
        existing = skills._find(identity, "材料来源核验")
        revision = db.read_conn.execute("SELECT revision FROM memory_stores WHERE store_type='skill' AND store_id=?", (existing["id"],)).fetchone()[0] if existing else 0
        result = await skills.change(identity, "材料来源核验", action="edit" if existing else "create",
            change_id=uuid.uuid4().hex, expected_revision=revision, basis="所有者提供可重复执行的材料核验流程",
            description="核查来源并检查材料完整性", text="# 材料来源核验\n\n先确认来源。\n检查资料完整性。\n\n机理：来源可追溯才能核实结果。",
            files={"references/checklist.md": "来源检查清单\n确认提供者与原始文件。"})
        assert "error" not in result, result
        return result
    finally:
        channel.close()
        db.close()


async def verify(config_dir, root, output, *, reuse=False):
    root.mkdir(parents=True, exist_ok=reuse)
    shutil.copyfile(config_dir / "config.json", root / "config.json")
    seeded = await seed(root)
    token = uuid.uuid4().hex
    log = (root / "service.log").open("wb")
    process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).with_name("observed_server.py")),
        "--data-dir", str(root), "--port", "18982", env={**os.environ, "AGENTCREW_TOKEN": token,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "backend"), "MEMORY_REQUEST_EVIDENCE": str(root / "requests.jsonl")},
        stdout=asyncio.subprocess.PIPE, stderr=log)
    tasks, exchanges = [], []
    try:
        ready = await asyncio.wait_for(process.stdout.readline(), 30)
        assert ready.startswith(b"AGENTCREW_READY "), "服务启动失败，请检查忽略目录中的日志"
        port = json.loads(ready.split(b" ", 1)[1])["port"]
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}) as client:
            instructions = [
                "请实际执行以下受控修改：先 skill_view 读取‘材料来源核验’全文，再读取它的 references/checklist.md。随后使用 skill_patch 的 patch 将唯一的‘先确认来源。’替换为‘先确认来源及提供者。’，description 改为‘核查来源提供者并检查完整性’，并将 references/checklist.md 更新为‘来源检查清单\\n确认提供者、原始文件与材料完整性。’。提供工具实际返回的 expected_revision 和真实依据。最后重新读取正文确认。流程只包含可泛化步骤和机理。",
                "把这个流程存成技能，名称‘材料目录核验’，描述‘整理目录并核对资料是否齐全’：先获取材料清单，再逐项核对目录中的文件，最后登记缺少的材料；机理是清单与目录逐项对应才能发现遗漏。当前任务只要求保存这份明确提供的流程。先读取创建目标状态，随后用 skill_patch create 保存流程，正文省略日期和单次任务编号。仅调用 skill_view、skill_patch。",
            ]
            for instruction in instructions:
                response = await client.post("/api/conversations", json={"instruction": instruction, "agent_id": "skill-validator", "client_request_id": uuid.uuid4().hex})
                exchanges.append({"method": "POST", "path": "/api/conversations", "status": response.status_code, "response": response.json()})
                assert response.status_code == 201
                created = response.json()["data"]
                task_id = created["task_run_id"]
                deadline = asyncio.get_running_loop().time() + 240
                decided = set()
                while True:
                    approvals = await client.get(f"/api/task-runs/{task_id}/approvals")
                    assert approvals.status_code == 200
                    for approval in approvals.json()["data"]:
                        call_id = approval["call_id"]
                        if call_id in decided:
                            continue
                        decision = await client.post(f"/api/tool-approvals/{call_id}", json={"decision": "reject_once", "input_hash": approval["input_hash"]})
                        exchanges.append({"method": "POST", "path": f"/api/tool-approvals/{call_id}", "status": decision.status_code, "response": decision.json()})
                        assert decision.status_code == 200
                        decided.add(call_id)
                    with sqlite3.connect(root / "agentcrew.db") as conn:
                        status = conn.execute("SELECT status FROM task_runs WHERE id=?", (task_id,)).fetchone()[0]
                        if status in {"completed", "failed", "cancelled", "interrupted"} or asyncio.get_running_loop().time() >= deadline:
                            events = [{"global_seq": r[0], "type": r[1], "payload": json.loads(r[2])} for r in conn.execute("SELECT global_seq,type,payload FROM run_events WHERE task_run_id=? ORDER BY global_seq", (task_id,))]
                            tasks.append({"task_run_id": task_id, "conversation_id": created["conversation"]["id"], "status": status, "events": events})
                            break
                    await asyncio.sleep(0.1)
                if status != "completed":
                    break
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), 15)
        log.close()
    with sqlite3.connect(root / "agentcrew.db") as conn:
        conn.row_factory = sqlite3.Row
        sqls = ["SELECT * FROM memory_skills ORDER BY name", "SELECT * FROM skill_reads ORDER BY created_at",
            "SELECT * FROM memory_usage ORDER BY entry_id", "SELECT * FROM memory_usage_hits ORDER BY created_at",
            "SELECT id,change_id,action,revision,before_text,after_text,before_metadata,after_metadata,before_files,after_files,source,basis,audit_seq,global_seq FROM memory_ledger ORDER BY id",
            "SELECT global_seq,type,payload FROM run_events WHERE type='skill.patched' ORDER BY global_seq",
            "SELECT conversation_id,body,sha256,global_seq FROM memory_snapshots ORDER BY created_at",
            "SELECT id,model,prompt_tokens,completion_tokens FROM llm_calls ORDER BY id",
            "SELECT id,model,status,result,error FROM memory_soul_generations ORDER BY created_at"]
        queries = [{"sql": sql, "rows": [dict(row) for row in conn.execute(sql)]} for sql in sqls]
        check = verify_with_anchor(conn, root / "chain-head.txt")
    evidence = {"validated_at": datetime.now(timezone.utc).isoformat(), "seeded": seeded, "tasks": tasks, "http": exchanges,
        "requests": [json.loads(line) for line in (root / "requests.jsonl").read_text().splitlines()], "queries": queries,
        "audit": {"ok": check.ok, "checked_count": check.checked_count, "reason": check.reason}, "service_exit": process.returncode,
        "files": [{"path": str(p.relative_to(root)), "sha256": sha256(p.read_bytes()), "mtime_ns": p.stat().st_mtime_ns}
            for p in sorted(root.rglob("*.md")) + sorted(root.rglob("*.meta.json")) + sorted(root.rglob("memory-snapshot.json"))]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tasks": [{"task_run_id": t["task_run_id"], "status": t["status"]} for t in tasks], "audit": evidence["audit"], "requests": len(evidence["requests"])}, ensure_ascii=False))
    assert len(tasks) == 2 and all(t["status"] == "completed" for t in tasks), "实际任务未完成，请检查证据"
    patched = [row for row in queries[4]["rows"] if json.loads(row["source"]).get("actor_type") == "agent"]
    assert any("先确认来源及提供者。" in row["after_text"] for row in patched), "实际模型未完成受控修改，请检查证据"
    assert len(queries[0]["rows"]) == 2 and len(queries[1]["rows"]) >= 2
    assert len(queries[2]["rows"]) >= 1 and check.ok and process.returncode == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse", action="store_true", help="继续使用已经由真实 aux 初始化的员工及既有账本")
    args = parser.parse_args()
    asyncio.run(verify(args.config_dir.absolute(), args.data_dir.absolute(), args.output.absolute(), reuse=args.reuse))
