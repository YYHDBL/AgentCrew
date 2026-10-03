"""M2-02：治理资源、身份、引用与授权，保留既有执行表。"""

V11_STATEMENTS = (
    """CREATE TABLE organizations(
        id TEXT PRIMARY KEY,name TEXT NOT NULL,slug TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled','archived')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
    """CREATE TABLE users(id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),created_at TEXT NOT NULL)""",
    """CREATE TABLE memberships(id TEXT PRIMARY KEY,org_id TEXT NOT NULL REFERENCES organizations(id),
        user_id TEXT NOT NULL REFERENCES users(id),role TEXT NOT NULL CHECK(role IN ('owner','admin','member')),
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
        UNIQUE(org_id,user_id))""",
    """CREATE TABLE workspaces(id TEXT PRIMARY KEY,org_id TEXT NOT NULL REFERENCES organizations(id),
        name TEXT NOT NULL,data_dir TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled','archived')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
    """CREATE TABLE workspace_members(workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        user_id TEXT NOT NULL REFERENCES users(id),enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),PRIMARY KEY(workspace_id,user_id))""",
    """CREATE TABLE agents(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        name TEXT NOT NULL,spec TEXT NOT NULL CHECK(json_valid(spec)),
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled','archived')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),created_by_user_id TEXT NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(workspace_id,id))""",
    """CREATE TABLE skills(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        name TEXT NOT NULL,description TEXT NOT NULL CHECK(length(description)<=60),current_version_id TEXT,
        source TEXT NOT NULL CHECK(source IN ('user','agent','system')),
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled','archived')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
        UNIQUE(workspace_id,name),FOREIGN KEY(id,current_version_id) REFERENCES skill_versions(skill_id,id))""",
    """CREATE TABLE skill_versions(id TEXT PRIMARY KEY,skill_id TEXT NOT NULL REFERENCES skills(id),
        version_no INTEGER NOT NULL CHECK(version_no>0),change_id TEXT NOT NULL UNIQUE,
        ledger_id INTEGER NOT NULL UNIQUE REFERENCES memory_ledger(id),content TEXT NOT NULL,
        metadata TEXT NOT NULL CHECK(json_valid(metadata)),files TEXT NOT NULL CHECK(json_valid(files)),
        sha256 TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(skill_id,version_no),UNIQUE(skill_id,id))""",
    """CREATE TRIGGER skill_versions_immutable_update BEFORE UPDATE ON skill_versions
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SKILL_VERSION'); END""",
    """CREATE TRIGGER skill_versions_immutable_delete BEFORE DELETE ON skill_versions
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SKILL_VERSION'); END""",
    """CREATE TABLE connectors(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        name TEXT NOT NULL,type TEXT NOT NULL CHECK(type IN ('http','mcp')),config TEXT NOT NULL CHECK(json_valid(config)),
        credential TEXT,status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled','archived')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
        UNIQUE(workspace_id,name))""",
    """CREATE TABLE governance_resources(resource_type TEXT NOT NULL CHECK(resource_type IN ('skill','connector','agent')),
        resource_id TEXT NOT NULL,workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        PRIMARY KEY(resource_type,resource_id))""",
    """CREATE TRIGGER register_governance_agent AFTER INSERT ON agents BEGIN
        INSERT INTO governance_resources VALUES('agent',NEW.id,NEW.workspace_id); END""",
    """CREATE TRIGGER register_governance_skill AFTER INSERT ON skills BEGIN
        INSERT INTO governance_resources VALUES('skill',NEW.id,NEW.workspace_id); END""",
    """CREATE TRIGGER register_governance_connector AFTER INSERT ON connectors BEGIN
        INSERT INTO governance_resources VALUES('connector',NEW.id,NEW.workspace_id); END""",
    """CREATE TABLE grants(id TEXT PRIMARY KEY,resource_type TEXT NOT NULL,resource_id TEXT NOT NULL,
        grantee_type TEXT NOT NULL CHECK(grantee_type IN ('agent','user')),grantee_id TEXT NOT NULL,
        grantee_agent_id TEXT REFERENCES agents(id),grantee_user_id TEXT REFERENCES users(id),
        granted_by_user_id TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,revoked_at TEXT,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),change_id TEXT NOT NULL UNIQUE,
        FOREIGN KEY(resource_type,resource_id) REFERENCES governance_resources(resource_type,resource_id),
        CHECK((grantee_type='agent' AND grantee_agent_id IS NOT NULL AND grantee_agent_id=grantee_id AND grantee_user_id IS NULL
               AND resource_type IN ('skill','connector')) OR
              (grantee_type='user' AND grantee_user_id IS NOT NULL AND grantee_user_id=grantee_id AND grantee_agent_id IS NULL AND resource_type='agent')))""",
    """CREATE UNIQUE INDEX unique_active_grant ON grants(resource_type,resource_id,grantee_type,grantee_id)
        WHERE revoked_at IS NULL""",
    """CREATE TRIGGER grant_same_workspace BEFORE INSERT ON grants WHEN NEW.grantee_type='agent'
        AND (SELECT workspace_id FROM agents WHERE id=NEW.grantee_id) !=
            (SELECT workspace_id FROM governance_resources WHERE resource_type=NEW.resource_type AND resource_id=NEW.resource_id)
        BEGIN SELECT RAISE(ABORT,'CROSS_WORKSPACE_GRANT'); END""",
    """CREATE TRIGGER grant_same_organization BEFORE INSERT ON grants WHEN NEW.grantee_type='user'
        AND NOT EXISTS(SELECT 1 FROM memberships m JOIN workspaces w ON w.org_id=m.org_id
            JOIN governance_resources r ON r.workspace_id=w.id
            WHERE m.user_id=NEW.grantee_id AND r.resource_type=NEW.resource_type AND r.resource_id=NEW.resource_id)
        BEGIN SELECT RAISE(ABORT,'CROSS_ORGANIZATION_GRANT'); END""",
    """CREATE TABLE governance_conversations(conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),agent_id TEXT NOT NULL REFERENCES agents(id),
        credential_owner_id TEXT NOT NULL REFERENCES users(id),effective_user_id TEXT NOT NULL REFERENCES users(id),
        FOREIGN KEY(workspace_id,agent_id) REFERENCES agents(workspace_id,id))""",
    """CREATE TABLE governance_rule_owners(rule_id TEXT PRIMARY KEY REFERENCES agent_permission_rules(id),
        agent_id TEXT NOT NULL REFERENCES agents(id),created_by_user_id TEXT NOT NULL REFERENCES users(id),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),change_id TEXT UNIQUE)""",
    """CREATE TABLE governance_changes(change_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,
        actor_id TEXT NOT NULL REFERENCES users(id),result TEXT NOT NULL CHECK(json_valid(result)),
        audit_seq INTEGER NOT NULL REFERENCES audit_log(seq),global_seq INTEGER NOT NULL REFERENCES run_events(global_seq))""",
    """CREATE TABLE governance_seed_steps(id TEXT PRIMARY KEY,created_at TEXT NOT NULL)""",
    """CREATE TABLE demo_identities(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
        credential_owner_id TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,revoked_at TEXT,
        change_id TEXT NOT NULL UNIQUE)""",
    """CREATE TABLE task_governance(task_run_id TEXT PRIMARY KEY REFERENCES task_runs(id),
        effective_user_id TEXT NOT NULL REFERENCES users(id),credential_owner_id TEXT NOT NULL REFERENCES users(id),
        agent_revision INTEGER NOT NULL,agent_spec TEXT NOT NULL CHECK(json_valid(agent_spec)),
        skill_versions TEXT NOT NULL CHECK(json_valid(skill_versions)),created_at TEXT NOT NULL)""",
    """CREATE TABLE job_governance(job_id TEXT PRIMARY KEY REFERENCES memory_jobs(id),
        effective_user_id TEXT NOT NULL REFERENCES users(id),credential_owner_id TEXT NOT NULL REFERENCES users(id))""",
)
