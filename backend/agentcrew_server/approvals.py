"""审批闸门服务（M0-C6，governance §2.3 三级闸门 / harness-session §6.2）。

事件序列（§6.2）：TOOL_PREPARED（落库）→ [闸门：PERMISSION_REQUESTED →
挂起等人工决定 → PERMISSION_RESOLVED] → TOOL_DISPATCHED → 执行 →
TOOL_COMPLETED/FAILED。所有事件经 EventStore 落库（与投影同事务）并扇出
SSE；每次决定写 audit_log 哈希链；allow_always / reject_always 按
always_scope_preview 的范围写 agent_permission_rules。

决定提交语义（v1.1）：同决定重试 → 200 幂等返回首次结果；不同决定 →
409 APPROVAL_STALE；携带的 input_hash 与当前调用不一致 → 409（防"批的是
A 执行的是 B"）；call_id 无 REQUESTED 记录 → 404。进程重启后 Future 丢失
但 pending 事件在库——未处理决定因活动执行方消失而过期。

外审回稿（C6 第三轮）写入纪律：
- 一切库写入（事件、审计、规则、链头快照）只经 WriteChannel 单写线程——
  绕开写通道直接用 write_conn 会与写线程的事务冲突（cannot start a
  transaction within a transaction）；
- 首次决定 = 查重 + 决定事件 + 投影 + 审计 +（可选）规则，**单事务提交**：
  并发提交同一审批只产生一个决定；任一环节失败整体回滚，幂等重试可完整
  补齐（此前审计失败后事件已落库，重试幂等返回但规则永远缺失）；
- 审计覆盖 governance §3 必入链动作：闸门放行（自动/规则）、审批请求与
  四种决定、规则写入、risk≥medium 工具的完成与失败；detail 带 input_hash。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentcrew_core.events import RunEventType
from agentcrew_core.tools import (
    ToolInvocation,
    ToolMetadata,
    ToolScheduler,
    WorkContext,
    always_scope_pattern,
    approval_target,
    evaluate_gate,
)
from agentcrew_core.tools.gate import PermissionRule
from agentcrew_core.tools.gate import GateResult
from agentcrew_core.tools.builtin.externalize import _check_path
from agentcrew_core.tools.judgment import bash_readonly, filesystem_boundary, normalize_filesystem
from agentcrew_core.tools.scheduler import input_hash

from .db.audit import SNAPSHOT_EVERY, append_audit, snapshot_chain_head
from .db.database import Database
from .db.event_store import EventStore

_log = logging.getLogger("agentcrew.approvals")

DECISIONS = ("allow_once", "allow_always", "reject_once", "reject_always")


class ApprovalNotFound(LookupError):
    pass


class ApprovalStale(RuntimeError):
    pass


@dataclass
class _Pending:
    future: asyncio.Future
    loop: asyncio.AbstractEventLoop  # future 所属事件循环（跨线程唤醒用）
    input_hash: str
    tool_name: str
    task_run_id: str
    conversation_id: str
    agent_id: str
    ctx: WorkContext | None = None  # 闸门放行 http_request 时登记本次授权
    attempt_no: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detail(**kw) -> str:
    return json.dumps(
        {k: v for k, v in kw.items() if v is not None},
        ensure_ascii=False, sort_keys=True,
    )


class ApprovalService:
    def __init__(self, db: Database, event_store: EventStore,
                 chain_head_path: Path):
        self._db = db
        self._store = event_store
        self._channel = event_store.channel  # 唯一写通道（含审计/规则/快照）
        self._chain_head_path = chain_head_path
        event_store.audit_anchor_path = chain_head_path
        self._pending: dict[str, _Pending] = {}
        self.scheduler: ToolScheduler | None = None  # cli 装配后注入

    # ── 闸门（调度器挂钩，ask 在此挂起）──────────────────────────
    async def gate(self, invocation: ToolInvocation, meta: ToolMetadata,
                   readonly_verdict: bool) -> str | GateResult:
        # task_run_id 由 run_tool 的调用链经 invocation 侧上下文携带——
        # 闸门挂钩本身拿不到 WorkContext，改从 pending 上下文注册表按
        # call_id 取（run_tool 先注册再执行）
        ctx_info = self._context_for(invocation.call_id)
        ih = input_hash(invocation.input)
        cwd = ctx_info.ctx.cwd if ctx_info.ctx is not None else None
        rules = await asyncio.to_thread(self._load_rules, ctx_info.agent_id)
        boundary = self._file_boundary(invocation, ctx_info.ctx)
        result = GateResult("deny", boundary) if boundary else evaluate_gate(
            meta, invocation.input, readonly_verdict, rules, ctx_info.agent_id, cwd)
        automation = self.automation.current_gate(ctx_info.task_run_id, meta, invocation.input, readonly_verdict, rules, cwd) if hasattr(self, "automation") else None
        if automation is not None:
            result = GateResult("deny", boundary) if boundary else automation
        confirmed_replay = False
        if meta.name == "schedule_task" and result.action == "ask":
            prior = self.proposals.prior_decision(ctx_info, invocation.call_id, invocation.input)
            if prior is not None:
                result = GateResult(prior, "本次调用读取持久真人决定")
                confirmed_replay = prior == "allow"
        if result.action == "allow":
            # governance §3：闸门放行（自动/规则命中）全部入审计链
            action = ("permission.confirmed_replay" if confirmed_replay else "permission.pre_authorized_pass" if automation is not None and result.reason.startswith("pre_authorized_pass") else "permission.rule_allowed" if result.matched_pattern
                      else "permission.auto_allowed")
            await self._audit("system", "gate", action, "tool_call",
                              invocation.call_id,
                              {"tool": meta.name, "input_hash": ih,
                               "reason": result.reason,
                               "pattern": result.matched_pattern,
                               "agent_id": ctx_info.agent_id})
            self._mark_http_approved(invocation, meta, ctx_info)
            return "allow"
        if result.action == "deny":
            await self._audit("system", "gate",
                              f"permission.denied:{result.reason}",
                              "tool_call", invocation.call_id,
                              {"tool": meta.name, "input_hash": ih,
                               "pattern": result.matched_pattern,
                               "agent_id": ctx_info.agent_id})
            return result if automation is not None or meta.requires_human_confirmation else "deny"
        # ask：发卡（含四选项/input_hash/target/范围预览）→ 挂起。
        # 请求事件与请求审计同事务（governance §2.3"全部进审计链"）
        pattern = always_scope_pattern(meta.name, invocation.input, cwd)
        proposal = self.proposals.prepare(ctx_info, invocation.input) if meta.name == "schedule_task" else None

        def _audit_requested(conn, event):
            append_audit(
                conn, ts=_now(), actor_type="system", actor_id="gate",
                action="permission.requested", resource_type="tool_call",
                resource_id=invocation.call_id,
                detail=_detail(tool=meta.name, input_hash=ih,
                               agent_id=ctx_info.agent_id, target=pattern),
            )
            if proposal is not None:
                self.proposals.register_in_tx(conn, ctx_info, invocation.call_id, proposal)

        await self._store.append(
            task_run_id=ctx_info.task_run_id,
            conversation_id=ctx_info.conversation_id,
            attempt_no=ctx_info.attempt_no,
            type=RunEventType.PERMISSION_REQUESTED,
            payload={
                "tool_call_id": invocation.call_id,
                "tool": meta.name,
                "risk": meta.risk_level,
                "options": ["allow_once", "reject_once"] if proposal is not None else list(DECISIONS),
                "input_hash": ih,
                "target": approval_target(meta.name, invocation.input, cwd),
                "always_scope_preview": pattern,
                **({"proposal_id": invocation.call_id} if proposal is not None else {}),
            },
            extra_writes=_audit_requested,
        )
        decision = await ctx_info.future
        if decision.startswith("allow"):
            self._mark_http_approved(invocation, meta, ctx_info)
            return "allow"
        return "deny"

    def _mark_http_approved(self, invocation: ToolInvocation,
                            meta: ToolMetadata, ctx_info: _Pending) -> None:
        """记录本次批准身份，连接器范围由执行服务独立检查。"""
        if meta.name == "http_request" and ctx_info.ctx is not None:
            ctx_info.ctx.approved_calls.add(invocation.call_id)

    # ── 决定提交（API 入口）──────────────────────────────────────
    async def submit(self, call_id: str, decision: str,
                     client_input_hash: str | None = None, request_identity=None, proposal_decision=None) -> dict[str, Any]:
        if decision not in DECISIONS:
            raise ApprovalStale(f"非法决定：{decision}")
        requested = await asyncio.to_thread(self._find_request, call_id)
        if requested is None:
            raise ApprovalNotFound(call_id)
        task_run_id, conversation_id, payload = requested
        if payload.get("tool") == "schedule_task" and proposal_decision is None:
            raise ApprovalStale("计划创建必须使用绑定提案及范围的真人决定接口")
        if client_input_hash is not None \
                and client_input_hash != payload.get("input_hash"):
            raise ApprovalStale("input_hash 与当前调用不一致（审批卡已过期）")
        agent_id = await asyncio.to_thread(
            self._agent_of_conversation, conversation_id)
        # 单写线程内的单事务：查重 → 决定事件+投影 → 审计 →（可选）规则。
        # 并发提交在此天然串行——一个 call_id 只会有一个首次决定。
        outcome = await self._channel.execute(
            lambda conn: self._decide_tx(
                conn, call_id=call_id, decision=decision,
                task_run_id=task_run_id, conversation_id=conversation_id,
                payload=payload, agent_id=agent_id, request_identity=request_identity, proposal_decision=proposal_decision))
        if outcome["kind"] == "existing":
            first_decision, decided_at = outcome["first"]
            if decision == first_decision:
                return {"decision": first_decision, "decided_at": decided_at,
                        "idempotent_replay": True}
            raise ApprovalStale(
                f"该审批已按 {first_decision} 处理（重试同决定可幂等返回）")
        event, audit_seq = outcome["event"], outcome["audit_seq"]
        if audit_seq % SNAPSHOT_EVERY == 0:  # 每 100 条快照链头（governance §3）
            await self._channel.execute(
                lambda conn: snapshot_chain_head(conn, self._chain_head_path))
        pending = self._pending.get(call_id)
        if pending is not None and not pending.future.done():
            # submit 可能跑在另一个事件循环（HTTP 服务线程）——跨线程唤醒
            # 必须走 call_soon_threadsafe，直接 set_result 不唤醒对方循环
            def deliver():
                if not pending.future.done():
                    pending.future.set_result(decision)
            pending.loop.call_soon_threadsafe(deliver)
        # decided_at 以决定事件的落库时刻为准（幂等重放读取同源）
        return {"decision": decision, "decided_at": event.ts,
                "idempotent_replay": False}

    def _decide_tx(self, conn, *, call_id: str, decision: str,
                   task_run_id: str, conversation_id: str, payload: dict,
                   agent_id: str, request_identity=None, proposal_decision=None) -> dict[str, Any]:
        published = []
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            first_audit_seq = conn.execute("SELECT coalesce(max(seq),0) FROM audit_log").fetchone()[0]
            actor_id = request_identity.effective_user_id if request_identity is not None else "owner"
            if request_identity is not None:
                self.identities.conversation(request_identity, conversation_id, conn)
                if decision in ("allow_always", "reject_always"):
                    workspace_id = self.identities.resources.get("agent", agent_id, conn)["workspace_id"]
                    self.identities.require(request_identity, "manage", workspace_id, conn)
            proposal_selected = None
            if payload.get("tool") == "schedule_task":
                proposal_selected = self.proposals.validate_decision(conn, call_id, decision, request_identity, proposal_decision)
            first = self._resolution_row(conn, call_id)
            if first is not None:
                conn.execute("COMMIT")  # 只读事务（并发窗口内已有首次决定）
                return {"kind": "existing", "first": first}
            pending = self._pending.get(call_id)
            task = conn.execute("SELECT status,current_attempt_no FROM task_runs WHERE id=?", (task_run_id,)).fetchone()
            if pending is None or pending.future.done() or task is None or task[0] not in {"running", "waiting_user"} or task[1] != pending.attempt_no:
                raise ApprovalStale("审批执行方已经停止，未处理审批已过期")
            if (pending.task_run_id, pending.conversation_id, pending.agent_id, pending.tool_name) != (task_run_id, conversation_id, agent_id, payload.get("tool")):
                raise ApprovalStale("审批绑定的任务或员工已经过期")
            call = conn.execute("SELECT tool_name,input,input_hash,status FROM tool_calls WHERE call_id=?", (call_id,)).fetchone()
            if call is None or call[2] != pending.input_hash or call[2] != payload.get("input_hash") or call[3] != "prepared":
                raise ApprovalStale("审批绑定的调用参数或状态已经过期")
            inputs = json.loads(call[1])
            boundary = self._file_boundary(ToolInvocation(call_id, call[0], inputs), pending.ctx)
            registry = pending.ctx.registry if pending.ctx.registry is not None else self.scheduler.registry
            meta = registry.get(call[0]).metadata
            result = evaluate_gate(meta, inputs, False, self._load_rules(agent_id, conn), agent_id, pending.ctx.cwd)
            if decision.startswith("allow") and (boundary or result.action == "deny"):
                raise ApprovalStale(boundary or result.reason)
            if hasattr(self, "grants"):
                self.grants.check_tool(task_run_id, call[0], inputs, conn)
                self.grants.check_binding(task_run_id, call_id, conn)
            event = self._store.append_in_tx(
                conn, task_run_id=task_run_id,
                conversation_id=conversation_id,
                attempt_no=pending.attempt_no,
                type=RunEventType.PERMISSION_RESOLVED,
                payload={"tool_call_id": call_id, "decision": decision, "actor_id": actor_id,
                    "credential_owner_id": request_identity.credential_owner_id if request_identity else "owner",
                    **({"proposal_id": call_id, "selected": proposal_selected} if proposal_decision is not None else {})},
            )
            published.append(event)
            audit_seq = append_audit(
                conn, ts=_now(), actor_type="user", actor_id=actor_id,
                action=f"permission.resolved:{decision}",
                resource_type="tool_call", resource_id=call_id,
                detail=_detail(target=payload.get("target"),
                               input_hash=payload.get("input_hash"),
                               agent_id=agent_id),
            )
            if proposal_decision is not None:
                published.extend(self.proposals.resolve_in_tx(conn, call_id, decision, request_identity, proposal_decision, proposal_selected))
                audit_seq = conn.execute("SELECT max(seq) FROM audit_log").fetchone()[0]
            if decision in ("allow_always", "reject_always"):
                effect = "allow" if decision == "allow_always" else "deny"
                pattern = payload.get("always_scope_preview") or ""
                rule_id = uuid.uuid4().hex
                if hasattr(self, "rules"):
                    rule = self.rules.insert(conn, rule_id, agent_id, {"change_id": "approval-rule-" + call_id,
                        "tool_name": payload["tool"], "pattern": pattern, "effect": effect}, actor_id)
                else:
                    conn.execute("INSERT INTO agent_permission_rules(id,agent_id,tool_name,pattern,effect,created_by_user_id,created_at) VALUES(?,?,?,?,?,?,?)",
                        (rule_id, agent_id, payload["tool"], pattern, effect, actor_id, _now()))
                audit_seq = append_audit(
                    conn, ts=_now(), actor_type="user", actor_id=actor_id,
                    action="permission.rule.created",
                    resource_type="permission_rule", resource_id=rule_id,
                    detail=_detail(tool=payload.get("tool"), pattern=pattern,
                                   effect=effect, agent_id=agent_id,
                                   input_hash=payload.get("input_hash")),
                )
                if hasattr(self, "rules"):
                    published.append(self._store.append_in_tx(conn, task_run_id=None, conversation_id=None,
                        type=RunEventType.GOVERNANCE_RULE_CHANGED, payload={"resource_type": "permission_rule", "resource_id": rule_id,
                            "change_id": rule["change_id"], "revision": rule["revision"], "actor_id": actor_id,
                            "credential_owner_id": request_identity.credential_owner_id, "audit_seq": audit_seq,
                            "source_task_run_id": task_run_id, "source_call_id": call_id,
                            "scope": self.rules.resources.scope("agent", agent_id, conn), **self.rules.event(rule)}))
        for item in published:
            self._store.publish(item)
        if first_audit_seq // SNAPSHOT_EVERY != audit_seq // SNAPSHOT_EVERY:
            snapshot_chain_head(conn, self._chain_head_path)
        return {"kind": "written", "event": event, "audit_seq": audit_seq}

    # ── pending 查询（界面刷新恢复审批卡，v1.4）──────────────────
    async def list_approvals(self, task_run_id: str, status: str) -> list[dict]:
        rows = await asyncio.to_thread(self._approval_rows, task_run_id)
        if status == "pending":
            return [r for r in rows if r["decision"] is None]
        return [r for r in rows if r["decision"] is not None]

    def _approval_rows(self, task_run_id: str) -> list[dict]:
        conn = self._db.read_conn
        task = conn.execute("SELECT status,current_attempt_no FROM task_runs WHERE id=?", (task_run_id,)).fetchone()
        rows = conn.execute(
            "SELECT payload, created_at FROM run_events"
            " WHERE task_run_id = ? AND type = 'permission.requested'"
            " ORDER BY global_seq", (task_run_id,),
        ).fetchall()
        out = []
        for payload_text, requested_at in rows:
            p = json.loads(payload_text)
            call_id = p.get("tool_call_id")
            resolved = self._find_resolution(call_id)
            pending = self._pending.get(call_id)
            active = pending is not None and not pending.future.done() and task is not None and task[0] in {"running", "waiting_user"} and task[1] == pending.attempt_no
            out.append({
                "call_id": call_id, "tool": p.get("tool"),
                "target": p.get("target"), "input_hash": p.get("input_hash"),
                "risk": p.get("risk"),
                "always_scope_preview": p.get("always_scope_preview"),
                "requested_at": requested_at,
                "decision": resolved[0] if resolved else None,
                "stale": resolved is None and not active,
                "attempt_no": pending.attempt_no if pending is not None else None,
            })
        return out

    # ── 执行编排（C8 前的进程内直调入口）─────────────────────────
    async def run_tool(self, *, task_run_id: str, conversation_id: str,
                       agent_id: str, invocation: ToolInvocation,
                       ctx: WorkContext) -> Any:
        """带闸门执行一次工具：事件落库（emit 接 EventStore）+ 三级闸门。"""
        ctx = replace(ctx, approved_calls=set(), filesystem=filesystem_boundary(ctx))
        fixed = ctx.filesystem
        ctx.filesystem_resolver = lambda: self._current_filesystem(fixed)
        if hasattr(self, "connectors"):
            ctx.registry = self.connectors.registry(task_run_id, self.scheduler.registry)
        if hasattr(self, "grants"):
            view = self.grants.check_tool(task_run_id, invocation.name, invocation.input)
            if "selected_connector" in view:
                ctx.connector_id = view["selected_connector"]["id"]
                ctx.allowed_hosts = list(view["selected_connector"]["config"]["allowed_hosts"])
                ctx.enforce_http_hosts = True
                if ctx.registry is not None:
                    from agentcrew_core.connectors import connector_effect
                    from agentcrew_core.tools import ToolRegistry
                    original = ctx.registry.get("http_request")
                    selected = view["selected_connector"]
                    classified = replace(original, metadata=replace(original.metadata, side_effect_class=connector_effect(
                        invocation.input.get("method", "GET"), invocation.input["url"], selected["config"])),
                        prepared_extras=lambda _input: {"connector_id": selected["id"], "connector_revision": selected["revision"]})
                    registry = ToolRegistry()
                    for name in ctx.registry.names():
                        registry.register(classified if name == "http_request" else ctx.registry.get(name))
                    ctx.registry = registry
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        ih = input_hash(invocation.input)
        self._pending[invocation.call_id] = _Pending(
            future=future, loop=loop, input_hash=ih,
            tool_name=invocation.name, task_run_id=task_run_id,
            conversation_id=conversation_id, agent_id=agent_id, ctx=ctx,
            attempt_no=self._db.read_conn.execute("SELECT current_attempt_no FROM task_runs WHERE id=?", (task_run_id,)).fetchone()[0],
        )
        ctx.emit = self._make_emit(task_run_id, conversation_id)
        try:
            assert self.scheduler is not None, "cli 未装配 scheduler"
            result = await self.scheduler.run(invocation, ctx)
            if result.details.get("record_failed") or result.error == "EVENT_PERSIST_FAILED":
                tool = (ctx.registry if ctx.registry is not None else self.scheduler.registry).get(invocation.name)
                if result.details.get("record_failed") and tool is not None and not tool.metadata.read_only:
                    await ctx.emit("tool.pending_verification", {"call_id": invocation.call_id})
                raise RuntimeError("EVENT_PERSIST_FAILED：工具事件未持久化，执行立即停止并核对真实效果")
            return result
        finally:
            self._pending.pop(invocation.call_id, None)

    def _context_for(self, call_id: str) -> _Pending:
        pending = self._pending.get(call_id)
        if pending is None:
            raise ApprovalNotFound(f"执行上下文不存在（重启后的孤儿卡）：{call_id}")
        return pending

    def _current_filesystem(self, fixed):
        code = [Path(path) for row in self._db.read_conn.execute("SELECT s.source_path,s.canonical_path FROM connector_startup_files s JOIN connectors c ON c.id=s.connector_id AND c.revision=s.connector_revision") for path in row]
        return normalize_filesystem(fixed.read_roots, fixed.protected, fixed.write_roots,
            [*fixed.readonly_roots, *code], canonical_roots=True)

    def check_current(self, conn, task_run_id, call_id):
        pending = self._pending.get(call_id)
        task = conn.execute("SELECT status,current_attempt_no FROM task_runs WHERE id=?", (task_run_id,)).fetchone()
        if pending is None or pending.future.cancelled() or task is None or pending.task_run_id != task_run_id or task[1] != pending.attempt_no:
            raise ApprovalStale("派发时审批执行方或尝试已经过期")
        row = conn.execute("SELECT tool_name,input FROM tool_calls WHERE task_run_id=? AND call_id=?", (task_run_id, call_id)).fetchone()
        if row is None:
            raise ApprovalStale("派发缺少调用绑定")
        inputs = json.loads(row[1])
        boundary = self._file_boundary(ToolInvocation(call_id, row[0], inputs), pending.ctx)
        registry = pending.ctx.registry if pending.ctx.registry is not None else self.scheduler.registry
        meta = registry.get(row[0]).metadata
        readonly = bash_readonly(inputs["command"], pending.ctx.cwd)[0] if row[0] == "bash" else meta.read_only
        result = evaluate_gate(meta, inputs, readonly, self._load_rules(pending.agent_id, conn), pending.agent_id, pending.ctx.cwd)
        if hasattr(self, "automation"):
            automated = self.automation.current_gate(task_run_id, meta, inputs, readonly, self._load_rules(pending.agent_id, conn), pending.ctx.cwd, conn)
            if automated is not None:
                result = automated
        if boundary or result.action == "deny":
            raise ApprovalStale(boundary or result.reason)
        if result.action == "ask":
            decision = self._resolution_row(conn, call_id)
            if decision is None or not decision[0].startswith("allow"):
                raise ApprovalStale("当前规则要求人工审批，调用缺少本次允许决定")

    def _make_emit(self, task_run_id: str, conversation_id: str):
        attempt_no = self._db.read_conn.execute("SELECT current_attempt_no FROM task_runs WHERE id=?", (task_run_id,)).fetchone()[0]
        async def sink(event_type: str, payload: dict) -> None:
            def audit_result(conn, event):
                if event_type not in {"tool.completed", "tool.failed", "tool.pending_verification"}:
                    return
                row = conn.execute("SELECT tool_name,risk_level,input_hash FROM tool_calls WHERE call_id=?", (payload["call_id"],)).fetchone()
                if row is not None and (row[1] in {"medium", "high"} or payload.get("error")):
                    append_audit(conn, ts=event.ts, actor_type="system", actor_id="tools", action=event_type,
                        resource_type="tool_call", resource_id=payload["call_id"],
                        detail=_detail(tool=row[0], input_hash=row[2], task_run_id=task_run_id,
                            ok=event_type == "tool.completed", error=payload.get("error")))
            return await self._store.append(
                task_run_id=task_run_id, conversation_id=conversation_id,
                attempt_no=attempt_no, type=RunEventType(event_type), payload=payload,
                extra_writes=audit_result,
            )
        return sink

    # ── 库操作（读：to_thread 内每线程只读连接）────────────────────
    @staticmethod
    def _file_boundary(invocation, ctx):
        if invocation.name in {"read_file", "write_file"}:
            return _check_path(ctx, invocation.input.get("path", ""), read_only=invocation.name == "read_file")
        return None

    def _load_rules(self, agent_id: str, conn=None) -> list[PermissionRule]:
        connection = conn if conn is not None else self._db.read_conn
        rows = connection.execute(
            "SELECT agent_id, tool_name, pattern, effect FROM"
            " agent_permission_rules WHERE agent_id = ? AND revoked_at IS NULL",
            (agent_id,),
        ).fetchall()
        return [PermissionRule(*row) for row in rows]

    def _agent_of_conversation(self, conversation_id: str) -> str:
        row = self._db.read_conn.execute(
            "SELECT agent_id FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        return row[0] if row else ""

    def _find_request(self, call_id: str):
        row = self._db.read_conn.execute(
            "SELECT task_run_id, conversation_id, payload FROM run_events"
            " WHERE type = 'permission.requested'"
            " AND json_extract(payload, '$.tool_call_id') = ?"
            " ORDER BY global_seq DESC LIMIT 1", (call_id,),
        ).fetchone()
        if row is None:
            return None
        return row[0], row[1], json.loads(row[2])

    def _find_resolution(self, call_id: str):
        row = self._db.read_conn.execute(
            "SELECT json_extract(payload, '$.decision'), created_at"
            " FROM run_events WHERE type = 'permission.resolved'"
            " AND json_extract(payload, '$.tool_call_id') = ?"
            " ORDER BY global_seq DESC LIMIT 1", (call_id,),
        ).fetchone()
        return (row[0], row[1]) if row else None

    @staticmethod
    def _resolution_row(conn, call_id: str):
        """写连接上的查重读（决定事务内调用，同一一致性快照）。"""
        row = conn.execute(
            "SELECT json_extract(payload, '$.decision'), created_at"
            " FROM run_events WHERE type = 'permission.resolved'"
            " AND json_extract(payload, '$.tool_call_id') = ?"
            " ORDER BY global_seq DESC LIMIT 1", (call_id,),
        ).fetchone()
        return (row[0], row[1]) if row else None

    # ── 审计链（只经写通道；链头快照同通道）────────────────────────
    async def _audit(self, actor_type: str, actor_id: str, action: str,
                     resource_type: str, resource_id: str,
                     detail: dict) -> None:
        def write(conn):
            pending = self._context_for(resource_id)
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                seq = append_audit(
                    conn, ts=_now(), actor_type=actor_type, actor_id=actor_id,
                    action=action, resource_type=resource_type,
                    resource_id=resource_id, detail=_detail(**detail),
                )
                scope = conn.execute("SELECT c.workspace_id,c.agent_id,w.org_id FROM conversations c LEFT JOIN workspaces w ON w.id=c.workspace_id WHERE c.id=?", (pending.conversation_id,)).fetchone()
                operator = conn.execute("SELECT effective_user_id FROM task_governance WHERE task_run_id=?", (pending.task_run_id,)).fetchone()
                authorization = self.grants.view(pending.task_run_id, conn)["sha256"] if hasattr(self, "grants") else input_hash(detail)
                event = self._store.append_in_tx(conn, task_run_id=pending.task_run_id, conversation_id=pending.conversation_id,
                    attempt_no=pending.attempt_no, type=RunEventType.GOVERNANCE_AUTHORIZATION_CHECKED,
                    payload=json.loads(_detail(actor_id=operator[0] if operator else "owner", agent_id=pending.agent_id, allowed=not action.startswith("permission.denied"),
                        reason=detail.get("reason", action), authorization_sha256=authorization, call_id=resource_id, audit_seq=seq,
                        scope={"org_id": scope[2], "workspace_id": scope[0], "agent_id": scope[1], "owner_id": None})))
            self._store.publish(event)
            return seq

        seq = await self._channel.execute(write)
        if seq % SNAPSHOT_EVERY == 0:  # 每 100 条快照链头（governance §3）
            await self._channel.execute(
                lambda conn: snapshot_chain_head(conn, self._chain_head_path))
        return None
