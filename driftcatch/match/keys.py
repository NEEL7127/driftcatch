"""Turning requests and events into comparable payloads.

Matching compares *payloads*, never raw bytes. A payload is the small, provider
agnostic projection of a request that actually determines behaviour.
"""

from __future__ import annotations

from typing import Any

from driftcatch.match.base import Kind, MatchQuery
from driftcatch.recording.fingerprint import canonical, hash_str
from driftcatch.recording.schema import Event, LLMEvent, LLMRequest, ToolEvent

__all__ = [
    "event_payload",
    "key_for",
    "llm_query",
    "tool_query",
]


def event_payload(event: Event) -> dict[str, Any]:
    """The comparable projection of a recorded event."""
    if isinstance(event, LLMEvent):
        return {
            "model": event.request.model,
            "messages": event.request.messages,
            "tools": event.request.tools,
            "params": event.request.params,
        }
    if isinstance(event, ToolEvent):
        return {"name": event.name, "args": event.args}
    raise TypeError(f"unsupported event type: {type(event)!r}")  # pragma: no cover


def llm_query(request: LLMRequest) -> MatchQuery:
    """Build a query from an outgoing model call."""
    return MatchQuery(
        kind="llm",
        payload={
            "model": request.model,
            "messages": request.messages,
            "tools": request.tools,
            "params": request.params,
        },
    )


def tool_query(name: str, args: dict[str, Any]) -> MatchQuery:
    """Build a query from an outgoing tool call."""
    return MatchQuery(kind="tool", payload={"name": name, "args": args}, name=name)


def key_for(payload: dict[str, Any]) -> str:
    """Stable hash of a payload, used as the match key."""
    return hash_str(canonical(payload))


def kind_of(event: Event) -> Kind:
    return "llm" if isinstance(event, LLMEvent) else "tool"
