"""Recording schema invariants."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from driftcatch.recording import (
    ErrorInfo,
    Event,
    LLMEvent,
    LLMRequest,
    LLMResponse,
    Outcome,
    Recording,
    Status,
    ToolEvent,
    Usage,
)

_event_adapter: TypeAdapter[Event] = TypeAdapter(Event)


def test_tool_event_rejects_both_result_and_error() -> None:
    """An event records a success or a failure, never both."""
    with pytest.raises(ValidationError, match="both a success payload and an error"):
        ToolEvent(
            index=0,
            name="issue_refund",
            args={},
            result={"ok": True},
            error=ErrorInfo(type="TimeoutError", message="boom"),
        )


def test_llm_event_rejects_both_response_and_error() -> None:
    with pytest.raises(ValidationError, match="both a success payload and an error"):
        LLMEvent(
            index=0,
            request=LLMRequest(model="m"),
            response=LLMResponse(content="hi"),
            error=ErrorInfo(type="APIError", message="boom", status=500),
        )


def test_error_only_event_is_valid() -> None:
    """Errors are behaviour worth pinning, so an error-only event must be legal."""
    event = ToolEvent(
        index=2,
        name="issue_refund",
        args={"id": 1},
        error=ErrorInfo(type="TimeoutError", message="upstream timed out"),
    )
    assert event.outcome is Outcome.ERROR
    assert event.result is None


def test_discriminated_union_parses_by_type() -> None:
    """Events deserialise into the right class from the ``type`` field alone."""
    llm = _event_adapter.validate_python(
        {"type": "llm", "index": 0, "request": {"model": "gpt-5.2"}}
    )
    tool = _event_adapter.validate_python(
        {"type": "tool", "index": 1, "name": "lookup_order", "args": {}}
    )
    assert isinstance(llm, LLMEvent)
    assert isinstance(tool, ToolEvent)


def test_decided_reports_tool_choices() -> None:
    """``decided`` is what feeds the trajectory and behaviour coverage."""
    event = LLMEvent(
        index=0,
        request=LLMRequest(model="gpt-5.2"),
        response=LLMResponse(tool_calls=[{"name": "lookup_order"}, {"name": "issue_refund"}]),
    )
    assert event.decided == ["lookup_order", "issue_refund"]


def test_decided_is_empty_without_a_response() -> None:
    event = LLMEvent(index=0, request=LLMRequest(model="gpt-5.2"))
    assert event.decided == []


def test_usage_total_tokens() -> None:
    assert Usage(input_tokens=10, output_tokens=5).total_tokens == 15


def test_recording_defaults_to_pending() -> None:
    """Nothing is golden until a human approves it."""
    recording = Recording(name="x", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert recording.status is Status.PENDING
    assert recording.is_golden is False


def test_recording_is_golden_when_approved(sample_recording: Recording) -> None:
    assert sample_recording.is_golden is True
