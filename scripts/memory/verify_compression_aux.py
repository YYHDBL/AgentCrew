"""M1-08：以已完成真实会话的原文调用当前 aux 结构化压缩。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agentcrew_core.loop import user_text_message
from agentcrew_server.memory.checkpoints import ContextCheckpoints
from agentcrew_server.providers import bind_slot

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "m1-summaries-validation-04"
OUTPUT = ROOT / "data" / "m1-intermediate" / "08-aux-smoke.json"


async def main():
    config = json.loads((DATA / "config.json").read_text(encoding="utf-8"))
    slot = bind_slot(config["models"]["aux"])
    with sqlite3.connect(DATA / "agentcrew.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT conversation_id FROM session_summaries GROUP BY conversation_id "
                           "ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
        conversation_id = row[0]
        tasks = conn.execute("SELECT t.id,t.instruction,m.content FROM task_runs t "
            "JOIN messages m ON m.task_run_id=t.id AND m.role='assistant' "
            "WHERE t.conversation_id=? AND t.status='completed' ORDER BY t.created_at LIMIT 6",
            (conversation_id,)).fetchall()
    messages = []
    for task in tasks:
        messages.append(user_text_message(task["instruction"]))
        messages.append({"role": "assistant", "content": [{"type": "text", "text": task["content"]}]})
    recorded = []

    async def sink(kind, payload):
        recorded.append({"type": kind, "payload": payload})

    async with httpx.AsyncClient(timeout=180) as client:
        summary, usage = await ContextCheckpoints(None, None, DATA)._summarize(
            conversation_id=conversation_id, old_messages=messages,
            previous=None, aux_slot=slot, client=client,
            sink=sink, on_progress=lambda: None)
    output = {"observed_at": datetime.now(timezone.utc).isoformat(),
              "conversation_id": conversation_id, "task_run_ids": [row["id"] for row in tasks],
              "model": slot.model, "summary": summary, "usage": usage,
              "budget_events": recorded}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"model": slot.model, "summary_fields": list(summary),
                      "usage": usage, "budget_events": len(recorded)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
