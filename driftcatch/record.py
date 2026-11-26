"""Recording capture.

Builds a PENDING recording from the events a run produced. Three things happen
in :meth:`Recorder.finalize` and the order matters:

1. redact, so credentials never reach disk;
2. refuse to continue if anything credential-shaped survived;
3. derive the fingerprint, so drift detection works without the user declaring
   anything.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter

from driftcatch import trajectory as trajectory_mod
from driftcatch.config import Config
from driftcatch.recording.fingerprint import compute_fingerprint
from driftcatch.recording.schema import (
    ErrorInfo,
    Event,
    Fingerprint,
    LLMEvent,
    LLMRequest,
    LLMResponse,
    Recording,
    RecordingMeta,
    Status,
    ToolEvent,
    Usage,
)
from driftcatch.redact import assert_clean, redact
from driftcatch.session import Session

__all__ = ["Recorder", "fingerprint_for"]

_event_adapter: TypeAdapter[Event] = TypeAdapter(Event)


def fingerprint_from_request(request: LLMRequest, config: Config) -> Fingerprint:
    """Fingerprint the behaviour-determining inputs of a single model call."""
    system_prompt = "\n".join(
        str(message.get("content", ""))
        for message in request.messages
        if message.get("role") == "system"
    )
    return compute_fingerprint(
        model_id=request.model,
        system_prompt=system_prompt,
        tool_schemas=request.tools,
        matcher_config={"matcher": config.matcher, "normalizers": config.normalizers},
    )


def fingerprint_for(events: list[Event], config: Config) -> Fingerprint:
    """Derive the behaviour-determining inputs from the captured events.

    Deriving these automatically is deliberate: asking users to declare their
    system prompt and tool schemas would be an adoption tax, and a declaration
    that drifts from reality is worse than none.
    """
    first_llm = next((e for e in events if isinstance(e, LLMEvent)), None)
    if first_llm is None:
        return compute_fingerprint(
            model_id="none",
            system_prompt="",
            tool_schemas=[],
            matcher_config={"matcher": config.matcher, "normalizers": config.normalizers},
        )
    return fingerprint_from_request(first_llm.request, config)


class Recorder:
    """Appends events to a session and turns them into a recording."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- capture ---------------------------------------------------------

    def add_llm(
        self,
        request: LLMRequest,
        *,
        response: LLMResponse | None = None,
        error: ErrorInfo | None = None,
        raw_response: dict[str, Any] | None = None,
        status_code: int = 200,
        usage: Usage | None = None,
        stream_chunks: list[str] | None = None,
        duration_ms: int | None = None,
    ) -> LLMEvent:
        event = LLMEvent(
            index=self.session.next_index(),
            duration_ms=duration_ms,
            request=request,
            response=response,
            error=error,
            raw_response=raw_response,
            status_code=status_code,
            usage=usage or Usage(),
            stream_chunks=stream_chunks,
        )
        self.session.add(event)
        return event

    def add_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        result: Any | None = None,
        error: ErrorInfo | None = None,
        duration_ms: int | None = None,
    ) -> ToolEvent:
        event = ToolEvent(
            index=self.session.next_index(),
            duration_ms=duration_ms,
            name=name,
            args=args,
            result=result,
            error=error,
        )
        self.session.add(event)
        return event

    # -- finalize --------------------------------------------------------

    def finalize(self, *, status: Status = Status.PENDING) -> Recording:
        """Produce the recording. Never writes; the caller decides that."""
        config = self.session.config

        raw = [event.model_dump(mode="json") for event in self.session.events]
        cleaned, redacted_fields = redact(raw, config.redactors)
        assert_clean(cleaned)

        events: list[Event] = [_event_adapter.validate_python(item) for item in cleaned]

        total = Usage()
        for event in events:
            if isinstance(event, LLMEvent):
                total.input_tokens += event.usage.input_tokens
                total.output_tokens += event.usage.output_tokens
                if event.usage.cost_usd:
                    total.cost_usd = (total.cost_usd or 0.0) + event.usage.cost_usd

        return Recording(
            name=self.session.name,
            status=status,
            created_at=datetime.now(UTC),
            fingerprint=fingerprint_for(events, config),
            events=events,
            trajectory=trajectory_mod.build(events),
            meta=RecordingMeta(
                usage=total,
                duration_ms=self.session.elapsed_ms(),
                redacted_fields=redacted_fields,
            ),
        )
