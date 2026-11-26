"""Exception hierarchy.

Every user-facing failure raises a ``DriftCatchError`` subclass carrying an
actionable message. Never raise a bare ``Exception`` from library code.
"""

from __future__ import annotations


class DriftCatchError(Exception):
    """Base class for ordinary errors raised by DriftCatch."""


class DriftSignal(BaseException):
    """Base for signals that must never be swallowed.

    Derived from ``BaseException``, not ``Exception``, on purpose. These are
    *test-framework control signals*, not application errors, and two layers of
    well-meaning code would otherwise eat them:

    * **Provider SDKs.** The Anthropic SDK catches ``Exception`` around its
      request path and re-raises it as ``APIConnectionError``, which would
      replace a precise "the system prompt changed at messages[0].content" with
      a useless "Connection error".
    * **The agent itself.** An agent with ``try: ... except Exception: fallback()``
      would silently absorb a replay mismatch, the test would pass, and a real
      behaviour regression would ship. That is a false green, which is worse than
      a false red.

    Catching these explicitly by name still works, which is all DriftCatch's own
    internals need.
    """


class RecordingNotFound(DriftCatchError):
    """No recording exists for the requested name."""


class RecordingVersionError(DriftCatchError):
    """The recording file was written by an incompatible version."""


class RecordingNotApproved(DriftCatchError):
    """A recording was used as golden before a human approved it."""


class InvalidTransition(DriftCatchError):
    """An illegal recording status transition was attempted."""


class NoMatchError(DriftSignal):
    """No recorded event matches the incoming request.

    This is the framework's core signal: the agent sent a request it did not
    send when the recording was made. It is never swallowed and never falls
    through to a live call.
    """

    def __init__(
        self,
        reason: str,
        *,
        diverged_at: int | None = None,
        diff: str | None = None,
    ) -> None:
        self.reason = reason
        self.diverged_at = diverged_at
        self.diff = diff
        location = f" at event #{diverged_at}" if diverged_at is not None else ""
        message = f"No matching recorded event{location}: {reason}"
        if diff:
            message = f"{message}\n{diff}"
        super().__init__(message)


class AmbiguousMatchError(DriftCatchError):
    """Several recorded events match equally well; the matcher must be tightened."""


class RedactionError(DriftCatchError):
    """A secret survived redaction, so the recording was not written."""


class RecordedError(DriftCatchError):
    """Re-raised stand-in for a failure captured during recording.

    Used when the original exception type cannot be reconstructed. Recorded
    failures must still surface on replay, otherwise retry and error-handling
    paths would never be exercised.
    """


class ConfigError(DriftCatchError):
    """The supplied configuration is invalid."""


class PrunedRecording(DriftCatchError):
    """A pruned recording was replayed.

    ``drift prune`` discards response bodies to save space, keeping only the
    trajectory. Such a recording can still be used for behaviour assertions and
    coverage, but it can no longer reproduce a reply - and serving an empty one
    would be a silent false green.
    """


class RecordingWriteError(DriftCatchError):
    """A recording could not be written to disk."""


class InvalidRecordingName(DriftCatchError):
    """A recording name is not a safe, simple identifier.

    Names become filenames, and they arrive from places that are not always
    trusted: CLI arguments, config, and - most importantly - an MCP tool call
    made by a coding agent, which may itself be acting on content it read
    somewhere. Without this check, ``../../../secrets`` reads and writes
    outside the recordings directory.
    """


class DriftDetected(DriftSignal):
    """A recording no longer reflects the inputs that produced it.

    Raised only under ``drift_policy="fail"``. The default policy warns instead,
    because a stale recording is information, not necessarily a failure - the
    human decides.

    A ``DriftSignal`` so that SDK and agent exception handling cannot bury it.
    """


class DriftWarning(UserWarning):
    """Warning issued when a recording goes stale under ``drift_policy="warn"``."""


class UnapprovedReplayWarning(UserWarning):
    """Warning issued when replaying a recording no human has approved."""


class LatencyNotMeasured(UserWarning):
    """A latency assertion ran during replay, where there is nothing to time.

    Replay serves recorded bytes from disk, so the only durations available are
    the ones captured when the run was recorded. Checking those during replay
    would compare a fixed number against itself and pass forever, which is a
    green light that means nothing. The assertion is skipped and this warning
    explains why, rather than failing a test that is correctly written.
    """
