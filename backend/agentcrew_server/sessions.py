"""会话装配与 FSM 服务（M0-C7：材料导入 F001 / 指令排队 F006 / state 快照）。

- FSM 真值唯一来源是 agentcrew_core.events.reducer（纯函数）；本服务在每次
  决策前从库重放该会话的 FSM 域事件取快照（fsm_snapshot 单读事务保证
  at_global_seq 与状态同源，backend-service §3 快照配对续播）；
- 同会话指令/排队操作经每会话 asyncio.Lock 序化——"并发两条指令，第二条
  必入队"的窗口在锁内重读 FSM 后闭合；
- 材料导入是真实文件复制（ADR-006）：逐文件校验（存在/常规文件/大小/数量
  上限），重名自动生成可辨认新名，部分失败不阻塞任务创建、逐文件回报；
  全部被拒 → 422 不建任务；
- client_request_id 幂等（backend-service §8）：conversations / task_runs
  上的部分唯一索引 + pending_queue 项内键，网络重试不产生重复任务/指令；
  创建路径按幂等键串行（查重→材料导入→创建，外审回稿——并发同键请求
  后到者读首次结果，不再撞唯一索引 500 / 留无归属材料目录）；
- 复合写闭包（创建/继续队列）的事件发布在写通道线程内、COMMIT 成功后、
  闭包返回前执行——发布顺序 = 提交顺序（对齐 EventStore 的
  publisher-in-closure 模式；闭包外发布会让写线程先发布更大 global_seq，
  SSE 去重把后到的较小序号永久丢弃，外审回稿致命项）；
- S09（C6 外审移交）：build_work_context 在会话装配处统一提供 cwd 与
  scope——read_file/write_file 相对路径、bash 子进程 cwd 都从这取。
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentcrew_core.events import RunEventType
from agentcrew_core.events.reducer import (
    ConversationState,
    can_cancel,
    can_continue_queue,
    can_queue,
    can_send,
    legal_actions,
    replay,
)
from agentcrew_core.tools import WorkContext
from agentcrew_core.tools.judgment import build_protected_paths

from .api.errors import ErrorCode
from .db.event_store import EventStore
from .db.queries import fsm_snapshot

if TYPE_CHECKING:  # 仅类型标注（运行期避免与 settings 互相导入）
    from .settings import SettingsService

_log = logging.getLogger("agentcrew.sessions")

DEFAULT_WORKSPACE_ID = "default"
DEFAULT_AGENT_ID = "default"

# ConversationSummary.state_badge 的映射口径（契约枚举）
_TERMINAL_FOR_APPROVALS = ("completed", "failed", "cancelled")


class SessionError(Exception):
    """业务失败（API 层映射错误码信封）。"""

    def __init__(self, code: ErrorCode, message: str, detail: object = None):
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 材料导入（真实文件复制；逐文件结果）────────────────────────────

def _unique_stored_name(name: str, used: set[str]) -> str:
    """重名自动生成可辨认新名：report.txt → report-2.txt → report-3.txt…"""
    if name not in used:
        return name
    stem, dot, suffix = name.partition(".")
    n = 2
    while f"{stem}-{n}{dot + suffix if dot else ''}" in used:
        n += 1
    return f"{stem}-{n}{dot + suffix if dot else ''}"


def import_materials(sources: list[str], materials_dir: Path,
                     limits: dict[str, int]) -> list[dict[str, Any]]:
    """逐文件：存在 / 常规文件 / ≤max_file_mb / 数量 ≤max_files，复制进
    materials/。部分失败逐文件回报，不抛异常（任务照建）。"""
    results: list[dict[str, Any]] = []
    materials_dir.mkdir(parents=True, exist_ok=True)
    used = {p.name for p in materials_dir.iterdir()}
    max_bytes = limits["max_file_mb"] * 1024 * 1024
    for idx, raw in enumerate(sources):
        original = str(raw)
        if idx >= limits["max_files"]:
            results.append({"original_path": original, "stored_name": "",
                            "size_bytes": None,
                            "error": f"超出单任务文件数上限（{limits['max_files']}）"})
            continue
        src = Path(raw).expanduser()
        try:
            if not src.exists():
                raise FileNotFoundError(f"文件不存在：{src}")
            if not src.is_file():
                raise ValueError(f"不是常规文件：{src}")
            size = src.stat().st_size
            if size > max_bytes:
                raise ValueError(
                    f"超出单文件大小上限（{limits['max_file_mb']}MB）：{size} 字节")
            stored = _unique_stored_name(src.name, used)
            shutil.copyfile(src, materials_dir / stored)
            used.add(stored)
            results.append({"original_path": original, "stored_name": stored,
                            "size_bytes": size, "error": None})
        except (OSError, ValueError) as e:
            results.append({"original_path": original, "stored_name": "",
                            "size_bytes": None, "error": str(e)})
    return results


def validate_folders(folders: list[str],
                     limits: dict[str, int]) -> list[dict[str, Any]]:
    """授权文件夹逐项校验：必须是存在的目录（realpath 入 folders_json，
    进入任务读写范围——范围内读写仍走三级闸门，harness §6.1）。"""
    out: list[dict[str, Any]] = []
    for idx, raw in enumerate(folders):
        if idx >= limits["max_folders"]:
            out.append({"path": str(raw),
                        "error": f"超出单任务文件夹数上限（{limits['max_folders']}）"})
            continue
        p = Path(raw).expanduser()
        if p.is_dir():
            out.append({"path": str(p.resolve()), "error": None})
        else:
            out.append({"path": str(raw), "error": f"文件夹不存在：{p}"})
    return out


# ── 会话服务 ────────────────────────────────────────────────────────

class SessionService:
    def __init__(self, db, event_store: EventStore, data_dir: Path,
                 settings: "SettingsService"):
        self._db = db
        self._store = event_store
        self._channel = event_store.channel
        self._data_dir = data_dir
        self._settings = settings  # limits 取生效配置（PATCH 成功后即为新值）
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self.memory_jobs = None

    @asynccontextmanager
    async def foreground(self):
        if self.memory_jobs is None:
            yield
        else:
            async with self.memory_jobs.foreground():
                yield

    async def _conv_lock(self, conversation_id: str) -> asyncio.Lock:
        return await self._scoped_lock(f"conv:{conversation_id}")

    # C9 恢复域共用件：会话串行域与 FSM 现值（resume 校验与直发/排队/
    # 继续互斥，同一锁同一状态源）
    async def lock_for(self, conversation_id: str) -> asyncio.Lock:
        return await self._conv_lock(conversation_id)

    def sync_fsm(self, conversation_id: str) -> ConversationState:
        return self._fsm_sync(conversation_id)[0]

    async def _idempotency_lock(self, client_request_id: str) -> asyncio.Lock:
        """创建幂等键串行域（外审回稿：查重+材料导入+创建 全程持锁）。"""
        return await self._scoped_lock(f"idem:{client_request_id}")

    async def _scoped_lock(self, key: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def _limits(self) -> dict[str, int]:
        return dict(self._settings.config.values.get("limits", {}))

    def limits_view(self) -> dict[str, int]:
        """GET /api/limits：材料与导入限制（backend-service §5）。"""
        return self._limits()

    # ── FSM 快照（决策前重放；读写同源）──────────────────────────

    def _fsm_sync(self, conversation_id: str) -> tuple[ConversationState, int]:
        events, head = fsm_snapshot(self._db.read_conn, conversation_id)
        return replay(events), head

    def _conversation_row(self, conversation_id: str) -> sqlite3.Row | None:
        return self._db.read_conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()

    def conversation_or_404(self, conversation_id: str) -> sqlite3.Row:
        row = self._conversation_row(conversation_id)
        if row is None:
            raise SessionError(ErrorCode.NOT_FOUND, f"会话不存在：{conversation_id}")
        return row

    def list_messages(self, conversation_id: str, *, limit: int = 50,
                      before: str | None = None) -> dict[str, Any]:
        """读取最新一页，按时间与 id 正序返回；before 为同会话排他游标。"""
        self.conversation_or_404(conversation_id)
        conn = self._db.read_conn
        where = "conversation_id = ?"
        params: list[Any] = [conversation_id]
        if before is not None:
            cursor = conn.execute(
                "SELECT created_at, id FROM messages"
                " WHERE conversation_id = ? AND id = ?",
                (conversation_id, before)).fetchone()
            if cursor is None:
                raise SessionError(ErrorCode.NOT_FOUND, f"消息游标不存在：{before}")
            where += " AND (created_at, id) < (?, ?)"
            params.extend(cursor)
        rows = conn.execute(
            "SELECT id, role, content, task_run_id, created_at FROM messages"
            f" WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ?",
            (*params, limit + 1)).fetchall()
        return {"items": [dict(row) for row in reversed(rows[:limit])],
                "has_more": len(rows) > limit}

    def _last_task_status(self, conversation_id: str) -> str | None:
        row = self._db.read_conn.execute(
            "SELECT status FROM task_runs WHERE conversation_id = ?"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _badge(fsm: ConversationState, last_task_status: str | None) -> str:
        if fsm.waiting_approvals > 0:
            return "waiting_approval"
        if fsm.waiting_questions > 0:
            return "waiting_question"
        if fsm.state in ("starting", "running"):
            return "running"
        if fsm.state == "error":
            return "failed"
        if last_task_status == "interrupted":
            return "interrupted"
        if last_task_status == "failed":
            return "failed"
        return "idle"

    def _summary(self, conv: sqlite3.Row) -> dict[str, Any]:
        fsm, _ = self._fsm_sync(conv["id"])
        return {
            "id": conv["id"],
            "title": conv["title"],
            "status": conv["status"],
            "agent_name": conv["agent_id"],
            "last_activity_at": conv["updated_at"],
            "state_badge": self._badge(fsm, self._last_task_status(conv["id"])),
        }

    # ── scope / 材料目录 / 工作目录（S09 会话装配）─────────────────

    def _workspace_dir(self, workspace_id: str) -> Path:
        return (self._data_dir / "workspaces" / workspace_id).resolve()

    def _materials_dir(self, conversation_id: str) -> Path:
        return (self._data_dir / "conversations" / conversation_id
                / "materials").resolve()

    def scope_of(self, conv: sqlite3.Row) -> dict[str, Any]:
        folders = json.loads(conv["folders_json"] or "[]")
        return {
            "workspace_dir": str(self._workspace_dir(conv["workspace_id"])),
            "materials_dir": str(self._materials_dir(conv["id"])),
            "folders": [{"path": f["path"], "access": f.get("access", "read_write")}
                        for f in folders],
        }

    def build_work_context(self, conversation_id: str,
                           task_run_id: str | None = None) -> WorkContext:
        """S09：cwd 与 scope 在会话装配处统一提供。scope = 工作区数据目录 ∪
        任务资料目录 ∪ folders_json 授权文件夹（harness §6.1，realpath 规范）；
        cwd = 工作区数据目录（read_file/write_file 相对路径与 bash 子进程的
        落点）。受保护路径清单在此一并装配。"""
        conv = self._conversation_row(conversation_id)
        if conv is None:
            raise SessionError(ErrorCode.NOT_FOUND, f"会话不存在：{conversation_id}")
        workspace = self._workspace_dir(conv["workspace_id"])
        workspace.mkdir(parents=True, exist_ok=True)
        materials = self._materials_dir(conversation_id)
        materials.mkdir(parents=True, exist_ok=True)
        folders = [Path(f["path"]) for f in json.loads(conv["folders_json"] or "[]")]
        scope = [workspace, materials] + [f.resolve() for f in folders]
        return WorkContext(
            scope=scope,
            protected=build_protected_paths(self._data_dir, Path.home()),
            artifacts_dir=(self._data_dir / "artifacts" / task_run_id
                           if task_run_id else None),
            task_run_id=task_run_id or "",
            cwd=workspace,
        )

    # ── 创建会话（首条指令 + 材料导入，F001）──────────────────────

    async def create_conversation(
        self, *, instruction: str, agent_id: str | None = None,
        client_request_id: str | None = None,
        import_files: list[str] | None = None,
        folders: list[str] | None = None,
    ) -> dict[str, Any]:
        if not client_request_id:
            return await self._create_conversation_once(
                instruction=instruction, agent_id=agent_id,
                client_request_id=None, import_files=import_files,
                folders=folders)
        # 幂等键串行（外审回稿）：查重在事务外、材料复制在前、插入在后——
        # 并发同键请求会双份导入且后到者撞唯一索引 500。全程持键锁后，
        # 后到请求在锁内重读首次结果
        async with await self._idempotency_lock(client_request_id):
            existing = await asyncio.to_thread(
                self._find_conversation_by_request_id, client_request_id)
            if existing is not None:
                return existing  # 网络重试幂等：返回首次结果，不重复建
            return await self._create_conversation_once(
                instruction=instruction, agent_id=agent_id,
                client_request_id=client_request_id,
                import_files=import_files, folders=folders)

    async def _create_conversation_once(
        self, *, instruction: str, agent_id: str | None,
        client_request_id: str | None,
        import_files: list[str] | None, folders: list[str] | None,
    ) -> dict[str, Any]:
        async with self.foreground():
            return await self._create_foreground_conversation(
                instruction=instruction, agent_id=agent_id,
                client_request_id=client_request_id,
                import_files=import_files, folders=folders)

    async def _create_foreground_conversation(
        self, *, instruction: str, agent_id: str | None,
        client_request_id: str | None,
        import_files: list[str] | None, folders: list[str] | None,
    ) -> dict[str, Any]:
        conv_id = uuid.uuid4().hex
        task_id = uuid.uuid4().hex
        agent = agent_id or DEFAULT_AGENT_ID
        limits = self._limits()
        materials_dir = self._data_dir / "conversations" / conv_id / "materials"
        file_results = await asyncio.to_thread(import_materials, list(import_files or []),
                                                materials_dir, limits)
        folder_results = await asyncio.to_thread(validate_folders, list(folders or []), limits)
        provided = len(file_results) + len(folder_results)
        rejected = sum(1 for r in file_results if r["error"]) + \
            sum(1 for r in folder_results if r["error"])
        if provided and rejected == provided:
            raise SessionError(
                ErrorCode.VALIDATION_ERROR, "全部材料被拒，任务未创建",
                detail={"files": file_results, "folders": folder_results})
        accepted = [{"path": f["path"], "access": "read_write"}
                    for f in folder_results if not f["error"]]

        ts = _now()
        events: list = []

        def tx(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("PRAGMA defer_foreign_keys=ON")
                conn.execute(
                    "INSERT INTO conversations (id, workspace_id, agent_id, status,"
                    " folders_json, client_request_id, created_at, updated_at)"
                    " VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
                    (conv_id, DEFAULT_WORKSPACE_ID, agent,
                     json.dumps(accepted, ensure_ascii=False), client_request_id,
                     ts, ts),
                )
                events.append(self._store.append_in_tx(
                    conn, task_run_id=task_id, conversation_id=conv_id,
                    type=RunEventType.RUN_QUEUED,
                    payload={"instruction": instruction,
                             "client_request_id": client_request_id},
                ))
                if import_files or folder_results:
                    # folders 逐项结果随材料事件持久化（外审回稿：算了不存
                    # 会让响应与幂等重试都拿不回逐项结果）
                    events.append(self._store.append_in_tx(
                        conn, task_run_id=task_id, conversation_id=conv_id,
                        type=RunEventType.MATERIALS_IMPORTED,
                        payload={"files": file_results,
                                 "folders": folder_results},
                    ))
                conn.execute("COMMIT")
            except Exception:
                events.clear()  # busy 重试重跑闭包：回滚路径清空防重复发布
                conn.execute("ROLLBACK")
                raise
            # COMMIT 成功后、闭包返回前发布（写线程内）——发布顺序 = 提交顺序
            for event in events:
                self._store.publish(event)

        await self._channel.execute(tx)
        return await asyncio.to_thread(self._conversation_data, conv_id, task_id)

    def _conversation_data(self, conv_id: str,
                           task_run_id: str) -> dict[str, Any]:
        conv = self.conversation_or_404(conv_id)
        materials = self._db.read_conn.execute(
            "SELECT original_path, stored_name, size_bytes, error"
            " FROM task_materials WHERE conversation_id = ? ORDER BY created_at",
            (conv_id,),
        ).fetchall()
        folders_row = self._db.read_conn.execute(
            "SELECT payload FROM run_events WHERE conversation_id = ?"
            " AND type = 'materials.imported' ORDER BY global_seq LIMIT 1",
            (conv_id,),
        ).fetchone()
        folder_results = (json.loads(folders_row[0]).get("folders", [])
                          if folders_row is not None else [])
        return {
            "conversation": self._summary(conv),
            "materials": [
                {"original_path": m[0], "stored_name": m[1],
                 "size_bytes": m[2], "error": m[3]} for m in materials],
            "folders": folder_results,
            "scope": self.scope_of(conv),
            "task_run_id": task_run_id,
        }

    def _find_conversation_by_request_id(self, client_request_id: str):
        row = self._db.read_conn.execute(
            "SELECT id FROM conversations WHERE client_request_id = ?",
            (client_request_id,),
        ).fetchone()
        if row is None:
            return None
        task = self._db.read_conn.execute(
            "SELECT id FROM task_runs WHERE conversation_id = ?"
            " AND client_request_id = ? ORDER BY created_at LIMIT 1",
            (row[0], client_request_id),
        ).fetchone()
        return self._conversation_data(row[0], task[0])

    # ── 指令（直跑 / 入队 / 审批 409，D5）────────────────────────

    async def send_instruction(self, conversation_id: str, text: str,
                               client_request_id: str | None = None
                               ) -> dict[str, Any]:
        async with await self._conv_lock(conversation_id):
            await asyncio.to_thread(self.conversation_or_404, conversation_id)
            if client_request_id:
                replayed = await asyncio.to_thread(
                    self._instruction_replay, conversation_id, client_request_id)
                if replayed is not None:
                    return replayed
            fsm, _ = await asyncio.to_thread(self._fsm_sync, conversation_id)

            if can_send(fsm):
                task_id = uuid.uuid4().hex
                async with self.foreground():
                    event = await self._store.append(
                        task_run_id=task_id, conversation_id=conversation_id,
                        type=RunEventType.RUN_QUEUED,
                        payload={"instruction": text,
                                 "client_request_id": client_request_id},
                    )
                return {"mode": "started", "queue_position": None,
                        "task_run_id": event.task_run_id}

            if fsm.waiting_approvals > 0:
                raise SessionError(
                    ErrorCode.APPROVAL_PENDING,
                    "有审批等待处理，先处理审批再发指令",
                    detail={"waiting_approvals": fsm.waiting_approvals,
                            "legal_actions": legal_actions(fsm)})

            if can_queue(fsm):
                item_id = uuid.uuid4().hex
                async with self.foreground():
                    await self._store.append(
                        task_run_id=None, conversation_id=conversation_id,
                        type=RunEventType.QUEUE_ITEM_ENQUEUED,
                        payload={"item_id": item_id, "text": text,
                                 "client_request_id": client_request_id},
                    )
                fsm2, _ = await asyncio.to_thread(
                    self._fsm_sync, conversation_id)
                return {"mode": "queued",
                        "queue_position": fsm2.queued_position(item_id),
                        "task_run_id": None}

            raise SessionError(
                ErrorCode.INVALID_TRANSITION,
                f"当前状态（{fsm.state}）不能接收指令",
                detail={"state": fsm.state, "legal_actions": legal_actions(fsm)})

    def _instruction_replay(self, conversation_id: str,
                            client_request_id: str) -> dict[str, Any] | None:
        """同 client_request_id 重试：已建任务 → started；仍在队列 → queued；
        已取消 → 明确的 cancelled 结果（已取消项继续占有幂等键——重试不得
        把用户取消的指令重新入队，外审回稿）。"""
        task = self._db.read_conn.execute(
            "SELECT id FROM task_runs WHERE conversation_id = ?"
            " AND client_request_id = ? ORDER BY created_at LIMIT 1",
            (conversation_id, client_request_id),
        ).fetchone()
        if task is not None:
            return {"mode": "started", "queue_position": None,
                    "task_run_id": task[0]}
        row = self._conversation_row(conversation_id)
        if row is None:
            return None
        for item in json.loads(row["pending_queue"] or "[]"):
            if item.get("client_request_id") != client_request_id:
                continue
            if item.get("state") == "queued":
                fsm, _ = self._fsm_sync(conversation_id)
                return {"mode": "queued",
                        "queue_position": fsm.queued_position(item["id"]),
                        "task_run_id": None}
            if item.get("state") == "cancelled":
                return {"mode": "cancelled", "queue_position": None,
                        "task_run_id": None}
        return None

    # ── 排队控制（F006）──────────────────────────────────────────

    def _has_queued_items(self, conversation_id: str) -> bool:
        row = self._db.read_conn.execute(
            "SELECT pending_queue FROM conversations WHERE id = ?",
            (conversation_id,)).fetchone()
        if row is None or not row[0]:
            return False
        return any(item.get("state") == "queued"
                   for item in json.loads(row[0]))

    async def emit_cancel_pair(self, conversation_id: str,
                               task_run_id: str,
                               attempt_no: int | None = None) -> None:
        """用户停止的成对终态：run.cancelled + 队列非空时 queue.paused，
        会话锁内**单事务**提交+发布（外审回稿修复：两事件分两次提交的
        异步窗口里，用户点"继续"会被晚到的 paused 再次暂停——与
        continue_queue 同锁串行 + 同事务相邻 global_seq 消除交错）。
        attempt_no 透传给 run.cancelled（run_attempts 行随之落终态）。"""
        async with await self._conv_lock(conversation_id):
            has_queued = await asyncio.to_thread(
                self._has_queued_items, conversation_id)
            events: list = []

            def tx(conn: sqlite3.Connection) -> None:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    events.append(self._store.append_in_tx(
                        conn, task_run_id=task_run_id,
                        conversation_id=conversation_id,
                        type=RunEventType.RUN_CANCELLED, payload={},
                        attempt_no=attempt_no,
                    ))
                    if has_queued:
                        events.append(self._store.append_in_tx(
                            conn, task_run_id=None,
                            conversation_id=conversation_id,
                            type=RunEventType.QUEUE_PAUSED, payload={},
                        ))
                    conn.execute("COMMIT")
                except Exception:
                    events.clear()  # busy 重试：回滚事件不得重复发布
                    conn.execute("ROLLBACK")
                    raise
                for event in events:  # COMMIT 后、闭包返回前发布（顺序=提交序）
                    self._store.publish(event)

            await self._channel.execute(tx)

    async def auto_dequeue_next(self, conversation_id: str,
                                before_publish=None) -> str | None:
        """终态自动接续（§4：completed/failed 出队队首；cancelled 已被 reducer
        置 queue_paused，此处自然不动作）。与 continue_queue 同锁同事务模式；
        竞态窗口（用户指令抢先直发占用 idle）内重读 FSM 后返回 None。
        before_publish(task_id) 在事件发布前、写线程内调用——调用方借此刻
        挂任务级先决信息（RunManager 的护栏提示），杜绝"runner 先取走"竞态。"""
        async with await self._conv_lock(conversation_id):
            fsm, _ = await asyncio.to_thread(self._fsm_sync, conversation_id)
            if fsm.state != "idle" or fsm.queue_paused or not fsm.queued_items:
                return None
            head = fsm.queued_items[0]
            head_crid = None
            for item in json.loads(
                    (await asyncio.to_thread(
                        self._conversation_row, conversation_id))
                    ["pending_queue"] or "[]"):
                if item.get("id") == head.id:
                    head_crid = item.get("client_request_id")
                    break
            task_id = uuid.uuid4().hex
            events: list = []

            def tx(conn: sqlite3.Connection) -> None:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if before_publish is not None:
                        before_publish(task_id)
                    events.append(self._store.append_in_tx(
                        conn, task_run_id=task_id,
                        conversation_id=conversation_id,
                        type=RunEventType.RUN_QUEUED,
                        payload={"instruction": head.text,
                                 "queue_item_id": head.id,
                                 "client_request_id": head_crid},
                    ))
                    conn.execute("COMMIT")
                except Exception:
                    events.clear()  # busy 重试：回滚事件不得重复发布
                    conn.execute("ROLLBACK")
                    raise
                for event in events:  # COMMIT 后、闭包返回前发布（顺序=提交序）
                    self._store.publish(event)

            await self._channel.execute(tx)
            return task_id

    async def continue_queue(self, conversation_id: str) -> None:
        async with await self._conv_lock(conversation_id), self.foreground():
            await asyncio.to_thread(self.conversation_or_404, conversation_id)
            fsm, _ = await asyncio.to_thread(self._fsm_sync, conversation_id)
            head = fsm.queued_items[0] if fsm.queued_items else None
            verifications = await asyncio.to_thread(
                self._pending_verifications, conversation_id)
            approvals = await asyncio.to_thread(
                self._unresolved_approvals, conversation_id)
            # 与 state_snapshot 的 can_continue_queue 同一判定源（外审回稿：
            # 快照不得宣称可继续而接口必 409）；不成立时按同一输入分解原因码
            if not can_continue_queue(
                    fsm, pending_verifications=len(verifications),
                    unresolved_approvals=len(approvals)):
                if fsm.state != "idle" or not fsm.queue_paused:
                    raise SessionError(
                        ErrorCode.QUEUE_PAUSED,
                        "仅队列暂停且无任务执行时可继续"
                        "（用户停止后队列进入暂停，等待显式继续）",
                        detail={"state": fsm.state,
                                "queue_paused": fsm.queue_paused,
                                "legal_actions": legal_actions(fsm)})
                if head is None:
                    raise SessionError(
                        ErrorCode.QUEUE_EMPTY, "队列为空，无可继续的指令",
                        detail={"legal_actions": legal_actions(fsm)})
                if verifications:
                    raise SessionError(
                        ErrorCode.PENDING_VERIFICATION,
                        "存在待核验调用，处理后才能继续队列",
                        detail={"pending_verifications": verifications})
                raise SessionError(
                    ErrorCode.APPROVAL_PENDING,
                    "存在未决审批，处理后才能继续队列",
                    detail={"pending_approvals": approvals})
            head_crid = None
            for item in json.loads(
                    (await asyncio.to_thread(
                        self._conversation_row, conversation_id))
                    ["pending_queue"] or "[]"):
                if item.get("id") == head.id:
                    head_crid = item.get("client_request_id")
                    break

            task_id = uuid.uuid4().hex
            events: list = []

            def tx(conn: sqlite3.Connection) -> None:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    # §4：QUEUE_RESUMED（清暂停）→ 队首出队 → starting
                    events.append(self._store.append_in_tx(
                        conn, task_run_id=None, conversation_id=conversation_id,
                        type=RunEventType.QUEUE_RESUMED, payload={},
                    ))
                    events.append(self._store.append_in_tx(
                        conn, task_run_id=task_id, conversation_id=conversation_id,
                        type=RunEventType.RUN_QUEUED,
                        payload={"instruction": head.text,
                                 "queue_item_id": head.id,
                                 "client_request_id": head_crid},
                    ))
                    conn.execute("COMMIT")
                except Exception:
                    events.clear()  # busy 重试：回滚事件不得重复发布
                    conn.execute("ROLLBACK")
                    raise
                # COMMIT 成功后、闭包返回前发布（写线程内）——发布顺序 = 提交顺序
                for event in events:
                    self._store.publish(event)

            await self._channel.execute(tx)

    def _pending_verifications(self, conversation_id: str) -> list[dict]:
        rows = self._db.read_conn.execute(
            "SELECT tc.call_id, tc.tool_name, tc.dispatched_at"
            " FROM tool_calls tc JOIN task_runs tr ON tr.id = tc.task_run_id"
            " WHERE tr.conversation_id = ? AND tc.status = 'pending_verification'"
            " ORDER BY tc.dispatched_at",
            (conversation_id,),
        ).fetchall()
        return [{"call_id": r[0], "tool": r[1], "dispatched_at": r[2]}
                for r in rows]

    def _unresolved_approvals(self, conversation_id: str) -> list[dict]:
        rows = self._db.read_conn.execute(
            "SELECT re.task_run_id, json_extract(re.payload, '$.tool_call_id'),"
            "       json_extract(re.payload, '$.tool'), re.created_at"
            " FROM run_events re"
            " WHERE re.conversation_id = ? AND re.type = 'permission.requested'"
            "   AND re.task_run_id IN (SELECT id FROM task_runs"
            "       WHERE conversation_id = ?"
            "       AND status NOT IN ('completed', 'failed', 'cancelled'))"
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM run_events r2 WHERE r2.type = 'permission.resolved'"
            "     AND json_extract(r2.payload, '$.tool_call_id')"
            "         = json_extract(re.payload, '$.tool_call_id'))",
            (conversation_id, conversation_id),
        ).fetchall()
        return [{"task_run_id": r[0], "call_id": r[1], "tool": r[2],
                 "requested_at": r[3]} for r in rows]

    async def cancel_queue_items(self, conversation_id: str,
                                 item_ids: list[str] | None,
                                 cancel_all: bool) -> list[str]:
        async with await self._conv_lock(conversation_id):
            await asyncio.to_thread(self.conversation_or_404, conversation_id)
            conv = await asyncio.to_thread(
                self._conversation_row, conversation_id)
            items = json.loads(conv["pending_queue"] or "[]")
            targets = set()
            for item in items:
                if item.get("state") != "queued":
                    continue
                if cancel_all or item.get("id") in (item_ids or ()):
                    targets.add(item["id"])
            if not targets:
                return []  # 无可取消项（幂等：重复取消返回空）
            await self._store.append(
                task_run_id=None, conversation_id=conversation_id,
                type=RunEventType.QUEUE_ITEM_CANCELLED,
                payload={"item_ids": sorted(targets)},
            )
            return sorted(targets)

    # ── state 快照（含 at_global_seq，v1.9 从 C3 移入本卡）─────────

    def state_snapshot(self, conversation_id: str) -> dict[str, Any]:
        self.conversation_or_404(conversation_id)
        fsm, head = self._fsm_sync(conversation_id)
        # can_continue_queue 与 POST queue/continue 同一判定源（外审回稿）：
        # 待核验/未决审批是接口的阻断条件，能力字段必须同样纳入
        verifications = self._pending_verifications(conversation_id)
        approvals = self._unresolved_approvals(conversation_id)
        return {
            "state": fsm.state,
            "waiting_approvals": fsm.waiting_approvals,
            "waiting_questions": fsm.waiting_questions,
            "queue_paused": fsm.queue_paused,
            "at_global_seq": head,
            "queue": [item.as_dict() for item in fsm.queued_items],
            "can_send": can_send(fsm),
            "can_queue": can_queue(fsm),
            "can_cancel": can_cancel(fsm),
            "can_continue_queue": can_continue_queue(
                fsm, pending_verifications=len(verifications),
                unresolved_approvals=len(approvals)),
            "current_task_run_id": fsm.current_task_run_id,
        }

    # ── 列表 ─────────────────────────────────────────────────────

    def list_conversations(self) -> list[dict[str, Any]]:
        rows = self._db.read_conn.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
        return [self._summary(row) for row in rows]
