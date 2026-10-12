"""M1-10：无前台任务的治理作业及按范围保存的成功水位。"""

V10_STATEMENTS = (
    """CREATE TABLE memory_jobs_new (
        id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('summary','memory_review','skill_review','curate')),
        trigger_key TEXT NOT NULL,trigger_global_seq INTEGER REFERENCES run_events(global_seq),
        conversation_id TEXT REFERENCES conversations(id),task_run_id TEXT REFERENCES task_runs(id),
        workspace_id TEXT NOT NULL,agent_id TEXT NOT NULL,model TEXT,config_version TEXT NOT NULL,config_snapshot TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','running','waiting_approval','completed','cancelled','interrupted','failed')),
        usage TEXT NOT NULL DEFAULT '{"input_tokens":0,"output_tokens":0}',error TEXT,report TEXT,created_at TEXT NOT NULL,finished_at TEXT,
        CHECK(kind='curate' OR (trigger_global_seq IS NOT NULL AND conversation_id IS NOT NULL AND task_run_id IS NOT NULL)),
        UNIQUE(kind,trigger_key))""",
    "INSERT INTO memory_jobs_new SELECT * FROM memory_jobs",
    "DROP TABLE memory_jobs",
    "ALTER TABLE memory_jobs_new RENAME TO memory_jobs",
    """CREATE TABLE memory_governance(
        workspace_id TEXT NOT NULL,agent_id TEXT NOT NULL,last_curate_at TEXT NOT NULL,
        job_id TEXT NOT NULL REFERENCES memory_jobs(id),report TEXT NOT NULL,
        PRIMARY KEY(workspace_id,agent_id))""",
)
