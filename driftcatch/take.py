"""``drift.take`` - the one entry point users touch.

Opening a take decides, from one call, whether this run records or replays, binds
a session for the duration, and yields the object every assertion reads.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from driftcatch import trajectory as trajectory_mod
from driftcatch._name import ENV_PREFIX
from driftcatch.config import Config, get_config
from driftcatch.errors import DriftCatchError, LatencyNotMeasured, RecordingNotFound
from driftcatch.intercept.http import intercept_http
from driftcatch.invariants import (
    Violation,
    check,
    invariants_path,
    load_invariants,
    render_violations,
)
from driftcatch.record import Recorder
from driftcatch.recording.io import (
    archive_previous,
    load_recording,
    recording_path,
    save_recording,
)
from driftcatch.recording.schema import (
    Event,
    LLMEvent,
    Recording,
    Status,
    ToolEvent,
    Trajectory,
    Usage,
)
from driftcatch.review import require_approved
from driftcatch.session import Mode, Session, use_session

__all__ = [
    "LatencyAssertion",
    "OutputAssertion",
    "Take",
    "TakeSpec",
    "take",
]


# --------------------------------------------------------------- assertions


def _clip(text: str, limit: int = 300) -> str:
    """Trim a model reply for a failure message without hiding the mismatch."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit]}... ({len(collapsed)} chars)"


class _Assertion:
    def __init__(self, take: Take) -> None:
        self._take = take

    def _fail(self, message: str) -> None:
        raise AssertionError(f"{message}\n\n{self._take.describe()}")


class ToolAssertion(_Assertion):
    """Assertions about which tools the agent used."""

    def called(self, name: str) -> Self:
        if name not in self._take.trajectory.tools_called:
            self._fail(f'expected tool "{name}" to be called, but it never was')
        return self

    def never(self, name: str) -> Self:
        if name in self._take.trajectory.tools_called:
            self._fail(f'expected tool "{name}" never to be called, but it was')
        return self

    def called_in_order(self, *names: str) -> Self:
        actual = self._take.trajectory.tools_called
        position = 0
        for wanted in names:
            while position < len(actual) and actual[position] != wanted:
                position += 1
            if position == len(actual):
                self._fail(f"expected tools in order {list(names)}, got {actual}")
            position += 1
        return self

    def call_count(self, name: str, count: int) -> Self:
        actual = self._take.trajectory.tools_called.count(name)
        if actual != count:
            self._fail(f'expected "{name}" to be called {count}x, it was called {actual}x')
        return self


class TrajectoryAssertion(_Assertion):
    """Assertions about the agent's behaviour as a whole."""

    def matches_golden(
        self, *, tolerance: str | None = None, pass_k: tuple[int, int] | None = None
    ) -> Self:
        take = self._take
        if pass_k is not None:
            raise DriftCatchError(
                "pass_k applies to a live behaviour check across several runs. "
                "Use @drift.live_check(name, runs=n) rather than a single take."
            )
        golden = take.golden
        if golden is None:
            self._fail(
                f"no approved recording to compare against for {take.name!r}; "
                f"record and approve one first"
            )
            return self
        level = tolerance or take.config.default_tolerance
        result = trajectory_mod.diff(golden.trajectory, take.trajectory, level)
        if not result.matched:
            self._fail(f"behaviour differs from golden ({level}):\n{trajectory_mod.render(result)}")
        return self


class CostAssertion(_Assertion):
    """Assertions about token and money spend - cost regressions are regressions."""

    def under(self, *, tokens: int | None = None, usd: float | None = None) -> Self:
        usage = self._take.usage
        if tokens is not None and usage.total_tokens > tokens:
            self._fail(f"expected under {tokens} tokens, used {usage.total_tokens}")
        if usd is not None and (usage.cost_usd or 0.0) > usd:
            self._fail(f"expected under ${usd}, spent ${usage.cost_usd}")
        return self


