"""Skill不可变版本，统一接入M1变更意图和任务引用。"""

import json
import uuid
from dataclasses import asdict

from agentcrew_core.events import RunEventType
from agentcrew_core.memory import context_entries, failure, sha256, suspected_injection
from ..db.audit import append_audit
from .resources import GovernanceError, canonical


class SkillVersions:
    def __init__(self, resources, memory):
        self.resources, self.memory, self.db = resources, memory, memory.db

    def replayed_publish(self, identity, skill_id, request):
        row = self.db.read_conn.execute("SELECT store_type,store_id,plan,status,result FROM memory_changes WHERE change_id=?", (request["change_id"],)).fetchone()
        if row is None:
            return None
        plan = json.loads(row[2])
        context = plan.get("request_context")
        expected = {"operation": "version_publish", "body": request}
        if row[0] != "skill" or row[1] != skill_id or plan["identity"] != asdict(identity) or \
                not isinstance(context, dict) or context.get("management_request") != expected:
            raise GovernanceError("IDEMPOTENCY_CONFLICT", "change_id 已绑定不同原始请求")
        if row[3] != "committed":
            raise GovernanceError("STORE_RECOVERING", "技能版本发布正在恢复", 503)
        return self.version(json.loads(row[4])["version_id"])

    def _publish(self, conn, *, skill_id, workspace, name, description, version_no, change_id,
                 ledger_id, content, metadata, files, source, created_at):
        version_id = uuid.uuid5(uuid.NAMESPACE_URL, "agentcrew:skill-version:" + change_id).hex
        existing = conn.execute("SELECT id FROM skill_versions WHERE change_id=?", (change_id,)).fetchone()
        if existing:
            return existing[0]
        if conn.execute("SELECT 1 FROM skills WHERE id=?", (skill_id,)).fetchone() is None:
            conn.execute("INSERT INTO skills(id,workspace_id,name,description,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (skill_id, workspace, name, description, "agent" if source["actor_type"] == "agent" else "user", created_at, created_at))
        conn.execute("INSERT INTO skill_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (version_id, skill_id, version_no, change_id,
            ledger_id, content, canonical(metadata), canonical(files), sha256(content.encode()), source["actor_id"], created_at))
        conn.execute("""UPDATE skills SET name=?,description=?,current_version_id=?,revision=revision+1,updated_at=? WHERE id=? AND
            (current_version_id IS NULL OR (SELECT version_no FROM skill_versions WHERE id=current_version_id)<=?)""",
            (name, description, version_id, created_at, skill_id, version_no))
        archived = bool(metadata["entries"] and metadata["entries"][0]["state"] == "archived")
        conn.execute("UPDATE skills SET status=CASE WHEN status='disabled' THEN 'disabled' ELSE ? END WHERE id=? AND current_version_id=?",
            ("archived" if archived else "active", skill_id, version_id))
        return version_id

    def publish_in_tx(self, conn, plan):
        skill_id, metadata = plan["store_id"], plan["after_metadata"]
        prefix = f"skills/{skill_id}/"
        files = [{"path": item["path"][len(prefix):], "content": item["after"], "sha256": item["after_sha256"]}
            for item in plan["files"] if item["path"].startswith(prefix) and item["path"][len(prefix):] in metadata.get("files", {})]
        version_id = self._publish(conn, skill_id=skill_id, workspace=plan["identity"]["workspace_id"], name=metadata["name"],
            description=metadata["description"], version_no=plan["revision"], change_id=plan["change_id"], ledger_id=plan["ledger_id"],
            content=plan["after_text"], metadata=metadata, files=files, source=plan["source"], created_at=plan["created_at"])
        management = (plan.get("request_context") or {}).get("management_resource")
        if management is not None and "status" in management["body"]:
            conn.execute("UPDATE skills SET status=? WHERE id=?", (management["body"]["status"], skill_id))
        payload = {"change_id": plan["change_id"], "resource_type": "skill", "resource_id": skill_id, "revision": self.resources.get("skill", skill_id, conn)["revision"],
            "actor_id": plan["source"]["actor_id"], "credential_owner_id": "owner", "scope": self.resources.scope("skill", skill_id, conn),
            "version_id": version_id, "version_no": plan["revision"], "ledger_id": plan["ledger_id"], "sha256": sha256(plan["after_text"].encode()),
            "status": self.resources.get("skill", skill_id, conn)["status"]}
        audit_seq = append_audit(conn, ts=plan["created_at"], actor_type=plan["source"]["actor_type"], actor_id=plan["source"]["actor_id"],
            action="governance.skill_version_published", resource_type="skill", resource_id=skill_id, detail=canonical(payload))
        event = self.memory.events.append_in_tx(conn, task_run_id=None, conversation_id=plan["identity"]["conversation_id"],
            type=RunEventType.GOVERNANCE_SKILL_VERSION_PUBLISHED, payload={**payload, "audit_seq": audit_seq})
        result = {"version_id": version_id, "version_no": plan["revision"]}
        creation = (plan.get("request_context") or {}).get("management_request", {}).get("operation") == "governance_create"
        if management is not None or creation:
            result["governance_resource"] = self.resources.get("skill", skill_id, conn)
        return result, event, audit_seq

    def version(self, version_id, conn=None):
        connection = conn if conn is not None else self.db.read_conn
        cursor = connection.execute("SELECT * FROM skill_versions WHERE id=?", (version_id,))
        row = cursor.fetchone()
        if row is None:
            raise GovernanceError("NOT_FOUND", "技能版本不存在", 404)
        result = dict(zip((column[0] for column in cursor.description), row))
        result["metadata"], result["files"] = json.loads(result["metadata"]), json.loads(result["files"])
        if sha256(result["content"].encode()) != result["sha256"]:
            raise GovernanceError("EXTERNAL_MODIFICATION", "技能版本校验失败")
        for file in result["files"]:
            if file["content"] is None or sha256(file["content"].encode()) != file["sha256"]:
                raise GovernanceError("EXTERNAL_MODIFICATION", "技能版本支撑文件校验失败")
        return result

    def task_snapshot(self, conn, agent_id, spec):
        result = {}
        for skill_id in spec.get("skill_ids", []):
            row = conn.execute("""SELECT s.current_version_id FROM skills s JOIN grants g ON g.resource_type='skill' AND g.resource_id=s.id
                WHERE s.id=? AND s.status='active' AND g.grantee_type='agent' AND g.grantee_id=? AND g.revoked_at IS NULL""", (skill_id, agent_id)).fetchone()
            if row is None or row[0] is None:
                continue
            version = self.version(row[0], conn)
            if context_entries(version["metadata"]["entries"]):
                result[skill_id] = row[0]
        return result

    def task_bindings(self, identity):
        row = self.db.read_conn.execute("SELECT skill_versions FROM task_governance WHERE task_run_id=?", (identity.task_run_id,)).fetchone()
        if row is None:
            raise GovernanceError("OUT_OF_SCOPE", "任务缺少技能版本快照", 403)
        return json.loads(row[0])

    def permitted(self, identity, skill_id):
        row = self.db.read_conn.execute("SELECT workspace_id,status FROM skills WHERE id=?", (skill_id,)).fetchone()
        if row is None or row[0] != identity.workspace_id or row[1] != "active":
            return False
        return self.db.read_conn.execute("SELECT 1 FROM grants WHERE resource_type='skill' AND resource_id=? AND grantee_type='agent' AND grantee_id=? AND revoked_at IS NULL", (skill_id, identity.agent_id)).fetchone() is not None

    def task_index(self, identity):
        items = []
        for skill_id, version_id in self.task_bindings(identity).items():
            if not self.permitted(identity, skill_id):
                continue
            version = self.version(version_id)
            metadata = version["metadata"]
            entries = context_entries(metadata["entries"])
            if not entries or any(suspected_injection(text) for text in (metadata["name"], metadata["description"], *metadata.get("files", {}))):
                continue
            entry = entries[0]
            items.append({"id": skill_id, "workspace_id": identity.workspace_id, "agent_id": identity.agent_id, "name": metadata["name"],
                "description": metadata["description"], "revision": version["version_no"], "version_id": version_id,
                "state": entry["state"], "hits": entry["hits"], "last_hit_at": entry["last_hit_at"]})
        return {"items": sorted(items, key=lambda item: (item["name"], item["id"]))}

    async def task_view(self, identity, name, file, call_id, reader):
        self.memory.identities.task(identity.task_run_id)
        bindings = self.task_bindings(identity)
        selected = next(((skill_id, self.version(version_id)) for skill_id, version_id in bindings.items()
            if self.version(version_id)["metadata"]["name"] == name), None)
        if selected is None:
            exists = self.db.read_conn.execute("SELECT id FROM skills WHERE workspace_id=? AND name=?", (identity.workspace_id, name)).fetchone()
            if exists:
                return failure("OUT_OF_SCOPE", "技能没有绑定到当前任务版本")
            if file is not None:
                return failure("NOT_FOUND", "技能不存在")
            await reader._record_read(identity, name, 0, call_id)
            return {"name": name, "exists": False, "revision": 0}
        skill_id, version = selected
        if not self.permitted(identity, skill_id):
            return failure("OUT_OF_SCOPE", "技能当前授权或资源状态已失效")
        metadata = version["metadata"]
        entries = context_entries(metadata["entries"])
        if not entries or any("[BLOCKED:" in entry["text"] for entry in entries) or any(suspected_injection(text) for text in (metadata["name"], metadata["description"], *metadata.get("files", {}))):
            return failure("REVIEW_REQUIRED", "任务技能版本未通过内容核查")
        if file is None:
            text = version["content"]
            await reader._record_read(identity, name, version["version_no"], call_id)
            await self.memory.record_hits([entries[0]["entry_id"]], use_id=call_id)
        else:
            validated = reader._file(skill_id, file)
            if isinstance(validated, dict):
                return validated
            selected_file = next((item for item in version["files"] if item["path"] == file), None)
            if selected_file is None:
                return failure("NOT_FOUND", "支撑文件不属于任务技能版本")
            text = selected_file["content"]
        if suspected_injection(text):
            return failure("REVIEW_REQUIRED", "任务技能内容包含疑似注入")
        return {"id": skill_id, "name": name, "description": metadata["description"], "exists": True,
            "revision": version["version_no"], "version_id": version["id"], "text": text, "sha256": sha256(text.encode()),
            "files": [item["path"] for item in version["files"]], **({"file": file} if file else {})}

    async def register_history(self):
        def operation(conn):
            count = 0
            for row in conn.execute("SELECT l.*,s.workspace_id FROM memory_ledger l JOIN memory_skills s ON s.id=l.store_id WHERE l.store_type='skill' ORDER BY l.id").fetchall():
                metadata, source = json.loads(row["after_metadata"]), json.loads(row["source"])
                files = [{**item, "path": item["path"].removeprefix(f"skills/{row['store_id']}/")}
                    for item in json.loads(row["after_files"]) if item["path"].removeprefix(f"skills/{row['store_id']}/") in metadata.get("files", {})]
                if conn.execute("SELECT 1 FROM skill_versions WHERE change_id=?", (row["change_id"],)).fetchone():
                    continue
                self._publish(conn, skill_id=row["store_id"], workspace=row["workspace_id"], name=metadata["name"], description=metadata["description"],
                    version_no=row["revision"], change_id=row["change_id"], ledger_id=row["id"], content=row["after_text"], metadata=metadata,
                    files=files, source=source, created_at=row["created_at"])
                count += 1
            for row in conn.execute("SELECT g.task_run_id,s.body FROM task_governance g JOIN task_runs t ON t.id=g.task_run_id LEFT JOIN memory_snapshots s ON s.conversation_id=t.conversation_id WHERE g.skill_binding_version=0").fetchall():
                versions = {}
                for item in (json.loads(row[1]).get("skill_index", []) if row[1] else []):
                    version = conn.execute("SELECT id FROM skill_versions WHERE skill_id=? AND version_no=?", (item["id"], item["revision"])).fetchone()
                    if version is None:
                        raise GovernanceError("EXTERNAL_MODIFICATION", "历史任务技能修订没有对应版本")
                    versions[item["id"]] = version[0]
                conn.execute("UPDATE task_governance SET skill_versions=?,skill_binding_version=1 WHERE task_run_id=?", (canonical(versions), row[0]))
            return {"id": "skill-history", "revision": 1, "registered_versions": count}, {"org_id": "demo-org", "workspace_id": None, "agent_id": None, "owner_id": "owner"}, {"status": "active"}
        watermark = self.db.read_conn.execute("SELECT coalesce(max(id),0) FROM memory_ledger WHERE store_type='skill'").fetchone()[0]
        return await self.resources.mutate(change_id=f"m2-skill-history-{watermark}", actor_id="owner", request={}, action="governance.skill_history_registered",
            kind="identity", resource_id="skill-history", operation=operation)
