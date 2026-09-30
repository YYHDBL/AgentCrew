"""C9 纯函数件测试：上下文重放器 / 副作用账本 / verifiable 哈希核验 /
llm.request_done 载荷契约锁定（projections.py 头部登记的字段逐字核对）。

重放器输入是 (seq, type, payload_json) 三元组——参数化单测（ADR-006 纪律：
不 mock provider；这里测的是纯函数，不碰 GLM）。
"""

from __future__ import annotations

import json

from agentcrew_core.loop import LoopDeps, LoopGates, run_task
from agentcrew_core.provider.types import StreamEvent, ToolCall, Usage
from agentcrew_core.recovery import (
    file_hash_matches,
    replay_messages,
    side_effect_ledger,
)
from agentcrew_core.tools.metadata import ToolResult


def _rows(*pairs) -> list[tuple[int, str, str]]:
    return [(i, t, json.dumps(p, ensure_ascii=False))
            for i, (t, p) in enumerate(pairs, start=1)]


# ── 重放器：完整往返 ───────────────────────────────────────────────

def test_replay_full_round_trip_with_tools_and_thinking():
    rows = _rows(
        ("run.queued", {"instruction": "整理发票"}),
        ("llm.request_done", {
            "text": "先读文件", "tool_uses": [
                {"id": "t1", "name": "read_file", "input": {"path": "a.csv"}}],
            "thinking_blocks": [{"text": "思考", "signature": "sig-1"}],
            "stop_reason": "tool_use"}),
        ("tool.prepared", {"call_id": "t1", "tool_name": "read_file"}),
        ("tool.dispatched", {"call_id": "t1"}),
        ("tool.completed", {"call_id": "t1", "output": "1,2,3"}),
        ("llm.request_done", {"text": "已整理", "tool_uses": [],
                              "stop_reason": "end_turn"}),
    )
    r = replay_messages(rows)
    assert not r.warnings and not r.needs_manual_review
    assert [m["role"] for m in r.messages] == \
        ["user", "assistant", "user", "assistant"]
    assert r.messages[0]["content"][0]["text"] == "整理发票"
    a1 = r.messages[1]["content"]
    assert a1[0] == {"type": "thinking", "thinking": "思考",
                     "signature": "sig-1"}, "thinking 块带签名原样回传"
    assert a1[1] == {"type": "text", "text": "先读文件"}
    assert a1[2] == {"type": "tool_use", "id": "t1", "name": "read_file",
                     "input": {"path": "a.csv"}}
    assert r.messages[2]["content"] == [{
        "type": "tool_result", "tool_use_id": "t1", "content": "1,2,3",
        "is_error": False}]
    assert r.messages[3]["content"][0]["text"] == "已整理"


def test_replay_failed_tool_marks_is_error():
    rows = _rows(
        ("run.queued", {"instruction": "x"}),
        ("llm.request_done", {"text": "", "tool_uses": [
            {"id": "t9", "name": "bash", "input": {"command": "ls"}}]}),
        ("tool.failed", {"call_id": "t9", "error": "EXIT_2",
                         "output": "No such file"}),
        ("llm.request_done", {"text": "done"}),
    )
    r = replay_messages(rows)
    tr = r.messages[2]["content"][0]
    assert tr["is_error"] is True
    assert tr["content"] == "错误：EXIT_2\nNo such file"


# ── 中断占位：ask_user 豁免 / 未派发 / 结果未记录 ──────────────────

def test_replay_interrupted_ask_user_placeholder():
    rows = _rows(
        ("run.queued", {"instruction": "x"}),
        ("llm.request_done", {"tool_uses": [
            {"id": "q1", "name": "ask_user", "input": {"question": "继续？"}}]}),
        ("tool.prepared", {"call_id": "q1", "tool_name": "ask_user"}),
        ("tool.dispatched", {"call_id": "q1"}),
        ("question.requested", {"request_id": "q1", "question": "继续？"}),
    )
    r = replay_messages(rows)
    tr = r.messages[2]["content"][0]
    assert tr["tool_use_id"] == "q1"
    assert tr["content"] == "（中断，未回答）", "§6.2 v1.8：合成占位告知可再问"
    assert tr["is_error"] is True


def test_replay_ask_user_answered_after_restart_uses_answer():
    rows = _rows(
        ("run.queued", {"instruction": "x"}),
        ("llm.request_done", {"tool_uses": [
            {"id": "q1", "name": "ask_user", "input": {"question": "继续？"}}]}),
        ("tool.dispatched", {"call_id": "q1"}),
        ("question.requested", {"request_id": "q1", "question": "继续？"}),
        ("question.answered", {"request_id": "q1", "answer": "继续"}),
    )
    r = replay_messages(rows)
    tr = r.messages[2]["content"][0]
    assert tr["content"] == "继续" and tr["is_error"] is False


