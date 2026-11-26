"""Replay: serving recorded events instead of calling the world.

Two guarantees define this module:

* **No network, ever.** Replay that silently falls through to a live call would
  make every test a lie about its own cost and determinism.
* **Recorded failures are re-raised.** A tool that timed out when recording must
  time out on replay, otherwise retry and error-handling paths are never tested.
"""

from __future__ import annotations

import builtins
import warnings
from typing import Any

from driftcatch.config import Config
from driftcatch.errors import DriftDetected, DriftWarning, RecordedError
from driftcatch.match.base import MatchState
from driftcatch.match.keys import llm_query, tool_query
from driftcatch.match.resolve import resolve
from driftcatch.recording.fingerprint import compare as fp_compare
from driftcatch.recording.schema import ErrorInfo, Event, LLMEvent, LLMRequest, ToolEvent
from driftcatch.session import Session

__all__ = ["Replayer", "raise_recorded"]


def _collect_client_exceptions() -> dict[str, type[BaseException]]:
    """Exception types from every installed client library.

    Both ``httpx`` and ``httpx2`` may be present (different SDKs use different
    ones), and a recorded ``ConnectTimeout`` should be re-raised as the same
    class the caller's SDK catches.
    """
    import importlib

    names = (
        "TimeoutException",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ConnectError",
        "ReadError",
        "RemoteProtocolError",
        "HTTPStatusError",
    )
    collected: dict[str, type[BaseException]] = {}
    for module_name in ("httpx2", "httpx"):  # later wins; httpx is the common case
        try:
            module = importlib.import_module(module_name)
        except ImportError:  # pragma: no cover - depends on the environment
            continue
        for name in names:
            candidate = getattr(module, name, None)
            if isinstance(candidate, type) and issubclass(candidate, BaseException):
                collected[name] = candidate
    return collected


#: Exception names that are not builtins but are worth reconstructing faithfully.
_EXTRA_EXCEPTIONS: dict[str, type[BaseException]] = _collect_client_exceptions()


def raise_recorded(error: ErrorInfo) -> None:
    """Re-raise a captured failure as faithfully as possible."""
    candidate = _EXTRA_EXCEPTIONS.get(error.type) or getattr(builtins, error.type, None)
    if isinstance(candidate, type) and issubclass(candidate, BaseException):
        try:
            raise candidate(error.message)
        except TypeError:  # pragma: no cover - exotic constructors
            pass
    raise RecordedError(f"{error.type}: {error.message}")


class Replayer:
    """Serves recorded events for the current session."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @property
    def _config(self) -> Config:
        return self.session.config

    @property
    def _state(self) -> MatchState:
        return self.session.match_state

    def _candidates(self) -> list[Event]:
        return self.session.candidates

    def _check_drift(self, request: LLMRequest) -> None:
        """Compare the live inputs against the approved ones, once per session.

        Running this *before* matching means a failed match can say "the system
        prompt changed" rather than merely "nothing matched". This is what turns
        a confusing replay failure into an actionable one.
        """
        golden = self.session.golden
        if golden is None or golden.fingerprint is None or self.session.changed_inputs:
            return
        if self.session.match_state.trail:
            return  # only the first model call defines the fingerprint

        from driftcatch.record import fingerprint_from_request

        current = fingerprint_from_request(request, self._config)
        changed = fp_compare(golden.fingerprint, current)
        if not changed:
            return

        self.session.stale = True
        self.session.changed_inputs = changed
        message = (
            f"recording {self.session.name!r} is STALE: "
            f"{', '.join(changed)} changed since it was approved"
        )
        if self._config.drift_policy == "fail":
            raise DriftDetected(message)
        if self._config.drift_policy == "warn":
            warnings.warn(
                f"{message}. Re-verify with: drift check --live {self.session.name}",
                DriftWarning,
                stacklevel=4,
            )

    def llm(self, request: LLMRequest) -> LLMEvent:
        """Return the recorded model event for ``request``.

        The caller rebuilds a transport response from it, because the verbatim
        provider body is what the caller's SDK knows how to parse.
        """
        self._check_drift(request)
        match = resolve(llm_query(request), self._candidates(), self._state, self._config)
        event = match.event
        if not isinstance(event, LLMEvent):  # pragma: no cover - matcher filters by kind
            raise RecordedError(f"expected an llm event, matched {event.type!r}")
        self.session.add_replayed(event)
        return event

    def tool(self, name: str, args: dict[str, Any]) -> Any:
        """Return the recorded tool result, or re-raise the recorded failure."""
        match = resolve(tool_query(name, args), self._candidates(), self._state, self._config)
        event = match.event
        if not isinstance(event, ToolEvent):  # pragma: no cover - matcher filters by kind
            raise RecordedError(f"expected a tool event, matched {event.type!r}")
        self.session.add_replayed(event)
        if event.error is not None:
            raise_recorded(event.error)
        return event.result
