"""M1-08：真实 SQLite 事件与检查点、保护边界及恢复完整性。"""

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from agentcrew_core.events import RunEventType
from agentcrew_core.memory.compression import (protected_messages, protected_start,
    summarizable_messages, with_summary)
from agentcrew_core.tools import ToolInvocation, ToolScheduler, WorkContext, build_default_registry
from agentcrew_server.db.database import Database
from agentcrew_server.db.event_store import EventStore
from agentcrew_server.db.migrations import run_migrations
from agentcrew_server.db.write_channel import WriteChannel
from agentcrew_server.config import load_config
from agentcrew_server.memory.checkpoints import CheckpointCorrupt, ContextCheckpoints
from agentcrew_server.recovery import RecoveryService
from agentcrew_server.sessions import SessionService
from agentcrew_server.settings import SettingsService
from agentcrew_server.tool_outputs import ArtifactIntegrityError, ToolOutputStore


SUMMARY = {"historical_tasks": "材料整理", "goals": "核对来源",
           "constraints_preferences": "保留原文", "completed_actions": "read_file 已完成",
           "current_status": "继续当前任务"}


def message(role, *blocks):
    return {"role": role, "content": list(blocks)}


def test_protection_extends_to_latest_instruction_and_complete_tool_pair():
    old = [message("user" if index == 0 else "assistant",
                   {"type": "text", "text": f"旧任务 {index}"})
           for index in range(10)]
    assistant = message("assistant", {"type": "tool_use", "id": "call-a",
                                      "name": "read_file", "input": {"path": "资料.txt"}})
    result = message("user", {"type": "tool_result", "tool_use_id": "call-a",
                              "content": "实际文件结果"})
    history = old + [assistant, result] + [message(
        "user" if index == 8 else "assistant", {"type": "text", "text": "当前任务" if index == 8 else "推进"})
        for index in range(19)]
    assert protected_start(history) == 10
    guarded = old + [message("user", {"type": "text", "text": "当前任务指令"})]
    guarded[10]["_task_instruction_id"] = "task-current"
    guarded += [message("assistant", {"type": "text", "text": "继续"}) for _ in range(25)]
    guarded.append(message("user", {"type": "text", "text": "工具重复纠偏"}))
    assert protected_start(guarded) == len(guarded) - 20
    protected = protected_messages(guarded, protected_start(guarded), "task-current")
    assert protected[0]["content"][0]["text"] == "当前任务指令"
    assert protected[-1]["content"][0]["text"] == "工具重复纠偏"
    assert all(row.get("_task_instruction_id") != "task-current" for row in
               summarizable_messages(guarded, protected_start(guarded), "task-current"))
    compressed = with_summary(SUMMARY, history[10:])
    assert json.loads(compressed[0]["content"][0]["text"].split("\n", 1)[1]) == SUMMARY
    assert compressed[1:] == history[10:]


