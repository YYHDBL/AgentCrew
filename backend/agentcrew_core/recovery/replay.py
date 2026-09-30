"""中断恢复的纯函数件（harness-session §7 / §2.3 持久化契约）。

- replay_messages：从 run_events 行重放重建消息上下文。唯一原料是
  llm.request_done 载荷（回复全文 + 全部 tool_use 块 + thinking 签名，
  v1.7）与 tool.* 终态事件；超限输出按 §2.3 读工件（read_artifact 注入，
  缺失 → "工件缺失"占位降级，任务继续）；
- side_effect_ledger：副作用账本两段系统提示的文本（§7）；
- file_hash_matches：verifiable 类启动核验（文件存在且 sha256 一致）。

rows 是 (seq, type, payload_json_text) 三元组——payload 文本在此解析，
损坏行跳过并显式告警（needs_manual_review=True，调用方拒绝静默继续）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# 与 loop.tool_results_message 相同的块结构；本地构造避免 core 内反向依赖
EXTERNALIZE_MARKER = "[输出超限，已外部化]"


@dataclass
class ReplayResult:
    messages: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 损坏事件行：上下文完整性无保证，调用方须显式处置（不静默）
    needs_manual_review: bool = False


def _assistant_from_request_done(p: dict) -> dict:
    content: list[dict] = [
        {"type": "thinking", "thinking": b.get("text", ""),
         "signature": b.get("signature", "")}
        for b in p.get("thinking_blocks") or []
    ]
    if p.get("text"):
        content.append({"type": "text", "text": p["text"]})
    content += [
        {"type": "tool_use", "id": u["id"], "name": u["name"],
         "input": u.get("input") or {}}
        for u in p.get("tool_uses") or []
    ]
    return {"role": "assistant", "content": content}


def replay_messages(
    rows: list[tuple[int, str, str]],
    read_artifact: Callable[[str], str | None] | None = None,
) -> ReplayResult:
    """按序重放一个 TaskRun 的全部事件（跨 attempt），重建消息上下文。

    每个带 tool_use 的 llm.request_done 之后，用后续 tool 终态事件配对
    tool_result；中断时无终态的调用按账本事实合成占位（§6.2 v1.8：
    ask_user=未回答；prepared 未派发=未执行；dispatched=结果未记录）。
    """
    out = ReplayResult()
    reader = read_artifact or (lambda _p: None)

    def warn(msg: str) -> None:
        out.warnings.append(msg)

    prepared: set[str] = set()
    dispatched: set[str] = set()
    # call_id → (content, is_error)；verification_submitted 晚到时覆盖
    outcomes: dict[str, tuple[str, bool]] = {}
    # ask_user 的 request_id == call_id；requested 集合 + 已见回答
    asked: set[str] = set()
    answers: dict[str, str | None] = {}
    has_answer: set[str] = set()

    def outcome_of(call_id: str, name: str) -> tuple[str, bool]:
        if call_id in outcomes:
            return outcomes[call_id]
        if name == "ask_user":
            if call_id in has_answer:
                ans = answers[call_id]
                return (ans if ans is not None else "（用户取消了回答）",
                        ans is None)
            return "（中断，未回答）", True  # §6.2 v1.8：可再问
        if call_id in dispatched:
            return "（中断，执行结果未记录）", True
        return "（中断，未派发执行）", True

    def flush(open_calls: list[dict]) -> None:
        if not open_calls:
            return
        content = []
        for u in open_calls:
            body, is_error = outcome_of(u["id"], u["name"])
            content.append({"type": "tool_result", "tool_use_id": u["id"],
                            "content": body, "is_error": is_error})
        out.messages.append({"role": "user", "content": content})

    open_calls: list[dict] = []
    for seq, etype, payload_text in sorted(rows, key=lambda r: r[0]):
        if etype == "run.queued":
            try:
                p = json.loads(payload_text)
            except (json.JSONDecodeError, TypeError):
                warn(f"seq={seq} run.queued 载荷损坏，指令原文不可恢复")
                out.needs_manual_review = True
                continue
            instruction = p.get("instruction", "")
            if instruction:
                out.messages.append(
                    {"role": "user",
                     "content": [{"type": "text", "text": instruction}]})
            continue
        if etype == "llm.request_done":
            try:
                p = json.loads(payload_text)
            except (json.JSONDecodeError, TypeError):
                warn(f"seq={seq} llm.request_done 载荷损坏，该回合上下文丢失")
                out.needs_manual_review = True
                flush(open_calls)  # 结构完整性：配对 tool_result 仍要补上
                open_calls = []
                continue
            flush(open_calls)
            msg = _assistant_from_request_done(p)
            out.messages.append(msg)
            open_calls = [c for c in msg["content"]
                          if c["type"] == "tool_use"]
            continue
        # tool / question 域：载荷损坏只丢该事件的事实，不判人工介入
        try:
            p = json.loads(payload_text)
        except (json.JSONDecodeError, TypeError):
            warn(f"seq={seq} {etype} 载荷损坏，跳过")
            continue
        call_id = p.get("call_id")
        if etype == "tool.prepared" and call_id:
            prepared.add(call_id)
        elif etype == "tool.dispatched" and call_id:
            dispatched.add(call_id)
        elif etype in ("tool.completed", "tool.skipped_idempotent"):
            body = p.get("output") or p.get("output_summary") or ""
            if p.get("artifact_path") and EXTERNALIZE_MARKER in body:
                # §2.3：超限输出读工件；缺失 → 占位降级（任务继续）
                real = reader(p["artifact_path"])
                if real is None:
                    warn(f"工件缺失：{p['artifact_path']}（该工具输出降级为占位）")
                    body = f"（工件缺失：{p['artifact_path']}，输出不可恢复）"
                else:
                    body = real
            outcomes[call_id] = (body, False)
        elif etype == "tool.failed":
            err = p.get("error") or "TOOL_FAILED"
            outcomes[call_id] = (
                f"错误：{err}" + (f"\n{p['output']}" if p.get("output") else ""),
                True)
        elif etype == "tool.verification_submitted":
            verdict = p.get("verdict")
            if verdict == "confirmed_executed":
                outcomes[call_id] = ("（中断后经用户确认已执行；原始输出未记录）",
                                     False)
            elif verdict == "confirmed_not_executed":
                outcomes[call_id] = ("（该调用未执行，需要时应重做）", False)
        elif etype == "question.requested":
            asked.add(p.get("request_id"))
        elif etype == "question.answered":
            rid = p.get("request_id")
            has_answer.add(rid)
            answers[rid] = p.get("answer")
    flush(open_calls)
    return out


# ── 副作用账本（§7 两段系统提示的文本件）───────────────────────────

RESUME_INSTRUCTION = (
    "【中断恢复·恢复指令】任务此前因后端重启中断，以上是中断前的完整历史"
    "与副作用账本。请继续完成原指令：已执行的调用不要再产生重复副作用；"
    "标注未执行的调用在任务需要时重做。"
)


def side_effect_ledger(calls: list[tuple[str, str, str]]) -> str:
    """calls = [(tool_name, input_json_text, status)]，按 prepared_at 序。

    两段文本：账本（已执行 / 未执行·结果不明）+ 恢复指令（常量）。
    completed=已执行；not_executed=确认未执行；ask_user 停 dispatched=
    中断未回答；prepared=未派发。failed 的结果上下文里已有，不进账本。
    """
    done: list[str] = []
    unknown: list[str] = []

    def brief(tool: str, input_text: str) -> str:
        try:
            inp = json.loads(input_text)
            key = next((k for k in ("path", "command", "url", "question")
                        if k in inp), None)
            arg = f"{key}={str(inp[key])[:60]}" if key else ""
        except (json.JSONDecodeError, TypeError):
            arg = ""
        return f"{tool}（{arg}）" if arg else tool

    for tool, input_text, status in calls:
        if status == "completed":
            done.append(brief(tool, input_text))
        elif status == "not_executed":
            unknown.append(brief(tool, input_text) + "——用户确认未执行，需要时重做")
        elif tool == "ask_user" and status == "dispatched":
            unknown.append(brief(tool, input_text) + "——中断，未获回答")
        elif status == "prepared":
            unknown.append(brief(tool, input_text) + "——中断，未派发执行")
    lines = ["【中断恢复·副作用账本】"]
    lines.append("你已执行：" + ("；".join(done) if done else "无"))
    lines.append("未执行/结果不明：" + ("；".join(unknown) if unknown else "无"))
    lines.append(RESUME_INSTRUCTION)
    return "\n".join(lines)


# ── verifiable 启动核验（§6.2：文件存在且内容哈希一致 → 补 completed）──

def file_hash_matches(path_text: str, expected_sha256: str) -> bool:
    try:
        digest = hashlib.sha256(
            Path(path_text).read_bytes()).hexdigest()
    except OSError:
        return False  # 不存在/不可读 = 无法核验
    return digest == expected_sha256
