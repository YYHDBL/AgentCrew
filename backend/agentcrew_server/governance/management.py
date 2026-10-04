"""治理资源的当前访问、修订与管理写入。"""

import json
import uuid
from pathlib import Path
from dataclasses import asdict

from agentcrew_core.memory.pagination import memory_page
from agentcrew_core.governance import role_allows
from ..memory.store import MemoryIdentity
from .resources import GovernanceError, canonical, now


class Management:
    def __init__(self, runtime):
        self.runtime = runtime
        self.resources, self.identities, self.db = runtime.governance, runtime.identities, runtime.db

    def access(self, identity, kind, resource_id, *, write=False, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        member = self.identities.current(identity, connection)
        resource = self.resources.get(kind, resource_id, connection)
        workspace_id = resource_id if kind == "workspace" else resource.get("workspace_id")
        org_id = resource_id if kind == "organization" else resource["org_id"] if kind == "workspace" else self.resources.get("workspace", workspace_id, connection)["org_id"]
        if org_id != member["org_id"] or not role_allows(member["role"], "manage" if write else "use"):
            raise GovernanceError("OUT_OF_SCOPE", "资源归属或当前角色禁止该操作", 403)
        if kind == "organization":
            if write and member["role"] != "owner":
                raise GovernanceError("OUT_OF_SCOPE", "组织管理要求owner", 403)
            return resource
        if member["role"] != "owner" and connection.execute("SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=? AND enabled=1",
                (workspace_id, identity.effective_user_id)).fetchone() is None:
            raise GovernanceError("OUT_OF_SCOPE", "工作区未向当前身份登记", 403)
        if member["role"] == "member":
            if resource["status"] != "active" or self.resources.get("workspace", workspace_id, connection)["status"] != "active":
                raise GovernanceError("OUT_OF_SCOPE", "资源或工作区已经禁用", 403)
            if kind == "agent":
                self.identities.agent(identity, resource_id, workspace_id, connection)
            elif kind == "skill" and connection.execute("""SELECT 1 FROM grants s JOIN agents a ON a.id=s.grantee_id
                JOIN grants u ON u.resource_type='agent' AND u.resource_id=a.id
                WHERE s.resource_type='skill' AND s.resource_id=? AND s.grantee_type='agent' AND s.revoked_at IS NULL
                AND a.status='active' AND u.grantee_type='user' AND u.grantee_id=? AND u.revoked_at IS NULL""",
                (resource_id, identity.effective_user_id)).fetchone() is None:
                raise GovernanceError("OUT_OF_SCOPE", "成员没有当前技能授权", 403)
        return resource

    def list(self, identity, kind, workspace_id, limit, after):
        member = self.identities.current(identity)
        self.identities.require(identity, "organization_manage" if member["role"] == "owner" else "use")
        if workspace_id is not None:
            self.access(identity, "workspace", workspace_id)
        table = self.resources.TABLES[kind]
        if kind == "workspace":
            rows = self.db.read_conn.execute("SELECT id FROM workspaces WHERE org_id=? AND (? IS NULL OR id=?) ORDER BY created_at,id",
                (member["org_id"], workspace_id, workspace_id)).fetchall()
        else:
            rows = self.db.read_conn.execute(f"SELECT r.id FROM {table} r JOIN workspaces w ON w.id=r.workspace_id WHERE w.org_id=? AND (? IS NULL OR r.workspace_id=?) ORDER BY r.created_at,r.id",
                (member["org_id"], workspace_id, workspace_id)).fetchall()
        items = []
        for row in rows:
            resource = self.resources.get(kind, row[0])
            workspace = row[0] if kind == "workspace" else resource["workspace_id"]
            if member["role"] == "admin" and self.db.read_conn.execute("SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=? AND enabled=1",
                    (workspace, identity.effective_user_id)).fetchone() is None:
                continue
            if member["role"] == "member" and workspace not in member["workspace_ids"]:
                continue
            if member["role"] == "member":
                if resource["status"] != "active":
                    continue
                if kind == "agent" and self.db.read_conn.execute("SELECT 1 FROM grants WHERE resource_type='agent' AND resource_id=? AND grantee_type='user' AND grantee_id=? AND revoked_at IS NULL",
                    (row[0], identity.effective_user_id)).fetchone() is None:
                    continue
                if kind == "skill" and self.db.read_conn.execute("""SELECT 1 FROM grants s JOIN agents a ON a.id=s.grantee_id
                    JOIN grants u ON u.resource_type='agent' AND u.resource_id=a.id WHERE s.resource_type='skill' AND s.resource_id=?
                    AND s.grantee_type='agent' AND s.revoked_at IS NULL AND a.status='active' AND u.grantee_type='user'
                    AND u.grantee_id=? AND u.revoked_at IS NULL""", (row[0], identity.effective_user_id)).fetchone() is None:
                    continue
            items.append(resource)
        return memory_page(items, {"identity": member, "kind": kind, "workspace": workspace_id, "order": "created_at,id"}, limit, after, key="id")

    async def create(self, identity, kind, body):
        member = self.identities.require(identity, "manage")
        resource_id = uuid.uuid5(uuid.NAMESPACE_URL, f'agentcrew:{kind}:{body["change_id"]}').hex
        if kind == "agent":
            self.access(identity, "workspace", body["workspace_id"], write=True)
        def operation(conn):
            if kind == "workspace":
                value = self.resources.create_workspace(conn, resource_id, body["name"], member["org_id"])
                conn.execute("INSERT INTO workspace_members VALUES(?,?,1,1)", (resource_id, identity.effective_user_id))
            else:
                self.access(identity, "workspace", body["workspace_id"], write=True, conn=conn)
                value = self.resources.create_agent(conn, resource_id, body["workspace_id"], body["name"], body["spec"], identity.effective_user_id)
            if body.get("status", "active") != "active":
                conn.execute(f"UPDATE {self.resources.TABLES[kind]} SET status=? WHERE id=?", (body["status"], resource_id))
                value = self.resources.get(kind, resource_id, conn)
            return value, self.resources.scope(kind, resource_id, conn), {"status": value["status"]}
        value = await self.resources.mutate(change_id=body["change_id"], actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request=body, action=f"governance.{kind}_created", kind=kind,
            resource_id=resource_id, operation=operation, authorize=lambda conn: self.identities.require(identity, "manage", conn=conn))
        if kind == "workspace":
            Path(value["data_dir"]).mkdir(parents=True, exist_ok=True)
        return value

    async def update(self, identity, kind, resource_id, body):
        self.access(identity, kind, resource_id, write=True)
        def operation(conn):
            current = self.access(identity, kind, resource_id, write=True, conn=conn)
            if current["revision"] != body["expected_revision"]:
                raise GovernanceError("REVISION_CONFLICT", "资源修订已经改变")
            if kind == "agent" and "spec" in body:
                self.resources.validate_agent_spec(conn, current["workspace_id"], body["spec"])
            for key in ("name", "status", "spec"):
                if key in body:
                    conn.execute(f"UPDATE {self.resources.TABLES[kind]} SET {key}=? WHERE id=?",
                        (canonical(body[key]) if key == "spec" else body[key], resource_id))
            conn.execute(f"UPDATE {self.resources.TABLES[kind]} SET revision=revision+1,updated_at=? WHERE id=?", (now(), resource_id))
            value = self.resources.get(kind, resource_id, conn)
            scope = {"org_id": resource_id, "workspace_id": None, "agent_id": None, "owner_id": None} if kind == "organization" else self.resources.scope(kind, resource_id, conn)
            return value, scope, {"status": value["status"]}
        return await self.resources.mutate(change_id=body["change_id"], actor_id=identity.effective_user_id,
            credential_owner_id=identity.credential_owner_id, request=body, action=f"governance.{kind}_changed", kind=kind,
            resource_id=resource_id, operation=operation, authorize=lambda conn: self.access(identity, kind, resource_id, write=True, conn=conn))

    async def skill_update(self, identity, skill_id, body):
        resource = self.access(identity, "skill", skill_id, write=True)
        owner = self.db.read_conn.execute("SELECT agent_id FROM memory_skills WHERE id=?", (skill_id,)).fetchone()
        actor = MemoryIdentity(resource["workspace_id"], owner[0], actor_id=identity.effective_user_id)
        context = {"management_resource": {"operation": "skill_update", "body": body, "skill_id": skill_id}}
        replay = self.db.read_conn.execute("SELECT input_hash,result,plan FROM memory_changes WHERE change_id=?", (body["change_id"],)).fetchone()
        if replay is not None:
            plan = json.loads(replay[2])
            if plan.get("request_context") != context or plan["identity"] != asdict(actor):
                raise GovernanceError("IDEMPOTENCY_CONFLICT", "change_id已经绑定不同技能管理请求")
            if replay[1] is None:
                raise GovernanceError("STORE_RECOVERING", "技能管理意图需要完成恢复", 503)
            return json.loads(replay[1])["governance_resource"]
        if resource["revision"] != body["expected_revision"]:
            raise GovernanceError("REVISION_CONFLICT", "技能资源修订已经改变")
        loaded = await self.runtime.memory.read(actor, "skill", skill_id)
        if "error" in loaded:
            raise GovernanceError(loaded["error"], loaded["message"])
        first = loaded["entries"][0]
        status = body.get("status", resource["status"])
        action = "archive" if status == "archived" else "restore" if first["state"] == "archived" and status == "active" else "edit"
        operation = {"action": action, "entry_hash": first["entry_hash"]}
        if action == "edit":
            operation["text"] = first["text"]
        result = await self.runtime.memory.change(actor, "skill", skill_id, change_id=body["change_id"],
            expected_revision=loaded["revision"], basis="人类管理技能名称、描述与资源状态", operations=[operation],
            skill={"name": body.get("name", resource["name"]), "description": body.get("description", resource["description"])}, request_context=context)
        if "error" in result:
            raise GovernanceError(result["error"], result["message"])
        return result["governance_resource"]
