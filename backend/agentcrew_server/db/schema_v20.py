"""绑定每次真人固化确认请求与明确的审查分析要求。"""

V20_STATEMENTS = (
    """CREATE TABLE promotion_requests(effective_user_id TEXT NOT NULL,client_request_id TEXT NOT NULL,
        report_id TEXT NOT NULL REFERENCES trace_reports(id),job_id TEXT NOT NULL REFERENCES memory_jobs(id),
        PRIMARY KEY(effective_user_id,client_request_id))""",
    "INSERT INTO promotion_requests SELECT effective_user_id,client_request_id,report_id,job_id FROM skill_promotions",
    "CREATE TABLE trace_review_requests(job_id TEXT PRIMARY KEY REFERENCES memory_jobs(id),instruction TEXT NOT NULL)",
    "CREATE TRIGGER trace_review_request_immutable BEFORE UPDATE ON trace_review_requests BEGIN SELECT RAISE(ABORT,'IMMUTABLE_REVIEW_REQUEST'); END",
)
