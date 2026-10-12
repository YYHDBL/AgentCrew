"""M1-05：Skill 身份、实际正文读取修订及真实资源引用。"""

V6_STATEMENTS = (
    """CREATE TABLE memory_skills (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, agent_id TEXT NOT NULL,
        name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
        description TEXT NOT NULL CHECK(length(description) BETWEEN 1 AND 60),
        created_at TEXT NOT NULL, UNIQUE(workspace_id,agent_id,name))""",
    """CREATE TABLE skill_reads (
        execution_id TEXT NOT NULL, user_turn_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL, agent_id TEXT NOT NULL, name TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 0), call_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(execution_id,user_turn_id,workspace_id,agent_id,name))""",
    """CREATE TABLE skill_references (
        resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
        skill_id TEXT NOT NULL REFERENCES memory_skills(id),
        active INTEGER NOT NULL CHECK(active IN (0,1)), created_at TEXT NOT NULL,
        PRIMARY KEY(resource_type,resource_id,skill_id))""",
)
