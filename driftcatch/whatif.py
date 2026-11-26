"""What-if mutation: replaying a real recording with one event changed.

Coverage reports which branches were never tested; this is how you test them
without re-recording or writing mocks. The mutation lives in memory only - the
recording on disk is never modified.
"""

from __future__ import annotations

from typing import Any

from driftcatch.errors import DriftCatchError
from driftcatch.recording.schema import ErrorInfo, Event, Recording, ToolEvent

__all__ = ["build_mutations"]


def _find_target(recording: Recording, tool: str | None, index: int | None) -> Event:
    if index is not None:
        for event in recording.events:
            if event.index == index:
                return event
        raise DriftCatchError(f"recording {recording.name!r} has no event at index {index}")

    for event in recording.events:
        if isinstance(event, ToolEvent) and event.name == tool:
            return event

    available = sorted({e.name for e in recording.events if isinstance(e, ToolEvent)})
    raise DriftCatchError(
        f"recording {recording.name!r} never calls tool {tool!r}. "
        f"Tools in this recording: {', '.join(available) or 'none'}"
    )


def build_mutations(recording: Recording, specs: list[dict[str, Any]]) -> dict[int, Event]:
    """Turn ``what_if`` specifications into replacement events keyed by index."""
    mutations: dict[int, Event] = {}

    for spec in specs:
        target = _find_target(recording, spec.get("tool"), spec.get("index"))
        replacement = target.model_copy(deep=True)

        raises = spec.get("raises")
        if raises:
            replacement.error = ErrorInfo(
                type=str(raises), message=f"injected by what_if for {recording.name}"
            )
            if isinstance(replacement, ToolEvent):
                replacement.result = None
            else:
                replacement.response = None
        else:
            replacement.error = None
            if isinstance(replacement, ToolEvent):
                replacement.result = spec.get("returns")
            else:
                raise DriftCatchError(
                    "what_if(returns=...) applies to tool events; "
                    "use raises= to inject a model-call failure"
                )

        mutations[target.index] = replacement

    return mutations
