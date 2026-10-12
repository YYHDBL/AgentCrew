"""可重复执行的旧身份登记与演示组织种子，不覆盖已有业务变更。"""

from __future__ import annotations

import sqlite3
import uuid
import json
from pathlib import Path

from agentcrew_core.events import RunEventType
from ..db.audit import append_audit
from ..memory.skills import MemorySkills
from ..memory.store import MemoryIdentity
from .resources import GovernanceError, canonical, identifier, now


ORG = "demo-org"
DEMO_AGENTS = (("xiaowen", "office", "小文", "办公助理"), ("xiaogang", "analytics", "小刚", "数据分析"))


def _grant(conn, kind, resource_id, grantee_type, grantee_id, change_id):
    conn.execute("""INSERT INTO grants(id,resource_type,resource_id,grantee_type,grantee_id,
        grantee_agent_id,grantee_user_id,granted_by_user_id,created_at,change_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (uuid.uuid5(uuid.NAMESPACE_URL, change_id).hex, kind, resource_id, grantee_type, grantee_id,
         grantee_id if grantee_type == "agent" else None, grantee_id if grantee_type == "user" else None,
         "owner", now(), change_id))
    if kind == "skill":
        conn.execute("INSERT INTO skill_references VALUES('agent',?,?,1,?) ON CONFLICT(resource_type,resource_id,skill_id) DO UPDATE SET active=1",
            (grantee_id, resource_id, now()))


async def seed(resources, memory):
    def bootstrap(conn):
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM governance_seed_steps WHERE id='legacy-identities'").fetchone():
                return
            timestamp = now()
            conn.execute("INSERT INTO organizations(id,name,slug,created_at,updated_at) VALUES(?,?,?,?,?)",
                         (ORG, "演示科技有限公司", "demo", timestamp, timestamp))
            for user_id, name, role in (("owner", "所有者", "owner"), ("wangming", "王明", "admin"), ("lilei", "李蕾", "member")):
                conn.execute("INSERT INTO users(id,name,created_at) VALUES(?,?,?)", (user_id, name, timestamp))
                conn.execute("INSERT INTO memberships(id,org_id,user_id,role,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                             ("membership-" + user_id, ORG, user_id, role, timestamp, timestamp))
            pairs = {(row[0], row[1]) for row in conn.execute("SELECT workspace_id,agent_id FROM conversations UNION SELECT workspace_id,agent_id FROM memory_skills UNION SELECT workspace_id,agent_id FROM memory_jobs")}
            pairs.add(("default", "default"))
            workspace_ids = {workspace for workspace, _ in pairs}
            workspace_ids.update(row[0] for row in conn.execute("SELECT store_id FROM memory_stores WHERE store_type='workspace'"))
            mapping = {}
            for workspace, agent in sorted(pairs):
                identifier(workspace)
                identifier(agent)
                if agent in mapping and mapping[agent] != workspace:
                    raise GovernanceError("LEGACY_IDENTITY_CONFLICT", "历史员工关联多个工作区，必须核查映射")
                mapping[agent] = workspace
            for row in conn.execute("SELECT store_id FROM memory_stores WHERE store_type='soul'"):
                if row[0] not in mapping:
                    raise GovernanceError("LEGACY_IDENTITY_CONFLICT", "历史Soul缺少员工工作区来源")
            for row in conn.execute("SELECT agent_id,created_by_user_id FROM agent_permission_rules"):
                if row[0] not in mapping or row[1] not in (None, "owner"):
                    raise GovernanceError("LEGACY_IDENTITY_CONFLICT", "历史规则的员工或创建用户未能验证")
            for row in conn.execute("SELECT store_id FROM memory_stores WHERE store_type='user'"):
                if row[0] != "owner":
                    raise GovernanceError("LEGACY_IDENTITY_CONFLICT", "历史用户库身份未能验证")
            for row in conn.execute("SELECT source FROM memory_ledger"):
                source = json.loads(row[0])
                if source["actor_type"] == "user" and source["actor_id"] != "owner" or source["actor_type"] == "agent" and source["actor_id"] not in mapping:
                    raise GovernanceError("LEGACY_IDENTITY_CONFLICT", "历史账本创建身份未能验证")
            for workspace in sorted(workspace_ids):
                identifier(workspace)
                resources.create_workspace(conn, workspace, "默认工作区" if workspace == "default" else workspace)
                conn.execute("UPDATE workspaces SET data_dir=? WHERE id=?", (str(resources.data_dir / "workspaces" / workspace), workspace))
            for agent, workspace in sorted(mapping.items()):
                resources.create_agent(conn, agent, workspace, "默认员工" if agent == "default" else agent,
                    {"position": "通用助理", "model_slot": "main", "skill_ids": [], "connector_ids": []})
            for row in conn.execute("SELECT * FROM memory_skills").fetchall():
                if conn.execute("SELECT 1 FROM memory_stores WHERE store_type='skill' AND store_id=?", (row["id"],)).fetchone() is None:
                    if conn.execute("SELECT 1 FROM memory_changes WHERE store_type='skill' AND store_id=? AND status='prepared'", (row["id"],)).fetchone():
                        raise GovernanceError("STORE_RECOVERING", "旧Skill写入意图尚未恢复", 503)
                    raise GovernanceError("LEGACY_IDENTITY_CONFLICT", "历史Skill缺少正文修订")
                conn.execute("INSERT INTO skills(id,workspace_id,name,description,source,created_at,updated_at) VALUES(?,?,?,?,'user',?,?)",
                    (row["id"], row["workspace_id"], row["name"], row["description"], row["created_at"], timestamp))
                _grant(conn, "skill", row["id"], "agent", row["agent_id"], "legacy-skill-" + row["id"])
                prior = resources.get("agent", row["agent_id"], conn)["spec"]
                prior["skill_ids"].append(row["id"])
                conn.execute("UPDATE agents SET spec=? WHERE id=?", (canonical(prior), row["agent_id"]))
            for row in conn.execute("SELECT id,workspace_id,agent_id FROM conversations").fetchall():
                conn.execute("INSERT INTO governance_conversations VALUES(?,?,?,'owner','owner')", tuple(row))
            for row in conn.execute("SELECT id,agent_id FROM agent_permission_rules").fetchall():
                conn.execute("INSERT INTO governance_rule_owners(rule_id,agent_id,created_by_user_id) VALUES(?,?,'owner')", tuple(row))
            for row in conn.execute("SELECT id FROM memory_jobs").fetchall():
                conn.execute("INSERT INTO job_governance VALUES(?,'owner','owner')", (row[0],))
            for workspace in ("office", "analytics"):
                if workspace not in workspace_ids:
                    resources.create_workspace(conn, workspace, "日常办公" if workspace == "office" else "数据分析")
                for user_id in ("wangming", "lilei"):
                    conn.execute("INSERT INTO workspace_members VALUES(?,?,1,1)", (workspace, user_id))
            for agent, workspace, name, position in DEMO_AGENTS:
                if agent in mapping:
                    raise GovernanceError("LEGACY_IDENTITY_CONFLICT", "演示员工标识与历史员工冲突")
                resources.create_agent(conn, agent, workspace, name,
                    {"position": position, "model_slot": "main", "skill_ids": [], "connector_ids": []})
            seq = append_audit(conn, ts=timestamp, actor_type="user", actor_id="owner", action="governance.seed",
                resource_type="organization", resource_id=ORG, detail=canonical({"legacy_mapping": mapping,
                    "credential_owner_id": "owner", "scope": {"org_id": ORG, "workspace_id": None, "agent_id": None, "owner_id": "owner"}}))
            event = resources.events.append_in_tx(conn, task_run_id=None, conversation_id=None,
                type=RunEventType.GOVERNANCE_RESOURCE_CHANGED, payload={"change_id": "seed-legacy", "resource_type": "organization",
                    "resource_id": ORG, "revision": 1, "actor_id": "owner", "credential_owner_id": "owner", "audit_seq": seq,
                    "status": "active", "scope": {"org_id": ORG, "workspace_id": None, "agent_id": None, "owner_id": "owner"}})
            conn.execute("INSERT INTO governance_seed_steps VALUES('legacy-identities',?)", (timestamp,))
        resources.events.publish(event)
    await resources.events.channel.execute(bootstrap)
    skills = MemorySkills(memory)
    for agent, workspace, name, position in DEMO_AGENTS:
        for skill_name, description, body in (("Excel处理", "核对表头并处理表格材料", "# Excel处理\n核对原始文件、表头和数据类型，确认处理目标后保存并核验输出。"),
                ("文档模板", "核查来源并组织文档内容", "# 文档模板\n核对材料来源，按标题、正文和依据组织内容，保存后检查完整正文。")) if agent == "xiaowen" else (
                ("SQL查询", "通过已授权数据库查询分析数据", "# SQL查询\n确认数据库范围与查询目标，使用参数化SQL读取真实数据并核验结果。"),):
            step = "seed-skill-" + agent + "-" + uuid.uuid5(uuid.NAMESPACE_URL, skill_name).hex
            if resources.db.read_conn.execute("SELECT 1 FROM governance_seed_steps WHERE id=?", (step,)).fetchone():
                continue
            result = await skills.change(MemoryIdentity(workspace, agent), skill_name, action="create", change_id=step,
                expected_revision=0, basis="所有者授权的演示种子技能", description=description, text=body)
            if "error" in result:
                raise GovernanceError(result["error"], result["message"])
            def register(conn):
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    if conn.execute("SELECT 1 FROM skills WHERE id=?", (result["store_id"],)).fetchone() is None:
                        conn.execute("INSERT INTO skills(id,workspace_id,name,description,source,created_at,updated_at) VALUES(?,?,?,?,'system',?,?)",
                            (result["store_id"], workspace, skill_name, description, now(), now()))
                    _grant(conn, "skill", result["store_id"], "agent", agent, step)
                    spec = resources.get("agent", agent, conn)["spec"]
                    spec["skill_ids"].append(result["store_id"])
                    conn.execute("UPDATE agents SET spec=? WHERE id=?", (canonical(spec), agent))
                    conn.execute("INSERT INTO governance_seed_steps VALUES(?,?)", (step, now()))
                    scope = resources.scope("agent", agent, conn)
                    payload = {"change_id": step, "resource_type": "skill", "resource_id": result["store_id"],
                        "revision": 1, "actor_id": "owner", "credential_owner_id": "owner", "status": "active", "scope": scope,
                        "grantee_id": agent, "grantee_type": "agent"}
                    seq = append_audit(conn, ts=now(), actor_type="user", actor_id="owner", action="governance.skill_seeded",
                        resource_type="skill", resource_id=result["store_id"], detail=canonical(payload))
                    event = resources.events.append_in_tx(conn, task_run_id=None, conversation_id=None,
                        type=RunEventType.GOVERNANCE_RESOURCE_CHANGED, payload={**payload, "audit_seq": seq})
                resources.events.publish(event)
            await resources.events.channel.execute(register)
    def finish(conn):
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM governance_seed_steps WHERE id='demo-grants'").fetchone():
                return
            timestamp = now()
            conn.execute("INSERT INTO connectors(id,workspace_id,name,type,config,created_at,updated_at) VALUES('office-http','office','HTTP','http',?,?,?)",
                (canonical({"url": "https://example.com", "allowed_hosts": ["example.com"], "allowed_ports": [443], "allow_loopback": False}), timestamp, timestamp))
            _grant(conn, "connector", "office-http", "agent", "xiaowen", "seed-http")
            for agent, workspace, _, _ in DEMO_AGENTS:
                _grant(conn, "agent", agent, "user", "lilei", "seed-visibility-" + agent)
            spec = resources.get("agent", "xiaowen", conn)["spec"]
            spec["connector_ids"].append("office-http")
            conn.execute("UPDATE agents SET spec=? WHERE id='xiaowen'", (canonical(spec),))
            rule_id = "seed-office-write"
            pattern = resources.get("workspace", "office", conn)["data_dir"]
            conn.execute("INSERT INTO agent_permission_rules VALUES(?,'xiaowen','write_file',?,'allow','owner',?,NULL)", (rule_id, pattern, timestamp))
            conn.execute("INSERT INTO governance_rule_owners(rule_id,agent_id,created_by_user_id,change_id) VALUES(?,'xiaowen','owner',?)", (rule_id, rule_id))
            seq = append_audit(conn, ts=timestamp, actor_type="user", actor_id="owner", action="governance.seed_completed",
                resource_type="organization", resource_id=ORG, detail=canonical({"scope": {"org_id": ORG, "workspace_id": None, "agent_id": None, "owner_id": "owner"}}))
            event = resources.events.append_in_tx(conn, task_run_id=None, conversation_id=None,
                type=RunEventType.GOVERNANCE_RESOURCE_CHANGED, payload={"change_id": "seed-demo-grants", "resource_type": "organization",
                    "resource_id": ORG, "revision": 1, "actor_id": "owner", "credential_owner_id": "owner", "audit_seq": seq,
                    "status": "active", "scope": {"org_id": ORG, "workspace_id": None, "agent_id": None, "owner_id": "owner"}})
            conn.execute("INSERT INTO governance_seed_steps VALUES('demo-grants',?)", (timestamp,))
        resources.events.publish(event)
    await resources.events.channel.execute(finish)
    for row in resources.db.read_conn.execute("SELECT data_dir FROM workspaces"):
        path = Path(row[0])
        if not path.is_relative_to(resources.data_dir / "workspaces") or path.is_symlink():
            raise GovernanceError("OUT_OF_SCOPE", "工作区数据目录超出服务管理范围", 403)
        path.mkdir(parents=True, exist_ok=True)
