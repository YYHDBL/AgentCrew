"""当前身份可见的审计查询、全量验证和只读诊断切换。"""

import json
import uuid

from agentcrew_core.events import RunEventType
from agentcrew_core.memory.pagination import memory_page

from ..db.audit import SNAPSHOT_EVERY, append_audit, snapshot_chain_head, verify_levels
from ..runtime import DiagnosticInfo
from ..secrets import redact
from .resources import canonical, now


class AuditService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.db = runtime.db
        self.identities = runtime.identities
        self.path = runtime.data_dir / "chain-head.txt"

    def _workspace(self, row, detail):
        scope = detail.get("scope", {})
        if scope.get("workspace_id"):
            return scope["workspace_id"]
        kind, identifier = row["resource_type"], row["resource_id"]
        statements = {
            "workspace": "SELECT id FROM workspaces WHERE id=?",
            "agent": "SELECT workspace_id FROM agents WHERE id=?",
            "skill": "SELECT workspace_id FROM skills WHERE id=?",
            "connector": "SELECT workspace_id FROM connectors WHERE id=?",
            "grant": "SELECT r.workspace_id FROM grants g JOIN governance_resources r ON r.resource_type=g.resource_type AND r.resource_id=g.resource_id WHERE g.id=?",
            "task_run": "SELECT c.workspace_id FROM task_runs t JOIN conversations c ON c.id=t.conversation_id WHERE t.id=?",
            "tool_call": "SELECT c.workspace_id FROM tool_calls x JOIN task_runs t ON t.id=x.task_run_id JOIN conversations c ON c.id=t.conversation_id WHERE x.call_id=?",
            "memory_job": "SELECT workspace_id FROM memory_jobs WHERE id=?",
        }
        if kind not in statements:
            return None
        found = self.db.read_conn.execute(statements[kind], (identifier,)).fetchone()
        return found[0] if found else None

    def query(self, identity, *, workspace_id=None, actor=None, action=None, resource=None, limit=50, after=None):
        member = self.identities.require(identity, "use", workspace_id)
        rows = self.db.read_conn.execute("SELECT * FROM audit_log ORDER BY seq DESC").fetchall()
        items = []
        for row in rows:
            valid = self.db.read_conn.execute("SELECT json_valid(?)", (row["detail"],)).fetchone()[0]
            object_detail = valid and self.db.read_conn.execute("SELECT json_type(?)", (row["detail"],)).fetchone()[0] == "object"
            detail = json.loads(row["detail"]) if object_detail else {"damaged_detail": row["detail"]}
            workspace = self._workspace(row, detail)
            org = detail.get("scope", {}).get("org_id")
            if workspace is not None:
                actual_org = self.db.read_conn.execute("SELECT org_id FROM workspaces WHERE id=?", (workspace,)).fetchone()
                org = actual_org[0] if actual_org else org
            if org is not None and org != member["org_id"]:
                continue
            if member["role"] == "member" and row["actor_id"] != identity.effective_user_id:
                continue
            if member["role"] != "owner" and workspace is not None and workspace not in member["workspace_ids"]:
                continue
            if member["role"] == "admin" and workspace is None:
                continue
            if workspace_id is not None and workspace != workspace_id:
                continue
            if actor is not None and row["actor_id"] != actor or action is not None and row["action"] != action:
                continue
            if resource is not None and resource not in {row["resource_type"], f'{row["resource_type"]}:{row["resource_id"]}'}:
                continue
            items.append(json.loads(redact(canonical({**dict(row), "detail": detail}))))
        return memory_page(items, {"identity": member, "workspace": workspace_id, "actor": actor,
            "action": action, "resource": resource, "order": "audit-seq-desc"}, limit, after, key="seq")

    async def verify(self, identity):
        def tx(conn):
            self.identities.require(identity, "owner", conn=conn)
            result = verify_levels(conn, self.path)
            if not result["ok"]:
                self.runtime.diagnostic = DiagnosticInfo(reason=f'审计验证失败：{result["reason"]}',
                    hint="请导出诊断记录，选择服务管理的已验证备份恢复，然后重启并完成两级验证")
                self.runtime.write_channel.read_only = True
                return result
            if self.runtime.diagnostic is not None:
                return result
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                payload = {"change_id": uuid.uuid4().hex, "resource_type": "audit", "resource_id": "chain", "revision": 1,
                    "actor_id": identity.effective_user_id, "credential_owner_id": identity.credential_owner_id,
                    "internal": result["internal"], "anchor": result["anchor"],
                    "scope": {"org_id": self.identities.current(identity, conn)["org_id"], "workspace_id": None, "agent_id": None, "owner_id": identity.effective_user_id}}
                seq = append_audit(conn, ts=now(), actor_type="user", actor_id=identity.effective_user_id,
                    action=RunEventType.GOVERNANCE_AUDIT_VERIFIED.value, resource_type="audit", resource_id="chain", detail=canonical(payload))
                payload["audit_seq"] = seq
                event = self.runtime.event_store.append_in_tx(conn, task_run_id=None, conversation_id=None,
                    type=RunEventType.GOVERNANCE_AUDIT_VERIFIED, payload=payload)
            if seq % SNAPSHOT_EVERY == 0:
                snapshot_chain_head(conn, self.path)
            self.runtime.event_store.publish(event)
            return result
        result = await self.runtime.write_channel.execute(tx, diagnostic=True)
        if not result["ok"]:
            await self.runtime.stop_execution()
        return result

    async def denied(self, identity, method, path, code, message):
        def tx(conn):
            if self.runtime.diagnostic is not None:
                return
            organization = conn.execute("SELECT org_id FROM memberships WHERE user_id=?", (identity.effective_user_id,)).fetchone()
            if organization is None:
                return
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                payload = json.loads(redact(canonical({"change_id": uuid.uuid4().hex, "resource_type": "api_request",
                    "resource_id": path, "revision": 1, "actor_id": identity.effective_user_id,
                    "credential_owner_id": identity.credential_owner_id, "method": method, "code": code, "reason": message,
                    "scope": {"org_id": organization[0], "workspace_id": None, "agent_id": None, "owner_id": identity.effective_user_id}})))
                seq = append_audit(conn, ts=now(), actor_type="user", actor_id=identity.effective_user_id,
                    action=RunEventType.GOVERNANCE_ACCESS_DENIED.value, resource_type="api_request", resource_id=payload["resource_id"], detail=canonical(payload))
                payload["audit_seq"] = seq
                event = self.runtime.event_store.append_in_tx(conn, task_run_id=None, conversation_id=None,
                    type=RunEventType.GOVERNANCE_ACCESS_DENIED, payload=payload)
            if seq % SNAPSHOT_EVERY == 0:
                snapshot_chain_head(conn, self.path)
            self.runtime.event_store.publish(event)
        await self.runtime.write_channel.execute(tx, diagnostic=True)
