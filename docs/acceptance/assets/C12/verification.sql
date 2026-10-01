-- 单任务顺序、终态与投影计数。
SELECT r.id AS task_run_id,r.status,r.current_attempt_no,
 (SELECT COUNT(*) FROM run_events e WHERE e.task_run_id=r.id) AS event_count,
 (SELECT MIN(seq) FROM run_events e WHERE e.task_run_id=r.id) AS min_seq,
 (SELECT MAX(seq) FROM run_events e WHERE e.task_run_id=r.id) AS max_seq,
 (SELECT COUNT(*) FROM llm_calls l JOIN steps s ON s.id=l.step_id WHERE s.task_run_id=r.id) AS llm_projection,
 (SELECT COUNT(*) FROM run_events e WHERE e.task_run_id=r.id AND e.type='llm.request_started') AS llm_events,
 (SELECT COUNT(*) FROM tool_calls t WHERE t.task_run_id=r.id) AS tool_projection,
 (SELECT COUNT(*) FROM run_events e WHERE e.task_run_id=r.id AND e.type='tool.prepared') AS tool_events,
 (SELECT COUNT(*) FROM run_events e WHERE e.task_run_id=r.id AND e.type='run.completed') AS completed_events
FROM task_runs r ORDER BY r.created_at;

-- 全部任务事件，保留每个序号用于逐项核验。
SELECT task_run_id,seq,global_seq,attempt_no,type FROM run_events
WHERE task_run_id IS NOT NULL ORDER BY global_seq;

-- 写入次数与持久审批规则。
SELECT task_run_id,json_extract(payload,'$.tool_name') AS tool_name,
 json_extract(payload,'$.input.path') AS path,COUNT(*) AS prepared_count
FROM run_events WHERE type='tool.prepared' AND json_extract(payload,'$.tool_name')='write_file'
GROUP BY task_run_id,json_extract(payload,'$.input.path');
SELECT tool_name,pattern,effect FROM agent_permission_rules;
SELECT global_seq,task_run_id,type,payload FROM run_events
WHERE type IN ('permission.requested','permission.resolved','run.interrupted','run.resumed','run.cancelled');

-- 排队指令、消息与实际运行数。
SELECT type,COUNT(*) AS event_count FROM run_events
WHERE conversation_id='29da1528ed1a40f997cc754912c9a4d3'
GROUP BY type ORDER BY type;
SELECT id,status,instruction FROM task_runs
WHERE conversation_id='29da1528ed1a40f997cc754912c9a4d3' ORDER BY created_at;
SELECT role,COUNT(*) AS message_count FROM messages
WHERE conversation_id='29da1528ed1a40f997cc754912c9a4d3' GROUP BY role;
SELECT global_seq,type,payload FROM run_events
WHERE conversation_id='29da1528ed1a40f997cc754912c9a4d3' AND (type LIKE 'queue.%' OR type='run.queued')
ORDER BY global_seq;
