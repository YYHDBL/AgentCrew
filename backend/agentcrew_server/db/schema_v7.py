"""M1-06：独立辅助作业、唯一任务摘要及原生检索索引。"""

V7_STATEMENTS = (
    """CREATE TABLE memory_job_state (
        id INTEGER PRIMARY KEY CHECK(id=1), start_global_seq INTEGER NOT NULL)""",
    "INSERT INTO memory_job_state VALUES(1,(SELECT COALESCE(MAX(global_seq),0) FROM run_events))",
    """CREATE TABLE memory_jobs (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('summary','memory_review','skill_review','curate')),
        trigger_key TEXT NOT NULL, trigger_global_seq INTEGER NOT NULL REFERENCES run_events(global_seq),
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        task_run_id TEXT NOT NULL REFERENCES task_runs(id),
        workspace_id TEXT NOT NULL, agent_id TEXT NOT NULL,
        model TEXT, config_version TEXT NOT NULL, config_snapshot TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','running','waiting_approval',
            'completed','cancelled','interrupted','failed')),
        usage TEXT NOT NULL DEFAULT '{"input_tokens":0,"output_tokens":0}',
        error TEXT, report TEXT, created_at TEXT NOT NULL, finished_at TEXT,
        UNIQUE(kind,trigger_key))""",
    """CREATE TABLE memory_job_calls (
        job_id TEXT NOT NULL REFERENCES memory_jobs(id), ordinal INTEGER NOT NULL,
        type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(job_id,ordinal))""",
    """CREATE TABLE session_summaries (
        id TEXT PRIMARY KEY, task_run_id TEXT NOT NULL UNIQUE REFERENCES task_runs(id),
        job_id TEXT NOT NULL UNIQUE REFERENCES memory_jobs(id),
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        text TEXT NOT NULL CHECK(length(text) BETWEEN 1 AND 200), created_at TEXT NOT NULL)""",
    "CREATE INDEX session_summaries_recent ON session_summaries(conversation_id,created_at DESC,id DESC)",
    """CREATE TRIGGER session_summaries_immutable BEFORE UPDATE ON session_summaries BEGIN
        SELECT RAISE(ABORT,'completed summaries are immutable'); END""",
    """CREATE TRIGGER session_summaries_delete_immutable BEFORE DELETE ON session_summaries BEGIN
        SELECT RAISE(ABORT,'completed summaries are immutable'); END""",
    "CREATE VIRTUAL TABLE summaries_fts USING fts5(text,content='session_summaries',tokenize='trigram')",
    """CREATE TRIGGER summaries_fts_insert AFTER INSERT ON session_summaries BEGIN
        INSERT INTO summaries_fts(rowid,text) VALUES(new.rowid,new.text); END""",
    """CREATE TRIGGER summaries_fts_delete AFTER DELETE ON session_summaries BEGIN
        INSERT INTO summaries_fts(summaries_fts,rowid,text) VALUES('delete',old.rowid,old.text); END""",
)