class OutputAssertion(_Assertion):
    """Assertions about what the model actually said.

    Deliberately mechanical: substring, regex, JSON shape and arbitrary
    predicates. There is no scoring and no LLM judge here. Whether an answer is
    *good* is evaluation, a different discipline served well by other tools;
    this checks whether an answer is *shaped the way your code requires*, which
    is what breaks the pipeline downstream when a model changes.
    """

    def _text(self) -> str:
        return self._take.output

    def not_empty(self) -> Self:
        if not self._text().strip():
            self._fail("expected some output, got an empty response")
        return self

    def contains(self, needle: str, *, case_sensitive: bool = True) -> Self:
        text = self._text()
        haystack = text if case_sensitive else text.lower()
        probe = needle if case_sensitive else needle.lower()
        if probe not in haystack:
            self._fail(f"expected output to contain {needle!r}\n  output: {_clip(text)}")
        return self

    def excludes(self, needle: str, *, case_sensitive: bool = True) -> Self:
        """The negative case, which is usually the safety-relevant one.

        Asserting that a reply does *not* contain an internal id, a stack trace
        or a refusal phrase is how a prompt change that starts leaking them
        gets caught.
        """
        text = self._text()
        haystack = text if case_sensitive else text.lower()
        probe = needle if case_sensitive else needle.lower()
        if probe in haystack:
            self._fail(f"expected output not to contain {needle!r}\n  output: {_clip(text)}")
        return self

    def matches(self, pattern: str) -> Self:
        if not re.search(pattern, self._text(), re.DOTALL):
            self._fail(f"output did not match /{pattern}/\n  output: {_clip(self._text())}")
        return self

    def equals(self, expected: str, *, strip: bool = True) -> Self:
        actual = self._text()
        if strip:
            actual, expected = actual.strip(), expected.strip()
        if actual != expected:
            self._fail(
                f"output differs\n  expected: {_clip(expected)}\n  actual:   {_clip(actual)}"
            )
        return self

    def is_json(self) -> Self:
        try:
            json.loads(self._text())
        except (json.JSONDecodeError, TypeError) as exc:
            self._fail(f"output is not valid JSON ({exc})\n  output: {_clip(self._text())}")
        return self

    def json_has(self, *keys: str) -> Self:
        """Parse the output as a JSON object and require these top-level keys.

        Enough to catch a model that stopped emitting a required field, without
        dragging in a schema-validation dependency for a check this small.
        """
        try:
            parsed = json.loads(self._text())
        except (json.JSONDecodeError, TypeError) as exc:
            self._fail(f"output is not valid JSON ({exc})\n  output: {_clip(self._text())}")
            return self
        if not isinstance(parsed, dict):
            self._fail(f"expected a JSON object, got {type(parsed).__name__}")
            return self
        missing = [key for key in keys if key not in parsed]
        if missing:
            self._fail(f"output JSON is missing {missing}\n  present: {sorted(parsed)}")
        return self

    def satisfies(self, predicate: Callable[[str], bool], *, description: str = "") -> Self:
        """Apply any plain-Python check to the output.

        The escape hatch for a rule this API has no verb for. The predicate must
        be deterministic - calling a model from inside it would make the test
        non-reproducible, which is the one property the rest of the framework
        exists to provide.
        """
        label = description or getattr(predicate, "__name__", "predicate")
        if not predicate(self._text()):
            self._fail(f"output failed {label}\n  output: {_clip(self._text())}")
        return self


