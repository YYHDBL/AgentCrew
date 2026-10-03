"""治理资源与同事务审计、事件和幂等记录。"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agentcrew_core.events import RunEventType
from ..db.audit import append_audit, snapshot_chain_head, SNAPSHOT_EVERY


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("资源标识无效")
    return value


class GovernanceError(ValueError):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class Resources:
    TABLES = {"organization": "organizations", "workspace": "workspaces", "agent": "agents", "skill": "skills", "connector": "connectors"}

    def __init__(self, db, events, data_dir):
        self.db, self.events, self.data_dir = db, events, Path(data_dir).resolve()

    def get(self, kind, resource_id, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        connection.row_factory = sqlite3.Row
        row = connection.execute(f"SELECT * FROM {self.TABLES[kind]} WHERE id=?", (resource_id,)).fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "资源不存在", 404)
        result = dict(row)
        for field in ("spec", "config"):
            if field in result:
                result[field] = json.loads(result[field])
        if "credential" in result:
            result["credential_configured"] = bool(result.pop("credential"))
        return result

    def scope(self, kind, resource_id, conn):
        resource = self.get(kind, resource_id, conn)
        workspace_id = resource_id if kind == "workspace" else resource.get("workspace_id")
        org_id = resource_id if kind == "organization" else self.get("workspace", workspace_id, conn)["org_id"]
        return {"org_id": org_id, "workspace_id": workspace_id,
                "agent_id": resource_id if kind == "agent" else None, "owner_id": None}

    async def mutate(self, *, change_id, actor_id, credential_owner_id="owner", request, action,
                     kind, resource_id, operation, event_type=RunEventType.GOVERNANCE_RESOURCE_CHANGED, authorize=None):
        identifier(change_id)
        digest = hashlib.sha256(canonical({"actor_id": actor_id, "credential_owner_id": credential_owner_id,
            "action": action, "kind": kind, "resource_id": resource_id, "request": request}).encode()).hexdigest()
        def tx(conn):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                if authorize is not None:
                    authorize(conn)
                prior = conn.execute("SELECT input_hash,result FROM governance_changes WHERE change_id=?", (change_id,)).fetchone()
                if prior:
                    if prior[0] != digest:
                        raise GovernanceError("IDEMPOTENCY_CONFLICT", "change_id 已绑定不同请求")
                    return json.loads(prior[1])
                result, scope, extra = operation(conn)
                ts = now()
                payload = {"change_id": change_id, "resource_type": kind, "resource_id": result.get("id", resource_id),
                    "revision": result.get("revision", 1), "actor_id": actor_id, "credential_owner_id": credential_owner_id,
                    "scope": scope, **extra}
                seq = append_audit(conn, ts=ts, actor_type="user", actor_id=actor_id, action=action,
                    resource_type=kind, resource_id=payload["resource_id"], detail=canonical(payload))
                payload["audit_seq"] = seq
                event = self.events.append_in_tx(conn, task_run_id=None, conversation_id=None, type=event_type, payload=payload)
                conn.execute("INSERT INTO governance_changes VALUES(?,?,?,?,?,?)", (change_id, digest, actor_id, canonical(result), seq, event.global_seq))
            self.events.publish(event)
            if seq % SNAPSHOT_EVERY == 0:
                snapshot_chain_head(conn, self.data_dir / "chain-head.txt")
            return result
        return await self.events.channel.execute(tx)

    def create_workspace(self, conn, resource_id, name, org_id="demo-org"):
        identifier(resource_id)
        timestamp = now()
        path = self.data_dir / "workspaces" / resource_id / "files"
        conn.execute("INSERT INTO workspaces(id,org_id,name,data_dir,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (resource_id, org_id, name, str(path), timestamp, timestamp))
        return self.get("workspace", resource_id, conn)

    def create_agent(self, conn, resource_id, workspace_id, name, spec, actor_id="owner"):
        identifier(resource_id)
        if set(spec) != {"position", "model_slot", "skill_ids", "connector_ids"} or not spec["position"] or spec["model_slot"] not in {"main", "aux"}:
            raise GovernanceError("VALIDATION_ERROR", "员工岗位或模型槽无效", 422)
        for key, kind in (("skill_ids", "skill"), ("connector_ids", "connector")):
            for reference in spec[key]:
                resource = self.get(kind, reference, conn)
                if resource["workspace_id"] != workspace_id or resource["status"] != "active":
                    raise GovernanceError("OUT_OF_SCOPE", "员工能力引用超出工作区或已禁用", 403)
        timestamp = now()
        conn.execute("INSERT INTO agents(id,workspace_id,name,spec,created_by_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (resource_id, workspace_id, name, canonical(spec), actor_id, timestamp, timestamp))
        return self.get("agent", resource_id, conn)
