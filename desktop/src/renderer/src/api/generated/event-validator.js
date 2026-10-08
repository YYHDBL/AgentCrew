var __getOwnPropNames = Object.getOwnPropertyNames;
var __commonJS = (cb, mod) => function __require() {
  try {
    return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
  } catch (e) {
    throw mod = 0, e;
  }
};

// node_modules/ajv/dist/runtime/ucs2length.js
var require_ucs2length = __commonJS({
  "node_modules/ajv/dist/runtime/ucs2length.js"(exports) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    function ucs2length(str) {
      const len = str.length;
      let length = 0;
      let pos = 0;
      let value;
      while (pos < len) {
        length++;
        value = str.charCodeAt(pos++);
        if (value >= 55296 && value <= 56319 && pos < len) {
          value = str.charCodeAt(pos);
          if ((value & 64512) === 56320)
            pos++;
        }
      }
      return length;
    }
    exports.default = ucs2length;
    ucs2length.code = 'require("ajv/dist/runtime/ucs2length").default';
  }
});

// <stdin>
var validate = validate10;
var stdin_default = validate10;
var schema11 = { "$schema": "http://json-schema.org/draft-07/schema#", "title": "EventFrame", "description": "\u5DF2\u63D0\u4EA4\u3001\u5DF2\u9274\u6743\u53CA\u5DF2\u8131\u654F\u7684\u5C55\u793A\u4E8B\u4EF6\uFF1B\u4EFB\u52A1seq\u4E0Eglobal_seq\u5206\u522B\u4FDD\u7559\u3002", "type": "object", "required": ["global_seq", "type", "payload", "ts"], "properties": { "global_seq": { "type": "integer", "minimum": 1 }, "type": { "$ref": "#/definitions/EventType" }, "payload": { "type": "object" }, "ts": { "type": "string", "minLength": 1 }, "task_run_id": { "type": "string" }, "seq": { "type": "integer", "minimum": 1 }, "attempt_no": { "type": ["integer", "null"], "minimum": 1 }, "conversation_id": { "type": "string" } }, "additionalProperties": true, "oneOf": [{ "properties": { "type": { "enum": ["run.queued", "run.started", "run.completed", "run.failed", "run.cancelled", "run.interrupted", "run.resumed", "step.started", "step.completed", "llm.request_started", "llm.request_done", "llm.request_failed", "permission.requested", "permission.resolved", "question.requested", "question.answered", "queue.paused", "queue.resumed", "queue.item_enqueued", "queue.item_cancelled", "artifact.created", "artifact.ready", "artifact.failed", "artifact.missing_detected", "materials.imported", "conversation.updated", "context.compacted", "context.budget_checked", "memory.updated", "memory.archived", "memory.snapshot_created", "skill.patched", "memory.job_status", "memory.summary_created", "memory.approval_requested", "memory.approval_resolved", "memory.curated", "governance.resource_changed", "governance.identity_changed", "governance.role_changed", "governance.grant_changed", "governance.rule_changed", "governance.skill_version_published", "governance.authorization_checked", "governance.audit_verified", "governance.backup_created", "governance.backup_restored", "governance.access_denied", "notification.created", "notification.updated", "runtime.power_changed"] } } }, { "properties": { "type": { "enum": ["tool.prepared"] }, "payload": { "$ref": "#/definitions/PreparedTool" } } }, { "properties": { "type": { "enum": ["tool.dispatched", "tool.completed", "tool.failed", "tool.skipped_idempotent", "tool.pending_verification", "tool.verification_submitted", "tool.result_externalized"] }, "payload": { "$ref": "#/definitions/ToolResult" } } }, { "properties": { "type": { "enum": ["cron.job_changed"] }, "payload": { "$ref": "#/definitions/CronChange" } } }, { "properties": { "type": { "enum": ["cron.job_fired", "cron.job_missed", "cron.job_skipped", "cron.job_failed", "cron.job_status"] }, "payload": { "$ref": "#/definitions/CronOccurrence" } } }, { "properties": { "type": { "enum": ["cron.proposal_requested", "cron.proposal_resolved"] }, "payload": { "$ref": "#/definitions/CronProposal" } } }, { "properties": { "type": { "enum": ["review.job_status"] }, "payload": { "$ref": "#/definitions/ReviewJob" } } }, { "properties": { "type": { "enum": ["audit.reported"] }, "payload": { "$ref": "#/definitions/AuditReport" } } }], "definitions": { "EventType": { "type": "string", "enum": ["run.queued", "run.started", "run.completed", "run.failed", "run.cancelled", "run.interrupted", "run.resumed", "step.started", "step.completed", "llm.request_started", "llm.request_done", "llm.request_failed", "tool.prepared", "tool.dispatched", "tool.completed", "tool.failed", "tool.skipped_idempotent", "tool.pending_verification", "permission.requested", "permission.resolved", "question.requested", "question.answered", "queue.paused", "queue.resumed", "queue.item_enqueued", "queue.item_cancelled", "tool.verification_submitted", "artifact.created", "artifact.ready", "artifact.failed", "artifact.missing_detected", "materials.imported", "conversation.updated", "context.compacted", "context.budget_checked", "tool.result_externalized", "memory.updated", "memory.archived", "memory.snapshot_created", "skill.patched", "memory.job_status", "memory.summary_created", "memory.approval_requested", "memory.approval_resolved", "memory.curated", "governance.resource_changed", "governance.identity_changed", "governance.role_changed", "governance.grant_changed", "governance.rule_changed", "governance.skill_version_published", "governance.authorization_checked", "governance.audit_verified", "governance.backup_created", "governance.backup_restored", "governance.access_denied", "cron.job_changed", "cron.job_fired", "cron.job_missed", "cron.job_skipped", "cron.job_failed", "cron.job_status", "cron.proposal_requested", "cron.proposal_resolved", "review.job_status", "audit.reported", "notification.created", "notification.updated", "runtime.power_changed"] }, "Scope": { "type": "object", "required": ["workspace_id", "agent_id", "owner_id"], "properties": { "org_id": { "type": "string" }, "workspace_id": { "type": ["string", "null"] }, "agent_id": { "type": ["string", "null"] }, "owner_id": { "type": ["string", "null"] } }, "additionalProperties": true }, "PreparedTool": { "type": "object", "required": ["call_id", "tool_name", "input_hash", "input"], "properties": { "call_id": { "type": "string" }, "tool_name": { "type": "string" }, "input_hash": { "type": "string" }, "input": { "type": "object" }, "side_effect_class": { "type": "string" }, "risk_level": { "type": "string" }, "read_only_verdict": { "type": "boolean" }, "step_id": { "type": "string" } }, "additionalProperties": true }, "ToolResult": { "type": "object", "required": ["call_id"], "properties": { "call_id": { "type": "string" }, "output": { "type": "string" }, "output_summary": { "type": "string" }, "error": { "type": "string" }, "details": { "type": "object" }, "artifact_path": { "type": "string" }, "note": { "type": ["string", "null"] }, "verdict": { "type": "string" } }, "additionalProperties": true }, "CronChange": { "type": "object", "required": ["job_id", "revision", "change_id", "enabled", "deleted_at", "scope", "audit_seq"], "properties": { "job_id": { "type": "string" }, "revision": { "type": "integer" }, "change_id": { "type": "string" }, "enabled": { "type": "boolean" }, "deleted_at": { "type": ["string", "null"] }, "scope": { "$ref": "#/definitions/Scope" }, "audit_seq": { "type": "integer" } }, "additionalProperties": true }, "CronOccurrence": { "type": "object", "required": ["job_id", "occurrence_id", "revision", "trigger", "scheduled_at", "triggered_at", "source_task_run_id", "retry_no", "status", "reason", "scope", "notification_id", "audit_seq"], "properties": { "job_id": { "type": "string" }, "occurrence_id": { "type": "string" }, "revision": { "type": "integer" }, "trigger": { "enum": ["scheduled", "manual"] }, "scheduled_at": { "type": ["integer", "null"] }, "triggered_at": { "type": "integer" }, "source_task_run_id": { "type": ["string", "null"] }, "retry_no": { "type": "integer" }, "status": { "type": "string" }, "reason": { "type": ["string", "null"] }, "scope": { "$ref": "#/definitions/Scope" }, "notification_id": { "type": ["string", "null"] }, "audit_seq": { "type": "integer" }, "retry_at": { "type": ["integer", "null"] }, "missed_count": { "type": "integer" }, "missed_through": { "type": ["integer", "null"] } }, "additionalProperties": true }, "CronProposal": { "type": "object", "required": ["proposal_id", "call_id", "input_hash", "revision", "scope"], "properties": { "proposal_id": { "type": "string" }, "call_id": { "type": "string" }, "input_hash": { "type": "string" }, "revision": { "type": "integer" }, "source_task_run_id": { "type": "string" }, "scope": { "$ref": "#/definitions/Scope" }, "decision": { "enum": ["allow_once", "reject_once", "expired"] }, "selected": { "type": "array", "items": { "type": "object" } }, "actor_id": { "type": "string" }, "job_id": { "type": ["string", "null"] }, "audit_seq": { "type": "integer" } }, "additionalProperties": true }, "ReviewJob": { "type": "object", "required": ["job_id", "kind", "status", "source_task_run_id", "attempt_no", "trigger_global_seq", "model", "config_version", "reason", "report_id", "skill_id", "usage", "scope"], "properties": { "job_id": { "type": "string" }, "kind": { "enum": ["trace_audit", "promote_skill"] }, "status": { "enum": ["queued", "running", "completed", "skipped", "cancelled", "interrupted", "failed"] }, "source_task_run_id": { "type": "string" }, "attempt_no": { "type": ["integer", "null"] }, "trigger_global_seq": { "type": "integer" }, "model": { "type": ["string", "null"] }, "config_version": { "type": "string" }, "reason": { "type": ["string", "null"] }, "report_id": { "type": ["string", "null"] }, "skill_id": { "type": ["string", "null"] }, "version_id": { "type": ["string", "null"] }, "usage": { "type": ["object", "null"] }, "scope": { "$ref": "#/definitions/Scope" } }, "additionalProperties": true }, "EventReference": { "type": "object", "required": ["task_run_id", "attempt_no", "seq", "global_seq"], "properties": { "task_run_id": { "type": "string" }, "attempt_no": { "type": ["integer", "null"] }, "seq": { "type": "integer", "minimum": 1 }, "global_seq": { "type": "integer", "minimum": 1 } }, "additionalProperties": false }, "AuditReport": { "type": "object", "required": ["report_id", "job_id", "source_task_run_id", "attempt_no", "source_global_seq", "model", "root_cause_event", "scope"], "properties": { "report_id": { "type": "string" }, "job_id": { "type": "string" }, "source_task_run_id": { "type": "string" }, "attempt_no": { "type": ["integer", "null"] }, "source_global_seq": { "type": "integer" }, "model": { "type": "string" }, "root_cause_event": { "anyOf": [{ "type": "null" }, { "$ref": "#/definitions/EventReference" }] }, "scope": { "$ref": "#/definitions/Scope" } }, "additionalProperties": true } } };
var schema13 = { "type": "object", "required": ["call_id"], "properties": { "call_id": { "type": "string" }, "output": { "type": "string" }, "output_summary": { "type": "string" }, "error": { "type": "string" }, "details": { "type": "object" }, "artifact_path": { "type": "string" }, "note": { "type": ["string", "null"] }, "verdict": { "type": "string" } }, "additionalProperties": true };
var schema25 = { "type": "string", "enum": ["run.queued", "run.started", "run.completed", "run.failed", "run.cancelled", "run.interrupted", "run.resumed", "step.started", "step.completed", "llm.request_started", "llm.request_done", "llm.request_failed", "tool.prepared", "tool.dispatched", "tool.completed", "tool.failed", "tool.skipped_idempotent", "tool.pending_verification", "permission.requested", "permission.resolved", "question.requested", "question.answered", "queue.paused", "queue.resumed", "queue.item_enqueued", "queue.item_cancelled", "tool.verification_submitted", "artifact.created", "artifact.ready", "artifact.failed", "artifact.missing_detected", "materials.imported", "conversation.updated", "context.compacted", "context.budget_checked", "tool.result_externalized", "memory.updated", "memory.archived", "memory.snapshot_created", "skill.patched", "memory.job_status", "memory.summary_created", "memory.approval_requested", "memory.approval_resolved", "memory.curated", "governance.resource_changed", "governance.identity_changed", "governance.role_changed", "governance.grant_changed", "governance.rule_changed", "governance.skill_version_published", "governance.authorization_checked", "governance.audit_verified", "governance.backup_created", "governance.backup_restored", "governance.access_denied", "cron.job_changed", "cron.job_fired", "cron.job_missed", "cron.job_skipped", "cron.job_failed", "cron.job_status", "cron.proposal_requested", "cron.proposal_resolved", "review.job_status", "audit.reported", "notification.created", "notification.updated", "runtime.power_changed"] };
var schema14 = { "type": "object", "required": ["job_id", "revision", "change_id", "enabled", "deleted_at", "scope", "audit_seq"], "properties": { "job_id": { "type": "string" }, "revision": { "type": "integer" }, "change_id": { "type": "string" }, "enabled": { "type": "boolean" }, "deleted_at": { "type": ["string", "null"] }, "scope": { "$ref": "#/definitions/Scope" }, "audit_seq": { "type": "integer" } }, "additionalProperties": true };
var schema15 = { "type": "object", "required": ["workspace_id", "agent_id", "owner_id"], "properties": { "org_id": { "type": "string" }, "workspace_id": { "type": ["string", "null"] }, "agent_id": { "type": ["string", "null"] }, "owner_id": { "type": ["string", "null"] } }, "additionalProperties": true };
function validate11(data, { instancePath = "", parentData, parentDataProperty, rootData = data } = {}) {
  let vErrors = null;
  let errors = 0;
  if (data && typeof data == "object" && !Array.isArray(data)) {
    if (data.job_id === void 0) {
      const err0 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "job_id" }, message: "must have required property 'job_id'" };
      if (vErrors === null) {
        vErrors = [err0];
      } else {
        vErrors.push(err0);
      }
      errors++;
    }
    if (data.revision === void 0) {
      const err1 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "revision" }, message: "must have required property 'revision'" };
      if (vErrors === null) {
        vErrors = [err1];
      } else {
        vErrors.push(err1);
      }
      errors++;
    }
    if (data.change_id === void 0) {
      const err2 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "change_id" }, message: "must have required property 'change_id'" };
      if (vErrors === null) {
        vErrors = [err2];
      } else {
        vErrors.push(err2);
      }
      errors++;
    }
    if (data.enabled === void 0) {
      const err3 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "enabled" }, message: "must have required property 'enabled'" };
      if (vErrors === null) {
        vErrors = [err3];
      } else {
        vErrors.push(err3);
      }
      errors++;
    }
    if (data.deleted_at === void 0) {
      const err4 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "deleted_at" }, message: "must have required property 'deleted_at'" };
      if (vErrors === null) {
        vErrors = [err4];
      } else {
        vErrors.push(err4);
      }
      errors++;
    }
    if (data.scope === void 0) {
      const err5 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "scope" }, message: "must have required property 'scope'" };
      if (vErrors === null) {
        vErrors = [err5];
      } else {
        vErrors.push(err5);
      }
      errors++;
    }
    if (data.audit_seq === void 0) {
      const err6 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "audit_seq" }, message: "must have required property 'audit_seq'" };
      if (vErrors === null) {
        vErrors = [err6];
      } else {
        vErrors.push(err6);
      }
      errors++;
    }
    if (data.job_id !== void 0) {
      if (typeof data.job_id !== "string") {
        const err7 = { instancePath: instancePath + "/job_id", schemaPath: "#/properties/job_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err7];
        } else {
          vErrors.push(err7);
        }
        errors++;
      }
    }
    if (data.revision !== void 0) {
      let data1 = data.revision;
      if (!(typeof data1 == "number" && (!(data1 % 1) && !isNaN(data1)))) {
        const err8 = { instancePath: instancePath + "/revision", schemaPath: "#/properties/revision/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err8];
        } else {
          vErrors.push(err8);
        }
        errors++;
      }
    }
    if (data.change_id !== void 0) {
      if (typeof data.change_id !== "string") {
        const err9 = { instancePath: instancePath + "/change_id", schemaPath: "#/properties/change_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err9];
        } else {
          vErrors.push(err9);
        }
        errors++;
      }
    }
    if (data.enabled !== void 0) {
      if (typeof data.enabled !== "boolean") {
        const err10 = { instancePath: instancePath + "/enabled", schemaPath: "#/properties/enabled/type", keyword: "type", params: { type: "boolean" }, message: "must be boolean" };
        if (vErrors === null) {
          vErrors = [err10];
        } else {
          vErrors.push(err10);
        }
        errors++;
      }
    }
    if (data.deleted_at !== void 0) {
      let data4 = data.deleted_at;
      if (typeof data4 !== "string" && data4 !== null) {
        const err11 = { instancePath: instancePath + "/deleted_at", schemaPath: "#/properties/deleted_at/type", keyword: "type", params: { type: schema14.properties.deleted_at.type }, message: "must be string,null" };
        if (vErrors === null) {
          vErrors = [err11];
        } else {
          vErrors.push(err11);
        }
        errors++;
      }
    }
    if (data.scope !== void 0) {
      let data5 = data.scope;
      if (data5 && typeof data5 == "object" && !Array.isArray(data5)) {
        if (data5.workspace_id === void 0) {
          const err12 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "workspace_id" }, message: "must have required property 'workspace_id'" };
          if (vErrors === null) {
            vErrors = [err12];
          } else {
            vErrors.push(err12);
          }
          errors++;
        }
        if (data5.agent_id === void 0) {
          const err13 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "agent_id" }, message: "must have required property 'agent_id'" };
          if (vErrors === null) {
            vErrors = [err13];
          } else {
            vErrors.push(err13);
          }
          errors++;
        }
        if (data5.owner_id === void 0) {
          const err14 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "owner_id" }, message: "must have required property 'owner_id'" };
          if (vErrors === null) {
            vErrors = [err14];
          } else {
            vErrors.push(err14);
          }
          errors++;
        }
        if (data5.org_id !== void 0) {
          if (typeof data5.org_id !== "string") {
            const err15 = { instancePath: instancePath + "/scope/org_id", schemaPath: "#/definitions/Scope/properties/org_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
            if (vErrors === null) {
              vErrors = [err15];
            } else {
              vErrors.push(err15);
            }
            errors++;
          }
        }
        if (data5.workspace_id !== void 0) {
          let data7 = data5.workspace_id;
          if (typeof data7 !== "string" && data7 !== null) {
            const err16 = { instancePath: instancePath + "/scope/workspace_id", schemaPath: "#/definitions/Scope/properties/workspace_id/type", keyword: "type", params: { type: schema15.properties.workspace_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err16];
            } else {
              vErrors.push(err16);
            }
            errors++;
          }
        }
        if (data5.agent_id !== void 0) {
          let data8 = data5.agent_id;
          if (typeof data8 !== "string" && data8 !== null) {
            const err17 = { instancePath: instancePath + "/scope/agent_id", schemaPath: "#/definitions/Scope/properties/agent_id/type", keyword: "type", params: { type: schema15.properties.agent_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err17];
            } else {
              vErrors.push(err17);
            }
            errors++;
          }
        }
        if (data5.owner_id !== void 0) {
          let data9 = data5.owner_id;
          if (typeof data9 !== "string" && data9 !== null) {
            const err18 = { instancePath: instancePath + "/scope/owner_id", schemaPath: "#/definitions/Scope/properties/owner_id/type", keyword: "type", params: { type: schema15.properties.owner_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err18];
            } else {
              vErrors.push(err18);
            }
            errors++;
          }
        }
      } else {
        const err19 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/type", keyword: "type", params: { type: "object" }, message: "must be object" };
        if (vErrors === null) {
          vErrors = [err19];
        } else {
          vErrors.push(err19);
        }
        errors++;
      }
    }
    if (data.audit_seq !== void 0) {
      let data10 = data.audit_seq;
      if (!(typeof data10 == "number" && (!(data10 % 1) && !isNaN(data10)))) {
        const err20 = { instancePath: instancePath + "/audit_seq", schemaPath: "#/properties/audit_seq/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err20];
        } else {
          vErrors.push(err20);
        }
        errors++;
      }
    }
  } else {
    const err21 = { instancePath, schemaPath: "#/type", keyword: "type", params: { type: "object" }, message: "must be object" };
    if (vErrors === null) {
      vErrors = [err21];
    } else {
      vErrors.push(err21);
    }
    errors++;
  }
  validate11.errors = vErrors;
  return errors === 0;
}
var schema16 = { "type": "object", "required": ["job_id", "occurrence_id", "revision", "trigger", "scheduled_at", "triggered_at", "source_task_run_id", "retry_no", "status", "reason", "scope", "notification_id", "audit_seq"], "properties": { "job_id": { "type": "string" }, "occurrence_id": { "type": "string" }, "revision": { "type": "integer" }, "trigger": { "enum": ["scheduled", "manual"] }, "scheduled_at": { "type": ["integer", "null"] }, "triggered_at": { "type": "integer" }, "source_task_run_id": { "type": ["string", "null"] }, "retry_no": { "type": "integer" }, "status": { "type": "string" }, "reason": { "type": ["string", "null"] }, "scope": { "$ref": "#/definitions/Scope" }, "notification_id": { "type": ["string", "null"] }, "audit_seq": { "type": "integer" }, "retry_at": { "type": ["integer", "null"] }, "missed_count": { "type": "integer" }, "missed_through": { "type": ["integer", "null"] } }, "additionalProperties": true };
function validate13(data, { instancePath = "", parentData, parentDataProperty, rootData = data } = {}) {
  let vErrors = null;
  let errors = 0;
  if (data && typeof data == "object" && !Array.isArray(data)) {
    if (data.job_id === void 0) {
      const err0 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "job_id" }, message: "must have required property 'job_id'" };
      if (vErrors === null) {
        vErrors = [err0];
      } else {
        vErrors.push(err0);
      }
      errors++;
    }
    if (data.occurrence_id === void 0) {
      const err1 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "occurrence_id" }, message: "must have required property 'occurrence_id'" };
      if (vErrors === null) {
        vErrors = [err1];
      } else {
        vErrors.push(err1);
      }
      errors++;
    }
    if (data.revision === void 0) {
      const err2 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "revision" }, message: "must have required property 'revision'" };
      if (vErrors === null) {
        vErrors = [err2];
      } else {
        vErrors.push(err2);
      }
      errors++;
    }
    if (data.trigger === void 0) {
      const err3 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "trigger" }, message: "must have required property 'trigger'" };
      if (vErrors === null) {
        vErrors = [err3];
      } else {
        vErrors.push(err3);
      }
      errors++;
    }
    if (data.scheduled_at === void 0) {
      const err4 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "scheduled_at" }, message: "must have required property 'scheduled_at'" };
      if (vErrors === null) {
        vErrors = [err4];
      } else {
        vErrors.push(err4);
      }
      errors++;
    }
    if (data.triggered_at === void 0) {
      const err5 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "triggered_at" }, message: "must have required property 'triggered_at'" };
      if (vErrors === null) {
        vErrors = [err5];
      } else {
        vErrors.push(err5);
      }
      errors++;
    }
    if (data.source_task_run_id === void 0) {
      const err6 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "source_task_run_id" }, message: "must have required property 'source_task_run_id'" };
      if (vErrors === null) {
        vErrors = [err6];
      } else {
        vErrors.push(err6);
      }
      errors++;
    }
    if (data.retry_no === void 0) {
      const err7 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "retry_no" }, message: "must have required property 'retry_no'" };
      if (vErrors === null) {
        vErrors = [err7];
      } else {
        vErrors.push(err7);
      }
      errors++;
    }
    if (data.status === void 0) {
      const err8 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "status" }, message: "must have required property 'status'" };
      if (vErrors === null) {
        vErrors = [err8];
      } else {
        vErrors.push(err8);
      }
      errors++;
    }
    if (data.reason === void 0) {
      const err9 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "reason" }, message: "must have required property 'reason'" };
      if (vErrors === null) {
        vErrors = [err9];
      } else {
        vErrors.push(err9);
      }
      errors++;
    }
    if (data.scope === void 0) {
      const err10 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "scope" }, message: "must have required property 'scope'" };
      if (vErrors === null) {
        vErrors = [err10];
      } else {
        vErrors.push(err10);
      }
      errors++;
    }
    if (data.notification_id === void 0) {
      const err11 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "notification_id" }, message: "must have required property 'notification_id'" };
      if (vErrors === null) {
        vErrors = [err11];
      } else {
        vErrors.push(err11);
      }
      errors++;
    }
    if (data.audit_seq === void 0) {
      const err12 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "audit_seq" }, message: "must have required property 'audit_seq'" };
      if (vErrors === null) {
        vErrors = [err12];
      } else {
        vErrors.push(err12);
      }
      errors++;
    }
    if (data.job_id !== void 0) {
      if (typeof data.job_id !== "string") {
        const err13 = { instancePath: instancePath + "/job_id", schemaPath: "#/properties/job_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err13];
        } else {
          vErrors.push(err13);
        }
        errors++;
      }
    }
    if (data.occurrence_id !== void 0) {
      if (typeof data.occurrence_id !== "string") {
        const err14 = { instancePath: instancePath + "/occurrence_id", schemaPath: "#/properties/occurrence_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err14];
        } else {
          vErrors.push(err14);
        }
        errors++;
      }
    }
    if (data.revision !== void 0) {
      let data2 = data.revision;
      if (!(typeof data2 == "number" && (!(data2 % 1) && !isNaN(data2)))) {
        const err15 = { instancePath: instancePath + "/revision", schemaPath: "#/properties/revision/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err15];
        } else {
          vErrors.push(err15);
        }
        errors++;
      }
    }
    if (data.trigger !== void 0) {
      let data3 = data.trigger;
      if (!(data3 === "scheduled" || data3 === "manual")) {
        const err16 = { instancePath: instancePath + "/trigger", schemaPath: "#/properties/trigger/enum", keyword: "enum", params: { allowedValues: schema16.properties.trigger.enum }, message: "must be equal to one of the allowed values" };
        if (vErrors === null) {
          vErrors = [err16];
        } else {
          vErrors.push(err16);
        }
        errors++;
      }
    }
    if (data.scheduled_at !== void 0) {
      let data4 = data.scheduled_at;
      if (!(typeof data4 == "number" && (!(data4 % 1) && !isNaN(data4))) && data4 !== null) {
        const err17 = { instancePath: instancePath + "/scheduled_at", schemaPath: "#/properties/scheduled_at/type", keyword: "type", params: { type: schema16.properties.scheduled_at.type }, message: "must be integer,null" };
        if (vErrors === null) {
          vErrors = [err17];
        } else {
          vErrors.push(err17);
        }
        errors++;
      }
    }
    if (data.triggered_at !== void 0) {
      let data5 = data.triggered_at;
      if (!(typeof data5 == "number" && (!(data5 % 1) && !isNaN(data5)))) {
        const err18 = { instancePath: instancePath + "/triggered_at", schemaPath: "#/properties/triggered_at/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err18];
        } else {
          vErrors.push(err18);
        }
        errors++;
      }
    }
    if (data.source_task_run_id !== void 0) {
      let data6 = data.source_task_run_id;
      if (typeof data6 !== "string" && data6 !== null) {
        const err19 = { instancePath: instancePath + "/source_task_run_id", schemaPath: "#/properties/source_task_run_id/type", keyword: "type", params: { type: schema16.properties.source_task_run_id.type }, message: "must be string,null" };
        if (vErrors === null) {
          vErrors = [err19];
        } else {
          vErrors.push(err19);
        }
        errors++;
      }
    }
    if (data.retry_no !== void 0) {
      let data7 = data.retry_no;
      if (!(typeof data7 == "number" && (!(data7 % 1) && !isNaN(data7)))) {
        const err20 = { instancePath: instancePath + "/retry_no", schemaPath: "#/properties/retry_no/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err20];
        } else {
          vErrors.push(err20);
        }
        errors++;
      }
    }
    if (data.status !== void 0) {
      if (typeof data.status !== "string") {
        const err21 = { instancePath: instancePath + "/status", schemaPath: "#/properties/status/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err21];
        } else {
          vErrors.push(err21);
        }
        errors++;
      }
    }
    if (data.reason !== void 0) {
      let data9 = data.reason;
      if (typeof data9 !== "string" && data9 !== null) {
        const err22 = { instancePath: instancePath + "/reason", schemaPath: "#/properties/reason/type", keyword: "type", params: { type: schema16.properties.reason.type }, message: "must be string,null" };
        if (vErrors === null) {
          vErrors = [err22];
        } else {
          vErrors.push(err22);
        }
        errors++;
      }
    }
    if (data.scope !== void 0) {
      let data10 = data.scope;
      if (data10 && typeof data10 == "object" && !Array.isArray(data10)) {
        if (data10.workspace_id === void 0) {
          const err23 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "workspace_id" }, message: "must have required property 'workspace_id'" };
          if (vErrors === null) {
            vErrors = [err23];
          } else {
            vErrors.push(err23);
          }
          errors++;
        }
        if (data10.agent_id === void 0) {
          const err24 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "agent_id" }, message: "must have required property 'agent_id'" };
          if (vErrors === null) {
            vErrors = [err24];
          } else {
            vErrors.push(err24);
          }
          errors++;
        }
        if (data10.owner_id === void 0) {
          const err25 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "owner_id" }, message: "must have required property 'owner_id'" };
          if (vErrors === null) {
            vErrors = [err25];
          } else {
            vErrors.push(err25);
          }
          errors++;
        }
        if (data10.org_id !== void 0) {
          if (typeof data10.org_id !== "string") {
            const err26 = { instancePath: instancePath + "/scope/org_id", schemaPath: "#/definitions/Scope/properties/org_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
            if (vErrors === null) {
              vErrors = [err26];
            } else {
              vErrors.push(err26);
            }
            errors++;
          }
        }
        if (data10.workspace_id !== void 0) {
          let data12 = data10.workspace_id;
          if (typeof data12 !== "string" && data12 !== null) {
            const err27 = { instancePath: instancePath + "/scope/workspace_id", schemaPath: "#/definitions/Scope/properties/workspace_id/type", keyword: "type", params: { type: schema15.properties.workspace_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err27];
            } else {
              vErrors.push(err27);
            }
            errors++;
          }
        }
        if (data10.agent_id !== void 0) {
          let data13 = data10.agent_id;
          if (typeof data13 !== "string" && data13 !== null) {
            const err28 = { instancePath: instancePath + "/scope/agent_id", schemaPath: "#/definitions/Scope/properties/agent_id/type", keyword: "type", params: { type: schema15.properties.agent_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err28];
            } else {
              vErrors.push(err28);
            }
            errors++;
          }
        }
        if (data10.owner_id !== void 0) {
          let data14 = data10.owner_id;
          if (typeof data14 !== "string" && data14 !== null) {
            const err29 = { instancePath: instancePath + "/scope/owner_id", schemaPath: "#/definitions/Scope/properties/owner_id/type", keyword: "type", params: { type: schema15.properties.owner_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err29];
            } else {
              vErrors.push(err29);
            }
            errors++;
          }
        }
      } else {
        const err30 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/type", keyword: "type", params: { type: "object" }, message: "must be object" };
        if (vErrors === null) {
          vErrors = [err30];
        } else {
          vErrors.push(err30);
        }
        errors++;
      }
    }
    if (data.notification_id !== void 0) {
      let data15 = data.notification_id;
      if (typeof data15 !== "string" && data15 !== null) {
        const err31 = { instancePath: instancePath + "/notification_id", schemaPath: "#/properties/notification_id/type", keyword: "type", params: { type: schema16.properties.notification_id.type }, message: "must be string,null" };
        if (vErrors === null) {
          vErrors = [err31];
        } else {
          vErrors.push(err31);
        }
        errors++;
      }
    }
    if (data.audit_seq !== void 0) {
      let data16 = data.audit_seq;
      if (!(typeof data16 == "number" && (!(data16 % 1) && !isNaN(data16)))) {
        const err32 = { instancePath: instancePath + "/audit_seq", schemaPath: "#/properties/audit_seq/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err32];
        } else {
          vErrors.push(err32);
        }
        errors++;
      }
    }
    if (data.retry_at !== void 0) {
      let data17 = data.retry_at;
      if (!(typeof data17 == "number" && (!(data17 % 1) && !isNaN(data17))) && data17 !== null) {
        const err33 = { instancePath: instancePath + "/retry_at", schemaPath: "#/properties/retry_at/type", keyword: "type", params: { type: schema16.properties.retry_at.type }, message: "must be integer,null" };
        if (vErrors === null) {
          vErrors = [err33];
        } else {
          vErrors.push(err33);
        }
        errors++;
      }
    }
    if (data.missed_count !== void 0) {
      let data18 = data.missed_count;
      if (!(typeof data18 == "number" && (!(data18 % 1) && !isNaN(data18)))) {
        const err34 = { instancePath: instancePath + "/missed_count", schemaPath: "#/properties/missed_count/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err34];
        } else {
          vErrors.push(err34);
        }
        errors++;
      }
    }
    if (data.missed_through !== void 0) {
      let data19 = data.missed_through;
      if (!(typeof data19 == "number" && (!(data19 % 1) && !isNaN(data19))) && data19 !== null) {
        const err35 = { instancePath: instancePath + "/missed_through", schemaPath: "#/properties/missed_through/type", keyword: "type", params: { type: schema16.properties.missed_through.type }, message: "must be integer,null" };
        if (vErrors === null) {
          vErrors = [err35];
        } else {
          vErrors.push(err35);
        }
        errors++;
      }
    }
  } else {
    const err36 = { instancePath, schemaPath: "#/type", keyword: "type", params: { type: "object" }, message: "must be object" };
    if (vErrors === null) {
      vErrors = [err36];
    } else {
      vErrors.push(err36);
    }
    errors++;
  }
  validate13.errors = vErrors;
  return errors === 0;
}
var schema18 = { "type": "object", "required": ["proposal_id", "call_id", "input_hash", "revision", "scope"], "properties": { "proposal_id": { "type": "string" }, "call_id": { "type": "string" }, "input_hash": { "type": "string" }, "revision": { "type": "integer" }, "source_task_run_id": { "type": "string" }, "scope": { "$ref": "#/definitions/Scope" }, "decision": { "enum": ["allow_once", "reject_once", "expired"] }, "selected": { "type": "array", "items": { "type": "object" } }, "actor_id": { "type": "string" }, "job_id": { "type": ["string", "null"] }, "audit_seq": { "type": "integer" } }, "additionalProperties": true };
function validate15(data, { instancePath = "", parentData, parentDataProperty, rootData = data } = {}) {
  let vErrors = null;
  let errors = 0;
  if (data && typeof data == "object" && !Array.isArray(data)) {
    if (data.proposal_id === void 0) {
      const err0 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "proposal_id" }, message: "must have required property 'proposal_id'" };
      if (vErrors === null) {
        vErrors = [err0];
      } else {
        vErrors.push(err0);
      }
      errors++;
    }
    if (data.call_id === void 0) {
      const err1 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "call_id" }, message: "must have required property 'call_id'" };
      if (vErrors === null) {
        vErrors = [err1];
      } else {
        vErrors.push(err1);
      }
      errors++;
    }
    if (data.input_hash === void 0) {
      const err2 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "input_hash" }, message: "must have required property 'input_hash'" };
      if (vErrors === null) {
        vErrors = [err2];
      } else {
        vErrors.push(err2);
      }
      errors++;
    }
    if (data.revision === void 0) {
      const err3 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "revision" }, message: "must have required property 'revision'" };
      if (vErrors === null) {
        vErrors = [err3];
      } else {
        vErrors.push(err3);
      }
      errors++;
    }
    if (data.scope === void 0) {
      const err4 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "scope" }, message: "must have required property 'scope'" };
      if (vErrors === null) {
        vErrors = [err4];
      } else {
        vErrors.push(err4);
      }
      errors++;
    }
    if (data.proposal_id !== void 0) {
      if (typeof data.proposal_id !== "string") {
        const err5 = { instancePath: instancePath + "/proposal_id", schemaPath: "#/properties/proposal_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err5];
        } else {
          vErrors.push(err5);
        }
        errors++;
      }
    }
    if (data.call_id !== void 0) {
      if (typeof data.call_id !== "string") {
        const err6 = { instancePath: instancePath + "/call_id", schemaPath: "#/properties/call_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err6];
        } else {
          vErrors.push(err6);
        }
        errors++;
      }
    }
    if (data.input_hash !== void 0) {
      if (typeof data.input_hash !== "string") {
        const err7 = { instancePath: instancePath + "/input_hash", schemaPath: "#/properties/input_hash/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err7];
        } else {
          vErrors.push(err7);
        }
        errors++;
      }
    }
    if (data.revision !== void 0) {
      let data3 = data.revision;
      if (!(typeof data3 == "number" && (!(data3 % 1) && !isNaN(data3)))) {
        const err8 = { instancePath: instancePath + "/revision", schemaPath: "#/properties/revision/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err8];
        } else {
          vErrors.push(err8);
        }
        errors++;
      }
    }
    if (data.source_task_run_id !== void 0) {
      if (typeof data.source_task_run_id !== "string") {
        const err9 = { instancePath: instancePath + "/source_task_run_id", schemaPath: "#/properties/source_task_run_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err9];
        } else {
          vErrors.push(err9);
        }
        errors++;
      }
    }
    if (data.scope !== void 0) {
      let data5 = data.scope;
      if (data5 && typeof data5 == "object" && !Array.isArray(data5)) {
        if (data5.workspace_id === void 0) {
          const err10 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "workspace_id" }, message: "must have required property 'workspace_id'" };
          if (vErrors === null) {
            vErrors = [err10];
          } else {
            vErrors.push(err10);
          }
          errors++;
        }
        if (data5.agent_id === void 0) {
          const err11 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "agent_id" }, message: "must have required property 'agent_id'" };
          if (vErrors === null) {
            vErrors = [err11];
          } else {
            vErrors.push(err11);
          }
          errors++;
        }
        if (data5.owner_id === void 0) {
          const err12 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "owner_id" }, message: "must have required property 'owner_id'" };
          if (vErrors === null) {
            vErrors = [err12];
          } else {
            vErrors.push(err12);
          }
          errors++;
        }
        if (data5.org_id !== void 0) {
          if (typeof data5.org_id !== "string") {
            const err13 = { instancePath: instancePath + "/scope/org_id", schemaPath: "#/definitions/Scope/properties/org_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
            if (vErrors === null) {
              vErrors = [err13];
            } else {
              vErrors.push(err13);
            }
            errors++;
          }
        }
        if (data5.workspace_id !== void 0) {
          let data7 = data5.workspace_id;
          if (typeof data7 !== "string" && data7 !== null) {
            const err14 = { instancePath: instancePath + "/scope/workspace_id", schemaPath: "#/definitions/Scope/properties/workspace_id/type", keyword: "type", params: { type: schema15.properties.workspace_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err14];
            } else {
              vErrors.push(err14);
            }
            errors++;
          }
        }
        if (data5.agent_id !== void 0) {
          let data8 = data5.agent_id;
          if (typeof data8 !== "string" && data8 !== null) {
            const err15 = { instancePath: instancePath + "/scope/agent_id", schemaPath: "#/definitions/Scope/properties/agent_id/type", keyword: "type", params: { type: schema15.properties.agent_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err15];
            } else {
              vErrors.push(err15);
            }
            errors++;
          }
        }
        if (data5.owner_id !== void 0) {
          let data9 = data5.owner_id;
          if (typeof data9 !== "string" && data9 !== null) {
            const err16 = { instancePath: instancePath + "/scope/owner_id", schemaPath: "#/definitions/Scope/properties/owner_id/type", keyword: "type", params: { type: schema15.properties.owner_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err16];
            } else {
              vErrors.push(err16);
            }
            errors++;
          }
        }
      } else {
        const err17 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/type", keyword: "type", params: { type: "object" }, message: "must be object" };
        if (vErrors === null) {
          vErrors = [err17];
        } else {
          vErrors.push(err17);
        }
        errors++;
      }
    }
    if (data.decision !== void 0) {
      let data10 = data.decision;
      if (!(data10 === "allow_once" || data10 === "reject_once" || data10 === "expired")) {
        const err18 = { instancePath: instancePath + "/decision", schemaPath: "#/properties/decision/enum", keyword: "enum", params: { allowedValues: schema18.properties.decision.enum }, message: "must be equal to one of the allowed values" };
        if (vErrors === null) {
          vErrors = [err18];
        } else {
          vErrors.push(err18);
        }
        errors++;
      }
    }
    if (data.selected !== void 0) {
      let data11 = data.selected;
      if (Array.isArray(data11)) {
        const len0 = data11.length;
        for (let i0 = 0; i0 < len0; i0++) {
          let data12 = data11[i0];
          if (!(data12 && typeof data12 == "object" && !Array.isArray(data12))) {
            const err19 = { instancePath: instancePath + "/selected/" + i0, schemaPath: "#/properties/selected/items/type", keyword: "type", params: { type: "object" }, message: "must be object" };
            if (vErrors === null) {
              vErrors = [err19];
            } else {
              vErrors.push(err19);
            }
            errors++;
          }
        }
      } else {
        const err20 = { instancePath: instancePath + "/selected", schemaPath: "#/properties/selected/type", keyword: "type", params: { type: "array" }, message: "must be array" };
        if (vErrors === null) {
          vErrors = [err20];
        } else {
          vErrors.push(err20);
        }
        errors++;
      }
    }
    if (data.actor_id !== void 0) {
      if (typeof data.actor_id !== "string") {
        const err21 = { instancePath: instancePath + "/actor_id", schemaPath: "#/properties/actor_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err21];
        } else {
          vErrors.push(err21);
        }
        errors++;
      }
    }
    if (data.job_id !== void 0) {
      let data14 = data.job_id;
      if (typeof data14 !== "string" && data14 !== null) {
        const err22 = { instancePath: instancePath + "/job_id", schemaPath: "#/properties/job_id/type", keyword: "type", params: { type: schema18.properties.job_id.type }, message: "must be string,null" };
        if (vErrors === null) {
          vErrors = [err22];
        } else {
          vErrors.push(err22);
        }
        errors++;
      }
    }
    if (data.audit_seq !== void 0) {
      let data15 = data.audit_seq;
      if (!(typeof data15 == "number" && (!(data15 % 1) && !isNaN(data15)))) {
        const err23 = { instancePath: instancePath + "/audit_seq", schemaPath: "#/properties/audit_seq/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err23];
        } else {
          vErrors.push(err23);
        }
        errors++;
      }
    }
  } else {
    const err24 = { instancePath, schemaPath: "#/type", keyword: "type", params: { type: "object" }, message: "must be object" };
    if (vErrors === null) {
      vErrors = [err24];
    } else {
      vErrors.push(err24);
    }
    errors++;
  }
  validate15.errors = vErrors;
  return errors === 0;
}
var schema20 = { "type": "object", "required": ["job_id", "kind", "status", "source_task_run_id", "attempt_no", "trigger_global_seq", "model", "config_version", "reason", "report_id", "skill_id", "usage", "scope"], "properties": { "job_id": { "type": "string" }, "kind": { "enum": ["trace_audit", "promote_skill"] }, "status": { "enum": ["queued", "running", "completed", "skipped", "cancelled", "interrupted", "failed"] }, "source_task_run_id": { "type": "string" }, "attempt_no": { "type": ["integer", "null"] }, "trigger_global_seq": { "type": "integer" }, "model": { "type": ["string", "null"] }, "config_version": { "type": "string" }, "reason": { "type": ["string", "null"] }, "report_id": { "type": ["string", "null"] }, "skill_id": { "type": ["string", "null"] }, "version_id": { "type": ["string", "null"] }, "usage": { "type": ["object", "null"] }, "scope": { "$ref": "#/definitions/Scope" } }, "additionalProperties": true };
function validate17(data, { instancePath = "", parentData, parentDataProperty, rootData = data } = {}) {
  let vErrors = null;
  let errors = 0;
  if (data && typeof data == "object" && !Array.isArray(data)) {
    if (data.job_id === void 0) {
      const err0 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "job_id" }, message: "must have required property 'job_id'" };
      if (vErrors === null) {
        vErrors = [err0];
      } else {
        vErrors.push(err0);
      }
      errors++;
    }
    if (data.kind === void 0) {
      const err1 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "kind" }, message: "must have required property 'kind'" };
      if (vErrors === null) {
        vErrors = [err1];
      } else {
        vErrors.push(err1);
      }
      errors++;
    }
    if (data.status === void 0) {
      const err2 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "status" }, message: "must have required property 'status'" };
      if (vErrors === null) {
        vErrors = [err2];
      } else {
        vErrors.push(err2);
      }
      errors++;
    }
    if (data.source_task_run_id === void 0) {
      const err3 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "source_task_run_id" }, message: "must have required property 'source_task_run_id'" };
      if (vErrors === null) {
        vErrors = [err3];
      } else {
        vErrors.push(err3);
      }
      errors++;
    }
    if (data.attempt_no === void 0) {
      const err4 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "attempt_no" }, message: "must have required property 'attempt_no'" };
      if (vErrors === null) {
        vErrors = [err4];
      } else {
        vErrors.push(err4);
      }
      errors++;
    }
    if (data.trigger_global_seq === void 0) {
      const err5 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "trigger_global_seq" }, message: "must have required property 'trigger_global_seq'" };
      if (vErrors === null) {
        vErrors = [err5];
      } else {
        vErrors.push(err5);
      }
      errors++;
    }
    if (data.model === void 0) {
      const err6 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "model" }, message: "must have required property 'model'" };
      if (vErrors === null) {
        vErrors = [err6];
      } else {
        vErrors.push(err6);
      }
      errors++;
    }
    if (data.config_version === void 0) {
      const err7 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "config_version" }, message: "must have required property 'config_version'" };
      if (vErrors === null) {
        vErrors = [err7];
      } else {
        vErrors.push(err7);
      }
      errors++;
    }
    if (data.reason === void 0) {
      const err8 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "reason" }, message: "must have required property 'reason'" };
      if (vErrors === null) {
        vErrors = [err8];
      } else {
        vErrors.push(err8);
      }
      errors++;
    }
    if (data.report_id === void 0) {
      const err9 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "report_id" }, message: "must have required property 'report_id'" };
      if (vErrors === null) {
        vErrors = [err9];
      } else {
        vErrors.push(err9);
      }
      errors++;
    }
    if (data.skill_id === void 0) {
      const err10 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "skill_id" }, message: "must have required property 'skill_id'" };
      if (vErrors === null) {
        vErrors = [err10];
      } else {
        vErrors.push(err10);
      }
      errors++;
    }
    if (data.usage === void 0) {
      const err11 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "usage" }, message: "must have required property 'usage'" };
      if (vErrors === null) {
        vErrors = [err11];
      } else {
        vErrors.push(err11);
      }
      errors++;
    }
    if (data.scope === void 0) {
      const err12 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "scope" }, message: "must have required property 'scope'" };
      if (vErrors === null) {
        vErrors = [err12];
      } else {
        vErrors.push(err12);
      }
      errors++;
    }
    if (data.job_id !== void 0) {
      if (typeof data.job_id !== "string") {
        const err13 = { instancePath: instancePath + "/job_id", schemaPath: "#/properties/job_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err13];
        } else {
          vErrors.push(err13);
        }
        errors++;
      }
    }
    if (data.kind !== void 0) {
      let data1 = data.kind;
      if (!(data1 === "trace_audit" || data1 === "promote_skill")) {
        const err14 = { instancePath: instancePath + "/kind", schemaPath: "#/properties/kind/enum", keyword: "enum", params: { allowedValues: schema20.properties.kind.enum }, message: "must be equal to one of the allowed values" };
        if (vErrors === null) {
          vErrors = [err14];
        } else {
          vErrors.push(err14);
        }
        errors++;
      }
    }
    if (data.status !== void 0) {
      let data2 = data.status;
      if (!(data2 === "queued" || data2 === "running" || data2 === "completed" || data2 === "skipped" || data2 === "cancelled" || data2 === "interrupted" || data2 === "failed")) {
        const err15 = { instancePath: instancePath + "/status", schemaPath: "#/properties/status/enum", keyword: "enum", params: { allowedValues: schema20.properties.status.enum }, message: "must be equal to one of the allowed values" };
        if (vErrors === null) {
          vErrors = [err15];
        } else {
          vErrors.push(err15);
        }
        errors++;
      }
    }
    if (data.source_task_run_id !== void 0) {
      if (typeof data.source_task_run_id !== "string") {
        const err16 = { instancePath: instancePath + "/source_task_run_id", schemaPath: "#/properties/source_task_run_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err16];
        } else {
          vErrors.push(err16);
        }
        errors++;
      }
    }
    if (data.attempt_no !== void 0) {
      let data4 = data.attempt_no;
      if (!(typeof data4 == "number" && (!(data4 % 1) && !isNaN(data4))) && data4 !== null) {
        const err17 = { instancePath: instancePath + "/attempt_no", schemaPath: "#/properties/attempt_no/type", keyword: "type", params: { type: schema20.properties.attempt_no.type }, message: "must be integer,null" };
        if (vErrors === null) {
          vErrors = [err17];
        } else {
          vErrors.push(err17);
        }
        errors++;
      }
    }
    if (data.trigger_global_seq !== void 0) {
      let data5 = data.trigger_global_seq;
      if (!(typeof data5 == "number" && (!(data5 % 1) && !isNaN(data5)))) {
        const err18 = { instancePath: instancePath + "/trigger_global_seq", schemaPath: "#/properties/trigger_global_seq/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err18];
        } else {
          vErrors.push(err18);
        }
        errors++;
      }
    }
    if (data.model !== void 0) {
      let data6 = data.model;
      if (typeof data6 !== "string" && data6 !== null) {
        const err19 = { instancePath: instancePath + "/model", schemaPath: "#/properties/model/type", keyword: "type", params: { type: schema20.properties.model.type }, message: "must be string,null" };
        if (vErrors === null) {
          vErrors = [err19];
        } else {
          vErrors.push(err19);
        }
        errors++;
      }
    }
    if (data.config_version !== void 0) {
      if (typeof data.config_version !== "string") {
        const err20 = { instancePath: instancePath + "/config_version", schemaPath: "#/properties/config_version/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err20];
        } else {
          vErrors.push(err20);
        }
        errors++;
      }
    }
    if (data.reason !== void 0) {
      let data8 = data.reason;
      if (typeof data8 !== "string" && data8 !== null) {
        const err21 = { instancePath: instancePath + "/reason", schemaPath: "#/properties/reason/type", keyword: "type", params: { type: schema20.properties.reason.type }, message: "must be string,null" };
        if (vErrors === null) {
          vErrors = [err21];
        } else {
          vErrors.push(err21);
        }
        errors++;
      }
    }
    if (data.report_id !== void 0) {
      let data9 = data.report_id;
      if (typeof data9 !== "string" && data9 !== null) {
        const err22 = { instancePath: instancePath + "/report_id", schemaPath: "#/properties/report_id/type", keyword: "type", params: { type: schema20.properties.report_id.type }, message: "must be string,null" };
        if (vErrors === null) {
          vErrors = [err22];
        } else {
          vErrors.push(err22);
        }
        errors++;
      }
    }
    if (data.skill_id !== void 0) {
      let data10 = data.skill_id;
      if (typeof data10 !== "string" && data10 !== null) {
        const err23 = { instancePath: instancePath + "/skill_id", schemaPath: "#/properties/skill_id/type", keyword: "type", params: { type: schema20.properties.skill_id.type }, message: "must be string,null" };
        if (vErrors === null) {
          vErrors = [err23];
        } else {
          vErrors.push(err23);
        }
        errors++;
      }
    }
    if (data.version_id !== void 0) {
      let data11 = data.version_id;
      if (typeof data11 !== "string" && data11 !== null) {
        const err24 = { instancePath: instancePath + "/version_id", schemaPath: "#/properties/version_id/type", keyword: "type", params: { type: schema20.properties.version_id.type }, message: "must be string,null" };
        if (vErrors === null) {
          vErrors = [err24];
        } else {
          vErrors.push(err24);
        }
        errors++;
      }
    }
    if (data.usage !== void 0) {
      let data12 = data.usage;
      if (!(data12 && typeof data12 == "object" && !Array.isArray(data12)) && data12 !== null) {
        const err25 = { instancePath: instancePath + "/usage", schemaPath: "#/properties/usage/type", keyword: "type", params: { type: schema20.properties.usage.type }, message: "must be object,null" };
        if (vErrors === null) {
          vErrors = [err25];
        } else {
          vErrors.push(err25);
        }
        errors++;
      }
    }
    if (data.scope !== void 0) {
      let data13 = data.scope;
      if (data13 && typeof data13 == "object" && !Array.isArray(data13)) {
        if (data13.workspace_id === void 0) {
          const err26 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "workspace_id" }, message: "must have required property 'workspace_id'" };
          if (vErrors === null) {
            vErrors = [err26];
          } else {
            vErrors.push(err26);
          }
          errors++;
        }
        if (data13.agent_id === void 0) {
          const err27 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "agent_id" }, message: "must have required property 'agent_id'" };
          if (vErrors === null) {
            vErrors = [err27];
          } else {
            vErrors.push(err27);
          }
          errors++;
        }
        if (data13.owner_id === void 0) {
          const err28 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "owner_id" }, message: "must have required property 'owner_id'" };
          if (vErrors === null) {
            vErrors = [err28];
          } else {
            vErrors.push(err28);
          }
          errors++;
        }
        if (data13.org_id !== void 0) {
          if (typeof data13.org_id !== "string") {
            const err29 = { instancePath: instancePath + "/scope/org_id", schemaPath: "#/definitions/Scope/properties/org_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
            if (vErrors === null) {
              vErrors = [err29];
            } else {
              vErrors.push(err29);
            }
            errors++;
          }
        }
        if (data13.workspace_id !== void 0) {
          let data15 = data13.workspace_id;
          if (typeof data15 !== "string" && data15 !== null) {
            const err30 = { instancePath: instancePath + "/scope/workspace_id", schemaPath: "#/definitions/Scope/properties/workspace_id/type", keyword: "type", params: { type: schema15.properties.workspace_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err30];
            } else {
              vErrors.push(err30);
            }
            errors++;
          }
        }
        if (data13.agent_id !== void 0) {
          let data16 = data13.agent_id;
          if (typeof data16 !== "string" && data16 !== null) {
            const err31 = { instancePath: instancePath + "/scope/agent_id", schemaPath: "#/definitions/Scope/properties/agent_id/type", keyword: "type", params: { type: schema15.properties.agent_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err31];
            } else {
              vErrors.push(err31);
            }
            errors++;
          }
        }
        if (data13.owner_id !== void 0) {
          let data17 = data13.owner_id;
          if (typeof data17 !== "string" && data17 !== null) {
            const err32 = { instancePath: instancePath + "/scope/owner_id", schemaPath: "#/definitions/Scope/properties/owner_id/type", keyword: "type", params: { type: schema15.properties.owner_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err32];
            } else {
              vErrors.push(err32);
            }
            errors++;
          }
        }
      } else {
        const err33 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/type", keyword: "type", params: { type: "object" }, message: "must be object" };
        if (vErrors === null) {
          vErrors = [err33];
        } else {
          vErrors.push(err33);
        }
        errors++;
      }
    }
  } else {
    const err34 = { instancePath, schemaPath: "#/type", keyword: "type", params: { type: "object" }, message: "must be object" };
    if (vErrors === null) {
      vErrors = [err34];
    } else {
      vErrors.push(err34);
    }
    errors++;
  }
  validate17.errors = vErrors;
  return errors === 0;
}
var schema22 = { "type": "object", "required": ["report_id", "job_id", "source_task_run_id", "attempt_no", "source_global_seq", "model", "root_cause_event", "scope"], "properties": { "report_id": { "type": "string" }, "job_id": { "type": "string" }, "source_task_run_id": { "type": "string" }, "attempt_no": { "type": ["integer", "null"] }, "source_global_seq": { "type": "integer" }, "model": { "type": "string" }, "root_cause_event": { "anyOf": [{ "type": "null" }, { "$ref": "#/definitions/EventReference" }] }, "scope": { "$ref": "#/definitions/Scope" } }, "additionalProperties": true };
var schema23 = { "type": "object", "required": ["task_run_id", "attempt_no", "seq", "global_seq"], "properties": { "task_run_id": { "type": "string" }, "attempt_no": { "type": ["integer", "null"] }, "seq": { "type": "integer", "minimum": 1 }, "global_seq": { "type": "integer", "minimum": 1 } }, "additionalProperties": false };
function validate19(data, { instancePath = "", parentData, parentDataProperty, rootData = data } = {}) {
  let vErrors = null;
  let errors = 0;
  if (data && typeof data == "object" && !Array.isArray(data)) {
    if (data.report_id === void 0) {
      const err0 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "report_id" }, message: "must have required property 'report_id'" };
      if (vErrors === null) {
        vErrors = [err0];
      } else {
        vErrors.push(err0);
      }
      errors++;
    }
    if (data.job_id === void 0) {
      const err1 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "job_id" }, message: "must have required property 'job_id'" };
      if (vErrors === null) {
        vErrors = [err1];
      } else {
        vErrors.push(err1);
      }
      errors++;
    }
    if (data.source_task_run_id === void 0) {
      const err2 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "source_task_run_id" }, message: "must have required property 'source_task_run_id'" };
      if (vErrors === null) {
        vErrors = [err2];
      } else {
        vErrors.push(err2);
      }
      errors++;
    }
    if (data.attempt_no === void 0) {
      const err3 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "attempt_no" }, message: "must have required property 'attempt_no'" };
      if (vErrors === null) {
        vErrors = [err3];
      } else {
        vErrors.push(err3);
      }
      errors++;
    }
    if (data.source_global_seq === void 0) {
      const err4 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "source_global_seq" }, message: "must have required property 'source_global_seq'" };
      if (vErrors === null) {
        vErrors = [err4];
      } else {
        vErrors.push(err4);
      }
      errors++;
    }
    if (data.model === void 0) {
      const err5 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "model" }, message: "must have required property 'model'" };
      if (vErrors === null) {
        vErrors = [err5];
      } else {
        vErrors.push(err5);
      }
      errors++;
    }
    if (data.root_cause_event === void 0) {
      const err6 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "root_cause_event" }, message: "must have required property 'root_cause_event'" };
      if (vErrors === null) {
        vErrors = [err6];
      } else {
        vErrors.push(err6);
      }
      errors++;
    }
    if (data.scope === void 0) {
      const err7 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "scope" }, message: "must have required property 'scope'" };
      if (vErrors === null) {
        vErrors = [err7];
      } else {
        vErrors.push(err7);
      }
      errors++;
    }
    if (data.report_id !== void 0) {
      if (typeof data.report_id !== "string") {
        const err8 = { instancePath: instancePath + "/report_id", schemaPath: "#/properties/report_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err8];
        } else {
          vErrors.push(err8);
        }
        errors++;
      }
    }
    if (data.job_id !== void 0) {
      if (typeof data.job_id !== "string") {
        const err9 = { instancePath: instancePath + "/job_id", schemaPath: "#/properties/job_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err9];
        } else {
          vErrors.push(err9);
        }
        errors++;
      }
    }
    if (data.source_task_run_id !== void 0) {
      if (typeof data.source_task_run_id !== "string") {
        const err10 = { instancePath: instancePath + "/source_task_run_id", schemaPath: "#/properties/source_task_run_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err10];
        } else {
          vErrors.push(err10);
        }
        errors++;
      }
    }
    if (data.attempt_no !== void 0) {
      let data3 = data.attempt_no;
      if (!(typeof data3 == "number" && (!(data3 % 1) && !isNaN(data3))) && data3 !== null) {
        const err11 = { instancePath: instancePath + "/attempt_no", schemaPath: "#/properties/attempt_no/type", keyword: "type", params: { type: schema22.properties.attempt_no.type }, message: "must be integer,null" };
        if (vErrors === null) {
          vErrors = [err11];
        } else {
          vErrors.push(err11);
        }
        errors++;
      }
    }
    if (data.source_global_seq !== void 0) {
      let data4 = data.source_global_seq;
      if (!(typeof data4 == "number" && (!(data4 % 1) && !isNaN(data4)))) {
        const err12 = { instancePath: instancePath + "/source_global_seq", schemaPath: "#/properties/source_global_seq/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err12];
        } else {
          vErrors.push(err12);
        }
        errors++;
      }
    }
    if (data.model !== void 0) {
      if (typeof data.model !== "string") {
        const err13 = { instancePath: instancePath + "/model", schemaPath: "#/properties/model/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err13];
        } else {
          vErrors.push(err13);
        }
        errors++;
      }
    }
    if (data.root_cause_event !== void 0) {
      let data6 = data.root_cause_event;
      const _errs15 = errors;
      let valid1 = false;
      const _errs16 = errors;
      if (data6 !== null) {
        const err14 = { instancePath: instancePath + "/root_cause_event", schemaPath: "#/properties/root_cause_event/anyOf/0/type", keyword: "type", params: { type: "null" }, message: "must be null" };
        if (vErrors === null) {
          vErrors = [err14];
        } else {
          vErrors.push(err14);
        }
        errors++;
      }
      var _valid0 = _errs16 === errors;
      valid1 = valid1 || _valid0;
      if (!valid1) {
        const _errs18 = errors;
        if (data6 && typeof data6 == "object" && !Array.isArray(data6)) {
          if (data6.task_run_id === void 0) {
            const err15 = { instancePath: instancePath + "/root_cause_event", schemaPath: "#/definitions/EventReference/required", keyword: "required", params: { missingProperty: "task_run_id" }, message: "must have required property 'task_run_id'" };
            if (vErrors === null) {
              vErrors = [err15];
            } else {
              vErrors.push(err15);
            }
            errors++;
          }
          if (data6.attempt_no === void 0) {
            const err16 = { instancePath: instancePath + "/root_cause_event", schemaPath: "#/definitions/EventReference/required", keyword: "required", params: { missingProperty: "attempt_no" }, message: "must have required property 'attempt_no'" };
            if (vErrors === null) {
              vErrors = [err16];
            } else {
              vErrors.push(err16);
            }
            errors++;
          }
          if (data6.seq === void 0) {
            const err17 = { instancePath: instancePath + "/root_cause_event", schemaPath: "#/definitions/EventReference/required", keyword: "required", params: { missingProperty: "seq" }, message: "must have required property 'seq'" };
            if (vErrors === null) {
              vErrors = [err17];
            } else {
              vErrors.push(err17);
            }
            errors++;
          }
          if (data6.global_seq === void 0) {
            const err18 = { instancePath: instancePath + "/root_cause_event", schemaPath: "#/definitions/EventReference/required", keyword: "required", params: { missingProperty: "global_seq" }, message: "must have required property 'global_seq'" };
            if (vErrors === null) {
              vErrors = [err18];
            } else {
              vErrors.push(err18);
            }
            errors++;
          }
          for (const key0 in data6) {
            if (!(key0 === "task_run_id" || key0 === "attempt_no" || key0 === "seq" || key0 === "global_seq")) {
              const err19 = { instancePath: instancePath + "/root_cause_event", schemaPath: "#/definitions/EventReference/additionalProperties", keyword: "additionalProperties", params: { additionalProperty: key0 }, message: "must NOT have additional properties" };
              if (vErrors === null) {
                vErrors = [err19];
              } else {
                vErrors.push(err19);
              }
              errors++;
            }
          }
          if (data6.task_run_id !== void 0) {
            if (typeof data6.task_run_id !== "string") {
              const err20 = { instancePath: instancePath + "/root_cause_event/task_run_id", schemaPath: "#/definitions/EventReference/properties/task_run_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
              if (vErrors === null) {
                vErrors = [err20];
              } else {
                vErrors.push(err20);
              }
              errors++;
            }
          }
          if (data6.attempt_no !== void 0) {
            let data8 = data6.attempt_no;
            if (!(typeof data8 == "number" && (!(data8 % 1) && !isNaN(data8))) && data8 !== null) {
              const err21 = { instancePath: instancePath + "/root_cause_event/attempt_no", schemaPath: "#/definitions/EventReference/properties/attempt_no/type", keyword: "type", params: { type: schema23.properties.attempt_no.type }, message: "must be integer,null" };
              if (vErrors === null) {
                vErrors = [err21];
              } else {
                vErrors.push(err21);
              }
              errors++;
            }
          }
          if (data6.seq !== void 0) {
            let data9 = data6.seq;
            if (!(typeof data9 == "number" && (!(data9 % 1) && !isNaN(data9)))) {
              const err22 = { instancePath: instancePath + "/root_cause_event/seq", schemaPath: "#/definitions/EventReference/properties/seq/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
              if (vErrors === null) {
                vErrors = [err22];
              } else {
                vErrors.push(err22);
              }
              errors++;
            }
            if (typeof data9 == "number") {
              if (data9 < 1 || isNaN(data9)) {
                const err23 = { instancePath: instancePath + "/root_cause_event/seq", schemaPath: "#/definitions/EventReference/properties/seq/minimum", keyword: "minimum", params: { comparison: ">=", limit: 1 }, message: "must be >= 1" };
                if (vErrors === null) {
                  vErrors = [err23];
                } else {
                  vErrors.push(err23);
                }
                errors++;
              }
            }
          }
          if (data6.global_seq !== void 0) {
            let data10 = data6.global_seq;
            if (!(typeof data10 == "number" && (!(data10 % 1) && !isNaN(data10)))) {
              const err24 = { instancePath: instancePath + "/root_cause_event/global_seq", schemaPath: "#/definitions/EventReference/properties/global_seq/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
              if (vErrors === null) {
                vErrors = [err24];
              } else {
                vErrors.push(err24);
              }
              errors++;
            }
            if (typeof data10 == "number") {
              if (data10 < 1 || isNaN(data10)) {
                const err25 = { instancePath: instancePath + "/root_cause_event/global_seq", schemaPath: "#/definitions/EventReference/properties/global_seq/minimum", keyword: "minimum", params: { comparison: ">=", limit: 1 }, message: "must be >= 1" };
                if (vErrors === null) {
                  vErrors = [err25];
                } else {
                  vErrors.push(err25);
                }
                errors++;
              }
            }
          }
        } else {
          const err26 = { instancePath: instancePath + "/root_cause_event", schemaPath: "#/definitions/EventReference/type", keyword: "type", params: { type: "object" }, message: "must be object" };
          if (vErrors === null) {
            vErrors = [err26];
          } else {
            vErrors.push(err26);
          }
          errors++;
        }
        var _valid0 = _errs18 === errors;
        valid1 = valid1 || _valid0;
      }
      if (!valid1) {
        const err27 = { instancePath: instancePath + "/root_cause_event", schemaPath: "#/properties/root_cause_event/anyOf", keyword: "anyOf", params: {}, message: "must match a schema in anyOf" };
        if (vErrors === null) {
          vErrors = [err27];
        } else {
          vErrors.push(err27);
        }
        errors++;
      } else {
        errors = _errs15;
        if (vErrors !== null) {
          if (_errs15) {
            vErrors.length = _errs15;
          } else {
            vErrors = null;
          }
        }
      }
    }
    if (data.scope !== void 0) {
      let data11 = data.scope;
      if (data11 && typeof data11 == "object" && !Array.isArray(data11)) {
        if (data11.workspace_id === void 0) {
          const err28 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "workspace_id" }, message: "must have required property 'workspace_id'" };
          if (vErrors === null) {
            vErrors = [err28];
          } else {
            vErrors.push(err28);
          }
          errors++;
        }
        if (data11.agent_id === void 0) {
          const err29 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "agent_id" }, message: "must have required property 'agent_id'" };
          if (vErrors === null) {
            vErrors = [err29];
          } else {
            vErrors.push(err29);
          }
          errors++;
        }
        if (data11.owner_id === void 0) {
          const err30 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/required", keyword: "required", params: { missingProperty: "owner_id" }, message: "must have required property 'owner_id'" };
          if (vErrors === null) {
            vErrors = [err30];
          } else {
            vErrors.push(err30);
          }
          errors++;
        }
        if (data11.org_id !== void 0) {
          if (typeof data11.org_id !== "string") {
            const err31 = { instancePath: instancePath + "/scope/org_id", schemaPath: "#/definitions/Scope/properties/org_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
            if (vErrors === null) {
              vErrors = [err31];
            } else {
              vErrors.push(err31);
            }
            errors++;
          }
        }
        if (data11.workspace_id !== void 0) {
          let data13 = data11.workspace_id;
          if (typeof data13 !== "string" && data13 !== null) {
            const err32 = { instancePath: instancePath + "/scope/workspace_id", schemaPath: "#/definitions/Scope/properties/workspace_id/type", keyword: "type", params: { type: schema15.properties.workspace_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err32];
            } else {
              vErrors.push(err32);
            }
            errors++;
          }
        }
        if (data11.agent_id !== void 0) {
          let data14 = data11.agent_id;
          if (typeof data14 !== "string" && data14 !== null) {
            const err33 = { instancePath: instancePath + "/scope/agent_id", schemaPath: "#/definitions/Scope/properties/agent_id/type", keyword: "type", params: { type: schema15.properties.agent_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err33];
            } else {
              vErrors.push(err33);
            }
            errors++;
          }
        }
        if (data11.owner_id !== void 0) {
          let data15 = data11.owner_id;
          if (typeof data15 !== "string" && data15 !== null) {
            const err34 = { instancePath: instancePath + "/scope/owner_id", schemaPath: "#/definitions/Scope/properties/owner_id/type", keyword: "type", params: { type: schema15.properties.owner_id.type }, message: "must be string,null" };
            if (vErrors === null) {
              vErrors = [err34];
            } else {
              vErrors.push(err34);
            }
            errors++;
          }
        }
      } else {
        const err35 = { instancePath: instancePath + "/scope", schemaPath: "#/definitions/Scope/type", keyword: "type", params: { type: "object" }, message: "must be object" };
        if (vErrors === null) {
          vErrors = [err35];
        } else {
          vErrors.push(err35);
        }
        errors++;
      }
    }
  } else {
    const err36 = { instancePath, schemaPath: "#/type", keyword: "type", params: { type: "object" }, message: "must be object" };
    if (vErrors === null) {
      vErrors = [err36];
    } else {
      vErrors.push(err36);
    }
    errors++;
  }
  validate19.errors = vErrors;
  return errors === 0;
}
var func2 = require_ucs2length().default;
function validate10(data, { instancePath = "", parentData, parentDataProperty, rootData = data } = {}) {
  let vErrors = null;
  let errors = 0;
  const _errs1 = errors;
  let valid0 = false;
  let passing0 = null;
  const _errs2 = errors;
  if (data && typeof data == "object" && !Array.isArray(data)) {
    if (data.type !== void 0) {
      let data0 = data.type;
      if (!(data0 === "run.queued" || data0 === "run.started" || data0 === "run.completed" || data0 === "run.failed" || data0 === "run.cancelled" || data0 === "run.interrupted" || data0 === "run.resumed" || data0 === "step.started" || data0 === "step.completed" || data0 === "llm.request_started" || data0 === "llm.request_done" || data0 === "llm.request_failed" || data0 === "permission.requested" || data0 === "permission.resolved" || data0 === "question.requested" || data0 === "question.answered" || data0 === "queue.paused" || data0 === "queue.resumed" || data0 === "queue.item_enqueued" || data0 === "queue.item_cancelled" || data0 === "artifact.created" || data0 === "artifact.ready" || data0 === "artifact.failed" || data0 === "artifact.missing_detected" || data0 === "materials.imported" || data0 === "conversation.updated" || data0 === "context.compacted" || data0 === "context.budget_checked" || data0 === "memory.updated" || data0 === "memory.archived" || data0 === "memory.snapshot_created" || data0 === "skill.patched" || data0 === "memory.job_status" || data0 === "memory.summary_created" || data0 === "memory.approval_requested" || data0 === "memory.approval_resolved" || data0 === "memory.curated" || data0 === "governance.resource_changed" || data0 === "governance.identity_changed" || data0 === "governance.role_changed" || data0 === "governance.grant_changed" || data0 === "governance.rule_changed" || data0 === "governance.skill_version_published" || data0 === "governance.authorization_checked" || data0 === "governance.audit_verified" || data0 === "governance.backup_created" || data0 === "governance.backup_restored" || data0 === "governance.access_denied" || data0 === "notification.created" || data0 === "notification.updated" || data0 === "runtime.power_changed")) {
        const err0 = { instancePath: instancePath + "/type", schemaPath: "#/oneOf/0/properties/type/enum", keyword: "enum", params: { allowedValues: schema11.oneOf[0].properties.type.enum }, message: "must be equal to one of the allowed values" };
        if (vErrors === null) {
          vErrors = [err0];
        } else {
          vErrors.push(err0);
        }
        errors++;
      }
    }
  }
  var _valid0 = _errs2 === errors;
  if (_valid0) {
    valid0 = true;
    passing0 = 0;
  }
  const _errs4 = errors;
  if (data && typeof data == "object" && !Array.isArray(data)) {
    if (data.type !== void 0) {
      if (!(data.type === "tool.prepared")) {
        const err1 = { instancePath: instancePath + "/type", schemaPath: "#/oneOf/1/properties/type/enum", keyword: "enum", params: { allowedValues: schema11.oneOf[1].properties.type.enum }, message: "must be equal to one of the allowed values" };
        if (vErrors === null) {
          vErrors = [err1];
        } else {
          vErrors.push(err1);
        }
        errors++;
      }
    }
    if (data.payload !== void 0) {
      let data2 = data.payload;
      if (data2 && typeof data2 == "object" && !Array.isArray(data2)) {
        if (data2.call_id === void 0) {
          const err2 = { instancePath: instancePath + "/payload", schemaPath: "#/definitions/PreparedTool/required", keyword: "required", params: { missingProperty: "call_id" }, message: "must have required property 'call_id'" };
          if (vErrors === null) {
            vErrors = [err2];
          } else {
            vErrors.push(err2);
          }
          errors++;
        }
        if (data2.tool_name === void 0) {
          const err3 = { instancePath: instancePath + "/payload", schemaPath: "#/definitions/PreparedTool/required", keyword: "required", params: { missingProperty: "tool_name" }, message: "must have required property 'tool_name'" };
          if (vErrors === null) {
            vErrors = [err3];
          } else {
            vErrors.push(err3);
          }
          errors++;
        }
        if (data2.input_hash === void 0) {
          const err4 = { instancePath: instancePath + "/payload", schemaPath: "#/definitions/PreparedTool/required", keyword: "required", params: { missingProperty: "input_hash" }, message: "must have required property 'input_hash'" };
          if (vErrors === null) {
            vErrors = [err4];
          } else {
            vErrors.push(err4);
          }
          errors++;
        }
        if (data2.input === void 0) {
          const err5 = { instancePath: instancePath + "/payload", schemaPath: "#/definitions/PreparedTool/required", keyword: "required", params: { missingProperty: "input" }, message: "must have required property 'input'" };
          if (vErrors === null) {
            vErrors = [err5];
          } else {
            vErrors.push(err5);
          }
          errors++;
        }
        if (data2.call_id !== void 0) {
          if (typeof data2.call_id !== "string") {
            const err6 = { instancePath: instancePath + "/payload/call_id", schemaPath: "#/definitions/PreparedTool/properties/call_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
            if (vErrors === null) {
              vErrors = [err6];
            } else {
              vErrors.push(err6);
            }
            errors++;
          }
        }
        if (data2.tool_name !== void 0) {
          if (typeof data2.tool_name !== "string") {
            const err7 = { instancePath: instancePath + "/payload/tool_name", schemaPath: "#/definitions/PreparedTool/properties/tool_name/type", keyword: "type", params: { type: "string" }, message: "must be string" };
            if (vErrors === null) {
              vErrors = [err7];
            } else {
              vErrors.push(err7);
            }
            errors++;
          }
        }
        if (data2.input_hash !== void 0) {
          if (typeof data2.input_hash !== "string") {
            const err8 = { instancePath: instancePath + "/payload/input_hash", schemaPath: "#/definitions/PreparedTool/properties/input_hash/type", keyword: "type", params: { type: "string" }, message: "must be string" };
            if (vErrors === null) {
              vErrors = [err8];
            } else {
              vErrors.push(err8);
            }
            errors++;
          }
        }
        if (data2.input !== void 0) {
          let data6 = data2.input;
          if (!(data6 && typeof data6 == "object" && !Array.isArray(data6))) {
            const err9 = { instancePath: instancePath + "/payload/input", schemaPath: "#/definitions/PreparedTool/properties/input/type", keyword: "type", params: { type: "object" }, message: "must be object" };
            if (vErrors === null) {
              vErrors = [err9];
            } else {
              vErrors.push(err9);
            }
            errors++;
          }
        }
        if (data2.side_effect_class !== void 0) {
          if (typeof data2.side_effect_class !== "string") {
            const err10 = { instancePath: instancePath + "/payload/side_effect_class", schemaPath: "#/definitions/PreparedTool/properties/side_effect_class/type", keyword: "type", params: { type: "string" }, message: "must be string" };
            if (vErrors === null) {
              vErrors = [err10];
            } else {
              vErrors.push(err10);
            }
            errors++;
          }
        }
        if (data2.risk_level !== void 0) {
          if (typeof data2.risk_level !== "string") {
            const err11 = { instancePath: instancePath + "/payload/risk_level", schemaPath: "#/definitions/PreparedTool/properties/risk_level/type", keyword: "type", params: { type: "string" }, message: "must be string" };
            if (vErrors === null) {
              vErrors = [err11];
            } else {
              vErrors.push(err11);
            }
            errors++;
          }
        }
        if (data2.read_only_verdict !== void 0) {
          if (typeof data2.read_only_verdict !== "boolean") {
            const err12 = { instancePath: instancePath + "/payload/read_only_verdict", schemaPath: "#/definitions/PreparedTool/properties/read_only_verdict/type", keyword: "type", params: { type: "boolean" }, message: "must be boolean" };
            if (vErrors === null) {
              vErrors = [err12];
            } else {
              vErrors.push(err12);
            }
            errors++;
          }
        }
        if (data2.step_id !== void 0) {
          if (typeof data2.step_id !== "string") {
            const err13 = { instancePath: instancePath + "/payload/step_id", schemaPath: "#/definitions/PreparedTool/properties/step_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
            if (vErrors === null) {
              vErrors = [err13];
            } else {
              vErrors.push(err13);
            }
            errors++;
          }
        }
      } else {
        const err14 = { instancePath: instancePath + "/payload", schemaPath: "#/definitions/PreparedTool/type", keyword: "type", params: { type: "object" }, message: "must be object" };
        if (vErrors === null) {
          vErrors = [err14];
        } else {
          vErrors.push(err14);
        }
        errors++;
      }
    }
  }
  var _valid0 = _errs4 === errors;
  if (_valid0 && valid0) {
    valid0 = false;
    passing0 = [passing0, 1];
  } else {
    if (_valid0) {
      valid0 = true;
      passing0 = 1;
    }
    const _errs26 = errors;
    if (data && typeof data == "object" && !Array.isArray(data)) {
      if (data.type !== void 0) {
        let data11 = data.type;
        if (!(data11 === "tool.dispatched" || data11 === "tool.completed" || data11 === "tool.failed" || data11 === "tool.skipped_idempotent" || data11 === "tool.pending_verification" || data11 === "tool.verification_submitted" || data11 === "tool.result_externalized")) {
          const err15 = { instancePath: instancePath + "/type", schemaPath: "#/oneOf/2/properties/type/enum", keyword: "enum", params: { allowedValues: schema11.oneOf[2].properties.type.enum }, message: "must be equal to one of the allowed values" };
          if (vErrors === null) {
            vErrors = [err15];
          } else {
            vErrors.push(err15);
          }
          errors++;
        }
      }
      if (data.payload !== void 0) {
        let data12 = data.payload;
        if (data12 && typeof data12 == "object" && !Array.isArray(data12)) {
          if (data12.call_id === void 0) {
            const err16 = { instancePath: instancePath + "/payload", schemaPath: "#/definitions/ToolResult/required", keyword: "required", params: { missingProperty: "call_id" }, message: "must have required property 'call_id'" };
            if (vErrors === null) {
              vErrors = [err16];
            } else {
              vErrors.push(err16);
            }
            errors++;
          }
          if (data12.call_id !== void 0) {
            if (typeof data12.call_id !== "string") {
              const err17 = { instancePath: instancePath + "/payload/call_id", schemaPath: "#/definitions/ToolResult/properties/call_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
              if (vErrors === null) {
                vErrors = [err17];
              } else {
                vErrors.push(err17);
              }
              errors++;
            }
          }
          if (data12.output !== void 0) {
            if (typeof data12.output !== "string") {
              const err18 = { instancePath: instancePath + "/payload/output", schemaPath: "#/definitions/ToolResult/properties/output/type", keyword: "type", params: { type: "string" }, message: "must be string" };
              if (vErrors === null) {
                vErrors = [err18];
              } else {
                vErrors.push(err18);
              }
              errors++;
            }
          }
          if (data12.output_summary !== void 0) {
            if (typeof data12.output_summary !== "string") {
              const err19 = { instancePath: instancePath + "/payload/output_summary", schemaPath: "#/definitions/ToolResult/properties/output_summary/type", keyword: "type", params: { type: "string" }, message: "must be string" };
              if (vErrors === null) {
                vErrors = [err19];
              } else {
                vErrors.push(err19);
              }
              errors++;
            }
          }
          if (data12.error !== void 0) {
            if (typeof data12.error !== "string") {
              const err20 = { instancePath: instancePath + "/payload/error", schemaPath: "#/definitions/ToolResult/properties/error/type", keyword: "type", params: { type: "string" }, message: "must be string" };
              if (vErrors === null) {
                vErrors = [err20];
              } else {
                vErrors.push(err20);
              }
              errors++;
            }
          }
          if (data12.details !== void 0) {
            let data17 = data12.details;
            if (!(data17 && typeof data17 == "object" && !Array.isArray(data17))) {
              const err21 = { instancePath: instancePath + "/payload/details", schemaPath: "#/definitions/ToolResult/properties/details/type", keyword: "type", params: { type: "object" }, message: "must be object" };
              if (vErrors === null) {
                vErrors = [err21];
              } else {
                vErrors.push(err21);
              }
              errors++;
            }
          }
          if (data12.artifact_path !== void 0) {
            if (typeof data12.artifact_path !== "string") {
              const err22 = { instancePath: instancePath + "/payload/artifact_path", schemaPath: "#/definitions/ToolResult/properties/artifact_path/type", keyword: "type", params: { type: "string" }, message: "must be string" };
              if (vErrors === null) {
                vErrors = [err22];
              } else {
                vErrors.push(err22);
              }
              errors++;
            }
          }
          if (data12.note !== void 0) {
            let data19 = data12.note;
            if (typeof data19 !== "string" && data19 !== null) {
              const err23 = { instancePath: instancePath + "/payload/note", schemaPath: "#/definitions/ToolResult/properties/note/type", keyword: "type", params: { type: schema13.properties.note.type }, message: "must be string,null" };
              if (vErrors === null) {
                vErrors = [err23];
              } else {
                vErrors.push(err23);
              }
              errors++;
            }
          }
          if (data12.verdict !== void 0) {
            if (typeof data12.verdict !== "string") {
              const err24 = { instancePath: instancePath + "/payload/verdict", schemaPath: "#/definitions/ToolResult/properties/verdict/type", keyword: "type", params: { type: "string" }, message: "must be string" };
              if (vErrors === null) {
                vErrors = [err24];
              } else {
                vErrors.push(err24);
              }
              errors++;
            }
          }
        } else {
          const err25 = { instancePath: instancePath + "/payload", schemaPath: "#/definitions/ToolResult/type", keyword: "type", params: { type: "object" }, message: "must be object" };
          if (vErrors === null) {
            vErrors = [err25];
          } else {
            vErrors.push(err25);
          }
          errors++;
        }
      }
    }
    var _valid0 = _errs26 === errors;
    if (_valid0 && valid0) {
      valid0 = false;
      passing0 = [passing0, 2];
    } else {
      if (_valid0) {
        valid0 = true;
        passing0 = 2;
      }
      const _errs48 = errors;
      if (data && typeof data == "object" && !Array.isArray(data)) {
        if (data.type !== void 0) {
          if (!(data.type === "cron.job_changed")) {
            const err26 = { instancePath: instancePath + "/type", schemaPath: "#/oneOf/3/properties/type/enum", keyword: "enum", params: { allowedValues: schema11.oneOf[3].properties.type.enum }, message: "must be equal to one of the allowed values" };
            if (vErrors === null) {
              vErrors = [err26];
            } else {
              vErrors.push(err26);
            }
            errors++;
          }
        }
        if (data.payload !== void 0) {
          if (!validate11(data.payload, { instancePath: instancePath + "/payload", parentData: data, parentDataProperty: "payload", rootData })) {
            vErrors = vErrors === null ? validate11.errors : vErrors.concat(validate11.errors);
            errors = vErrors.length;
          }
        }
      }
      var _valid0 = _errs48 === errors;
      if (_valid0 && valid0) {
        valid0 = false;
        passing0 = [passing0, 3];
      } else {
        if (_valid0) {
          valid0 = true;
          passing0 = 3;
        }
        const _errs51 = errors;
        if (data && typeof data == "object" && !Array.isArray(data)) {
          if (data.type !== void 0) {
            let data23 = data.type;
            if (!(data23 === "cron.job_fired" || data23 === "cron.job_missed" || data23 === "cron.job_skipped" || data23 === "cron.job_failed" || data23 === "cron.job_status")) {
              const err27 = { instancePath: instancePath + "/type", schemaPath: "#/oneOf/4/properties/type/enum", keyword: "enum", params: { allowedValues: schema11.oneOf[4].properties.type.enum }, message: "must be equal to one of the allowed values" };
              if (vErrors === null) {
                vErrors = [err27];
              } else {
                vErrors.push(err27);
              }
              errors++;
            }
          }
          if (data.payload !== void 0) {
            if (!validate13(data.payload, { instancePath: instancePath + "/payload", parentData: data, parentDataProperty: "payload", rootData })) {
              vErrors = vErrors === null ? validate13.errors : vErrors.concat(validate13.errors);
              errors = vErrors.length;
            }
          }
        }
        var _valid0 = _errs51 === errors;
        if (_valid0 && valid0) {
          valid0 = false;
          passing0 = [passing0, 4];
        } else {
          if (_valid0) {
            valid0 = true;
            passing0 = 4;
          }
          const _errs54 = errors;
          if (data && typeof data == "object" && !Array.isArray(data)) {
            if (data.type !== void 0) {
              let data25 = data.type;
              if (!(data25 === "cron.proposal_requested" || data25 === "cron.proposal_resolved")) {
                const err28 = { instancePath: instancePath + "/type", schemaPath: "#/oneOf/5/properties/type/enum", keyword: "enum", params: { allowedValues: schema11.oneOf[5].properties.type.enum }, message: "must be equal to one of the allowed values" };
                if (vErrors === null) {
                  vErrors = [err28];
                } else {
                  vErrors.push(err28);
                }
                errors++;
              }
            }
            if (data.payload !== void 0) {
              if (!validate15(data.payload, { instancePath: instancePath + "/payload", parentData: data, parentDataProperty: "payload", rootData })) {
                vErrors = vErrors === null ? validate15.errors : vErrors.concat(validate15.errors);
                errors = vErrors.length;
              }
            }
          }
          var _valid0 = _errs54 === errors;
          if (_valid0 && valid0) {
            valid0 = false;
            passing0 = [passing0, 5];
          } else {
            if (_valid0) {
              valid0 = true;
              passing0 = 5;
            }
            const _errs57 = errors;
            if (data && typeof data == "object" && !Array.isArray(data)) {
              if (data.type !== void 0) {
                if (!(data.type === "review.job_status")) {
                  const err29 = { instancePath: instancePath + "/type", schemaPath: "#/oneOf/6/properties/type/enum", keyword: "enum", params: { allowedValues: schema11.oneOf[6].properties.type.enum }, message: "must be equal to one of the allowed values" };
                  if (vErrors === null) {
                    vErrors = [err29];
                  } else {
                    vErrors.push(err29);
                  }
                  errors++;
                }
              }
              if (data.payload !== void 0) {
                if (!validate17(data.payload, { instancePath: instancePath + "/payload", parentData: data, parentDataProperty: "payload", rootData })) {
                  vErrors = vErrors === null ? validate17.errors : vErrors.concat(validate17.errors);
                  errors = vErrors.length;
                }
              }
            }
            var _valid0 = _errs57 === errors;
            if (_valid0 && valid0) {
              valid0 = false;
              passing0 = [passing0, 6];
            } else {
              if (_valid0) {
                valid0 = true;
                passing0 = 6;
              }
              const _errs60 = errors;
              if (data && typeof data == "object" && !Array.isArray(data)) {
                if (data.type !== void 0) {
                  if (!(data.type === "audit.reported")) {
                    const err30 = { instancePath: instancePath + "/type", schemaPath: "#/oneOf/7/properties/type/enum", keyword: "enum", params: { allowedValues: schema11.oneOf[7].properties.type.enum }, message: "must be equal to one of the allowed values" };
                    if (vErrors === null) {
                      vErrors = [err30];
                    } else {
                      vErrors.push(err30);
                    }
                    errors++;
                  }
                }
                if (data.payload !== void 0) {
                  if (!validate19(data.payload, { instancePath: instancePath + "/payload", parentData: data, parentDataProperty: "payload", rootData })) {
                    vErrors = vErrors === null ? validate19.errors : vErrors.concat(validate19.errors);
                    errors = vErrors.length;
                  }
                }
              }
              var _valid0 = _errs60 === errors;
              if (_valid0 && valid0) {
                valid0 = false;
                passing0 = [passing0, 7];
              } else {
                if (_valid0) {
                  valid0 = true;
                  passing0 = 7;
                }
              }
            }
          }
        }
      }
    }
  }
  if (!valid0) {
    const err31 = { instancePath, schemaPath: "#/oneOf", keyword: "oneOf", params: { passingSchemas: passing0 }, message: "must match exactly one schema in oneOf" };
    if (vErrors === null) {
      vErrors = [err31];
    } else {
      vErrors.push(err31);
    }
    errors++;
  } else {
    errors = _errs1;
    if (vErrors !== null) {
      if (_errs1) {
        vErrors.length = _errs1;
      } else {
        vErrors = null;
      }
    }
  }
  if (data && typeof data == "object" && !Array.isArray(data)) {
    if (data.global_seq === void 0) {
      const err32 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "global_seq" }, message: "must have required property 'global_seq'" };
      if (vErrors === null) {
        vErrors = [err32];
      } else {
        vErrors.push(err32);
      }
      errors++;
    }
    if (data.type === void 0) {
      const err33 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "type" }, message: "must have required property 'type'" };
      if (vErrors === null) {
        vErrors = [err33];
      } else {
        vErrors.push(err33);
      }
      errors++;
    }
    if (data.payload === void 0) {
      const err34 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "payload" }, message: "must have required property 'payload'" };
      if (vErrors === null) {
        vErrors = [err34];
      } else {
        vErrors.push(err34);
      }
      errors++;
    }
    if (data.ts === void 0) {
      const err35 = { instancePath, schemaPath: "#/required", keyword: "required", params: { missingProperty: "ts" }, message: "must have required property 'ts'" };
      if (vErrors === null) {
        vErrors = [err35];
      } else {
        vErrors.push(err35);
      }
      errors++;
    }
    if (data.global_seq !== void 0) {
      let data31 = data.global_seq;
      if (!(typeof data31 == "number" && (!(data31 % 1) && !isNaN(data31)))) {
        const err36 = { instancePath: instancePath + "/global_seq", schemaPath: "#/properties/global_seq/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err36];
        } else {
          vErrors.push(err36);
        }
        errors++;
      }
      if (typeof data31 == "number") {
        if (data31 < 1 || isNaN(data31)) {
          const err37 = { instancePath: instancePath + "/global_seq", schemaPath: "#/properties/global_seq/minimum", keyword: "minimum", params: { comparison: ">=", limit: 1 }, message: "must be >= 1" };
          if (vErrors === null) {
            vErrors = [err37];
          } else {
            vErrors.push(err37);
          }
          errors++;
        }
      }
    }
    if (data.type !== void 0) {
      let data32 = data.type;
      if (typeof data32 !== "string") {
        const err38 = { instancePath: instancePath + "/type", schemaPath: "#/definitions/EventType/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err38];
        } else {
          vErrors.push(err38);
        }
        errors++;
      }
      if (!(data32 === "run.queued" || data32 === "run.started" || data32 === "run.completed" || data32 === "run.failed" || data32 === "run.cancelled" || data32 === "run.interrupted" || data32 === "run.resumed" || data32 === "step.started" || data32 === "step.completed" || data32 === "llm.request_started" || data32 === "llm.request_done" || data32 === "llm.request_failed" || data32 === "tool.prepared" || data32 === "tool.dispatched" || data32 === "tool.completed" || data32 === "tool.failed" || data32 === "tool.skipped_idempotent" || data32 === "tool.pending_verification" || data32 === "permission.requested" || data32 === "permission.resolved" || data32 === "question.requested" || data32 === "question.answered" || data32 === "queue.paused" || data32 === "queue.resumed" || data32 === "queue.item_enqueued" || data32 === "queue.item_cancelled" || data32 === "tool.verification_submitted" || data32 === "artifact.created" || data32 === "artifact.ready" || data32 === "artifact.failed" || data32 === "artifact.missing_detected" || data32 === "materials.imported" || data32 === "conversation.updated" || data32 === "context.compacted" || data32 === "context.budget_checked" || data32 === "tool.result_externalized" || data32 === "memory.updated" || data32 === "memory.archived" || data32 === "memory.snapshot_created" || data32 === "skill.patched" || data32 === "memory.job_status" || data32 === "memory.summary_created" || data32 === "memory.approval_requested" || data32 === "memory.approval_resolved" || data32 === "memory.curated" || data32 === "governance.resource_changed" || data32 === "governance.identity_changed" || data32 === "governance.role_changed" || data32 === "governance.grant_changed" || data32 === "governance.rule_changed" || data32 === "governance.skill_version_published" || data32 === "governance.authorization_checked" || data32 === "governance.audit_verified" || data32 === "governance.backup_created" || data32 === "governance.backup_restored" || data32 === "governance.access_denied" || data32 === "cron.job_changed" || data32 === "cron.job_fired" || data32 === "cron.job_missed" || data32 === "cron.job_skipped" || data32 === "cron.job_failed" || data32 === "cron.job_status" || data32 === "cron.proposal_requested" || data32 === "cron.proposal_resolved" || data32 === "review.job_status" || data32 === "audit.reported" || data32 === "notification.created" || data32 === "notification.updated" || data32 === "runtime.power_changed")) {
        const err39 = { instancePath: instancePath + "/type", schemaPath: "#/definitions/EventType/enum", keyword: "enum", params: { allowedValues: schema25.enum }, message: "must be equal to one of the allowed values" };
        if (vErrors === null) {
          vErrors = [err39];
        } else {
          vErrors.push(err39);
        }
        errors++;
      }
    }
    if (data.payload !== void 0) {
      let data33 = data.payload;
      if (!(data33 && typeof data33 == "object" && !Array.isArray(data33))) {
        const err40 = { instancePath: instancePath + "/payload", schemaPath: "#/properties/payload/type", keyword: "type", params: { type: "object" }, message: "must be object" };
        if (vErrors === null) {
          vErrors = [err40];
        } else {
          vErrors.push(err40);
        }
        errors++;
      }
    }
    if (data.ts !== void 0) {
      let data34 = data.ts;
      if (typeof data34 === "string") {
        if (func2(data34) < 1) {
          const err41 = { instancePath: instancePath + "/ts", schemaPath: "#/properties/ts/minLength", keyword: "minLength", params: { limit: 1 }, message: "must NOT have fewer than 1 characters" };
          if (vErrors === null) {
            vErrors = [err41];
          } else {
            vErrors.push(err41);
          }
          errors++;
        }
      } else {
        const err42 = { instancePath: instancePath + "/ts", schemaPath: "#/properties/ts/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err42];
        } else {
          vErrors.push(err42);
        }
        errors++;
      }
    }
    if (data.task_run_id !== void 0) {
      if (typeof data.task_run_id !== "string") {
        const err43 = { instancePath: instancePath + "/task_run_id", schemaPath: "#/properties/task_run_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err43];
        } else {
          vErrors.push(err43);
        }
        errors++;
      }
    }
    if (data.seq !== void 0) {
      let data36 = data.seq;
      if (!(typeof data36 == "number" && (!(data36 % 1) && !isNaN(data36)))) {
        const err44 = { instancePath: instancePath + "/seq", schemaPath: "#/properties/seq/type", keyword: "type", params: { type: "integer" }, message: "must be integer" };
        if (vErrors === null) {
          vErrors = [err44];
        } else {
          vErrors.push(err44);
        }
        errors++;
      }
      if (typeof data36 == "number") {
        if (data36 < 1 || isNaN(data36)) {
          const err45 = { instancePath: instancePath + "/seq", schemaPath: "#/properties/seq/minimum", keyword: "minimum", params: { comparison: ">=", limit: 1 }, message: "must be >= 1" };
          if (vErrors === null) {
            vErrors = [err45];
          } else {
            vErrors.push(err45);
          }
          errors++;
        }
      }
    }
    if (data.attempt_no !== void 0) {
      let data37 = data.attempt_no;
      if (!(typeof data37 == "number" && (!(data37 % 1) && !isNaN(data37))) && data37 !== null) {
        const err46 = { instancePath: instancePath + "/attempt_no", schemaPath: "#/properties/attempt_no/type", keyword: "type", params: { type: schema11.properties.attempt_no.type }, message: "must be integer,null" };
        if (vErrors === null) {
          vErrors = [err46];
        } else {
          vErrors.push(err46);
        }
        errors++;
      }
      if (typeof data37 == "number") {
        if (data37 < 1 || isNaN(data37)) {
          const err47 = { instancePath: instancePath + "/attempt_no", schemaPath: "#/properties/attempt_no/minimum", keyword: "minimum", params: { comparison: ">=", limit: 1 }, message: "must be >= 1" };
          if (vErrors === null) {
            vErrors = [err47];
          } else {
            vErrors.push(err47);
          }
          errors++;
        }
      }
    }
    if (data.conversation_id !== void 0) {
      if (typeof data.conversation_id !== "string") {
        const err48 = { instancePath: instancePath + "/conversation_id", schemaPath: "#/properties/conversation_id/type", keyword: "type", params: { type: "string" }, message: "must be string" };
        if (vErrors === null) {
          vErrors = [err48];
        } else {
          vErrors.push(err48);
        }
        errors++;
      }
    }
  } else {
    const err49 = { instancePath, schemaPath: "#/type", keyword: "type", params: { type: "object" }, message: "must be object" };
    if (vErrors === null) {
      vErrors = [err49];
    } else {
      vErrors.push(err49);
    }
    errors++;
  }
  validate10.errors = vErrors;
  return errors === 0;
}
export {
  stdin_default as default,
  validate
};
