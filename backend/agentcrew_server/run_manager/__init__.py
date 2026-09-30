"""RunManager（M0-C8/C9）：每 TaskRun 一个 asyncio 任务、注册表与取消传播。

- 启动：订阅总线（internal，不占 SSE 名额）监听 run.queued / run.resumed →
  派发 runner（resume 自 C9：_runner 跳过 run.started、上下文由
  RecoveryService 重放重建 + 副作用账本注入、attempt_no 与回合号跨 attempt
  续接——steps 唯一键与回合上限都按任务计）；全局并发 FIFO
  （gates.global_concurrency，信号量）；
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
from agentcrew_core.tools.scheduler import (
    INVALID_PARAMS,
    input_hash,
    redact_event_input,
    validate_tool_input,
)

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
    # 名额持有标记：ask_user 等待期间释放全局名额（欠账#1），终态收尾
    # 只在持有时释放一次（防双释放）
    sem_held: bool = False
    # 终态发射闸（外审二轮 F2）：终态写入**提交前**置位——取消落在
    # _finish 的 await 期间时，取消处理器据此放行在途写入、绝不补发
    # 第二条终态（双终态会让 replay 在 idle 上抛 InvalidTransition，
    # 会话 FSM 永久损坏）
    terminal_inflight: bool = False


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
        self._recovery = None  # C9：RecoveryService（cli 装配后注回，resume 重建用）
        self._shutting_down = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dispatch_sub: Subscription | None = None
        self._dispatch_task: asyncio.Task | None = None
        self._sem = asyncio.Semaphore(
            int(self._settings.config.values.get("gates", {}).get(
                "global_concurrency", 8)))
        self._http = httpx.AsyncClient(timeout=180.0)  # 全部 attempt 共享

    # ── 生命周期 ──────────────────────────────────────────────────

    def wire(self, recovery) -> None:
        """C9：RecoveryService 注回（resume runner 的上下文重建）。"""
        self._recovery = recovery

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
            self._spawn(event.task_run_id, event.conversation_id,
                        resume=event.type == RunEventType.RUN_RESUMED)
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

    def _spawn(self, task_run_id: str, conversation_id: str,
               *, resume: bool = False) -> None:
        if self._shutting_down or task_run_id in self._runs:
            return
        task = self._loop.create_task(
            self._runner(task_run_id, conversation_id, resume=resume),
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

    async def _assemble_attempt(self, conversation_id: str, task_run_id: str):
        """attempt 装配：配置/环境块/工具 schema 在此刻绑定（v1.7 配置
        快照 + fingerprint）。initial 与 resume 共用（start_resume 发
        run.resumed 前取同一份指纹）。DB 读取全在线程内。"""

        def build_all():
            slot = self._bind_slot("main")
            conv = self._sessions.conversation_or_404(conversation_id)
            scope = self._sessions.scope_of(conv)
            folders = [f["path"] for f in
                       json.loads(conv["folders_json"] or "[]")]
            tools = self._scheduler.registry.schemas()
            system = SYSTEM_PROMPT + "\n\n" + env_block(
                datetime.now(), scope["workspace_dir"],
                scope["materials_dir"], folders, conv["workspace_id"])
            return (slot, tools, system, conv["agent_id"],
                    context_fingerprint(system, tools,
                                        self._model_config(slot)))

        return await asyncio.to_thread(build_all)

    async def start_resume(self, task_run_id: str, conversation_id: str,
                           resume_reason: str, attempt_no: int) -> None:
        """C9：发 run.resumed（含与 run.started 同源的指纹）→ 总线派发
        resume runner。校验已由 RecoveryService.resume 完成。"""
        _slot, _tools, _system, _agent, fingerprint = \
            await self._assemble_attempt(conversation_id, task_run_id)
        await self._emit(task_run_id, conversation_id, "run.resumed", {
            "attempt_no": attempt_no,
            "attempt_id": uuid.uuid4().hex,
            "resume_reason": resume_reason,
            "context_fingerprint": fingerprint,
        }, attempt_no=attempt_no)

    async def _runner(self, task_run_id: str, conversation_id: str,
                      *, resume: bool = False) -> None:
        run = self._runs[task_run_id]
        watchdog: asyncio.Task | None = None
        terminal: str | None = None
        fail_reason = ""
        attempt_no = 1
        try:
            # 名额等待纳入取消处理覆盖范围（外审致命①）：等待期被
            # request_cancel 取消同样走统一终态（run.cancelled 成对落库）
            await self._sem.acquire()
            run.sem_held = True
            # 配置绑定在取得名额之后（外审建议④）：等待期间 PATCH 的
            # gates/models 一并被本次 attempt 吸收，指纹与行为一致
            gates = self._bind_gates()
            expected = "running" if resume else "queued"  # resumed 投影已置 running
            row = await asyncio.to_thread(self._task_row, task_run_id)
            if row is None or row["status"] != expected:
                _log.warning("run.skip task=%s status=%s（非 %s 不启动）",
                             task_run_id, row["status"] if row else "缺失",
                             expected)
                return
            conv = await asyncio.to_thread(
                self._sessions.conversation_or_404, conversation_id)
            run.agent_id = conv["agent_id"]

            # ── attempt 装配：配置/环境块/工具 schema 全部在此刻绑定 ──
            slot, tools, system, _agent_id, fingerprint = \
                await self._assemble_attempt(conversation_id, task_run_id)
            provider = GLMAnthropicProvider({"main": slot}, client=self._http)
            ctx = await asyncio.to_thread(
                self._sessions.build_work_context, conversation_id,
                task_run_id)
            ctx.ask_resolver = self._questions.make_resolver(
                task_run_id, conversation_id)

            async def sink(event_type: str, payload: dict) -> None:
                await self._emit(task_run_id, conversation_id,
                                 event_type, payload, attempt_no=attempt_no)

            ctx.emit = sink

            if resume:
                # C9 §7：attempt 行已由 run.resumed 投影建立，不重发
                # run.started；上下文从事件重放重建 + 副作用账本注入
                attempt_no = row["current_attempt_no"] or 2
                replay, ledger = await asyncio.to_thread(
                    self._recovery.rebuild_for_resume, task_run_id)
                for warning in replay.warnings:
                    _log.warning("resume.replay_warning task=%s %s",
                                 task_run_id, warning)
                messages = replay.messages
                system = system + "\n\n" + ledger
            else:
                attempt_id = uuid.uuid4().hex
                await self._emit(task_run_id, conversation_id, "run.started", {
                    "attempt_no": 1, "attempt_id": attempt_id,
                    "kind": "initial", "context_fingerprint": fingerprint,
                })
                messages = await asyncio.to_thread(
                    self._history_messages, conversation_id)
                instruction = row["instruction"]
                guardrail = self._guardrails.pop(task_run_id, None)
                messages.append(user_text_message(
                    instruction + (f"\n\n{guardrail}" if guardrail else "")))

            # 停滞计时从 attempt 生效重置起（外审建议④）：等名额耗时
            # 不计入窗口，启动即误判 stalled
            run.last_progress = time.monotonic()
            watchdog = self._loop.create_task(
                self._watchdog(run, gates), name=f"watchdog:{task_run_id[:12]}")

            async def execute(call: ToolCall) -> ToolResult:
                if call.name == "ask_user":
                    return await self._ask_user_direct(
                        run, sink, ctx, call)
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
                start_ordinal=(await asyncio.to_thread(
                    self._next_ordinal, task_run_id) if resume else 1),
            )
            result = await run_task(messages, deps)
            if result.status == "completed":
                terminal = "completed"
                await self._finish(task_run_id, conversation_id,
                                   "run.completed",
                                   {"final_text": result.final_text,
                                    "outcome": "completed"}, run=run,
                                   attempt_no=attempt_no)
            else:
                terminal = "failed"
                fail_reason = result.reason
                await self._finish(task_run_id, conversation_id,
                                   "run.failed", {"reason": result.reason},
                                   run=run, attempt_no=attempt_no)
        except asyncio.CancelledError:
            # 终态时序：进入此处时模型流已 aclose、工具批已收割（run_task
            # 的 finally/TaskGroup 保证）；之后才结清并落终态事件
            if self._shutting_down:
                raise  # §6：优雅关闭不写终态（C9 对账域）
            # 已接收取消：uncancel 让终态写入的 await 正常执行（否则
            # 挂起的取消请求会在下一个 await 立即重抛——等待名额期取消
            # 也走这里，外审致命①）；finally 里显式重抛恢复取消语义
            asyncio.current_task().uncancel()
            try:
                if run.terminal_inflight:
                    # F2：终态 job 已提交写通道（闭包提交+发布不受协程
                    # 取消影响）——等待其落库后放行（terminal 保持
                    # completed/failed，接续语义不受取消影响）；等待超时
                    # 只告警不发第二条：宁缺终态交 C9 对账收敛，不要双
                    # 终态把会话 replay 打死
                    if not await self._await_terminal(task_run_id):
                        _log.error(
                            "run.terminal_inflight_timeout task=%s"
                            "（在途终态未观测到落库，交 C9 对账）", task_run_id)
                else:
                    await self._settle_dispatched(task_run_id,
                                                  conversation_id)
                    if run.fail_reason:
                        terminal = "failed"
                        fail_reason = run.fail_reason
                        await self._finish(task_run_id, conversation_id,
                                           "run.failed",
                                           {"reason": run.fail_reason}, run=run,
                                           attempt_no=attempt_no)
                    else:
                        terminal = "cancelled"
                        # 成对终态（run.cancelled + 队列非空时 queue.paused）
                        # 单事务提交且与 continue_queue 同锁——消除"两事件
                        # 之间点继续被晚到 paused 再次暂停"竞态（外审建议⑤）；
                        # 同样先置闸（本处理器 await 期间再被取消时防重入双发）
                        run.terminal_inflight = True
                        await self._sessions.emit_cancel_pair(
                            conversation_id, task_run_id, attempt_no=attempt_no)
            finally:
                raise
        except BaseException as e:  # noqa: BLE001 —— asyncio 任务异常→RUN_FAILED
            _log.exception("run.crash task=%s", task_run_id)
            terminal = "failed"
            fail_reason = f"internal:{type(e).__name__}"
            try:
                # F2 防线：兜底补发前查终态事件是否已在库
                if not await self._terminal_event_exists(task_run_id):
                    await self._finish(task_run_id, conversation_id,
                                       "run.failed", {"reason": fail_reason},
                                       run=run, attempt_no=attempt_no)
                else:
                    _log.info("run.terminal_already_present task=%s"
                              "（兜底路径不再补发）", task_run_id)
            except Exception:  # noqa: BLE001 —— 终态写入失败如实记录
                _log.exception("run.final_write_failed task=%s", task_run_id)
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if run.sem_held:
                self._sem.release()
                run.sem_held = False
            if terminal == "failed":
                # v1.4：failed 也自动接续，注入护栏提示（§4）
                await self._maybe_continue(
                    conversation_id,
                    GUARDRAIL_TEXT.format(reason=fail_reason or "unknown"))
            elif terminal == "completed":
                await self._maybe_continue(conversation_id)

    async def _finish(self, task_run_id: str, conversation_id: str,
                      event_type: str, payload: dict,
                      run: "_Run | None" = None,
                      attempt_no: int = 1) -> None:
        """统一终态出口（外审致命②）：终态事件写入前，先结清本任务
        dispatched 无结果的调用（含结果事件落库失败的 record_failed——
        事件没落库，投影行停在 dispatched，同被此扫描覆盖）转
        tool.pending_verification 落库；终态事件是"执行真正结束"的
        证据（v1.7 契约③）。进程崩溃后的启动对账归 C9，此处只管
        运行中任务的终态前结清。
        run.terminal_inflight 在终态 job 提交前置位（F2 闸）；终态 emit
        套 shield——排队中的写通道 job 会被协程取消连坐（executor
        future 未开始时可被 cancel），shield 后取消只打断 await、job
        必然落库，取消处理器等待即得、绝不覆写。"""
        await self._settle_dispatched(task_run_id, conversation_id)
        if run is not None:
            run.terminal_inflight = True
        await asyncio.shield(
            self._emit(task_run_id, conversation_id, event_type, payload,
                       attempt_no=attempt_no))

    async def _settle_dispatched(self, task_run_id: str,
                                 conversation_id: str) -> None:
        rows = await asyncio.to_thread(self._dispatched_calls, task_run_id)
        for call_id in rows:
            await self._emit(task_run_id, conversation_id,
                             "tool.pending_verification",
                             {"call_id": call_id})

    # ── F2 终态发射闸的观测件 ─────────────────────────────────────

    async def _await_terminal(self, task_run_id: str,
                              timeout: float = 5.0) -> bool:
        """等待在途终态落库（F2：取消撞终态提交窗口时，job 已提交写通道，
        串行执行后必然落库）。返回是否观测到；超时=写通道故障，交 C9。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self._terminal_event_exists(task_run_id):
                return True
            await asyncio.sleep(0.05)
        return await self._terminal_event_exists(task_run_id)

    async def _terminal_event_exists(self, task_run_id: str) -> bool:
        row = await asyncio.to_thread(self._terminal_row, task_run_id)
        return row is not None

    def _terminal_row(self, task_run_id: str):
        return self._db.read_conn.execute(
            "SELECT 1 FROM run_events WHERE task_run_id = ?"
            " AND type IN ('run.completed', 'run.failed', 'run.cancelled')"
            " LIMIT 1", (task_run_id,)).fetchone()

    def _dispatched_calls(self, task_run_id: str) -> list[str]:
        """终态前待结清的调用。ask_user 豁免：交互原语无副作用可核验，
        取消时的已知结局就是"提问未获回答"（账本停 dispatched + 库中
        未回答状态，C9 按此合成占位 tool_result）——若转待核验会把
        "停止后继续队列"永久堵死（验证提交 API 属 C9）。"""
        return [r[0] for r in self._db.read_conn.execute(
            "SELECT call_id FROM tool_calls"
            " WHERE task_run_id = ? AND status = 'dispatched'"
            " AND tool_name != 'ask_user'"
            " ORDER BY prepared_at", (task_run_id,)).fetchall()]

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

    async def _ask_user_direct(self, run: _Run, sink, ctx: WorkContext,
                               call: ToolCall) -> ToolResult:
        """ask_user 是交互原语：不走调度器（无 60s 超时、不占工具 4 名额），
        三态账本事件（prepared/dispatched/completed/failed）照发保持一致。
        欠账#1（外审回稿）：等待用户回答前**释放全局并发名额**、收到回答后
        重新取得（asyncio.Semaphore FIFO 公平）——8 个挂起的提问不再饿死
        其他任务；等待中取消则名额已还、由终态收尾按 sem_held 跳过释放。
        取消时账本停在 dispatched、库中留下未回答状态，属 C9 对账域。"""
        tool = self._scheduler.registry.get("ask_user")
        assert tool is not None, "注册表缺 ask_user"
        meta = tool.metadata
        invalid = validate_tool_input(meta.parameters, call.input)
        if invalid is not None:
            await sink("tool.prepared", {
                "call_id": call.id, "tool_name": meta.name,
                "side_effect_class": meta.side_effect_class,
                "input_hash": input_hash(call.input),
                "risk_level": meta.risk_level,
                "input": redact_event_input(meta.name, call.input),
            })
            await sink("tool.failed", {"call_id": call.id,
                                       "error": INVALID_PARAMS,
                                       "output_summary": ""})
            return ToolResult(ok=False, error=INVALID_PARAMS,
                              details={"message": invalid})
        await sink("tool.prepared", {
            "call_id": call.id, "tool_name": meta.name,
            "side_effect_class": meta.side_effect_class,
            "input_hash": input_hash(call.input),
            "risk_level": meta.risk_level,
            "input": redact_event_input(meta.name, call.input),
        })
        await sink("tool.dispatched", {"call_id": call.id})
        self._sem.release()      # 等用户不占全局名额（欠账#1）
        run.sem_held = False
        try:
            result = await tool.execute(
                ToolInvocation(call_id=call.id, name=call.name,
                               input=call.input), ctx)
        except BaseException:
            raise  # 取消/异常：名额已还，终态收尾不再重复释放
        await self._sem.acquire()  # 回答后重取（FIFO 公平）
        run.sem_held = True
        payload = {"call_id": call.id, "output": result.output or "",
                   "output_summary": (result.output or "")[:2000]}
        if not result.ok:
            payload["error"] = result.error
        await sink("tool.completed" if result.ok else "tool.failed", payload)
        return result

    # ── 库读取 ────────────────────────────────────────────────────

    def _task_row(self, task_run_id: str):
        return self._db.read_conn.execute(
            "SELECT id, conversation_id, instruction, status,"
            " current_attempt_no FROM task_runs"
            " WHERE id = ?", (task_run_id,)).fetchone()

    def _next_ordinal(self, task_run_id: str) -> int:
        """resume 续接回合号：steps(task_run_id, ordinal) 唯一，且回合
        上限按任务计（重置 = 预算翻倍）。"""
        row = self._db.read_conn.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM steps"
            " WHERE task_run_id = ?", (task_run_id,)).fetchone()
        return int(row[0])

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
        if row["status"] in ("completed", "failed", "cancelled"):
            return {"status": row["status"], "already_terminal": True}
        run = self._runs.get(task_run_id)
        if run is not None:
            run.task.cancel()  # runner 落 run.cancelled（reducer 置队列暂停）
            return {"status": "cancelling"}
        # 无 runner：queued（启动前遗留）/ interrupted / waiting_verification
        # （重启对账后，C9 显式放弃）——成对终态经会话锁单事务落库（含
        # 队列非空时的 queue.paused，外审建议⑤）；reducer 自 C9 起
        # run.cancelled 在 idle 下合法（对账后 FSM 已回 idle）
        await self._sessions.emit_cancel_pair(row["conversation_id"],
                                              task_run_id)
        return {"status": "cancelled"}

    def run_count(self) -> int:
        return len(self._runs)
