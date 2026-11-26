"""Per-test session state.

All interception state lives here, bound to a :class:`~contextvars.ContextVar`.
Nothing is stored at module level, because ``pytest-xdist`` and asyncio both run
tests concurrently and a shared global would interleave events between tests -
in a tool whose entire value proposition is determinism.

Interceptors call :func:`current_session`; when it returns ``None`` they pass
through untouched, so importing DriftCatch costs production nothing.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Literal

from driftcatch.config import Config
from driftcatch.match.base import MatchState
from driftcatch.recording.schema import Event, Recording

__all__ = ["Mode", "Session", "current_session", "require_session", "use_session"]

Mode = Literal["record", "replay", "live"]


@dataclass
class Session:
    """Everything one recorded or replayed run needs."""

    name: str
    mode: Mode
    config: Config
    #: The approved recording being replayed. ``None`` while recording fresh.
    golden: Recording | None = None
    #: Events captured during this run.
    events: list[Event] = field(default_factory=list)
    match_state: MatchState = field(default_factory=MatchState)
    started_at: float = field(default_factory=time.monotonic)
    stale: bool = False
    changed_inputs: list[str] = field(default_factory=list)
    #: Event index overrides applied by ``what_if``.
    mutations: dict[int, Event] = field(default_factory=dict)

    #: Positions in ``events`` that were served from the golden rather than
    #: executed. A what-if run can mix the two, so this is per-event rather
    #: than a property of the session's mode.
    replayed_positions: set[int] = field(default_factory=set)

    @property
    def candidates(self) -> list[Event]:
        """Events available to match against during replay."""
        if self.golden is None:
            return []
        if not self.mutations:
            return list(self.golden.events)
        return [self.mutations.get(e.index, e) for e in self.golden.events]

    @property
    def is_replaying(self) -> bool:
        return self.mode == "replay"

    @property
    def is_what_if(self) -> bool:
        """True when a mutation is in effect.

        Past the mutation point the agent is on a branch that was never
        recorded, so unmatched calls there are expected rather than drift.
        """
        return bool(self.mutations)

    def next_index(self) -> int:
        return len(self.events)

    def add(self, event: Event) -> Event:
        self.events.append(event)
        return event

    def add_replayed(self, event: Event) -> Event:
        """Add an event that was *served* from the golden, not executed.

        The replayer hands back the golden's own event object, so the event
        still carries the duration measured when it was first recorded. Noting
        its position here lets a caller tell "this run took 51ms" apart from
        "a previous run took 51ms and this one read it off disk" - a
        distinction latency assertions depend on to avoid reporting a
        performance guarantee nobody verified.
        """
        self.replayed_positions.add(len(self.events))
        return self.add(event)

    def was_replayed(self, position: int) -> bool:
        """Whether the event at this position in ``events`` came off disk."""
        return position in self.replayed_positions

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)


_current: ContextVar[Session | None] = ContextVar("driftcatch_session", default=None)


def current_session() -> Session | None:
    """The session for the current test, task or thread, if any."""
    return _current.get()


def require_session() -> Session:
    """Like :func:`current_session` but raises when there is none."""
    session = _current.get()
    if session is None:  # pragma: no cover - guarded by callers
        raise RuntimeError("no active DriftCatch session; use `with drift.take(...)`")
    return session


@contextmanager
def use_session(session: Session) -> Iterator[Session]:
    """Bind ``session`` for the duration of the block."""
    token: Token[Session | None] = _current.set(session)
    try:
        yield session
    finally:
        _current.reset(token)
