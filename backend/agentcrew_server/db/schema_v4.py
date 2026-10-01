"""M1-03：不可修改会话快照、独立注入统计与真实 soul 生成记录。"""

V4_STATEMENTS = (
    """CREATE TABLE memory_snapshots (
        conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
        snapshot_id TEXT NOT NULL UNIQUE, owner_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL, agent_id TEXT NOT NULL,
        body TEXT NOT NULL, sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('prepared','committed')),
        global_seq INTEGER UNIQUE REFERENCES run_events(global_seq), created_at TEXT NOT NULL)""",
    """CREATE TRIGGER memory_snapshot_immutable BEFORE UPDATE ON memory_snapshots
        WHEN OLD.status='committed'
        BEGIN SELECT RAISE(ABORT,'memory snapshot immutable'); END""",
    """CREATE TRIGGER memory_snapshot_no_delete BEFORE DELETE ON memory_snapshots
        BEGIN SELECT RAISE(ABORT,'memory snapshot immutable'); END""",
    """CREATE TABLE memory_injections (
        snapshot_id TEXT NOT NULL REFERENCES memory_snapshots(snapshot_id),
        entry_id TEXT NOT NULL, store_type TEXT NOT NULL, store_id TEXT NOT NULL,
        blocked INTEGER NOT NULL CHECK(blocked IN (0,1)), created_at TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,entry_id))""",
    """CREATE TABLE memory_usage (
        entry_id TEXT PRIMARY KEY, hits INTEGER NOT NULL CHECK(hits>=0), last_hit_at TEXT NOT NULL)""",
    """CREATE TABLE memory_soul_generations (
        id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        task_run_id TEXT NOT NULL REFERENCES task_runs(id),
        model TEXT NOT NULL, prompt TEXT NOT NULL, events TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled','interrupted')),
        result TEXT, error TEXT, created_at TEXT NOT NULL, finished_at TEXT)""",
)
