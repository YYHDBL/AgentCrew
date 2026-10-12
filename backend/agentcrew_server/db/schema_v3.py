"""M1-02：三库、持久化意图、不可修改账本及每回合保存次数。"""

V3_STATEMENTS = (
    """CREATE TABLE run_events_v3 (
        global_seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE, task_run_id TEXT REFERENCES task_runs(id),
        seq INTEGER, conversation_id TEXT REFERENCES conversations(id),
        agent_run_id TEXT, attempt_no INTEGER, type TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)""",
    """INSERT INTO run_events_v3 SELECT * FROM run_events ORDER BY global_seq""",
    "DROP TABLE run_events",
    "ALTER TABLE run_events_v3 RENAME TO run_events",
    """CREATE UNIQUE INDEX idx_run_events_task_seq ON run_events(task_run_id, seq)
        WHERE task_run_id IS NOT NULL""",
    "CREATE INDEX idx_run_events_conversation ON run_events(conversation_id, global_seq)",
    "CREATE INDEX idx_run_events_task ON run_events(task_run_id, seq)",
    """CREATE TABLE memory_stores (
        store_type TEXT NOT NULL, store_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        text TEXT NOT NULL, metadata TEXT NOT NULL,
        text_sha256 TEXT NOT NULL, metadata_sha256 TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(store_type, store_id))""",
    """CREATE TABLE memory_entries (
        entry_id TEXT PRIMARY KEY, store_type TEXT NOT NULL, store_id TEXT NOT NULL,
        entry_hash TEXT NOT NULL, text TEXT NOT NULL, state TEXT NOT NULL
        CHECK(state IN ('active','stale','archived','pinned')),
        hits INTEGER NOT NULL DEFAULT 0, last_hit_at TEXT,
        created_at TEXT NOT NULL, source TEXT NOT NULL, basis TEXT NOT NULL,
        needs_review INTEGER NOT NULL CHECK(needs_review IN (0,1)),
        approved_by TEXT, approved_at TEXT,
        UNIQUE(store_type, store_id, entry_hash),
        FOREIGN KEY(store_type,store_id) REFERENCES memory_stores(store_type,store_id))""",
    """CREATE TABLE memory_changes (
        change_id TEXT PRIMARY KEY, input_hash TEXT NOT NULL,
        store_type TEXT NOT NULL, store_id TEXT NOT NULL,
        expected_revision INTEGER NOT NULL, plan TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('prepared','committed')),
        result TEXT, created_at TEXT NOT NULL, committed_at TEXT)""",
    """CREATE UNIQUE INDEX idx_memory_pending_store ON memory_changes(store_type,store_id)
        WHERE status='prepared'""",
    """CREATE TABLE memory_ledger (
        id INTEGER PRIMARY KEY, change_id TEXT NOT NULL UNIQUE REFERENCES memory_changes(change_id),
        store_type TEXT NOT NULL, store_id TEXT NOT NULL, action TEXT NOT NULL,
        revision INTEGER NOT NULL, before_text TEXT NOT NULL, after_text TEXT NOT NULL,
        before_metadata TEXT NOT NULL, after_metadata TEXT NOT NULL,
        before_files TEXT NOT NULL, after_files TEXT NOT NULL,
        source TEXT NOT NULL, basis TEXT NOT NULL,
        restored_ledger_id INTEGER REFERENCES memory_ledger(id),
        audit_seq INTEGER NOT NULL REFERENCES audit_log(seq),
        global_seq INTEGER NOT NULL UNIQUE REFERENCES run_events(global_seq), created_at TEXT NOT NULL)""",
    """CREATE TRIGGER memory_ledger_no_update BEFORE UPDATE ON memory_ledger
        BEGIN SELECT RAISE(ABORT,'memory_ledger immutable'); END""",
    """CREATE TRIGGER memory_ledger_no_delete BEFORE DELETE ON memory_ledger
        BEGIN SELECT RAISE(ABORT,'memory_ledger immutable'); END""",
    """CREATE TABLE memory_write_turns (
        execution_id TEXT NOT NULL, user_turn_id TEXT NOT NULL,
        failures INTEGER NOT NULL CHECK(failures BETWEEN 0 AND 3),
        PRIMARY KEY(execution_id,user_turn_id))""",
    """CREATE TABLE memory_failed_attempts (
        change_id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, result TEXT NOT NULL)""",
)
