.headers on
.mode csv
SELECT max(version) AS schema_version FROM schema_migrations;
SELECT id,workspace_id,agent_id,owner_id,revision,enabled,next_run_at,run_count,retry_count,deleted_at FROM cron_jobs ORDER BY created_at,id;
SELECT id,job_id,revision,trigger,scheduled_at,triggered_at,actor_id,client_request_id,status,retry_count FROM cron_job_runs ORDER BY triggered_at,id;
SELECT resource_type,resource_id,skill_id,active FROM skill_references WHERE resource_type='cron' ORDER BY resource_id,skill_id;
SELECT global_seq,task_run_id,seq,type,created_at FROM run_events WHERE type LIKE 'cron.%' ORDER BY global_seq;
SELECT seq,actor_id,action,resource_type,resource_id FROM audit_log WHERE action LIKE 'cron.%' ORDER BY seq;
SELECT max(global_seq) AS event_watermark FROM run_events;
SELECT count(*) AS audit_count,max(seq) AS audit_seq FROM audit_log;
PRAGMA foreign_key_check;
