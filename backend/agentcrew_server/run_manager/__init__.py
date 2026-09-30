"""RunManager（M0-C8）：每 TaskRun 一个 asyncio 任务、注册表与取消传播。

- 启动：订阅总线（internal，不占 SSE 名额）监听 run.queued / run.resumed →
  派发 runner；全局并发 FIFO（gates.global_concurrency，信号量）；
- 配置绑定（v1.7）：每次 attempt 开始时快照 models/gates/system prompt/
  tools schema——运行中 PATCH settings 不影响本任务，下一新任务用新版
  （attempt 记 context_fingerprint；模型槽经独立 Provider 实例绑定，共享
  httpx 连接池）；
- 取消传播：task.cancel() 打到模型流（aclose）与工具子进程（C5 已杀组收割）
  与 ask_user 等待（S07 干净释放）；用户停止 → run.cancelled（reducer 置
  queue_paused 不自动接续）；优雅关闭 → 取消但不写终态（§6，C9 对账域）；
- 停滞看门狗：模型 delta / 工具完成 / 审批与提问决定都刷新 last_progress；
  等待用户（审批/提问）期间不计时（§5）；超阈值 → RUN_FAILED(stalled)；
- 终态时序契约（v1.7）：run_task 返回时模型流已 aclose、工具批已全部收割
  （TaskGroup），终态事件在此之后才发；
- 接续规则（v1.4）：completed/failed → sessions.auto_dequeue_next（failed
  时注入护栏提示给下一任务）；cancelled → 不接续（reducer 已暂停队列）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx

from agentcrew_core.events import RunEventType
from agentcrew_core.loop import (
    LoopDeps,
    LoopGates,
    context_fingerprint,
    env_block,
    run_task,
    user_text_message,
)
from agentcrew_core.provider.glm_anthropic import (
    DEFAULT_BASE_URL,
    GLMAnthropicProvider,
    SlotConfig,
)
from agentcrew_core.provider.types import ToolCall
from agentcrew_core.tools import ToolInvocation, ToolScheduler, WorkContext
from agentcrew_core.tools.metadata import ToolResult
from agentcrew_core.tools.scheduler import input_hash, redact_event_input

from ..bus import Subscription, Topic

if TYPE_CHECKING:
    from .approvals import ApprovalService
    from .db.database import Database
    from .db.event_store import EventStore
    from .questions import QuestionService
    from .sessions import SessionService
    from .settings import SettingsService

_log = logging.getLogger("agentcrew.run_manager")

SYSTEM_PROMPT = (
    "你是 AgentCrew 的数字员工，在用户的 macOS 电脑上本地执行任务。\n"
    "通过工具真实执行每一个步骤；需要用户澄清时使用 ask_user 提问；"
    "写操作会按权限体系弹审批，被拒绝时不要原样重试，改变方法或如实汇报。"
)

GUARDRAIL_TEXT = (
    "【系统提示】上一条指令已失败（原因：{reason}）。请确认是否仍需要执行"
    "本条指令；如不需要，请直接说明，不要执行。"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _Run:
    """一个运行中任务的监督状态（总线回调与看门狗共用）。"""

    task_run_id: str
    conversation_id: str
    task: asyncio.Task
    agent_id: str = ""
    last_progress: float = field(default_factory=time.monotonic)
    waiting: int = 0          # 挂起的审批/提问数（>0 时看门狗不计时）
    fail_reason: str | None = None  # 看门狗置 stalled 后再 cancel


class RunManager:
    def __init__(
        self,
        *,
        db: "Database",
        event_store: "EventStore",
        bus,
        settings: "SettingsService",
        sessions: "SessionService",
        approvals: "ApprovalService",
        scheduler: "ToolScheduler",
        questions: "QuestionService",
    ):
        self._db = db
        self._store = event_store
        self._bus = bus
        self._settings = settings
        self._sessions = sessions
        self._approvals = approvals
        self._scheduler = scheduler
        self._questions = questions
        self._runs: dict[str, _Run] = {}
        self._guardrails: dict[str, str] = {}  # task_id → 护栏提示（接续注入）
        self._shutting_down = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dispatch_sub: Subscription | None = None
        self._dispatch_task: asyncio.Task | None = None
        self._sem = asyncio.Semaphore(
            int(self._settings.config.values.get("gates", {}).get(
                "global_concurrency", 8)))
        self._http = httpx.AsyncClient(timeout=180.0)  # 全部 attempt 共享

    # ── 生命周期 ──────────────────────────────────────────────────

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._dispatch_sub = self._bus.subscribe(Topic("all"), internal=True)
        self._dispatch_task = self._loop.create_task(
            self._dispatch(), name="run-manager:dispatch")

    async def shutdown(self) -> None:
        """§6 优雅关闭：取消任务不写终态（C9 对账收敛为 interrupted）。"""
        self._shutting_down = True
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
        for run in list(self._runs.values()):
            run.task.cancel()
        if self._runs:
            await asyncio.wait(
                [r.task for r in list(self._runs.values())], timeout=5)
        if self._dispatch_sub is not None:
            self._bus.unsubscribe(self._dispatch_sub)
        await self._http.aclose()

    async def _dispatch(self) -> None:
        sub = self._dispatch_sub
        assert sub is not None
        sub.bind_consumer()
        while True:
            if sub.shutdown_requested:
                return
            item = sub.take_nowait()
            if item is None:
                await sub.wait_for_data()
                continue
            self._on_event(item)

    def _on_event(self, event) -> None:
        if event.type in (RunEventType.RUN_QUEUED, RunEventType.RUN_RESUMED):
            self._spawn(event.task_run_id, event.conversation_id)
            return
        run = self._runs.get(event.task_run_id or "")
        if run is None:
            return
        if event.type in (RunEventType.PERMISSION_REQUESTED,
                          RunEventType.QUESTION_REQUESTED):
            run.waiting += 1
        elif event.type in (RunEventType.PERMISSION_RESOLVED,
                            RunEventType.QUESTION_ANSWERED):
            run.waiting = max(0, run.waiting - 1)
            run.last_progress = time.monotonic()  # 决定即进展（§5）

    def _spawn(self, task_run_id: str, conversation_id: str) -> None:
        if self._shutting_down or task_run_id in self._runs:
            return
        task = self._loop.create_task(
            self._runner(task_run_id, conversation_id),
            name=f"run:{task_run_id[:12]}")
        self._runs[task_run_id] = _Run(
            task_run_id=task_run_id, conversation_id=conversation_id, task=task)
        task.add_done_callback(lambda _: self._runs.pop(task_run_id, None))

    # ── 配置绑定（v1.7：attempt 快照，PATCH 不影响本任务）──────────

    def _bind_gates(self) -> LoopGates:
        gates = self._settings.config.values.get("gates", {})
        return LoopGates(
            max_steps=int(gates.get("max_steps", 40)),
            stall_seconds=int(gates.get("stall_seconds", 600)),
            repeat_limit=int(gates.get("repeat_limit", 3)),
            token_budget=int(gates.get("token_budget", 2_000_000)),
        )

    def _bind_slot(self, slot: str) -> SlotConfig:
        entry = self._settings.config.values.get("models", {}).get(slot, {})
        return SlotConfig(
            model=entry.get("model", ""),
            api_key=entry.get("api_key", ""),
            base_url=entry.get("base_url") or DEFAULT_BASE_URL,
            max_tokens=int(entry.get("max_tokens", 4096)),
        )

    @staticmethod
    def _model_config(cfg: SlotConfig) -> dict[str, Any]:
        """fingerprint 的 model_config——绝不放明文 key（只有摘要）。"""
        return {
            "model": cfg.model,
            "base_url": cfg.base_url,
            "max_tokens": cfg.max_tokens,
            "api_key_sha256": input_hash(
                {"api_key": cfg.api_key})[:16],
        }

    # ── runner（每 TaskRun 一个协程）──────────────────────────────

    async def _emit(self, task_run_id: str, conversation_id: str,
                    event_type: str, payload: dict,
                    attempt_no: int | None = 1):
        return await self._store.append(
            task_run_id=task_run_id, conversation_id=conversation_id,
            type=RunEventType(event_type), payload=payload,
            attempt_no=attempt_no,
        )

    async def _runner(self, task_run_id: str, conversation_id: str) -> None:
        run = self._runs[task_run_id]
        gates = self._bind_gates()
        await self._sem.acquire()
        watchdog: asyncio.Task | None = None
        terminal: str | None = None
        fail_reason = ""
        try:
            row = await asyncio.to_thread(
                self._task_row, task_run_id)
            if row is None or row["status"] != "queued":
                _log.warning("run.skip task=%s status=%s（非 queued 不启动）",
                             task_run_id, row["status"] if row else "缺失")
                return
            conv = await asyncio.to_thread(
                self._sessions.conversation_or_404, conversation_id)
            run.agent_id = conv["agent_id"]

            # ── attempt 装配：配置/环境块/工具 schema 全部在此刻绑定 ──
            slot = self._bind_slot("main")
            provider = GLMAnthropicProvider(
                {"main": slot}, client=self._http)
            ctx = await asyncio.to_thread(
                self._sessions.build_work_context, conversation_id,
                task_run_id)
            ctx.ask_resolver = self._questions.make_resolver(
                task_run_id, conversation_id)

            async def sink(event_type: str, payload: dict) -> None:
                await self._emit(task_run_id, conversation_id,
                                 event_type, payload)

            ctx.emit = sink
            tools = self._scheduler.registry.schemas()
            materials_dir = self._sessions.scope_of(conv)["materials_dir"]
            folders = [f["path"] for f in json.loads(conv["folders_json"] or "[]")]
            system = SYSTEM_PROMPT + "\n\n" + env_block(
                datetime.now(), str(ctx.cwd), materials_dir, folders,
                conv["workspace_id"])
            fingerprint = context_fingerprint(
                system, tools, self._model_config(slot))

            attempt_id = uuid.uuid4().hex
            await self._emit(task_run_id, conversation_id, "run.started", {
                "attempt_no": 1, "attempt_id": attempt_id,
                "kind": "initial", "context_fingerprint": fingerprint,
            })
            watchdog = self._loop.create_task(
                self._watchdog(run, gates), name=f"watchdog:{task_run_id[:12]}")

            messages = await asyncio.to_thread(
                self._history_messages, conversation_id)
            instruction = row["instruction"]
            guardrail = self._guardrails.pop(task_run_id, None)
            messages.append(user_text_message(
                instruction + (f"\n\n{guardrail}" if guardrail else "")))

            async def execute(call: ToolCall) -> ToolResult:
                if call.name == "ask_user":
                    return await self._ask_user_direct(
                        sink, ctx, call)
                return await self._approvals.run_tool(
                    task_run_id=task_run_id, conversation_id=conversation_id,
                    agent_id=run.agent_id,
                    invocation=ToolInvocation(
                        call_id=call.id, name=call.name, input=call.input),
                    ctx=ctx)

            deps = LoopDeps(
                request=lambda: provider.stream(
                    "main", messages, tools, system=system),
                execute=execute, emit=sink,
                on_progress=lambda: setattr(
                    run, "last_progress", time.monotonic()),
                gates=gates, model=slot.model,
            )
            result = await run_task(messages, deps)
            if result.status == "completed":
                terminal = "completed"
                await self._emit(task_run_id, conversation_id,
                                 "run.completed", {
                                     "final_text": result.final_text,
                                     "outcome": "completed"})
            else:
                terminal = "failed"
                fail_reason = result.reason
                await self._emit(task_run_id, conversation_id,
                                 "run.failed", {"reason": result.reason})
        except asyncio.CancelledError:
            # 终态时序：进入此处时模型流已 aclose、工具批已收割（run_task
            # 的 finally/TaskGroup 保证）；之后才落终态事件
            if self._shutting_down:
                raise  # §6：优雅关闭不写终态（C9 对账域）
            try:
                if run.fail_reason:
                    terminal = "failed"
                    fail_reason = run.fail_reason
                    await self._emit(task_run_id, conversation_id,
                                     "run.failed",
                                     {"reason": run.fail_reason})
                else:
                    terminal = "cancelled"
                    await self._emit(task_run_id, conversation_id,
                                     "run.cancelled", {})
                    # 队列非空 → 补发 queue.paused（§4：reducer 语义的
                    # 持久化投影，C7 约定 run.cancelled + queue.paused 成对）
                    if await asyncio.to_thread(
                            self._has_queued_items, conversation_id):
                        await self._store.append(
                            task_run_id=None,
                            conversation_id=conversation_id,
                            type=RunEventType.QUEUE_PAUSED, payload={})
            finally:
                raise
        except BaseException as e:  # noqa: BLE001 —— asyncio 任务异常→RUN_FAILED
            _log.exception("run.crash task=%s", task_run_id)
            terminal = "failed"
            fail_reason = f"internal:{type(e).__name__}"
            try:
                await self._emit(task_run_id, conversation_id, "run.failed",
                                 {"reason": fail_reason})
            except Exception:  # noqa: BLE001 —— 终态写入失败如实记录
                _log.exception("run.final_write_failed task=%s", task_run_id)
        finally:
            if watchdog is not None:
                watchdog.cancel()
            self._sem.release()
            if terminal == "failed":
                # v1.4：failed 也自动接续，注入护栏提示（§4）
                await self._maybe_continue(
                    conversation_id,
                    GUARDRAIL_TEXT.format(reason=fail_reason or "unknown"))
            elif terminal == "completed":
                await self._maybe_continue(conversation_id)

    async def _watchdog(self, run: _Run, gates: LoopGates) -> None:
        interval = max(1.0, min(5.0, gates.stall_seconds / 10))
        while True:
            await asyncio.sleep(interval)
            if run.waiting > 0:
                run.last_progress = time.monotonic()  # 等用户时不计时
                continue
            if time.monotonic() - run.last_progress > gates.stall_seconds:
                _log.warning("run.stalled task=%s（>%ss 无进展）",
                             run.task_run_id, gates.stall_seconds)
                run.fail_reason = "stalled"
                run.task.cancel()
                return

    async def _maybe_continue(self, conversation_id: str,
                              guardrail: str | None = None) -> None:
        if self._shutting_down:
            return
        try:
            # 护栏在事件发布前、写线程内挂上（before_publish）——杜绝
            # "新 runner 先取走 guardrail"的派发竞态
            task_id = await self._sessions.auto_dequeue_next(
                conversation_id,
                before_publish=(lambda tid: self._guardrails.__setitem__(
                    tid, guardrail)) if guardrail is not None else None)
        except Exception:  # noqa: BLE001 —— 接续失败不拖垮已终态的任务
            _log.exception("run.continue_failed conversation=%s", conversation_id)
            return

    # ── ask_user 直通（S07：独立生命周期，不进调度器）──────────────

    async def _ask_user_direct(self, sink, ctx: WorkContext,
                               call: ToolCall) -> ToolResult:
        """ask_user 是交互原语：不走调度器（不占并发名额、无 60s 超时）；
        三态账本事件（prepared/dispatched/completed/failed）照发保持一致。
        取消时账本停在 dispatched、库中留下未回答状态（question.requested
        无 answered），属 C9 对账域。"""
        tool = self._scheduler.registry.get("ask_user")
        assert tool is not None, "注册表缺 ask_user"
        meta = tool.metadata
        await sink("tool.prepared", {
            "call_id": call.id, "tool_name": meta.name,
            "side_effect_class": meta.side_effect_class,
            "input_hash": input_hash(call.input),
            "risk_level": meta.risk_level,
            "input": redact_event_input(meta.name, call.input),
        })
        await sink("tool.dispatched", {"call_id": call.id})
        result = await tool.execute(
            ToolInvocation(call_id=call.id, name=call.name, input=call.input),
            ctx)
        payload = {"call_id": call.id, "output": result.output or "",
                   "output_summary": (result.output or "")[:2000]}
        if not result.ok:
            payload["error"] = result.error
        await sink("tool.completed" if result.ok else "tool.failed", payload)
        return result

    # ── 库读取 ────────────────────────────────────────────────────

    def _task_row(self, task_run_id: str):
        return self._db.read_conn.execute(
            "SELECT id, conversation_id, instruction, status FROM task_runs"
            " WHERE id = ?", (task_run_id,)).fetchone()

    def _history_messages(self, conversation_id: str) -> list[dict]:
        """同会话已完成任务的（指令, 最终回复）对——上下文连续（v1.7：模型
        本可见此前交换）；失败/取消的任务不进上下文，由护栏提示显式化。"""
        rows = self._db.read_conn.execute(
            "SELECT id, instruction FROM task_runs"
            " WHERE conversation_id = ? AND status = 'completed'"
            " ORDER BY created_at", (conversation_id,)).fetchall()
        messages: list[dict] = []
        for task_id, instruction in rows:
            reply = self._db.read_conn.execute(
                "SELECT content FROM messages"
                " WHERE task_run_id = ? AND role = 'assistant'"
                " ORDER BY created_at DESC LIMIT 1", (task_id,)).fetchone()
            messages.append(user_text_message(instruction))
            if reply and reply[0]:
                messages.append({"role": "assistant",
                                 "content": [{"type": "text",
                                              "text": reply[0]}]})
        return messages

    # ── 取消（API 入口）───────────────────────────────────────────

    async def request_cancel(self, task_run_id: str) -> dict:
        row = await asyncio.to_thread(self._task_row, task_run_id)
        if row is None:
            raise LookupError(f"任务不存在：{task_run_id}")
        if row["status"] in ("completed", "failed", "cancelled",
                             "interrupted"):
            return {"status": row["status"], "already_terminal": True}
        run = self._runs.get(task_run_id)
        if run is not None:
            run.task.cancel()  # runner 落 run.cancelled（reducer 置队列暂停）
            return {"status": "cancelling"}
        # 无 runner（如启动前遗留的 queued 任务）：直接落终态（+ 队列暂停）
        await self._emit(task_run_id, row["conversation_id"],
                         "run.cancelled", {})
        if await asyncio.to_thread(
                self._has_queued_items, row["conversation_id"]):
            await self._store.append(
                task_run_id=None, conversation_id=row["conversation_id"],
                type=RunEventType.QUEUE_PAUSED, payload={})
        return {"status": "cancelled"}

    def _has_queued_items(self, conversation_id: str) -> bool:
        row = self._db.read_conn.execute(
            "SELECT pending_queue FROM conversations WHERE id = ?",
            (conversation_id,)).fetchone()
        if row is None or not row[0]:
            return False
        return any(item.get("state") == "queued"
                   for item in json.loads(row[0]))

    def run_count(self) -> int:
        return len(self._runs)