class LatencyAssertion(_Assertion):
    """Assertions about how long real calls took.

    Only meaningful on a run that made real calls - record mode or ``--live``.
    During replay the bytes come off disk, so the recorded durations describe
    the original call and comparing them would pass unconditionally. In that
    case every check here is skipped and ``LatencyNotMeasured`` is warned, so
    the test still runs offline instead of quietly reporting a performance
    guarantee it never verified.
    """

    def _applicable(self) -> bool:
        if self._take.mode == "replay":
            warnings.warn(
                f"latency assertions skipped for take {self._take.name!r}: replay "
                f"serves recorded bytes, so there is no real call to time. Record the "
                f"take or use drift check --live to check latency.",
                LatencyNotMeasured,
                stacklevel=3,
            )
            return False
        return True

    def under(self, *, total_ms: int | None = None, per_call_ms: int | None = None) -> Self:
        if not self._applicable():
            return self
        measured = self._take.latencies
        if total_ms is not None:
            total = sum(ms for _, ms in measured)
            if total > total_ms:
                self._fail(f"expected the run under {total_ms}ms, took {total}ms")
        if per_call_ms is not None:
            slow = [(label, ms) for label, ms in measured if ms > per_call_ms]
            if slow:
                detail = ", ".join(f"{label}={ms}ms" for label, ms in slow)
                self._fail(f"expected every call under {per_call_ms}ms; over: {detail}")
        return self

    def within_factor_of_golden(self, factor: float = 2.0) -> Self:
        """Fail when this run took more than ``factor`` times the golden's time.

        A relative budget rather than an absolute one, because a fixed
        millisecond threshold is a flaky test on shared CI hardware while a
        ratio against the same machine's recorded baseline is far steadier.
        """
        if not self._applicable():
            return self
        golden = self._take.golden
        if golden is None or not golden.meta.duration_ms:
            return self
        current = sum(ms for _, ms in self._take.latencies)
        budget = golden.meta.duration_ms * factor
        if current > budget:
            self._fail(
                f"run took {current}ms, more than {factor}x the golden's "
                f"{golden.meta.duration_ms}ms (budget {budget:.0f}ms)"
            )
        return self


class InvariantAssertion(_Assertion):
    """Assertions against the rules mined from the approved corpus.

    These are rules nobody wrote by hand - they were learned from what the
    agent has always done - so this guards behaviour in scenarios that have no
    hand-written assertion at all.
    """

    def hold(self, *, approved_only: bool = True) -> Self:
        take = self._take
        learned = load_invariants(invariants_path(take.config.recordings_dir))
        if not learned:
            return self
        violations = check(take.trajectory, take.events, learned, approved_only=approved_only)
        if violations:
            self._fail("learned invariants broken:\n" + render_violations(violations))
        return self


# -------------------------------------------------------------------- take