def test_checkpoint_event_transaction_immutability_and_damage(tmp_path):
    db = Database(tmp_path / "agentcrew.db")
    run_migrations(db.write_conn, tmp_path / "backups")
    channel = WriteChannel(db.write_conn)
    events = EventStore(channel)
    checkpoints = ContextCheckpoints(db, events, tmp_path)
    db.write_conn.execute("INSERT INTO conversations(id,workspace_id,agent_id,created_at,updated_at) "
                          "VALUES('conv-08','ws','agent','2026-10-03','2026-10-03')")
    db.write_conn.execute("INSERT INTO task_runs(id,conversation_id,instruction,status,created_at,updated_at) "
                          "VALUES('task-08','conv-08','核对资料','running','2026-10-03','2026-10-03')")
    body = json.dumps({"conversation_id": "conv-08", "snapshot_id": "snapshot-08",
                       "scope": {"owner_id": "owner", "workspace_id": "ws", "agent_id": "agent"}})
    digest = hashlib.sha256(body.encode()).hexdigest()
    snapshot_path = tmp_path / "conversations" / "conv-08" / "memory-snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(body, encoding="utf-8")

    async def scenario():
        event = await events.append(task_run_id="task-08", conversation_id="conv-08",
            type=RunEventType.MEMORY_SNAPSHOT_CREATED,
            payload={"snapshot_id": "snapshot-08", "snapshot_sha256": digest})
        db.write_conn.execute("INSERT INTO memory_snapshots "
            "(conversation_id,snapshot_id,owner_id,workspace_id,agent_id,body,sha256,status,global_seq,created_at) "
            "VALUES('conv-08','snapshot-08','owner','ws','agent',?,?,'committed',?,'2026-10-03')",
            (body, digest, event.global_seq))
        await events.append(task_run_id="task-08", conversation_id="conv-08",
                            type=RunEventType.CONTEXT_BUDGET_CHECKED, payload={"model": "真实预算"})
        saved = await checkpoints.save(conversation_id="conv-08", task_run_id="task-08",
            attempt_no=1, snapshot_id="snapshot-08", summary=SUMMARY,
            messages=with_summary(SUMMARY, [message("user", {"type": "text", "text": "当前任务"})]),
            aux_model="deepseek-v4.1-flash", aux_usage={"input_tokens": 31, "output_tokens": 20},
            before_tokens=490_000, after_tokens=260, summarized_messages=25)
        first = checkpoints.latest("conv-08")
        assert first["id"] == saved["id"] and first["summary"] == SUMMARY
        assert first["event_global_seq"] == saved["event_global_seq"]
        duplicate = await checkpoints.save(conversation_id="conv-08", task_run_id="task-08",
            attempt_no=1, snapshot_id="snapshot-08", summary=SUMMARY,
            messages=first["messages"], source_global_seq=first["source_global_seq"],
            aux_model="deepseek-v4.1-flash", aux_usage={"input_tokens": 31, "output_tokens": 20},
            before_tokens=490_000, after_tokens=260, summarized_messages=25)
        assert duplicate["id"] == first["id"]
        assert db.read_conn.execute("SELECT COUNT(*) FROM run_events WHERE type='context.compacted'").fetchone()[0] == 1
        assert db.read_conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='context.compacted'").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.write_conn.execute("UPDATE context_checkpoints SET summary_json='{}' WHERE id=?",
                                  (saved["id"],))
        await events.append(task_run_id="task-08", conversation_id="conv-08",
                            type=RunEventType.CONTEXT_BUDGET_CHECKED, payload={"model": "继续"})
        newer = await checkpoints.save(conversation_id="conv-08", task_run_id="task-08",
            attempt_no=1, snapshot_id="snapshot-08", summary=SUMMARY,
            messages=first["messages"], aux_model="deepseek-v4.1-flash",
            aux_usage={"input_tokens": 28, "output_tokens": 19}, before_tokens=490_000,
            after_tokens=270, summarized_messages=30)
        assert checkpoints.latest("conv-08")["id"] == newer["id"]
        assert newer["replaced_from_global_seq"] == first["source_global_seq"] + 1
        assert db.read_conn.execute("SELECT COUNT(*) FROM context_checkpoints").fetchone()[0] == 2
        await events.append(task_run_id="task-08", conversation_id="conv-08",
                            type=RunEventType.LLM_REQUEST_DONE,
                            payload={"llm_call_id": "call-after-checkpoint", "text": "恢复后继续"})
        settings = SettingsService(load_config(tmp_path, {}), tmp_path, channel,
                                   tmp_path / "chain-head.txt")
        sessions = SessionService(db, events, tmp_path, settings)
        recovery = RecoveryService(db, events, sessions, tmp_path, checkpoints=checkpoints)
        resumed, _ledger = recovery.rebuild_for_resume("task-08")
        assert resumed.messages[:len(first["messages"])] == first["messages"]
        assert resumed.messages[-1]["content"][0]["text"] == "恢复后继续"
        assert len(resumed.messages) == len(first["messages"]) + 1
        await events.append(task_run_id="task-following", conversation_id="conv-08",
                            type=RunEventType.RUN_QUEUED,
                            payload={"instruction": "检查新文件"})
        await events.append(task_run_id="task-following", conversation_id="conv-08",
                            type=RunEventType.LLM_REQUEST_DONE,
                            payload={"llm_call_id": "call-following", "text": "已检查新文件"})
        await events.append(task_run_id="task-following", conversation_id="conv-08",
                            type=RunEventType.RUN_COMPLETED,
                            payload={"final_text": "已检查新文件"})
        continued = checkpoints.completed_after(checkpoints.latest("conv-08"))
        assert continued[:len(first["messages"])] == first["messages"]
        assert continued[-2]["content"][0]["text"] == "检查新文件"
        assert continued[-1]["content"][0]["text"] == "已检查新文件"
        assert len(continued) == len(first["messages"]) + 2
        db.write_conn.execute("UPDATE run_events SET payload='{}' WHERE global_seq=?",
                              (newer["event_global_seq"],))
        with pytest.raises(CheckpointCorrupt, match="EVENT_MISMATCH"):
            checkpoints.latest("conv-08")

    try:
        asyncio.run(scenario())
    finally:
        channel.close()
        db.close()


