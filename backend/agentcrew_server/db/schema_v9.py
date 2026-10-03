"""M1-09：持久化事件计数、水位及独立作业审批。"""

V9_STATEMENTS = (
    "CREATE TABLE memory_review_state(id INTEGER PRIMARY KEY CHECK(id=1),start_global_seq INTEGER NOT NULL)",
    "INSERT INTO memory_review_state VALUES(1,(SELECT COALESCE(MAX(global_seq),0) FROM run_events))",
    """CREATE TABLE memory_trigger_state(
        conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
        user_turns INTEGER NOT NULL DEFAULT 0, tool_iterations INTEGER NOT NULL DEFAULT 0,
        memory_watermark INTEGER NOT NULL DEFAULT 0, skill_watermark INTEGER NOT NULL DEFAULT 0,
        user_event_global_seq INTEGER NOT NULL DEFAULT 0, tool_event_global_seq INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE memory_counted_events(
        global_seq INTEGER PRIMARY KEY REFERENCES run_events(global_seq),
        conversation_id TEXT NOT NULL REFERENCES conversations(id),kind TEXT NOT NULL CHECK(kind IN ('user','tool')))""",
    """CREATE TABLE memory_job_approvals(
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES memory_jobs(id),
        call_id TEXT NOT NULL, input_hash TEXT NOT NULL, tool TEXT NOT NULL,
        input TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','allowed','rejected','expired')),
        requested_at TEXT NOT NULL, resolved_at TEXT, decision TEXT,
        UNIQUE(job_id,call_id))""",
)
