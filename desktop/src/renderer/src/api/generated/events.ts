/* 从正式事件契约生成，请运行 npm run gen:api。 */

/**
 * 已提交、已鉴权及已脱敏的展示事件；任务seq与global_seq分别保留。
 */
export type EventFrame = {
  global_seq: number;
  type: EventType;
  payload: {
    [k: string]: unknown;
  };
  ts: string;
  task_run_id?: string;
  seq?: number;
  attempt_no?: number | null;
  conversation_id?: string;
  [k: string]: unknown;
} & (
  | {
      type?:
        | "run.queued"
        | "run.started"
        | "run.completed"
        | "run.failed"
        | "run.cancelled"
        | "run.interrupted"
        | "run.resumed"
        | "step.started"
        | "step.completed"
        | "llm.request_started"
        | "llm.request_done"
        | "llm.request_failed"
        | "permission.requested"
        | "permission.resolved"
        | "question.requested"
        | "question.answered"
        | "queue.paused"
        | "queue.resumed"
        | "queue.item_enqueued"
        | "queue.item_cancelled"
        | "artifact.created"
        | "artifact.ready"
        | "artifact.failed"
        | "artifact.missing_detected"
        | "materials.imported"
        | "conversation.updated"
        | "context.compacted"
        | "context.budget_checked"
        | "memory.updated"
        | "memory.archived"
        | "memory.snapshot_created"
        | "skill.patched"
        | "memory.job_status"
        | "memory.summary_created"
        | "memory.approval_requested"
        | "memory.approval_resolved"
        | "memory.curated"
        | "governance.resource_changed"
        | "governance.identity_changed"
        | "governance.role_changed"
        | "governance.grant_changed"
        | "governance.rule_changed"
        | "governance.skill_version_published"
        | "governance.authorization_checked"
        | "governance.audit_verified"
        | "governance.backup_created"
        | "governance.backup_restored"
        | "governance.access_denied"
        | "notification.created"
        | "notification.updated"
        | "runtime.power_changed";
      [k: string]: unknown;
    }
  | {
      type?: "tool.prepared";
      payload?: PreparedTool;
      [k: string]: unknown;
    }
  | {
      type?:
        | "tool.dispatched"
        | "tool.completed"
        | "tool.failed"
        | "tool.skipped_idempotent"
        | "tool.pending_verification"
        | "tool.verification_submitted"
        | "tool.result_externalized";
      payload?: ToolResult;
      [k: string]: unknown;
    }
  | {
      type?: "cron.job_changed";
      payload?: CronChange;
      [k: string]: unknown;
    }
  | {
      type?: "cron.job_fired" | "cron.job_missed" | "cron.job_skipped" | "cron.job_failed" | "cron.job_status";
      payload?: CronOccurrence;
      [k: string]: unknown;
    }
  | {
      type?: "cron.proposal_requested" | "cron.proposal_resolved";
      payload?: CronProposal;
      [k: string]: unknown;
    }
  | {
      type?: "review.job_status";
      payload?: ReviewJob;
      [k: string]: unknown;
    }
  | {
      type?: "audit.reported";
      payload?: AuditReport;
      [k: string]: unknown;
    }
);
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "EventType".
 */
