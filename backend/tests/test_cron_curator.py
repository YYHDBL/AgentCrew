"""M3-03：启用计划的实际 Skill 引用与 M1 curator 生命周期。"""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone

from agentcrew_core.governance import RequestIdentity
from agentcrew_core.memory.curation import curation_plan
from agentcrew_server.approvals import ApprovalService
from agentcrew_server.bus import EventBus
from agentcrew_server.config import load_config
from agentcrew_server.cron.store import CronStore
from agentcrew_server.governance.grants import Grants
from agentcrew_server.governance.rules import Rules
from agentcrew_server.memory.jobs import MemoryJobs
from agentcrew_server.memory.store import MemoryIdentity
from agentcrew_server.runtime import RuntimeState
from agentcrew_server.settings import SettingsService

from test_governance_skills import governed
from test_governance_resources import resources
from test_cron_store import proposal


def test_actual_curator_uses_and_releases_cron_reference(governed):
    service, memory, versions, sessions, skills = governed
    shutil.copy2(os.environ["AGENTCREW_TEST_CONFIG_SOURCE"], service.data_dir / "config.json")
    settings = SettingsService(load_config(service.data_dir), service.data_dir, service.events.channel, service.data_dir / "chain-head.txt")
    identities = sessions.identities
    approvals = ApprovalService(service.db, service.events, service.data_dir / "chain-head.txt")
    approvals.rules = Rules(service, identities)
    runtime = RuntimeState(log=logging.getLogger("cron-curator"), data_dir=service.data_dir, db=service.db,
        event_store=service.events, governance=service, identities=identities, approvals=approvals,
        sessions=sessions, settings=settings, skill_versions=versions)
    cron = CronStore(runtime)
    grants = Grants(service, identities)
    jobs = MemoryJobs(memory, EventBus(), settings)
    jobs.identities = identities
    jobs.review.sessions.identities = identities
    owner = RequestIdentity("owner", "owner")
    evidence = {}

    async def check():
        created = await skills.change(MemoryIdentity("office", "xiaowen"), "计划引用核查流程", action="create",
            change_id="cron-curator-skill", expected_revision=0, description="核查实际材料来源及编号", basis="所有者建立真实核查流程",
            text="# 计划引用核查流程\n核查材料来源、编号及内容，保存实际文件核验结果。")
        assert "error" not in created, created
        skill_id = created["store_id"]
        grant = await grants.create(owner, {"change_id": "cron-curator-grant", "resource_type": "skill", "resource_id": skill_id,
            "grantee_type": "agent", "grantee_id": "xiaowen"})
        job = await cron.create(owner, proposal({"kind": "every", "every_ms": 86400000, "tz": "UTC"}, "实际curator计划"))
        await grants.revoke(owner, grant["id"], {"change_id": "cron-curator-revoke", "expected_revision": grant["revision"]})
        first = await jobs.curator.enqueue("office", "xiaowen", "cron-curator-enabled", identity=owner)
        assert isinstance(first, str), first
        await jobs.curator.run(first)
        first_report = jobs.review.view(first)["report"]
        assert any(row["store_id"] == skill_id and row["reason"] == "active_skill_reference"
                   and any(reference["resource_type"] == "cron" and reference["resource_id"] == job["id"] for reference in row["references"])
                   for row in first_report["exempt"])
        value = await memory.read(MemoryIdentity("office", "xiaowen"), "skill", skill_id)
        future = datetime.now(timezone.utc) + timedelta(days=31)
        assert curation_plan(value["entries"], now=future, quota=None, referenced=True)[0] == []
        await cron.edit(owner, job["id"], {"change_id": "cron-curator-disabled", "expected_revision": 1, "enabled": False})
        second = await jobs.curator.enqueue("office", "xiaowen", "cron-curator-disabled", identity=owner)
        assert isinstance(second, str), second
        await jobs.curator.run(second)
        second_report = jobs.review.view(second)["report"]
        assert not any(row["store_id"] == skill_id for row in second_report["exempt"])
        refs = jobs.curator.references(skill_id, "office", "xiaowen")
        assert refs == []
        plan, exempt = curation_plan(value["entries"], now=future, quota=None, referenced=bool(refs))
        assert plan[0]["action"] == "archive" and exempt == []
        assert service.db.read_conn.execute("SELECT count(*) FROM llm_calls").fetchone()[0] == 0
        assert service.db.read_conn.execute("SELECT count(*) FROM memory_job_calls").fetchone()[0] == 0
        evidence.update({"job_id": job["id"], "skill_id": skill_id, "first_curator_job": first, "second_curator_job": second,
            "enabled_report": first_report, "disabled_report": second_report,
            "pure_time_parameter": future.isoformat(), "released_curation_plan": plan,
            "actual_clock": datetime.now(timezone.utc).isoformat(), "model_calls": 0})
        await jobs.shutdown()
    asyncio.run(check())
    (service.data_dir / "cron-curator-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
