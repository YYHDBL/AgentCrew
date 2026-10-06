"""M3-09：真人确认绑定的模型技能固化作业。"""

V19_STATEMENTS = (
    """CREATE TABLE skill_promotions(
        job_id TEXT PRIMARY KEY REFERENCES memory_jobs(id),report_id TEXT NOT NULL UNIQUE REFERENCES trace_reports(id),
        effective_user_id TEXT NOT NULL,client_request_id TEXT NOT NULL,proposal TEXT NOT NULL CHECK(json_valid(proposal)),
        skill_id TEXT NOT NULL,name TEXT NOT NULL,expected_revision INTEGER NOT NULL,confirmed_at TEXT NOT NULL,
        version_id TEXT REFERENCES skill_versions(id),result TEXT,
        UNIQUE(effective_user_id,client_request_id))""",
    """CREATE TRIGGER promotion_binding_immutable BEFORE UPDATE ON skill_promotions
        WHEN NEW.job_id<>OLD.job_id OR NEW.report_id<>OLD.report_id OR NEW.effective_user_id<>OLD.effective_user_id
        OR NEW.client_request_id<>OLD.client_request_id OR NEW.proposal<>OLD.proposal OR NEW.skill_id<>OLD.skill_id
        OR NEW.name<>OLD.name OR NEW.expected_revision<>OLD.expected_revision OR NEW.confirmed_at<>OLD.confirmed_at
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_PROMOTION_BINDING'); END""",
)