export type EventType =
  | "run.queued"
  | "run.started"
  | "run.completed"
  | "run.failed"
  | "run.cancelled"
  | "run.interrupted"
  | "run.resumed"
  | "step.started"
  | "step.completed"
  | "llm.request_started"
  | "llm.request_done"
  | "llm.request_failed"
  | "tool.prepared"
  | "tool.dispatched"
  | "tool.completed"
  | "tool.failed"
  | "tool.skipped_idempotent"
  | "tool.pending_verification"
  | "permission.requested"
  | "permission.resolved"
  | "question.requested"
  | "question.answered"
  | "queue.paused"
  | "queue.resumed"
  | "queue.item_enqueued"
  | "queue.item_cancelled"
  | "tool.verification_submitted"
  | "artifact.created"
  | "artifact.ready"
  | "artifact.failed"
  | "artifact.missing_detected"
  | "materials.imported"
  | "conversation.updated"
  | "context.compacted"
  | "context.budget_checked"
  | "tool.result_externalized"
  | "memory.updated"
  | "memory.archived"
  | "memory.snapshot_created"
  | "skill.patched"
  | "memory.job_status"
  | "memory.summary_created"
  | "memory.approval_requested"
  | "memory.approval_resolved"
  | "memory.curated"
  | "governance.resource_changed"
  | "governance.identity_changed"
  | "governance.role_changed"
  | "governance.grant_changed"
  | "governance.rule_changed"
  | "governance.skill_version_published"
  | "governance.authorization_checked"
  | "governance.audit_verified"
  | "governance.backup_created"
  | "governance.backup_restored"
  | "governance.access_denied"
  | "cron.job_changed"
  | "cron.job_fired"
  | "cron.job_missed"
  | "cron.job_skipped"
  | "cron.job_failed"
  | "cron.job_status"
  | "cron.proposal_requested"
  | "cron.proposal_resolved"
  | "review.job_status"
  | "audit.reported"
  | "notification.created"
  | "notification.updated"
  | "runtime.power_changed";

/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "Scope".
 */
export interface Scope {
  org_id?: string;
  workspace_id: string | null;
  agent_id: string | null;
  owner_id: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "PreparedTool".
 */
export interface PreparedTool {
  call_id: string;
  tool_name: string;
  input_hash: string;
  input: {
    [k: string]: unknown;
  };
  side_effect_class?: string;
  risk_level?: string;
  read_only_verdict?: boolean;
  step_id?: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "ToolResult".
 */
export interface ToolResult {
  call_id: string;
  output?: string;
  output_summary?: string;
  error?: string;
  details?: {
    [k: string]: unknown;
  };
  artifact_path?: string;
  note?: string | null;
  verdict?: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "CronChange".
 */
export interface CronChange {
  job_id: string;
  revision: number;
  change_id: string;
  enabled: boolean;
  deleted_at: string | null;
  scope: Scope;
  audit_seq: number;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "CronOccurrence".
 */
export interface CronOccurrence {
  job_id: string;
  occurrence_id: string;
  revision: number;
  trigger: "scheduled" | "manual";
  scheduled_at: number | null;
  triggered_at: number;
  source_task_run_id: string | null;
  retry_no: number;
  status: string;
  reason: string | null;
  scope: Scope;
  notification_id: string | null;
  audit_seq: number;
  retry_at?: number | null;
  missed_count?: number;
  missed_through?: number | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "CronProposal".
 */
export interface CronProposal {
  proposal_id: string;
  call_id: string;
  input_hash: string;
  revision: number;
  source_task_run_id?: string;
  scope: Scope;
  decision?: "allow_once" | "reject_once" | "expired";
  selected?: {
    [k: string]: unknown;
  }[];
  actor_id?: string;
  job_id?: string | null;
  audit_seq?: number;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "ReviewJob".
 */
export interface ReviewJob {
  job_id: string;
  kind: "trace_audit" | "promote_skill";
  status: "queued" | "running" | "completed" | "skipped" | "cancelled" | "interrupted" | "failed";
  source_task_run_id: string;
  attempt_no: number | null;
  trigger_global_seq: number;
  model: string | null;
  config_version: string;
  reason: string | null;
  report_id: string | null;
  skill_id: string | null;
  version_id?: string | null;
  usage: {
    [k: string]: unknown;
  } | null;
  scope: Scope;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "EventReference".
 */
export interface EventReference {
  task_run_id: string;
  attempt_no: number | null;
  seq: number;
  global_seq: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "AuditReport".
 */
export interface AuditReport {
  report_id: string;
  job_id: string;
  source_task_run_id: string;
  attempt_no: number | null;
  source_global_seq: number;
  model: string;
  root_cause_event: null | EventReference;
  scope: Scope;
  [k: string]: unknown;
}