class Take:
    """The result of one recorded or replayed run."""

    def __init__(self, session: Session, golden: Recording | None) -> None:
        self._session = session
        self.golden = golden
        self.trajectory: Trajectory = Trajectory()
        self.recording: Recording | None = None

    # -- identity ---------------------------------------------------------

    @property
    def name(self) -> str:
        return self._session.name

    @property
    def mode(self) -> Mode:
        return self._session.mode

    @property
    def config(self) -> Config:
        return self._session.config

    @property
    def events(self) -> list[Event]:
        return self._session.events

    @property
    def stale(self) -> bool:
        return self._session.stale

    @property
    def changed_inputs(self) -> list[str]:
        return list(self._session.changed_inputs)

    @property
    def usage(self) -> Usage:
        total = Usage()
        for event in self.events:
            if isinstance(event, LLMEvent):
                total.input_tokens += event.usage.input_tokens
                total.output_tokens += event.usage.output_tokens
                if event.usage.cost_usd:
                    total.cost_usd = (total.cost_usd or 0.0) + event.usage.cost_usd
        return total

    @property
    def tools_called(self) -> list[str]:
        return list(self.trajectory.tools_called)

    @property
    def output(self) -> str:
        """The final model reply, as text.

        ``LLMResponse.text`` falls back to the reasoning field, so a reasoning
        model that puts its whole answer in ``reasoning_content`` still reports
        an answer here instead of an empty string.
        """
        for event in reversed(self.events):
            if isinstance(event, LLMEvent) and event.response is not None:
                return event.response.text
        return ""

    @property
    def outputs(self) -> list[str]:
        """Every model reply in order, for multi-turn runs."""
        return [
            event.response.text
            for event in self.events
            if isinstance(event, LLMEvent) and event.response is not None
        ]

    @property
    def latencies(self) -> list[tuple[str, int]]:
        """``(label, milliseconds)`` for every step that was actually timed.

        Steps with no measurement are left out rather than reported as zero, so
        a replayed run yields an empty list instead of a convincing-looking set
        of instantaneous calls.
        """
        measured: list[tuple[str, int]] = []
        for position, event in enumerate(self.events):
            # A replayed event is the golden's own object and still carries the
            # duration measured when it was recorded. Reporting that as this
            # run's latency would describe a call this run never made.
            if event.duration_ms is None or self._session.was_replayed(position):
                continue
            label = event.name if isinstance(event, ToolEvent) else f"llm[{event.index}]"
            measured.append((label, event.duration_ms))
        return measured

    @property
    def latency_ms(self) -> int:
        """Total measured call time. Zero when nothing was timed."""
        return sum(ms for _, ms in self.latencies)

    # -- assertions -------------------------------------------------------

    def assert_tools(self) -> ToolAssertion:
        return ToolAssertion(self)

    def assert_trajectory(self) -> TrajectoryAssertion:
        return TrajectoryAssertion(self)

    def assert_cost(self) -> CostAssertion:
        return CostAssertion(self)

    def assert_invariants(self) -> InvariantAssertion:
        return InvariantAssertion(self)

    def assert_output(self) -> OutputAssertion:
        return OutputAssertion(self)

    def assert_latency(self) -> LatencyAssertion:
        return LatencyAssertion(self)

    def invariant_violations(self, *, approved_only: bool = True) -> list[Violation]:
        """Learned rules this run broke, without failing the test."""
        learned = load_invariants(invariants_path(self.config.recordings_dir))
        if not learned:
            return []
        return check(self.trajectory, self.events, learned, approved_only=approved_only)

    # -- reporting --------------------------------------------------------

    def describe(self) -> str:
        """Context appended to every assertion failure."""
        lines = [
            f"  take:       {self.name}  (mode: {self.mode})",
            f"  trajectory: {' -> '.join(self.tools_called) or '(no tools)'}",
            f"  events:     {len(self.events)}  tokens: {self.usage.total_tokens}",
        ]
        if self.stale:
            lines.append(f"  drift:      STALE - changed: {', '.join(self.changed_inputs)}")
        return "\n".join(lines)

    def _finish(self) -> None:
        self.trajectory = trajectory_mod.build(self.events)


# --------------------------------------------------------------- take spec


