"""Resolution pipeline: normalize, match, and explain failures precisely.

The failure path matters as much as the success path. When a request does not
match, DriftCatch reports the **first divergence only** and stops. One prompt
change must produce one clear error, never a cascade of fifty.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from driftcatch.config import Config
from driftcatch.errors import NoMatchError
from driftcatch.match.base import (
    Confidence,
    Match,
    MatchQuery,
    MatchState,
    NoMatch,
)
from driftcatch.match.keys import event_payload, key_for
from driftcatch.match.normalizers import apply_normalizers
from driftcatch.match.registry import get_matcher
from driftcatch.recording.fingerprint import canonical
from driftcatch.recording.schema import Event

__all__ = ["NormalizationContext", "resolve"]

_MAX_VALUE_CHARS = 160


class NormalizationContext:
    """Normalized views of the query and candidates.

    Candidate payloads and keys are cached on the :class:`MatchState`, so they
    survive across every resolution in a session rather than being recomputed
    from scratch for each request.
    """

    def __init__(
        self,
        query: MatchQuery,
        normalizers: list[str],
        state: MatchState | None = None,
    ) -> None:
        self.normalizers = list(normalizers)
        self._signature = ",".join(self.normalizers)
        self._state = state
        self._raw_query = query.payload
        self._norm_query = apply_normalizers(query.payload, self.normalizers)
        self.query_key = key_for(self._norm_query)
        self._local_payloads: dict[tuple[int, str], dict[str, Any]] = {}
        self._local_keys: dict[tuple[int, str], str] = {}
        self._raw: dict[int, dict[str, Any]] = {}

    @property
    def _payloads(self) -> dict[tuple[int, str], dict[str, Any]]:
        return self._state.payload_cache if self._state is not None else self._local_payloads

    @property
    def _keys(self) -> dict[tuple[int, str], str]:
        return self._state.key_cache if self._state is not None else self._local_keys

    def normalized_query(self) -> dict[str, Any]:
        return self._norm_query

    def raw_query(self) -> dict[str, Any]:
        return self._raw_query

    def payload_of(self, event: Event) -> dict[str, Any]:
        cache_key = (event.index, self._signature)
        if cache_key not in self._payloads:
            self._payloads[cache_key] = apply_normalizers(event_payload(event), self.normalizers)
        return self._payloads[cache_key]

    def raw_payload_of(self, event: Event) -> dict[str, Any]:
        if event.index not in self._raw:
            self._raw[event.index] = event_payload(event)
        return self._raw[event.index]

    def key_of(self, event: Event) -> str:
        cache_key = (event.index, self._signature)
        if cache_key not in self._keys:
            self._keys[cache_key] = key_for(self.payload_of(event))
        return self._keys[cache_key]

    def raw_equal(self, event: Event) -> bool:
        return canonical(self.raw_payload_of(event)) == canonical(self._raw_query)


def _truncate(value: Any) -> str:
    text = value if isinstance(value, str) else canonical(value)
    if len(text) > _MAX_VALUE_CHARS:
        return f"{text[:_MAX_VALUE_CHARS]}..."
    return text


def _first_difference(recorded: Any, current: Any, path: str = "") -> tuple[str, Any, Any] | None:
    """Depth-first search for the first place two payloads disagree."""
    if isinstance(recorded, dict) and isinstance(current, dict):
        for key in sorted(set(recorded) | set(current), key=str):
            child = f"{path}.{key}" if path else str(key)
            if key not in recorded:
                return child, "<absent>", current[key]
            if key not in current:
                return child, recorded[key], "<absent>"
            found = _first_difference(recorded[key], current[key], child)
            if found is not None:
                return found
        return None
    if isinstance(recorded, list) and isinstance(current, list):
        for index in range(max(len(recorded), len(current))):
            child = f"{path}[{index}]"
            if index >= len(recorded):
                return child, "<absent>", current[index]
            if index >= len(current):
                return child, recorded[index], "<absent>"
            found = _first_difference(recorded[index], current[index], child)
            if found is not None:
                return found
        return None
    if recorded != current:
        return path or "<root>", recorded, current
    return None


def _divergence_report(
    query: MatchQuery,
    candidates: Sequence[Event],
    state: MatchState,
    ctx: NormalizationContext,
) -> tuple[int | None, str]:
    """Build the human-readable explanation of why nothing matched."""
    available = state.available(candidates, query.kind)
    if not available:
        return None, (
            f"  the recording holds no further {query.kind} events, but the agent "
            f"made another {query.kind} call.\n"
            "  The agent is doing more work than it did when this was recorded."
        )

    nearest = available[0]
    difference = _first_difference(ctx.payload_of(nearest), ctx.normalized_query())

    lines = [f"  diverged at event #{nearest.index} ({query.kind})"]
    if difference is None:
        lines.append("  payloads agree after normalization but no strategy accepted them;")
        lines.append("  the matcher may be too strict for this recording.")
        return nearest.index, "\n".join(lines)

    where, recorded_value, current_value = difference
    lines.append(f"    field:    {where}")
    lines.append(f"    recorded: {_truncate(recorded_value)}")
    lines.append(f"    current:  {_truncate(current_value)}")
    lines.append("")
    lines.append("  The agent sent a request it did not send when this was recorded.")
    lines.append("  If the change is intended, re-record and re-approve:")
    lines.append("    drift record            # the previous version is archived")
    lines.append("    drift approve <name> --by <who>")
    return nearest.index, "\n".join(lines)


def resolve(
    query: MatchQuery,
    candidates: Sequence[Event],
    state: MatchState,
    config: Config,
) -> Match:
    """Pick the recorded event for ``query`` or raise :class:`NoMatchError`.

    Never falls through to a live call and never serves a nearby response: a
    false green is worse than a false red.
    """
    ctx = NormalizationContext(query, config.normalizers, state)
    result = get_matcher(config.matcher).match(query, candidates, state, ctx)

    if isinstance(result, NoMatch):
        index, diff = _divergence_report(query, candidates, state, ctx)
        raise NoMatchError(
            result.reason,
            diverged_at=result.diverged_at if result.diverged_at is not None else index,
            diff=diff,
        )

    if config.strict and result.confidence is Confidence.FUZZY:
        raise NoMatchError(
            f"only a fuzzy match was available (score {result.score:.2f}) and strict "
            "mode is on. Tighten the matcher or set strict=False deliberately.",
            diverged_at=result.event.index,
        )

    state.take(result.event)
    state.note(query, result, ctx.query_key)
    return result
