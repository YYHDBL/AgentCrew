"""M3-07：不可变员工计划提案及真人批准绑定。"""

V17_STATEMENTS = (
    """CREATE TABLE cron_proposals (
        id TEXT PRIMARY KEY,task_run_id TEXT NOT NULL REFERENCES task_runs(id),
        attempt_no INTEGER NOT NULL,call_id TEXT NOT NULL UNIQUE REFERENCES tool_calls(call_id),
        input_hash TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        proposal TEXT NOT NULL CHECK(json_valid(proposal)),candidates TEXT NOT NULL CHECK(json_valid(candidates)),
        selected TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(selected)),
        status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','expired')),
        job_id TEXT UNIQUE REFERENCES cron_jobs(id),decided_by TEXT REFERENCES users(id),
        credential_owner_id TEXT REFERENCES users(id),decided_at TEXT,decision_hash TEXT,
        created_at TEXT NOT NULL,created_audit_seq INTEGER NOT NULL REFERENCES audit_log(seq))""",
    "CREATE INDEX cron_proposals_task ON cron_proposals(task_run_id,attempt_no,status)",
    """CREATE TRIGGER cron_proposal_immutable BEFORE UPDATE ON cron_proposals
        WHEN new.task_run_id<>old.task_run_id OR new.attempt_no<>old.attempt_no OR new.call_id<>old.call_id
        OR new.input_hash<>old.input_hash OR new.proposal<>old.proposal OR new.candidates<>old.candidates
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_PROPOSAL'); END""",
)
