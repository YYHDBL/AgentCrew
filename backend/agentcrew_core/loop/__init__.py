"""ReAct 主循环与回合守门（harness-session §5 / harness-design §2–§3）。

run_task 是纯编排：模型流（provider.stream）、工具执行、事件出口全部注入，
core 不碰 IO。守门全在本模块：回合上限、token 硬上限（压缩是 M1）、重复
调用守门、输出解析重试；停滞看门狗的**计时**在 RunManager（progress 回调）。

终态时序契约（v1.7）：模型流显式 aclose（①）+ 工具批 TaskGroup 全收割
（②，bash 进程组在 C5 已保证杀+wait）之后 run_task 才返回，RUN_COMPLETED/
FAILED 事件由 RunManager 在返回后发射——终态事件是"执行真正结束"的证据。

C4 移交（外审）：已产出事件后的流中断由适配层以 error(retryable) 上交，
本层决定整轮重发时**先丢弃上一轮已收到的部分输出**（messages 不受污染，
丢弃的回合只留 llm.request_failed 事件事实）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable, Literal

from ..provider.types import StreamEvent, ToolCall, Usage
from ..tools.metadata import ToolResult
from ..tools.scheduler import INVALID_PARAMS, input_hash

# ── 守门参数（harness-session §6.3；token 硬上限 C8 起生效，压缩 M1）─────


@dataclass(frozen=True)
class LoopGates:
    max_steps: int = 40
    stall_seconds: int = 600
    repeat_limit: int = 3
    token_budget: int = 2_000_000


# ── 环境块（v1.7：注入 system prompt 尾部，每任务开始时刷新）────────────


def env_block(now: datetime, cwd: str, materials_dir: str,
              folders: list[str], workspace_name: str) -> str:
    """环境事实块：日期时间 / macOS / 任务 cwd / 资料目录 / 授权文件夹 /
    工作区名（harness-session §5）。"""
    weekdays = "一二三四五六日"
    weekday = f"星期{weekdays[now.weekday()]}"
    lines = [
        "【环境信息】",
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（{weekday}）",
        "操作系统：macOS",
        f"工作目录：{cwd}（任务内相对路径以此为基础）",
        f"资料目录：{materials_dir}（用户为本次任务提供的材料所在）",
        f"授权文件夹：{'、'.join(folders) if folders else '无'}",
        f"工作区：{workspace_name}",
    ]
    return "\n".join(lines)


# ── context_fingerprint（v1.7：每次 attempt 记录，跨版本恢复可解释）──────


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_json(obj: Any) -> str:
    canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def context_fingerprint(system_prompt: str, tools_schema: list[dict],
                        model_config: dict[str, Any]) -> dict[str, Any]:
    """{system_prompt_hash, tools_schema_hash, model_config}——model_config
    由调用方组装且**不得含明文 key**（只放 api_key 的 sha256 摘要）。"""
    return {
        "system_prompt_hash": _sha256_text(system_prompt),
        "tools_schema_hash": _sha256_json(tools_schema),
        "model_config": dict(model_config),
    }


# ── 重复调用守门（§5 三招之一；纯函数语义，参数化单测）─────────────────


@dataclass
class RepeatGate:
    """连续 limit 次相同 (tool, input_hash) 且结果未变 → "correct"（注入纠偏）；
    纠偏后再达 limit → "doom"（RUN_FAILED(doom_loop)）。不同调用或不同结果
    即重置连击。"""

    limit: int = 3
    _key: tuple | None = field(default=None, repr=False)
    _result: str = field(default="", repr=False)
    _streak: int = field(default=0, repr=False)
    _corrected: bool = field(default=False, repr=False)

    def observe(self, tool_name: str, call: ToolCall,
                result: ToolResult) -> Literal["ok", "correct", "doom"]:
        key = (tool_name, input_hash(call.input))
        result_key = _sha256_text(f"{result.ok}|{result.error}|{result.output}")
        if key == self._key and result_key == self._result:
            self._streak += 1
        else:
            self._key, self._result, self._streak = key, result_key, 1
        if self._streak >= self.limit:
            if self._corrected:
                return "doom"
            self._corrected = True
            return "correct"
        return "ok"


# ── Anthropic 块式消息组装（保守姿态：thinking 块带 signature 回传）──────


def user_text_message(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def assistant_message(thinking_blocks: list[dict], text: str,
                      calls: list[ToolCall]) -> dict:
    content: list[dict] = [
        {"type": "thinking", "thinking": b["text"], "signature": b["signature"]}
        for b in thinking_blocks
    ]
    if text:
        content.append({"type": "text", "text": text})
    content += [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
        for c in calls
    ]
    return {"role": "assistant", "content": content}


def tool_results_message(calls: list[ToolCall],
                         results: list[ToolResult]) -> dict:
    """工具结果配对回填（tool_use_id 一一对应）；失败结果带 is_error 标记。"""
    content = []
    for call, result in zip(calls, results):
        body = result.output if result.ok else (
            f"错误：{result.error or 'TOOL_FAILED'}"
            + (f"\n{result.output}" if result.output else ""))
        content.append({
            "type": "tool_result", "tool_use_id": call.id,
            "content": body, "is_error": not result.ok,
        })
    return {"role": "user", "content": content}


# ── 错误分类（§5 三招之三：解析重试的判定依据，参数化单测）───────────────

PARSE_ERROR_MARKERS = ("tool_use 参数", "未关闭的内容块", "流解析失败")


def is_parse_error(error) -> bool:
    """适配层上游解析失败（坏 tool_use JSON / 块未关闭 / SSE 解析）——
    回填重说通道；其余非重试错误（鉴权/内容过滤/账户）直接失败。"""
    if error.retryable:
        return False
    return any(m in (error.message or "") for m in PARSE_ERROR_MARKERS)


CORRECTION_TEXT = (
    "【系统纠偏】你已连续 {limit} 次以相同参数调用 {tool} 且得到相同结果。"
    "请改变方法推进任务，或明确汇报当前障碍；不要再重复相同的调用。"
)

# C4 移交：已产出事件后的可重试中断（网络/截断）整轮重发上限
ROUND_RESEND_LIMIT = 1
# §5 三招之三：解析重试上限，仍坏 → RUN_FAILED(unparseable)
PARSE_RETRY_LIMIT = 2


# ── 主循环 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LoopDeps:
    request: Callable[[], AsyncIterator[StreamEvent]]  # 每回合新建模型流
    execute: Callable[[ToolCall], Awaitable[ToolResult]]
    emit: Callable[[str, dict], Awaitable[None]]       # 事件出口（落库+扇出）
    on_progress: Callable[[], None]                    # 停滞看门狗心跳
    gates: LoopGates
    model: str                                         # llm.request_started 用


@dataclass(frozen=True)
class LoopResult:
    status: Literal["completed", "failed"]
    final_text: str = ""
    reason: str = ""                                   # failed 时必填


async def run_task(messages: list[dict], deps: LoopDeps) -> LoopResult:
    """ReAct 主循环（harness-session §5 骨架）。

    messages 由调用方初始化（历史 + 本任务指令），本循环只追加 assistant
    tool_use / tool_result / 纠偏消息；无 tool_call 的回合即最终回复。"""
    gates = deps.gates
    repeat = RepeatGate(gates.repeat_limit)
    round_resends = 0
    parse_retries = 0
    tokens_used = 0
    ordinal = 0
    while True:
        ordinal += 1
        if ordinal > gates.max_steps:
            return LoopResult("failed", reason="max_steps")
        if tokens_used >= gates.token_budget:
            return LoopResult("failed", reason="token_budget")

        step_id = uuid.uuid4().hex
        await deps.emit("step.started", {
            "step_id": step_id, "ordinal": ordinal, "model_slot": "main"})
        retry_no = 0
        llm_call_id = ""
        started = time.monotonic()
        while True:
            llm_call_id = uuid.uuid4().hex
            await deps.emit("llm.request_started", {
                "llm_call_id": llm_call_id, "step_id": step_id,
                "model": deps.model, "retry_no": retry_no})
            # 每次尝试都从空白缓冲开始——整轮重发前丢弃部分输出（C4 移交）
            text_parts: list[str] = []
            thinking_blocks: list[dict] = []
            calls: list[ToolCall] = []
            usage: Usage | None = None
            stop_reason: str | None = None
            error = None
            stream = deps.request()
            try:
                async for ev in stream:
                    deps.on_progress()
                    if ev.type == "text_delta":
                        text_parts.append(ev.text or "")
                    elif ev.type == "thinking_block":
                        thinking_blocks.append(
                            {"text": ev.text or "",
                             "signature": ev.signature or ""})
                    elif ev.type == "tool_call":
                        calls.append(ev.tool_call)
                    elif ev.type == "usage":
                        usage = ev.usage
                    elif ev.type == "done":
                        stop_reason = ev.stop_reason
                        break
                    elif ev.type == "error":
                        error = ev.error
                        break
            finally:
                # 终态时序①：模型流已关闭（break 后生成器仍挂起，必须显式关）
                await stream.aclose()
            if error is None:
                break
            await deps.emit("llm.request_failed", {
                "llm_call_id": llm_call_id, "step_id": step_id,
                "error": f"{error.error_class.value}: {error.message}",
                "retry_no": retry_no})
            # 整轮重发仅限"已产出部分输出"（C4 移交前提 + §6.3 上限语义）：
            # 零产出的可重试错误 = 适配层内部退避 3 次已耗尽，循环层再重发
            # 一整轮会把最坏请求数放大到 8——按耗尽处理直接失败（外审二轮）
            produced = bool(text_parts or thinking_blocks or calls)
            if (error.retryable and produced
                    and round_resends < ROUND_RESEND_LIMIT):
                round_resends += 1
                retry_no += 1
                continue  # 丢弃部分输出后整轮重发
            if is_parse_error(error) and parse_retries < PARSE_RETRY_LIMIT:
                parse_retries += 1
                retry_no += 1
                continue  # 坏 tool_use → 重采样（上限 2，仍坏 unparseable）
            reason = ("unparseable" if is_parse_error(error)
                      else f"provider:{error.error_class.value}")
            return LoopResult("failed", reason=reason)

        in_tok = usage.input_tokens if usage else 0
        out_tok = usage.output_tokens if usage else 0
        tokens_used += in_tok + out_tok
        full_text = "".join(text_parts)
        latency_ms = int((time.monotonic() - started) * 1000)
        # llm.request_done 载荷 = 回复全文 + 全部 tool_use 块（含 thinking
        # 块与签名）——C9 恢复重建对话与结果配对的唯一依据（v1.7）
        await deps.emit("llm.request_done", {
            "llm_call_id": llm_call_id, "step_id": step_id,
            "prompt_tokens": in_tok, "completion_tokens": out_tok,
            "latency_ms": latency_ms, "text": full_text,
            "tool_uses": [{"id": c.id, "name": c.name, "input": c.input}
                          for c in calls],
            "thinking_blocks": thinking_blocks,
            "stop_reason": stop_reason,
        })
        await deps.emit("step.completed", {
            "step_id": step_id, "input_tokens": in_tok,
            "output_tokens": out_tok, "latency_ms": latency_ms})

        if not calls:
            messages.append(assistant_message(thinking_blocks, full_text, []))
            return LoopResult("completed", final_text=full_text)

        # v1.9：assistant tool_use 消息先入历史（结果配对的前提）
        messages.append(assistant_message(thinking_blocks, full_text, calls))
        # 工具批：TaskGroup 保证异常/取消时兄弟任务一并收割（终态时序②）
        results: list[ToolResult | None] = [None] * len(calls)
        async with asyncio.TaskGroup() as tg:
            for i, call in enumerate(calls):

                async def _one(i: int = i, call: ToolCall = call) -> None:
                    results[i] = await deps.execute(call)

                tg.create_task(_one())
        corrected_tool: str | None = None
        invalid_params = 0
        for call, result in zip(calls, results):
            assert result is not None  # TaskGroup 正常退出必已回填
            deps.on_progress()
            if result.error == INVALID_PARAMS:
                # 参数 schema 不符（外审回稿）：与流解析错误共用同一条
                # "错误回填重说"通道与计数——不再烧回合等模型自纠
                invalid_params += 1
                continue
            verdict = repeat.observe(call.name, call, result)
            if verdict == "doom":
                return LoopResult("failed", reason="doom_loop")
            if verdict == "correct":
                corrected_tool = call.name
        parse_retries += invalid_params
        if parse_retries > PARSE_RETRY_LIMIT:
            return LoopResult("failed", reason="unparseable")
        messages.append(tool_results_message(calls, results))
        if corrected_tool is not None:
            messages.append(user_text_message(
                CORRECTION_TEXT.format(limit=gates.repeat_limit,
                                       tool=corrected_tool)))
        # 下一轮模型请求
