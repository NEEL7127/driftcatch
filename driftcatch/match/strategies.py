"""Built-in match strategies.

Each strategy answers "which recorded event serves this request?" differently.
They all respect one rule: a served event is *consumed*, so the second identical
request naturally receives the second recorded response. That single rule covers
retries and ``pending -> done`` polling without special cases.
"""

from __future__ import annotations

from collections.abc import Sequence

from driftcatch.errors import AmbiguousMatchError
from driftcatch.match.base import (
    Confidence,
    Match,
    MatchContext,
    Matcher,
    MatchQuery,
    MatchResult,
    MatchState,
    NoMatch,
)
from driftcatch.match.registry import get_matcher, matcher, register_matcher
from driftcatch.recording.schema import Event, ToolEvent

__all__ = ["ChainMatcher", "FieldMatcher", "PositionalMatcher", "ToolCallMatcher", "chain"]


def _confidence(ctx: MatchContext, event: Event) -> Confidence:
    """EXACT when the raw payloads agreed; NORMALIZED when normalizers were needed."""
    return Confidence.EXACT if ctx.raw_equal(event) else Confidence.NORMALIZED


@matcher("field")
class FieldMatcher:
    """Compare the normalized payload: model, messages, tools (or name, args).

    The default. Precise, and the only cost is that you must declare which
    fields are noise via normalizers.
    """

    name = "field"

    def match(
        self,
        query: MatchQuery,
        candidates: Sequence[Event],
        state: MatchState,
        ctx: MatchContext,
    ) -> MatchResult:
        hits = [
            e for e in state.available(candidates, query.kind) if ctx.key_of(e) == ctx.query_key
        ]
        if not hits:
            return NoMatch(reason="no unconsumed recorded event has this request payload")
        return Match(event=hits[0], confidence=_confidence(ctx, hits[0]), matcher=self.name)


@matcher("positional")
class PositionalMatcher:
    """Serve the next unconsumed event of the same kind, ignoring content.

    Robust to noisy payloads; breaks when the agent adds or removes a step.
    """

    name = "positional"

    def match(
        self,
        query: MatchQuery,
        candidates: Sequence[Event],
        state: MatchState,
        ctx: MatchContext,
    ) -> MatchResult:
        available = state.available(candidates, query.kind)
        if not available:
            return NoMatch(reason=f"the recording has no remaining {query.kind} events to serve")
        event = available[0]
        return Match(event=event, confidence=_confidence(ctx, event), matcher=self.name)


@matcher("tool_call")
class ToolCallMatcher:
    """Match tool calls on name first, then normalized arguments.

    Gives a clearer failure than ``field`` when a tool is called that the
    recording never saw, which is the common agent regression.
    """

    name = "tool_call"

    def match(
        self,
        query: MatchQuery,
        candidates: Sequence[Event],
        state: MatchState,
        ctx: MatchContext,
    ) -> MatchResult:
        if query.kind != "tool":
            return get_matcher("field").match(query, candidates, state, ctx)

        available = [e for e in state.available(candidates, "tool") if isinstance(e, ToolEvent)]
        same_name = [e for e in available if e.name == query.name]
        if not same_name:
            return NoMatch(reason=f"tool {query.name!r} was never called in this recording")

        exact = [e for e in same_name if ctx.key_of(e) == ctx.query_key]
        if not exact:
            return NoMatch(reason=f"tool {query.name!r} was called with different arguments")
        return Match(event=exact[0], confidence=_confidence(ctx, exact[0]), matcher=self.name)


class ChainMatcher:
    """Try several strategies in order; the first match wins."""

    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names
        self.name = "chain(" + ",".join(names) + ")"

    def match(
        self,
        query: MatchQuery,
        candidates: Sequence[Event],
        state: MatchState,
        ctx: MatchContext,
    ) -> MatchResult:
        last: NoMatch | None = None
        for name in self.names:
            result = get_matcher(name).match(query, candidates, state, ctx)
            if isinstance(result, Match):
                result.matcher = self.name
                return result
            last = result
        return last or NoMatch(reason="no strategy in the chain produced a match")


def chain(*names: str) -> Matcher:
    """Build and register a composite matcher, e.g. ``chain("positional", "field")``."""
    if not names:
        raise AmbiguousMatchError("chain() needs at least one strategy name")
    for name in names:
        get_matcher(name)  # fail fast on typos
    instance = ChainMatcher(tuple(names))
    register_matcher(instance.name, instance)
    return instance