def test_replay_ask_user_answer_null_is_rejection():
    rows = _rows(
        ("run.queued", {"instruction": "x"}),
        ("llm.request_done", {"tool_uses": [
            {"id": "q1", "name": "ask_user", "input": {"question": "？"}}]}),
        ("question.requested", {"request_id": "q1"}),
        ("question.answered", {"request_id": "q1", "answer": None}),
    )
    tr = replay_messages(rows).messages[2]["content"][0]
    assert tr["content"] == "（用户取消了回答）" and tr["is_error"] is True


def test_replay_prepared_not_dispatched_placeholder():
    rows = _rows(
        ("run.queued", {"instruction": "x"}),
        ("llm.request_done", {"tool_uses": [
            {"id": "w1", "name": "write_file",
             "input": {"path": "a.txt", "content": "A"}}]}),
        ("tool.prepared", {"call_id": "w1", "tool_name": "write_file"}),
        ("permission.requested", {"tool_call_id": "w1"}),
    )
    tr = replay_messages(rows).messages[2]["content"][0]
    assert tr["content"] == "（中断，未派发执行）"


def test_replay_dispatched_no_terminal_placeholder():
    rows = _rows(
        ("run.queued", {"instruction": "x"}),
        ("llm.request_done", {"tool_uses": [
            {"id": "b1", "name": "bash", "input": {"command": "sleep 5"}}]}),
        ("tool.prepared", {"call_id": "b1"}),
        ("tool.dispatched", {"call_id": "b1"}),
    )
    tr = replay_messages(rows).messages[2]["content"][0]
    assert tr["content"] == "（中断，执行结果未记录）"


def test_replay_verification_verdicts():
    base = [
        ("run.queued", {"instruction": "x"}),
        ("llm.request_done", {"tool_uses": [
            {"id": "b1", "name": "bash", "input": {"command": "sleep 5"}}]}),
        ("tool.dispatched", {"call_id": "b1"}),
        ("tool.pending_verification", {"call_id": "b1"}),
    ]
    executed = _rows(*(base + [
        ("tool.verification_submitted",
         {"call_id": "b1", "verdict": "confirmed_executed"})]))
    tr = replay_messages(executed).messages[2]["content"][0]
    assert "经用户确认已执行" in tr["content"] and not tr["is_error"]

    not_run = _rows(*(base + [
        ("tool.verification_submitted",
         {"call_id": "b1", "verdict": "confirmed_not_executed"})]))
    tr = replay_messages(not_run).messages[2]["content"][0]
    assert tr["content"] == "（该调用未执行，需要时应重做）"


# ── 损坏行与工件降级 ───────────────────────────────────────────────

def test_replay_corrupt_request_done_flags_manual_review():
    rows = [
        (1, "run.queued", json.dumps({"instruction": "x"})),
        (2, "llm.request_done", "{broken json"),
        (3, "llm.request_done", json.dumps({"text": "后面照常"})),
    ]
    r = replay_messages(rows)
    assert r.needs_manual_review is True, "损坏回合不得静默"
    assert any("损坏" in w for w in r.warnings)
    assert r.messages[-1]["content"][0]["text"] == "后面照常", "好行照常重放"


def test_replay_corrupt_tool_event_only_warns():
    rows = [
        (1, "run.queued", json.dumps({"instruction": "x"})),
        (2, "llm.request_done", json.dumps({"tool_uses": [
            {"id": "t1", "name": "bash", "input": {}}]})),
        (3, "tool.dispatched", json.dumps({"call_id": "t1"})),
        (4, "tool.completed", "not-json"),
    ]
    r = replay_messages(rows)
    assert r.needs_manual_review is False, "工具事件损坏≠上下文失真"
    assert r.warnings, "但要显式告警"
    assert r.messages[2]["content"][0]["content"] == "（中断，执行结果未记录）"


def test_replay_externalized_output_reads_artifact(tmp_path):
    art = tmp_path / "b1.out"
    art.write_text("大输出" * 100, encoding="utf-8")
    rows = _rows(
        ("run.queued", {"instruction": "x"}),
        ("llm.request_done", {"tool_uses": [
            {"id": "b1", "name": "bash", "input": {"command": "cat f"}}]}),
        ("tool.completed", {
            "call_id": "b1", "output": f"[输出超限，已外部化] {art}",
            "artifact_path": str(art)}),
    )
    r = replay_messages(rows, read_artifact=lambda p: art.read_text("utf-8"))
    assert r.messages[2]["content"][0]["content"] == "大输出" * 100


