"""Core matcher types.

The matcher answers one question: *for this request, which recorded event do I
serve?* Everything else in DriftCatch is plumbing around that answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol

from driftcatch.recording.schema import Event

__all__ = [
    "Confidence",
    "Kind",
    "Match",
    "MatchContext",
    "MatchQuery",
    "MatchRecord",
    "MatchResult",
    "MatchState",
    "Matcher",
    "NoMatch",
]

Kind = Literal["llm", "tool"]


class Confidence(StrEnum):
    """How sure the matcher is, reported on every match.

    ``EXACT``      the raw payloads were identical.
    ``NORMALIZED`` they matched only after normalizers stripped noise.
    ``FUZZY``      a similarity threshold was used. Fails under ``strict``.
    """

    EXACT = "exact"
    NORMALIZED = "normalized"
    FUZZY = "fuzzy"


@dataclass(frozen=True)
class MatchQuery:
    """An outgoing request, in the shape the matcher compares."""

    kind: Kind
    payload: dict[str, Any]
    name: str | None = None


@dataclass
class Match:
    """A recorded event chosen for the request."""

    event: Event
    confidence: Confidence
    score: float = 1.0
    matcher: str = ""


@dataclass
class NoMatch:
    """No recorded event fits.

    This is the framework's core signal, not an inconvenience: the agent sent a
    request it did not send when the recording was made.
    """

    reason: str
    diverged_at: int | None = None
    diff: str | None = None


MatchResult = Match | NoMatch


@dataclass
class MatchRecord:
    """One line of the match audit trail, surfaced by ``drift audit``."""

    order: int
    kind: Kind
    event_index: int
    confidence: Confidence
    matcher: str
    key: str


@dataclass
class MatchState:
    """Mutable per-session matching state.

    ``consumed`` is the mechanism that makes repeated identical requests work:
    the first call takes event A, so the second call naturally takes event B.
    That single rule covers retries and ``pending -> done`` polling without a
    separate counter.
    """

    consumed: set[int] = field(default_factory=set)
    counters: dict[str, int] = field(default_factory=dict)
    trail: list[MatchRecord] = field(default_factory=list)
    #: Normalized payloads and match keys, cached for the whole session and
    #: keyed by (event index, normalizer signature). Without this, every request
    #: re-normalizes every candidate, which is O(n^2) normalizations over a run
    #: and becomes the dominant cost on long recordings.
    payload_cache: dict[tuple[int, str], dict[str, Any]] = field(default_factory=dict)
    key_cache: dict[tuple[int, str], str] = field(default_factory=dict)

    def available(self, candidates: Sequence[Event], kind: Kind) -> list[Event]:
        """Candidates of ``kind`` that have not been served yet, in order."""
        wanted = "llm" if kind == "llm" else "tool"
        return [e for e in candidates if e.type == wanted and e.index not in self.consumed]

    def take(self, event: Event) -> None:
        self.consumed.add(event.index)

    def note(self, query: MatchQuery, match: Match, key: str) -> None:
        self.counters[key] = self.counters.get(key, 0) + 1
        self.trail.append(
            MatchRecord(
                order=len(self.trail),
                kind=query.kind,
                event_index=match.event.index,
                confidence=match.confidence,
                matcher=match.matcher,
                key=key,
            )
        )


class MatchContext(Protocol):
    """Normalized views of the query and candidates, cached per resolution.

    Normalization happens once, in ``resolve``, so strategies never re-run the
    pipeline and every strategy compares like for like.
    """

    normalizers: list[str]
    query_key: str

    def normalized_query(self) -> dict[str, Any]: ...
    def key_of(self, event: Event) -> str: ...
    def payload_of(self, event: Event) -> dict[str, Any]: ...
    def raw_equal(self, event: Event) -> bool: ...


class Matcher(Protocol):
    """Strategy interface. Register implementations by name."""

    name: str

    def match(
        self,
        query: MatchQuery,
        candidates: Sequence[Event],
        state: MatchState,
        ctx: MatchContext,
    ) -> MatchResult: ...
