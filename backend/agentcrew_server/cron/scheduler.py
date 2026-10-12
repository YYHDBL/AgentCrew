"""真实时钟的单进程计划等待、错过登记与冲突跳过。"""

import asyncio
import json
import uuid

from agentcrew_core.cron.schedule import missed_range, next_run_at
from agentcrew_core.events import RunEventType as T
from ..bus import Topic
from ..db.audit import append_audit, SNAPSHOT_EVERY, snapshot_chain_head
from ..governance.resources import canonical, now
from .store import now_ms

GRACE_MS = 2000


class CronScheduler:
    def __init__(self, runtime):
        self.runtime, self.store, self.db = runtime, runtime.cron_store, runtime.db
        self.events, self.bus = runtime.event_store, runtime.bus
        self.executor = None
        self._timers = {}
        self._listener = self._subscription = None
        self._heartbeat = None
        self._last_heartbeat = now_ms()
        self._gap = None
        self._closed = False

    async def start(self):
        self._subscription = self.bus.subscribe(Topic("all"), internal=True)
        self._last_heartbeat = now_ms()
        self._heartbeat = asyncio.create_task(self._observe_clock(), name="cron:clock")
        await self.refresh(startup=True)
        self._listener = asyncio.create_task(self._listen(), name="cron:configuration")

    def _observe_gap(self, stamp):
        if stamp - self._last_heartbeat > GRACE_MS:
            self._gap = (self._last_heartbeat, stamp)

    async def _observe_clock(self):
        while not self._closed:
            await asyncio.sleep(0.25)
            stamp = now_ms()
            self._observe_gap(stamp)
            self._last_heartbeat = stamp

    async def _listen(self):
        self._subscription.bind_consumer()
        while not self._closed:
            event = self._subscription.take_nowait()
            if event is None:
                await self._subscription.wait_for_data()
                continue
            if event.type == T.CRON_JOB_CHANGED:
                await self.refresh()

    async def refresh(self, *, startup=False):
        rows = await asyncio.to_thread(lambda: [dict(row) for row in self.db.read_conn.execute(
            "SELECT * FROM cron_jobs WHERE enabled=1 AND deleted_at IS NULL AND next_run_at IS NOT NULL")])
        active = {row["id"] for row in rows}
        cancelled = []
        for job_id, (revision, task) in list(self._timers.items()):
            row = next((row for row in rows if row["id"] == job_id), None)
            if job_id not in active or row["revision"] != revision or startup:
                task.cancel()
                cancelled.append(task)
                del self._timers[job_id]
        if cancelled:
            await asyncio.wait(cancelled)
        for row in rows:
            if row["id"] not in self._timers:
                task = asyncio.create_task(self._timer(row["id"], row["revision"], startup), name="cron:" + row["id"])
                task.add_done_callback(self._finished)
                self._timers[row["id"]] = (row["revision"], task)

    @staticmethod
    def _finished(task):
        if not task.cancelled() and task.exception() is not None:
            raise task.exception()

    async def _timer(self, job_id, revision, startup):
        while not self._closed:
            row = await asyncio.to_thread(lambda: self.db.read_conn.execute("SELECT * FROM cron_jobs WHERE id=?", (job_id,)).fetchone())
            if row is None or not row["enabled"] or row["deleted_at"] or row["revision"] != revision or row["next_run_at"] is None:
                return
            row = dict(row)
            stamp, scheduled = now_ms(), row["next_run_at"]
            self._observe_gap(stamp)
            if scheduled > stamp:
                await asyncio.sleep(min((scheduled - stamp) / 1000, 60))
                startup = False
                continue
            at = json.loads(row["schedule"])["kind"] == "at"
            past_at = at and scheduled <= row["anchor_at"]
            crossed_gap = self._gap is not None and self._gap[0] < scheduled <= self._gap[1]
            missed = startup or past_at or crossed_gap or stamp - scheduled > GRACE_MS
            coverage = await asyncio.to_thread(missed_range, json.loads(row["schedule"]), scheduled, stamp, row["anchor_at"]) if missed else None
            occurrence = await self.register_due(row, stamp, coverage)
            startup = False
            if occurrence is not None and occurrence["status"] == "fired":
                await self.dispatch_registered(occurrence["id"])

    async def register_due(self, expected, triggered_at, coverage):
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM cron_jobs WHERE id=?", (expected["id"],)).fetchone()
                if row is None or not row["enabled"] or row["deleted_at"] or row["revision"] != expected["revision"] or row["next_run_at"] != expected["next_run_at"]:
                    return None
                job = self.store.decode(row)
                scheduled = row["next_run_at"]
                prior = conn.execute("SELECT id FROM cron_job_runs WHERE job_id=? AND scheduled_at=?", (job["id"], scheduled)).fetchone()
                if prior is not None:
                    raise RuntimeError("计划next_run_at重复指向已登记发生")
                note = "错过到期窗口，不补跑" if coverage is not None else self.store.conflict(conn, job)
                status = "missed" if coverage is not None else "skipped" if note else "fired"
                next_at = coverage["next"] if coverage is not None else None if job["schedule"]["kind"] == "at" else next_run_at(job["schedule"], scheduled, row["anchor_at"])
                occurrence_id, timestamp = uuid.uuid4().hex, now()
                conn.execute("INSERT INTO cron_job_runs(id,job_id,revision,trigger,scheduled_at,triggered_at,actor_id,status,note,missed_count,missed_through,created_at,updated_at) VALUES(?,?,?,'scheduled',?,?,?,?,?,?,?,?,?)",
                    (occurrence_id, job["id"], job["revision"], scheduled, triggered_at, row["owner_id"], status, note,
                     coverage["count"] if coverage else 0, coverage["through"] if coverage else None, timestamp, timestamp))
                prepared = self.executor.prepare_in_tx(conn, job, occurrence_id) if self.executor is not None else []
                occurrence = self.store.occurrence(occurrence_id, conn)
                status, note = occurrence["status"], occurrence["note"]
                conn.execute("UPDATE cron_jobs SET next_run_at=?,last_status=?,updated_at=?,enabled=? WHERE id=?",
                    (next_at, status, timestamp, 0 if status == "missed" and job["schedule"]["kind"] == "at" else row["enabled"], job["id"]))
                current = self.store.decode(conn.execute("SELECT * FROM cron_jobs WHERE id=?", (job["id"],)).fetchone())
                self.store.references(conn, current)
                payload = {"job_id": job["id"], "occurrence_id": occurrence_id, "revision": job["revision"], "trigger": "scheduled",
                    "scheduled_at": scheduled, "triggered_at": triggered_at, "source_task_run_id": occurrence["task_run_id"], "retry_no": 0,
                    "status": status, "reason": note, "scope": self.store.scope(job, conn), "notification_id": None,
                    "missed_count": coverage["count"] if coverage else 0, "missed_through": coverage["through"] if coverage else None}
                payload["audit_seq"] = append_audit(conn, ts=timestamp, actor_type="system", actor_id="cron", action="cron.job_" + status,
                    resource_type="cron_job", resource_id=job["id"], detail=canonical(payload))
                kind = {"missed": T.CRON_JOB_MISSED, "skipped": T.CRON_JOB_SKIPPED, "fired": T.CRON_JOB_FIRED, "failed": T.CRON_JOB_FAILED}[status]
                event = self.events.append_in_tx(conn, task_run_id=None, conversation_id=None, type=kind, payload=payload)
                notification = self.executor.notify_in_tx(conn, occurrence, job, event) if self.executor is not None and status != "fired" else None
                result = self.store.occurrence(occurrence_id, conn)
            for queued in prepared:
                self.events.publish(queued)
            self.events.publish(event)
            if notification is not None:
                self.events.publish(notification)
            if payload["audit_seq"] % SNAPSHOT_EVERY == 0:
                snapshot_chain_head(conn, self.runtime.data_dir / "chain-head.txt")
            return result
        return await self.events.channel.execute(tx)

    async def dispatch_registered(self, occurrence_id):
        if self.executor is not None:
            await self.executor.dispatch(occurrence_id)

    async def wake(self):
        await self.refresh(startup=True)

    async def shutdown(self):
        self._closed = True
        tasks = [task for _, task in self._timers.values()]
        if self._listener is not None:
            tasks.append(self._listener)
        if self._heartbeat is not None:
            tasks.append(self._heartbeat)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks)
        self._timers.clear()
        if self._subscription is not None:
            self.bus.unsubscribe(self._subscription)
