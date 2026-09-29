"""M0 初始建表 DDL（版本 1）。

表结构唯一权威：harness-session §2（执行域 12 表）+ governance §2.3/§3
（agent_permission_rules、audit_log）。共 13 张（含 schema_migrations，
后者由迁移 runner 自举，不在本 DDL 内）。

约定：
- JSON 一律存 TEXT（SQLite 无原生 JSON 类型，读取方负责 json.loads）；
- 时间戳一律 UTC ISO-8601 TEXT；
- conversations.workspace_id/agent_id 与 agent_permission_rules.agent_id
  的父表（workspaces/agents）属治理域 M2——当前为普通列，M2 加表后补 FK。
"""

V1_STATEMENTS: tuple[str, ...] = (
    # ── 容器与任务（harness-session §2.1）──────────────────────────
    """
    CREATE TABLE conversations (
        id                  TEXT PRIMARY KEY,
        workspace_id        TEXT NOT NULL,
        agent_id            TEXT NOT NULL,
        title               TEXT,
        status              TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'archived')),
        agent_spec_snapshot TEXT NOT NULL DEFAULT '{}',
        pending_queue       TEXT NOT NULL DEFAULT '[]',
        queue_paused        INTEGER NOT NULL DEFAULT 0,
        folders_json        TEXT NOT NULL DEFAULT '[]',
        created_at          TEXT NOT NULL,
        updated_at          TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE task_runs (
        id                  TEXT PRIMARY KEY,
        conversation_id     TEXT NOT NULL REFERENCES conversations(id),
        instruction         TEXT NOT NULL,
        status              TEXT NOT NULL
                            CHECK (status IN ('queued', 'running', 'waiting_user',
                                              'waiting_verification', 'interrupted',
                                              'completed', 'failed', 'cancelled')),
        current_attempt_no  INTEGER NOT NULL DEFAULT 0,
        version             INTEGER NOT NULL DEFAULT 1,
        cron_job_id         TEXT,
        created_at          TEXT NOT NULL,
        updated_at          TEXT NOT NULL,
        finished_at         TEXT
    )
    """,
    """
    CREATE TABLE run_attempts (
        id                  TEXT PRIMARY KEY,
        task_run_id         TEXT NOT NULL REFERENCES task_runs(id),
        attempt_no          INTEGER NOT NULL,
        kind                TEXT NOT NULL CHECK (kind IN ('initial', 'resume')),
        status              TEXT NOT NULL,
        outcome             TEXT,
        resume_reason       TEXT,
        context_fingerprint TEXT,
        started_at          TEXT NOT NULL,
        ended_at            TEXT,
        UNIQUE (task_run_id, attempt_no)
    )
    """,
    """
    CREATE TABLE run_events (
        global_seq         INTEGER PRIMARY KEY AUTOINCREMENT,
        id                 TEXT NOT NULL UNIQUE,
        task_run_id        TEXT NOT NULL REFERENCES task_runs(id),
        seq                INTEGER NOT NULL,
        conversation_id    TEXT NOT NULL REFERENCES conversations(id),
        agent_run_id       TEXT,
        attempt_no         INTEGER,
        type               TEXT NOT NULL,
        payload            TEXT NOT NULL DEFAULT '{}',
        created_at         TEXT NOT NULL,
        UNIQUE (task_run_id, seq)
    )
    """,
    "CREATE INDEX idx_run_events_conversation ON run_events (conversation_id, global_seq)",
    "CREATE INDEX idx_run_events_task ON run_events (task_run_id, seq)",
    # ── 投影表（harness-session §2.2，可由事件重放全量重建）────────
    """
    CREATE TABLE steps (
        id            TEXT PRIMARY KEY,
        task_run_id   TEXT NOT NULL REFERENCES task_runs(id),
        ordinal       INTEGER NOT NULL,
        model_slot    TEXT,
        input_tokens  INTEGER,
        output_tokens INTEGER,
        latency_ms    INTEGER,
        status        TEXT NOT NULL,
        UNIQUE (task_run_id, ordinal)
    )
    """,
    """
    CREATE TABLE llm_calls (
        id                TEXT PRIMARY KEY,
        step_id           TEXT NOT NULL REFERENCES steps(id),
        model             TEXT NOT NULL,
        prompt_tokens     INTEGER,
        completion_tokens INTEGER,
        latency_ms        INTEGER,
        retry_no          INTEGER NOT NULL DEFAULT 0,
        error             TEXT
    )
    """,
    """
    CREATE TABLE tool_calls (
        id                 TEXT PRIMARY KEY,
        call_id            TEXT NOT NULL UNIQUE,
        task_run_id        TEXT NOT NULL REFERENCES task_runs(id),
        step_id            TEXT REFERENCES steps(id),
        tool_name          TEXT NOT NULL,
        side_effect_class  TEXT NOT NULL
                           CHECK (side_effect_class IN ('verifiable',
                                                        'external_idempotency',
                                                        'outcome_unknown')),
        input_hash         TEXT NOT NULL,
        status             TEXT NOT NULL
                           CHECK (status IN ('prepared', 'dispatched', 'completed',
                                             'outcome_unknown', 'pending_verification',
                                             'not_executed', 'failed')),
        risk_level         TEXT NOT NULL,
        input              TEXT NOT NULL DEFAULT '{}',
        output_summary     TEXT,
        artifact_path      TEXT,
        error              TEXT,
        prepared_at        TEXT NOT NULL,
        dispatched_at      TEXT,
        completed_at       TEXT
    )
    """,
    """
    CREATE TABLE messages (
        id              TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        task_run_id     TEXT REFERENCES task_runs(id),
        role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content         TEXT NOT NULL,
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE task_materials (
        id              TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        original_path   TEXT NOT NULL,
        stored_name     TEXT NOT NULL,
        size_bytes      INTEGER,
        error           TEXT,
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE artifacts (
        id              TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        task_run_id     TEXT NOT NULL REFERENCES task_runs(id),
        tool_call_id    TEXT REFERENCES tool_calls(id),
        path            TEXT NOT NULL,
        name            TEXT NOT NULL,
        ext             TEXT,
        size_bytes      INTEGER,
        status          TEXT NOT NULL
                        CHECK (status IN ('generating', 'ready', 'failed', 'missing')),
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    # ── 治理（governance §2.3/§3；M0 建好备用，写入自 C6 起）────────
    """
    CREATE TABLE agent_permission_rules (
        id                 TEXT PRIMARY KEY,
        agent_id           TEXT NOT NULL,
        tool_name          TEXT NOT NULL,
        pattern            TEXT NOT NULL,
        effect             TEXT NOT NULL CHECK (effect IN ('allow', 'deny')),
        created_by_user_id TEXT,
        created_at         TEXT NOT NULL,
        revoked_at         TEXT
    )
    """,
    """
    CREATE TABLE audit_log (
        seq           INTEGER PRIMARY KEY,
        ts            TEXT NOT NULL,
        actor_type    TEXT NOT NULL CHECK (actor_type IN ('user', 'agent', 'system', 'curator')),
        actor_id      TEXT NOT NULL,
        action        TEXT NOT NULL,
        resource_type TEXT,
        resource_id   TEXT,
        detail        TEXT NOT NULL DEFAULT '{}',
        prev_hash     TEXT NOT NULL,
        hash          TEXT NOT NULL
    )
    """,
)
