.headers on
.mode csv
SELECT t.id,t.conversation_id,t.status,t.current_attempt_no,
 (SELECT count(*) FROM run_events e WHERE e.task_run_id=t.id) AS event_count,
 (SELECT max(seq) FROM run_events e WHERE e.task_run_id=t.id) AS last_seq,
 (SELECT count(*) FROM llm_calls l JOIN steps s ON s.id=l.step_id WHERE s.task_run_id=t.id) AS llm_calls,
 (SELECT count(*) FROM run_events e WHERE e.task_run_id=t.id AND e.type='llm.request_started') AS llm_events,
 (SELECT count(*) FROM tool_calls c WHERE c.task_run_id=t.id) AS tool_calls,
 (SELECT count(*) FROM run_events e WHERE e.task_run_id=t.id AND e.type='tool.prepared') AS tool_events
FROM task_runs t ORDER BY t.created_at,t.id;
SELECT s.task_run_id,count(*) AS call_count,sum(l.prompt_tokens) AS prompt_tokens,
 sum(l.completion_tokens) AS completion_tokens,sum(l.latency_ms) AS latency_ms,
 sum(l.prompt_tokens IS NULL OR l.completion_tokens IS NULL) AS missing_usage
FROM llm_calls l JOIN steps s ON s.id=l.step_id GROUP BY s.task_run_id;
SELECT task_run_id,attempt_no,kind,status,outcome,started_at,ended_at
FROM run_attempts ORDER BY task_run_id,attempt_no;
SELECT global_seq,task_run_id,seq,attempt_no,type,created_at FROM run_events ORDER BY global_seq;
SELECT task_run_id,call_id,tool_name,status,input_hash,prepared_at,dispatched_at,completed_at FROM tool_calls ORDER BY prepared_at;
SELECT count(*) AS audit_count,max(seq) AS audit_seq FROM audit_log;
SELECT seq,hash FROM audit_log ORDER BY seq DESC LIMIT 1;
