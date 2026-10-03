"""M1-08：原子压缩事件与不可修改的上下文检查点。"""

V8_STATEMENTS = (
    """CREATE TABLE context_checkpoints (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        task_run_id TEXT NOT NULL REFERENCES task_runs(id),
        attempt_no INTEGER NOT NULL CHECK(attempt_no>=1),
        source_global_seq INTEGER NOT NULL REFERENCES run_events(global_seq),
        event_global_seq INTEGER NOT NULL UNIQUE REFERENCES run_events(global_seq),
        snapshot_id TEXT NOT NULL REFERENCES memory_snapshots(snapshot_id),
        summary_json TEXT NOT NULL,
        aux_model TEXT NOT NULL,
        aux_usage_json TEXT NOT NULL,
        retained_messages_json TEXT NOT NULL,
        artifacts_json TEXT NOT NULL,
        replaced_from_global_seq INTEGER NOT NULL,
        replaced_to_global_seq INTEGER NOT NULL,
        before_tokens INTEGER NOT NULL,
        after_tokens INTEGER NOT NULL,
        summarized_messages INTEGER NOT NULL,
        template_version TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(conversation_id,source_global_seq))""",
    "CREATE INDEX context_checkpoints_latest ON context_checkpoints(conversation_id,event_global_seq DESC)",
    """CREATE TRIGGER context_checkpoints_no_update BEFORE UPDATE ON context_checkpoints
        BEGIN SELECT RAISE(ABORT,'context checkpoints are immutable'); END""",
    """CREATE TRIGGER context_checkpoints_no_delete BEFORE DELETE ON context_checkpoints
        BEGIN SELECT RAISE(ABORT,'context checkpoints are immutable'); END""",
)