def test_replay_missing_artifact_degrades_with_placeholder():
    path = "/nonexistent/b1.out"
    rows = _rows(
        ("run.queued", {"instruction": "x"}),
        ("llm.request_done", {"tool_uses": [
            {"id": "b1", "name": "bash", "input": {}}]}),
        ("tool.completed", {
            "call_id": "b1", "output": f"[输出超限，已外部化] {path}",
            "artifact_path": path}),
    )
    r = replay_messages(rows, read_artifact=lambda p: None)
    assert "工件缺失" in r.messages[2]["content"][0]["content"], "降级继续"
    assert any("工件缺失" in w for w in r.warnings)


def test_replay_read_file_externalized_reads_artifact():
    """S3：判定按 artifact_path 字段驱动——read_file 的指针文案
    ("[输出 N 字节超限，已外部化]") 不命中 bash 专属 marker，旧实现
    退化为指针文本；字段驱动后必须读回工件内容。"""
    art = "/artifacts/read-1.out"
    rows = _rows(
        ("run.queued", {"instruction": "x"}),
        ("llm.request_done", {"tool_uses": [
            {"id": "r1", "name": "read_file", "input": {"path": "big.log"}}]}),
        ("tool.completed", {
            "call_id": "r1",
            "output": f"[输出 104876 字节超限，已外部化] {art}",
            "artifact_path": art}),
    )
    r = replay_messages(rows, read_artifact=lambda p: "大文件全文")
    assert r.messages[2]["content"][0]["content"] == "大文件全文"
    assert not r.warnings


def test_replay_failed_with_artifact_reads_artifact():
    """S3：tool.failed 带工件指针的分支同样读工件（错误行 + 工件全文）。"""
    art = "/artifacts/fail-1.out"
    rows = _rows(
        ("run.queued", {"instruction": "x"}),
        ("llm.request_done", {"tool_uses": [
            {"id": "b9", "name": "bash", "input": {"command": "cat big"}}]}),
        ("tool.failed", {
            "call_id": "b9", "error": "EXIT_1",
            "output": f"[输出超限，已外部化] {art}",
            "artifact_path": art}),
    )
    r = replay_messages(rows, read_artifact=lambda p: "失败的完整输出")
    body = r.messages[2]["content"][0]["content"]
    assert body == "错误：EXIT_1\n失败的完整输出", body
    assert r.messages[2]["content"][0]["is_error"] is True


def test_replay_failed_missing_artifact_placeholder():
    path = "/nonexistent/fail.out"
    rows = _rows(
        ("run.queued", {"instruction": "x"}),
        ("llm.request_done", {"tool_uses": [
            {"id": "b9", "name": "bash", "input": {}}]}),
        ("tool.failed", {
            "call_id": "b9", "error": "EXIT_1",
            "output": f"[输出超限，已外部化] {path}",
            "artifact_path": path}),
    )
    r = replay_messages(rows, read_artifact=lambda p: None)
    body = r.messages[2]["content"][0]["content"]
    assert body.startswith("错误：EXIT_1") and "工件缺失" in body
    assert any("工件缺失" in w for w in r.warnings)


# ── 副作用账本 ─────────────────────────────────────────────────────

def test_ledger_sections():
    text = side_effect_ledger([
        ("c1", "write_file", json.dumps({"path": "half.xlsx", "content": "x"}),
         "completed"),
        ("c2", "bash", json.dumps({"command": "sleep 30"}), "not_executed"),
        ("c3", "ask_user", json.dumps({"question": "继续？"}), "dispatched"),
        ("c4", "read_file", json.dumps({"path": "a"}), "failed"),
    ])
    assert "你已执行：write_file（path=half.xlsx）" in text
    assert "bash（command=sleep 30）——用户确认未执行，需要时重做" in text
    assert "ask_user（question=继续？）——中断，未获回答" in text
    assert "read_file" not in text.replace("read_file（path=a）", ""), \
        "failed 不进账本"


