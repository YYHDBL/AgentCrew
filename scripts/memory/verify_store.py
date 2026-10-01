"""M1-02：真实 HTTP 服务、当前模型及三库任务验收。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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

from agentcrew_server.db.audit import verify_with_anchor


async def verify(config_dir: Path, root: Path, output: Path):
    root.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(config_dir / "config.json", root / "config.json")
    token = uuid.uuid4().hex
    log = (root / "service.log").open("wb")
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "agentcrew_server", "--data-dir", str(root), "--port", "18970",
        env={**os.environ, "AGENTCREW_TOKEN": token}, stdout=asyncio.subprocess.PIPE, stderr=log)
    try:
        ready = await asyncio.wait_for(process.stdout.readline(), 30)
        assert ready.startswith(b"AGENTCREW_READY "), "服务启动失败，查看忽略目录中的日志"
        port = json.loads(ready.split(b" ", 1)[1])["port"]
        db = sqlite3.connect(root / "agentcrew.db")
        db.row_factory = sqlite3.Row
        results = []
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=30) as client:
            for target, text in (("user", "用户喜欢以简洁中文汇报"), ("workspace", "项目资料使用项目编号分类保存"), ("soul", "处理资料时先确认用户提供的列顺序")):
                instruction = (f"这是一次持久化记忆操作。请实际调用 memory_write，先以 target={target}、action=read 获取当前修订，"
                    f"再以 action=add、读取到的 expected_revision、text={text}、basis=用户在本任务明确要求保存写入该库。"
                    "这条事实由用户明确提供。完成后只汇报工具实际结果。")
                response = await client.post("/api/conversations", json={"instruction": instruction, "agent_id": "memory-validator", "client_request_id": uuid.uuid4().hex})
                assert response.status_code == 201, response.text
                created = response.json()["data"]
                task_id = created["task_run_id"]
                conv_id = created["conversation"]["id"]
                deadline = asyncio.get_running_loop().time() + 180
                while True:
                    task = db.execute("SELECT status FROM task_runs WHERE id=?", (task_id,)).fetchone()
                    if task[0] in {"completed", "failed", "cancelled", "interrupted"}:
                        break
                    assert asyncio.get_running_loop().time() < deadline, f"任务未完成：{task_id}"
                    await asyncio.sleep(0.25)
                event_rows = db.execute("SELECT global_seq,type,payload FROM run_events WHERE conversation_id=? ORDER BY global_seq", (conv_id,)).fetchall()
                events = [{"global_seq": e[0], "type": e[1], "payload": json.loads(e[2])} for e in event_rows]
                memory_changes = db.execute("SELECT change_id,store_type,store_id,status,result FROM memory_changes WHERE json_extract(plan,'$.identity.task_run_id')=?", (task_id,)).fetchall()
                results.append({"target": target, "conversation_id": conv_id, "task_run_id": task_id, "status": task[0],
                    "events": events, "changes": [dict(row) for row in memory_changes]})
            queries = []
            for sql in ("SELECT store_type,store_id,revision,text_sha256,metadata_sha256 FROM memory_stores ORDER BY store_type,store_id",
                        "SELECT id,change_id,store_type,store_id,action,revision,audit_seq,global_seq,source,basis FROM memory_ledger ORDER BY global_seq",
                        "SELECT tool_name,status,COUNT(*) AS count FROM tool_calls GROUP BY tool_name,status",
                        "SELECT seq,action,resource_id,detail FROM audit_log ORDER BY seq"):
                queries.append({"sql": sql, "rows": [dict(row) for row in db.execute(sql)]})
            files = [{"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mtime_ns": path.stat().st_mtime_ns}
                for path in sorted(root.rglob("*.md")) + sorted(root.rglob("*.meta.json"))]
        db.close()
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), 15)
        log.close()
    with sqlite3.connect(root / "agentcrew.db") as conn:
        check = verify_with_anchor(conn, root / "chain-head.txt")
    evidence = {"validated_at": datetime.now(timezone.utc).isoformat(), "tasks": results, "queries": queries, "files": files,
        "audit": {"ok": check.ok, "checked_count": check.checked_count, "reason": check.reason}, "service_exit": process.returncode}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tasks": [{"target": t["target"], "task_run_id": t["task_run_id"], "status": t["status"], "changes": len(t["changes"])} for t in results], "audit": evidence["audit"], "service_exit": process.returncode}, ensure_ascii=False))
    assert all(t["status"] == "completed" for t in results)
    assert check.ok and process.returncode == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("data"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.config_dir.absolute(), args.data_dir.absolute(), args.output.absolute()))
