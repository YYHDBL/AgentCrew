"""持久通知的权限过滤、已读与 presented 认领（M3-14）。"""

from agentcrew_core.events import RunEventType as T
from agentcrew_core.memory.pagination import memory_page
from ..governance.resources import GovernanceError, now
from ..runs import Runs, display_value

COLUMNS = "id,global_seq,owner_id,workspace_id,agent_id,job_id,task_run_id,kind,severity,message,read_at,presented_at,created_at"


def notification_row(row):
    return display_value(dict(row))


class Notifications:
    def __init__(self, runtime):
        self.runtime, self.db = runtime, runtime.db
        self.identities, self.events = runtime.identities, runtime.event_store

    def visible(self, identity, row, current, conn):
        if row["workspace_id"] not in current["workspace_ids"]:
            return False
        if current["role"] == "member" and (row["owner_id"] != identity.effective_user_id
                                            or not self.identities.visible_scope(identity, row["workspace_id"], row["agent_id"])):
            return False
        if current["role"] != "owner" and row["task_run_id"] is not None:
            origin = conn.execute("SELECT gc.effective_user_id FROM task_runs t JOIN governance_conversations gc ON gc.conversation_id=t.conversation_id WHERE t.id=?",
                (row["task_run_id"],)).fetchone()
            if origin is None or origin[0] != identity.effective_user_id:
                return False
        return True

    def scope(self, row, conn):
        org = conn.execute("SELECT org_id FROM workspaces WHERE id=?", (row["workspace_id"],)).fetchone()
        return {"org_id": org[0] if org else None, "workspace_id": row["workspace_id"],
                "agent_id": row["agent_id"], "owner_id": row["owner_id"]}

    def list(self, identity, workspace, limit, after):
        with Runs(self.runtime).snapshot(identity) as conn:
            current = self.identities.require(identity, "use", workspace, conn)
            rows = [notification_row(row) for row in conn.execute(
                f"SELECT {COLUMNS} FROM runtime_notifications ORDER BY global_seq DESC,id DESC", [])]
            selected = [row for row in rows
                        if (workspace is None or row["workspace_id"] == workspace)
                        and self.visible(identity, row, current, conn)]
            unread = sum(1 for row in selected if row["read_at"] is None)
            return {**memory_page(selected, {"actor": identity.effective_user_id, "workspace": workspace, "order": "global-seq-desc"},
                                  limit, after, key="global_seq"), "unread_count": unread}

    async def mark(self, identity, notification_id, action):
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                current = self.identities.current(identity, conn)
                row = conn.execute(f"SELECT {COLUMNS} FROM runtime_notifications WHERE id=?", (notification_id,)).fetchone()
                if row is None:
                    raise GovernanceError("NOT_FOUND", "通知不存在", 404)
                record = notification_row(row)
                if not self.visible(identity, record, current, conn):
                    raise GovernanceError("OUT_OF_SCOPE", "当前身份无权访问该通知", 403)
                timestamp = now()
                column = "read_at" if action == "read" else "presented_at"
                claimed = conn.execute(f"UPDATE runtime_notifications SET {column}=? WHERE id=? AND {column} IS NULL",
                                       (timestamp, notification_id)).rowcount == 1
                record = notification_row(conn.execute(f"SELECT {COLUMNS} FROM runtime_notifications WHERE id=?", (notification_id,)).fetchone())
                if claimed:
                    event = self.events.append_in_tx(conn, task_run_id=None, conversation_id=None, type=T.NOTIFICATION_UPDATED,
                        payload={"notification_id": notification_id, "read_at": record["read_at"], "presented_at": record["presented_at"],
                                 "scope": self.scope(record, conn)})
                else:
                    event = None
            if event is not None:
                self.events.publish(event)
            return {"notification": record, "claimed": claimed}
        return await self.events.channel.execute(tx)