class TakeSpec:
    """A configured take, usable as a context manager.

    Returned rather than entering immediately so that ``what_if`` can refine it:
    ``with drift.take("x").what_if(tool="t", returns={}) as take:``
    """

    def __init__(
        self,
        name: str,
        *,
        mode: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self._mode = mode
        self._overrides = overrides or {}
        self._mutations: list[dict[str, Any]] = []
        self._cm: Any = None
        self._session_cm: Any = None
        self._take: Take | None = None

    # -- what-if ----------------------------------------------------------

    def what_if(
        self,
        *,
        tool: str | None = None,
        returns: Any = None,
        raises: str | None = None,
        index: int | None = None,
    ) -> TakeSpec:
        """Replay with one recorded event changed.

        Lets an untested branch be exercised using a real recording as the
        starting point, instead of hand-written mocks. The file on disk is never
        modified.
        """
        if tool is None and index is None:
            raise DriftCatchError("what_if needs either tool= or index=")
        self._mutations.append({"tool": tool, "index": index, "returns": returns, "raises": raises})
        return self

    # -- lifecycle --------------------------------------------------------

    def _resolve_config(self) -> Config:
        return get_config().merged(**self._overrides)

    def _resolve_mode(self, path: Path) -> Mode:
        explicit = self._mode or os.environ.get(f"{ENV_PREFIX}_MODE")
        if explicit and explicit != "auto":
            if explicit not in {"record", "replay", "live"}:
                raise DriftCatchError(f"unknown mode {explicit!r}")
            return explicit  # type: ignore[return-value]
        return "replay" if path.exists() else "record"

    def _apply_mutations(self, session: Session, golden: Recording | None) -> None:
        if not self._mutations or golden is None:
            return
        from driftcatch.whatif import build_mutations

        session.mutations = build_mutations(golden, self._mutations)

    def __enter__(self) -> Take:
        config = self._resolve_config()
        path = recording_path(self.name, config.recordings_dir)
        mode = self._resolve_mode(path)

        golden: Recording | None = None
        if path.exists():
            golden = load_recording(path)
            # Only meaningful when the recording is about to be trusted as
            # golden. Re-recording over an unapproved recording is ordinary, and
            # warning "replaying ... not approved" there is simply untrue.
            if mode == "replay":
                require_approved(golden, config)
        elif mode == "replay":
            raise RecordingNotFound(
                f"no recording named {self.name!r} in {config.recordings_dir}. "
                f"Record one first by running without --drift-mode=replay."
            )

        session = Session(name=self.name, mode=mode, config=config, golden=golden)
        self._apply_mutations(session, golden)

        self._session_cm = use_session(session)
        self._session_cm.__enter__()
        self._cm = intercept_http()
        self._cm.__enter__()

        self._take = Take(session, golden)
        return self._take

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._cm is not None:
                self._cm.__exit__(exc_type, exc, tb)
        finally:
            take = self._take
            session = take._session if take is not None else None
            try:
                if take is not None and exc_type is None:
                    take._finish()
                    if session is not None and session.mode == "record":
                        self._persist(take, session)
            finally:
                if self._session_cm is not None:
                    self._session_cm.__exit__(exc_type, exc, tb)

    def _persist(self, take: Take, session: Session) -> None:
        """Write a freshly recorded run as PENDING, awaiting human approval."""
        recording = Recorder(session).finalize(status=Status.PENDING)
        directory = session.config.recordings_dir
        path = recording_path(self.name, directory)

        # Never destroy a golden. The replaced version is what you want to diff
        # the new behaviour against, and a re-record done by mistake has to be
        # recoverable.
        archived = archive_previous(self.name, directory)

        save_recording(recording, path)
        take.recording = recording

        if archived is not None:
            warnings.warn(
                f"re-recorded {self.name!r}; it is PENDING again and needs approval. "
                f"The replaced version is kept, so you can compare "
                f"(drift bisect {self.name} --previous) or undo "
                f"(drift restore {self.name}).",
                UserWarning,
                stacklevel=2,
            )

        size = path.stat().st_size
        if size > session.config.max_recording_bytes:
            warnings.warn(
                f"recording {self.name!r} is {size / 1_000_000:.1f} MB, above the "
                f"configured budget; consider `drift prune` or a smaller scenario.",
                UserWarning,
                stacklevel=2,
            )
        if recording.meta.redacted_fields:
            count = len(recording.meta.redacted_fields)
            warnings.warn(
                f"redacted {count} sensitive field(s) from recording {self.name!r}",
                UserWarning,
                stacklevel=2,
            )


def take(name: str, *, mode: str | None = None, **overrides: Any) -> TakeSpec:
    """Record or replay a run.

    Records when no recording exists, replays when one does. Pass ``mode`` to
    force a choice, or set ``DRIFTCATCH_MODE``.
    """
    return TakeSpec(name, mode=mode, overrides=overrides)


def _utcnow() -> datetime:  # pragma: no cover - trivial
    return datetime.now(UTC)
