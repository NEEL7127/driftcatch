"""Shared fixtures.

Unit tests must never touch the network. Everything here is built in memory or
in a tmp_path.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from driftcatch.recording import (
    LLMEvent,
    LLMRequest,
    LLMResponse,
    Outcome,
    Recording,
    Status,
    Step,
    ToolEvent,
    Trajectory,
    Usage,
    compute_fingerprint,
)

# A marker string that lives only inside an event body. Used to prove that event
# bodies are compressed on disk while metadata stays plaintext.
PROMPT_MARKER = "SYSTEM_PROMPT_BODY_MARKER"


@pytest.fixture
def sample_recording() -> Recording:
    """A two-event approved recording: one model call, one tool call."""
    created = datetime(2026, 3, 2, 10, 14, tzinfo=UTC)
    return Recording(
        name="refund_1182",
        status=Status.APPROVED,
        created_at=created,
        approved_at=datetime(2026, 3, 2, 10, 21, tzinfo=UTC),
        approved_by="neel",
        fingerprint=compute_fingerprint(
            model_id="gpt-5.2-2026-03",
            system_prompt=PROMPT_MARKER,
            tool_schemas=[{"name": "lookup_order"}],
            matcher_config={"matcher": "field"},
        ),
        events=[
            LLMEvent(
                index=0,
                request=LLMRequest(
                    model="gpt-5.2-2026-03",
                    messages=[{"role": "system", "content": PROMPT_MARKER}],
                    tools=[{"name": "lookup_order"}],
                    params={"temperature": 0},
                ),
                response=LLMResponse(
                    tool_calls=[{"name": "lookup_order", "args": {"id": 1182}}],
                    finish_reason="tool_calls",
                ),
                usage=Usage(input_tokens=910, output_tokens=40),
            ),
            ToolEvent(
                index=1,
                name="lookup_order",
                args={"id": 1182},
                result={"id": 1182, "total": 40},
            ),
        ],
        trajectory=Trajectory(
            steps=[
                Step(kind="llm", index=0, decided=["lookup_order"]),
                Step(
                    kind="tool",
                    index=1,
                    name="lookup_order",
                    outcome=Outcome.OK,
                    parent=0,
                ),
            ],
            tools_called=["lookup_order"],
            terminal=Outcome.OK,
        ),
    )
