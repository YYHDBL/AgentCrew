-- 补考整理任务的完整生命周期与全部事件序号。
SELECT id,status,current_attempt_no,created_at,finished_at FROM task_runs
WHERE id='908cd21feaff4a668e284a4ab295db8f';
SELECT seq,global_seq,attempt_no,type FROM run_events
WHERE task_run_id='908cd21feaff4a668e284a4ab295db8f' ORDER BY seq;

-- 连续性、模型投影与工具投影计数。
SELECT r.id,
 (SELECT COUNT(*) FROM run_events e WHERE e.task_run_id=r.id) AS event_count,
 (SELECT MIN(seq) FROM run_events e WHERE e.task_run_id=r.id) AS min_seq,
 (SELECT MAX(seq) FROM run_events e WHERE e.task_run_id=r.id) AS max_seq,
 (SELECT COUNT(*) FROM llm_calls l JOIN steps s ON s.id=l.step_id WHERE s.task_run_id=r.id) AS llm_projection,
 (SELECT COUNT(*) FROM run_events e WHERE e.task_run_id=r.id AND e.type='llm.request_started') AS llm_events,
 (SELECT COUNT(*) FROM tool_calls t WHERE t.task_run_id=r.id) AS tool_projection,
 (SELECT COUNT(*) FROM run_events e WHERE e.task_run_id=r.id AND e.type='tool.prepared') AS tool_events
FROM task_runs r WHERE r.id='908cd21feaff4a668e284a4ab295db8f';

-- 写入次数、实际工具状态与恢复后审批结果。
SELECT json_extract(payload,'$.input.path') AS path,COUNT(*) AS prepared_count
FROM run_events WHERE task_run_id='908cd21feaff4a668e284a4ab295db8f'
 AND type='tool.prepared' AND json_extract(payload,'$.tool_name')='write_file'
GROUP BY json_extract(payload,'$.input.path');
SELECT call_id,tool_name,status FROM tool_calls
WHERE task_run_id='908cd21feaff4a668e284a4ab295db8f' ORDER BY prepared_at;
SELECT global_seq,type,json_extract(payload,'$.tool_call_id') AS tool_call_id,
 json_extract(payload,'$.decision') AS decision FROM run_events
WHERE task_run_id='908cd21feaff4a668e284a4ab295db8f'
 AND type IN ('permission.requested','permission.resolved','run.interrupted','run.resumed','run.completed')
ORDER BY global_seq;