def test_checkpoint_verifies_retained_real_tool_artifact(tmp_path):
    db = Database(tmp_path / "agentcrew.db")
    run_migrations(db.write_conn, tmp_path / "backups")
    channel = WriteChannel(db.write_conn)
    events = EventStore(channel)
    checkpoints = ContextCheckpoints(db, events, tmp_path)
    db.write_conn.execute("INSERT INTO conversations(id,workspace_id,agent_id,created_at,updated_at) "
                          "VALUES('conv-art','ws','agent','2026-10-03','2026-10-03')")
    db.write_conn.execute("INSERT INTO task_runs(id,conversation_id,instruction,status,created_at,updated_at) "
                          "VALUES('task-art','conv-art','读取资料','running','2026-10-03','2026-10-03')")
    source = tmp_path / "original.txt"
    source.write_text("真实资料内容😀" * 12_000, encoding="utf-8")
    snapshot_path = tmp_path / "conversations" / "conv-art" / "memory-snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_body = json.dumps({"conversation_id": "conv-art", "snapshot_id": "snapshot-art",
                               "scope": {"owner_id": "owner", "workspace_id": "ws", "agent_id": "agent"}})
    snapshot_path.write_text(snapshot_body, encoding="utf-8")
    snapshot_sha = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()

    async def scenario():
        snapshot_event = await events.append(task_run_id="task-art",
            conversation_id="conv-art", type=RunEventType.MEMORY_SNAPSHOT_CREATED,
            payload={"snapshot_id": "snapshot-art", "snapshot_sha256": snapshot_sha})
        db.write_conn.execute("INSERT INTO memory_snapshots "
            "(conversation_id,snapshot_id,owner_id,workspace_id,agent_id,body,sha256,status,global_seq,created_at) "
            "VALUES('conv-art','snapshot-art','owner','ws','agent',?,?,'committed',?,'2026-10-03')",
            (snapshot_body, snapshot_sha, snapshot_event.global_seq))

        async def emit(kind, payload):
            return await events.append(task_run_id="task-art", conversation_id="conv-art",
                                       type=RunEventType(kind), payload=payload)

        context = WorkContext(scope=[tmp_path], protected=[], cwd=tmp_path,
            task_run_id="task-art", artifacts_dir=tmp_path / "artifacts" / "task-art",
            output_store=ToolOutputStore(), emit=emit)
        result = await ToolScheduler(build_default_registry()).run(
            ToolInvocation("real-read-art", "read_file", {"path": str(source)}), context)
        assert result.ok and result.artifact_path
        retained = [message("assistant", {"type": "tool_use", "id": "real-read-art",
                                         "name": "read_file", "input": {"path": str(source)}}),
                    message("user", {"type": "tool_result", "tool_use_id": "real-read-art",
                                     "content": result.output}),
                    message("user", {"type": "text", "text": "继续核验"})]
        await checkpoints.save(conversation_id="conv-art", task_run_id="task-art",
            attempt_no=1, snapshot_id="snapshot-art", summary=SUMMARY,
            messages=with_summary(SUMMARY, retained), aux_model="deepseek-v4.1-flash",
            aux_usage={"input_tokens": 50, "output_tokens": 20},
            before_tokens=490_000, after_tokens=300,
            summarized_messages=30)
        assert len(json.loads(checkpoints.latest("conv-art")["artifacts_json"])) == 1
        small_source = tmp_path / "small.txt"
        small_source.write_text("实际短输出\n", encoding="utf-8")
        small = await ToolScheduler(build_default_registry()).run(
            ToolInvocation("real-read-small", "read_file", {"path": str(small_source)}), context)
        assert small.ok and small.artifact_path is None
        history = [message("assistant", {"type": "tool_use", "id": "real-read-small",
                                          "name": "read_file", "input": {"path": str(small_source)}}),
                   message("user", {"type": "tool_result", "tool_use_id": "real-read-small",
                                    "content": small.output}),
                   message("user", {"type": "text", "text": "继续当前任务"})]
        source_event = db.read_conn.execute("SELECT global_seq FROM run_events WHERE type='tool.completed' "
            "AND json_extract(payload,'$.call_id')='real-read-small'").fetchone()[0]
        await checkpoints._prune_old_results("conv-art", history, 2, context)
        pruned = db.read_conn.execute("SELECT payload FROM run_events WHERE type='tool.result_externalized' "
            "AND json_extract(payload,'$.call_id')='real-read-small'").fetchone()
        assert pruned is not None
        archive = json.loads(pruned[0])
        assert archive["source_global_seq"] == source_event
        assert Path(archive["artifact_path"]).read_bytes() == small_source.read_bytes()
        assert archive["sha256"] in history[1]["content"][0]["content"]
        delivery = tmp_path / "delivery.txt"
        written = await ToolScheduler(build_default_registry()).run(
            ToolInvocation("real-write-delivery", "write_file",
                           {"path": str(delivery), "content": "真实交付材料"}), context)
        assert written.ok
        delivery_before = (delivery.read_bytes(), delivery.stat().st_mtime_ns)
        write_history = [message("assistant", {"type": "tool_use", "id": "real-write-delivery",
                                                "name": "write_file", "input": {"path": str(delivery)}}),
                         message("user", {"type": "tool_result", "tool_use_id": "real-write-delivery",
                                          "content": written.output})]
        await checkpoints._prune_old_results("conv-art", write_history, 2, context)
        assert (delivery.read_bytes(), delivery.stat().st_mtime_ns) == delivery_before
        ids = {row[0] for row in db.read_conn.execute(
            "SELECT id FROM artifacts WHERE tool_call_id='real-write-delivery'")}
        assert ids == {"real-write-delivery", "real-write-delivery-context-output"}
        archived_delivery = tmp_path / "artifacts" / "task-art" / "real-write-delivery-context-output.out"
        archived_before = (archived_delivery.read_bytes(), archived_delivery.stat().st_mtime_ns)
        await checkpoints._prune_old_results("conv-art", write_history, 2, context)
        assert (archived_delivery.read_bytes(), archived_delivery.stat().st_mtime_ns) == archived_before
        assert db.read_conn.execute("SELECT COUNT(*) FROM run_events WHERE type='tool.result_externalized' "
            "AND json_extract(payload,'$.call_id')='real-write-delivery'").fetchone()[0] == 1
        artifact = result.artifact_path
        original = source.read_bytes()
        assert Path(artifact).read_bytes() == original
        Path(artifact).write_text("外部修改", encoding="utf-8")
        with pytest.raises(ArtifactIntegrityError, match="ARTIFACT_HASH_MISMATCH"):
            checkpoints.latest("conv-art")
        Path(artifact).write_bytes(original)
        Path(artifact).unlink()
        with pytest.raises(ArtifactIntegrityError, match="ARTIFACT_MISSING"):
            checkpoints.latest("conv-art")
        Path(artifact).write_bytes(original)

        dual = await ToolScheduler(build_default_registry()).run(
            ToolInvocation("real-dual", "bash", {"command":
                f"cat '{source}'; cat '{source}' 1>&2"}), context)
        assert dual.ok and dual.artifact_path and dual.details["stderr_artifact"]
        dual_messages = with_summary(SUMMARY, [
            message("assistant", {"type": "tool_use", "id": "real-dual",
                                  "name": "bash", "input": {"command": "读取真实文件"}}),
            message("user", {"type": "tool_result", "tool_use_id": "real-dual",
                             "content": dual.output}),
            message("user", {"type": "text", "text": "继续核验"})])
        await checkpoints.save(conversation_id="conv-art", task_run_id="task-art",
            attempt_no=1, snapshot_id="snapshot-art", summary=SUMMARY,
            messages=dual_messages, aux_model="deepseek-v4.1-flash",
            aux_usage={"input_tokens": 48, "output_tokens": 19},
            before_tokens=490_000, after_tokens=320, summarized_messages=35)
        refs = json.loads(checkpoints.latest("conv-art")["artifacts_json"])
        assert {ref["artifact_path"] for ref in refs} == {
            dual.artifact_path, dual.details["stderr_artifact"]["artifact_path"]}
        Path(dual.artifact_path).write_text("损坏标准输出", encoding="utf-8")
        with pytest.raises(ArtifactIntegrityError, match="ARTIFACT_HASH_MISMATCH"):
            checkpoints.latest("conv-art")

    try:
        asyncio.run(scenario())
    finally:
        channel.close()
        db.close()
