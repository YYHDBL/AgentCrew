"""M3-02：自有历史状态计算，校验水位内的等待、终态与核验。"""

from agentcrew_core.events import Event, RunEventType


def test_display_retains_actual_tool_parameter_schema():
    from agentcrew_server.runs import event_frame
    from agentcrew_core.tools import build_default_registry
    schemas = build_default_registry().schemas()
    event = Event(1, "schema", "task", 1, "conversation", RunEventType.LLM_REQUEST_STARTED,
                  {"tool_declarations": schemas}, attempt_no=1)
    assert event_frame(event)["payload"]["tool_declarations"] == schemas


def test_schema_shaped_payload_cannot_exempt_sensitive_fields():
    from secrets import token_urlsafe
    from agentcrew_server.runs import display_value, event_frame
    secret = token_urlsafe(32)
    payload = {"type": "object", "properties": {"name": {"type": "string"}},
               "api_key": secret, "Authorization": secret, "identity_token": secret}
    displayed = display_value(payload)
    assert secret not in str(displayed)
    assert set(displayed) == {"type", "properties"}
    event = Event(1, "result", "task", 1, "conversation", RunEventType.TOOL_COMPLETED,
                  {"details": payload, "tool_declarations": [payload]}, attempt_no=1)
    assert secret not in str(event_frame(event))


def test_historical_state_uses_only_supplied_event_watermark():
    from agentcrew_core.events.run_projection import project_run_state
    events = [Event(index, str(index), "task", index, "conversation", RunEventType(kind), payload, attempt_no=attempt, ts=str(index))
              for index, kind, payload, attempt in (
                  (1, "run.queued", {}, None), (2, "run.started", {"attempt_no": 1}, 1),
                  (3, "question.requested", {"request_id": "question"}, 1),
                  (4, "question.answered", {"request_id": "question"}, 1),
                  (5, "run.completed", {}, 1))]
    assert project_run_state(events[:3])["status"] == "waiting_user"
    assert project_run_state(events)["status"] == "completed"
    assert project_run_state(events)["finished_at"] == "5"


def test_verification_preserves_pending_until_all_calls_settle():
    from agentcrew_core.events.run_projection import project_run_state
    events = [Event(index, str(index), "task", index, "conversation", RunEventType(kind), payload, attempt_no=1, ts=str(index))
              for index, kind, payload in (
                  (1, "run.queued", {}), (2, "run.started", {"attempt_no": 1}),
                  (3, "tool.pending_verification", {"call_id": "first"}),
                  (4, "tool.pending_verification", {"call_id": "second"}),
                  (5, "run.failed", {"reason": "unknown_effect"}),
                  (6, "tool.verification_submitted", {"call_id": "first", "verdict": "confirmed_executed"}),
                  (7, "tool.verification_submitted", {"call_id": "second", "verdict": "confirmed_not_executed"}))]
    assert project_run_state(events[:6])["status"] == "waiting_verification"
    assert project_run_state(events)["status"] == "interrupted"
    assert project_run_state(events)["finished_at"] is None
