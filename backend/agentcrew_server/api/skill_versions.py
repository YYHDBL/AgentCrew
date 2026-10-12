"""技能版本读取、发布和正文恢复，复用唯一M1写入服务。"""

import json

from fastapi import Query, Request

from agentcrew_core.memory.pagination import memory_page
from ..memory.skills import MemorySkills
from ..memory.store import MemoryIdentity
from ..governance.resources import GovernanceError
from .memory import MemoryChangeBody, checked


class SkillVersionBody(MemoryChangeBody):
    restore_version_id: str | None = None


def install_skill_version_routes(app, runtime):
    versions = runtime.skill_versions
    reader = MemorySkills(runtime.memory)

    def authorize(request, skill_id, *, write=False):
        identity = request.state.identity
        resource = runtime.governance.get("skill", skill_id)
        current = runtime.identities.require(identity, "manage" if write else "use", resource["workspace_id"])
        if current["role"] == "member":
            access = runtime.db.read_conn.execute("""SELECT 1 FROM grants skill_grant JOIN agents a ON a.id=skill_grant.grantee_id
                JOIN grants user_grant ON user_grant.resource_type='agent' AND user_grant.resource_id=a.id
                WHERE skill_grant.resource_type='skill' AND skill_grant.resource_id=? AND skill_grant.grantee_type='agent'
                AND skill_grant.revoked_at IS NULL AND a.status='active' AND user_grant.grantee_type='user'
                AND user_grant.grantee_id=? AND user_grant.revoked_at IS NULL""", (skill_id, identity.effective_user_id)).fetchone()
            if access is None or resource["status"] != "active":
                raise GovernanceError("OUT_OF_SCOPE", "成员没有该技能的当前授权", 403)
        return identity, resource

    @app.get("/api/skills/{id}/versions")
    async def list_versions(id: str, request: Request, limit: int = Query(50, ge=1, le=200), after: str | None = None):
        identity, _resource = authorize(request, id)
        rows = runtime.db.read_conn.execute("SELECT id FROM skill_versions WHERE skill_id=? ORDER BY version_no DESC", (id,)).fetchall()
        return memory_page([versions.version(row[0]) for row in rows],
            {"actor": identity.effective_user_id, "skill": id, "order": "version_no DESC"}, limit, after, key="id")

    @app.get("/api/skills/{id}/versions/{version_id}")
    async def get_version(id: str, version_id: str, request: Request):
        authorize(request, id)
        version = versions.version(version_id)
        if version["skill_id"] != id:
            raise GovernanceError("OUT_OF_SCOPE", "版本属于其他技能", 403)
        return version

    @app.post("/api/skills/{id}/versions")
    async def publish_version(id: str, body: SkillVersionBody, request: Request):
        identity, resource = authorize(request, id, write=True)
        owner = runtime.db.read_conn.execute("SELECT agent_id,name FROM memory_skills WHERE id=?", (id,)).fetchone()
        actor = MemoryIdentity(resource["workspace_id"], owner[0], actor_id=identity.effective_user_id)
        replayed = versions.replayed_publish(actor, id, body.model_dump())
        if replayed is not None:
            return replayed
        if body.name is not None and body.name != owner[1]:
            raise GovernanceError("VALIDATION_ERROR", "名称必须对应目标技能身份", 422)
        current = checked(await runtime.memory.read(actor, "skill", id))
        text, description, files = body.text, body.description, body.files
        if body.restore_version_id is not None:
            if any(value is not None for value in (body.text, body.files, body.name, body.description)):
                raise GovernanceError("VALIDATION_ERROR", "版本恢复不能同时指定正文、文件、名称或描述", 422)
            restored = versions.version(body.restore_version_id)
            if restored["skill_id"] != id:
                raise GovernanceError("OUT_OF_SCOPE", "恢复版本属于其他技能", 403)
            text, description = restored["content"], restored["metadata"]["description"]
            files = {path: None for path in current["metadata"].get("files", {})}
            files.update({file["path"]: file["content"] for file in restored["files"]})
        if text is None:
            text = current["text"]
        result = checked(await reader.change(actor, owner[1], action="edit", change_id=body.change_id,
            expected_revision=body.expected_revision, basis=body.basis, text=text, description=description, files=files,
            management_request={"operation": "version_publish", "body": body.model_dump()}))
        return versions.version(result["version_id"])
