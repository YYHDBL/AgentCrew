"""M3-03：计划配置、唯一发生、执行关联和持久通知。"""

V16_STATEMENTS = (
    """CREATE TABLE cron_jobs (
        id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        agent_id TEXT NOT NULL REFERENCES agents(id),owner_id TEXT NOT NULL REFERENCES users(id),
        credential_owner_id TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>0),schedule TEXT NOT NULL CHECK(json_valid(schedule)),
        target TEXT NOT NULL CHECK(json_valid(target)),metadata TEXT NOT NULL CHECK(json_valid(metadata)),
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),anchor_at INTEGER NOT NULL,
        next_run_at INTEGER,last_run_at INTEGER,last_status TEXT,run_count INTEGER NOT NULL DEFAULT 0,
        retry_count INTEGER NOT NULL DEFAULT 0,max_retries INTEGER NOT NULL DEFAULT 3 CHECK(max_retries=3),
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT,
        FOREIGN KEY(workspace_id,agent_id) REFERENCES agents(workspace_id,id))""",
    "CREATE INDEX cron_jobs_due ON cron_jobs(enabled,next_run_at) WHERE deleted_at IS NULL",
    """CREATE TABLE cron_job_runs (
        id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES cron_jobs(id),revision INTEGER NOT NULL,
        trigger TEXT NOT NULL CHECK(trigger IN ('scheduled','manual')),scheduled_at INTEGER,
        triggered_at INTEGER NOT NULL,actor_id TEXT NOT NULL REFERENCES users(id),client_request_id TEXT,
        status TEXT NOT NULL CHECK(status IN ('fired','missed','skipped','failed','completed','interrupted','pending_verification','retry_wait','cancelled')),
        note TEXT,retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 3),retry_at INTEGER,
        task_run_id TEXT REFERENCES task_runs(id),missed_count INTEGER NOT NULL DEFAULT 0,
        missed_through INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
        CHECK((trigger='scheduled' AND scheduled_at IS NOT NULL AND client_request_id IS NULL)
           OR (trigger='manual' AND scheduled_at IS NULL AND client_request_id IS NOT NULL)),
        UNIQUE(job_id,scheduled_at),UNIQUE(job_id,actor_id,client_request_id))""",
    "CREATE INDEX cron_runs_history ON cron_job_runs(job_id,triggered_at,id)",
    """CREATE TABLE cron_run_attempts (
        occurrence_id TEXT NOT NULL REFERENCES cron_job_runs(id),retry_no INTEGER NOT NULL CHECK(retry_no BETWEEN 0 AND 3),
        task_run_id TEXT NOT NULL REFERENCES task_runs(id),attempt_no INTEGER NOT NULL CHECK(attempt_no>0),
        status TEXT NOT NULL,started_at TEXT,finished_at TEXT,error TEXT,
        PRIMARY KEY(occurrence_id,retry_no,attempt_no),UNIQUE(task_run_id,attempt_no))""",
    """CREATE TABLE runtime_notifications (
        id TEXT PRIMARY KEY,global_seq INTEGER NOT NULL UNIQUE REFERENCES run_events(global_seq),
        owner_id TEXT NOT NULL REFERENCES users(id),workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        agent_id TEXT NOT NULL REFERENCES agents(id),job_id TEXT REFERENCES cron_jobs(id),
        task_run_id TEXT REFERENCES task_runs(id),kind TEXT NOT NULL,
        severity TEXT NOT NULL CHECK(severity IN ('badge','warning','error')),message TEXT NOT NULL,
        read_at TEXT,presented_at TEXT,created_at TEXT NOT NULL)""",
)