def test_ledger_ask_user_answered_fact_matches_replay():
    """S4：回答已落库（等名额窗口内 kill）时账本不得再写"未获回答"——
    与重放的真答事实一致（单一事实源）。"""
    text = side_effect_ledger([
        ("q1", "ask_user", json.dumps({"question": "继续？"}), "dispatched"),
        ("q2", "ask_user", json.dumps({"question": "选哪个？"}), "dispatched"),
    ], answered={"q1": "继续", "q2": None})
    assert "q1" not in text  # call_id 不泄漏到台账文本
    assert "ask_user（question=继续？）——已获回答：继续" in text, \
        "已获回答的事实必须与重放一致"
    assert "ask_user（question=选哪个？）——用户取消了回答" in text
    assert "未获回答" not in text


# ── verifiable 哈希核验 ────────────────────────────────────────────

def test_file_hash_matches(tmp_path):
    import hashlib
    f = tmp_path / "out.txt"
    f.write_text("C9-A", encoding="utf-8")
    sha = hashlib.sha256(b"C9-A").hexdigest()
    assert file_hash_matches(str(f), sha) is True
    assert file_hash_matches(str(f), "0" * 64) is False, "哈希不一致"
    assert file_hash_matches(str(tmp_path / "nope"), sha) is False, \
        "文件不存在"


# ── llm.request_done 载荷契约锁定（C9 重建的唯一原料）──────────────

def test_llm_request_done_payload_contract():
    """逐字段核对 loop 发射的 llm.request_done：回复全文 + 全部 tool_use
    块 + thinking 签名（projections.py 头部契约 / v1.7）。字段漂移 = 恢复
    重建原料断裂，此测试必须红。"""
    calls = [ToolCall(id="t1", name="bash", input={"command": "pwd"})]
    rounds = iter([calls, None])
    emitted: list[tuple[str, dict]] = []

    async def scenario():
        async def request():
            batch = next(rounds)
            if batch is not None:
                yield StreamEvent(type="thinking_block", text="想一下",
                                  signature="sig-abc")
                yield StreamEvent(type="text_delta", text="执行 ")
                yield StreamEvent(type="text_delta", text="中")
                for c in batch:
                    yield StreamEvent(type="tool_call", tool_call=c)
            else:
                yield StreamEvent(type="text_delta", text="完成")
            yield StreamEvent(type="usage", usage=Usage(11, 7))
            yield StreamEvent(type="done",
                              stop_reason="tool_use" if batch else "end_turn")

        async def execute(call):
            return ToolResult(ok=True, output="ok")

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        deps = LoopDeps(request=request, execute=execute, emit=emit,
                        on_progress=lambda: None, gates=LoopGates(),
                        model="glm-test")
        result = await run_task([], deps)
        assert result.status == "completed"

    import asyncio
    asyncio.run(scenario())
    dones = [p for t, p in emitted if t == "llm.request_done"]
    assert len(dones) == 2
    keys = {"llm_call_id", "step_id", "prompt_tokens", "completion_tokens",
            "latency_ms", "text", "tool_uses", "thinking_blocks",
            "stop_reason"}
    assert set(dones[0]) == keys, f"字段漂移：{set(dones[0]) ^ keys}"
    assert dones[0]["text"] == "执行 中", "回复全文（delta 拼接）"
    assert dones[0]["prompt_tokens"] == 11 and dones[0]["completion_tokens"] == 7
    assert dones[0]["tool_uses"] == [{"id": "t1", "name": "bash",
                                      "input": {"command": "pwd"}}]
    assert dones[0]["thinking_blocks"] == [{"text": "想一下",
                                            "signature": "sig-abc"}]
    assert dones[0]["stop_reason"] == "tool_use"
    assert dones[1]["tool_uses"] == [] and dones[1]["stop_reason"] == "end_turn"


def test_run_task_resumes_ordinal_continuously():
    """resume 续接回合号（steps 唯一键 + 回合上限按任务计——重置会翻倍
    预算并撞 UNIQUE(task_run_id, ordinal)，真实 kill -9 验收首跑抓到）。"""
    emitted: list[tuple[str, dict]] = []

    async def scenario():
        async def request():
            yield StreamEvent(type="text_delta", text="done")
            yield StreamEvent(type="done", stop_reason="end_turn")

        async def execute(call):
            return ToolResult(ok=True)

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        deps = LoopDeps(request=request, execute=execute, emit=emit,
                        on_progress=lambda: None, gates=LoopGates(),
                        model="glm-test", start_ordinal=4)
        result = await run_task([], deps)
        assert result.status == "completed"

    import asyncio
    asyncio.run(scenario())
    ordinals = [p["ordinal"] for t, p in emitted if t == "step.started"]
    assert ordinals == [4], f"回合号应续接为 4，实际 {ordinals}"
