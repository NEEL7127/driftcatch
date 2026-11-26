"""Tool interception.

Model calls travel over HTTP and can be hooked generically. Tools are arbitrary
Python functions, so there is **no universal automatic capture** - claiming
otherwise would be dishonest. The documented default is an explicit one-line
decorator, which always works:

    @drift.tool
    def lookup_order(order_id: int) -> dict: ...

With no active session the wrapper calls straight through, so decorated tools
cost production nothing.
"""

from __future__ import annotations

import functools
import inspect
import time
from collections.abc import Callable
from typing import Any, TypeVar, cast

from driftcatch.errors import NoMatchError
from driftcatch.record import Recorder
from driftcatch.recording.schema import ErrorInfo
from driftcatch.replay import Replayer
from driftcatch.session import Session, current_session

__all__ = ["tool"]

F = TypeVar("F", bound=Callable[..., Any])


def _json_safe(value: Any) -> Any:
    """Coerce an argument into something a recording can store."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump())
        except Exception:  # pragma: no cover - defensive
            pass
    return repr(value)


def _bind(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Normalise call arguments to a name -> value mapping.

    Binding by name means ``f(1)`` and ``f(x=1)`` produce the same recorded
    arguments, so a harmless refactor at the call site is not read as drift.
    """
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return {name: _json_safe(value) for name, value in bound.arguments.items()}
    except (TypeError, ValueError):  # pragma: no cover - exotic signatures
        return {
            "args": [_json_safe(a) for a in args],
            "kwargs": {k: _json_safe(v) for k, v in kwargs.items()},
        }


#: Sentinel meaning "fall through and run the real function".
_EXECUTE: Any = object()


def _elapsed_ms(started: float) -> int:
    """Milliseconds since ``started``, never negative."""
    return max(0, round((time.perf_counter() - started) * 1000))


def _unmatched_fallback(session: Session, name: str, arguments: dict[str, Any]) -> Any:
    """Decide what an unmatched tool call does during a what-if run.

    Outside a what-if run an unmatched call is drift and the error propagates.
    Inside one, the agent is on a branch that was never recorded, so there is
    nothing to replay and the configured policy applies.
    """
    if not session.is_what_if:
        raise
    policy = session.config.what_if_unmatched
    if policy == "error":
        raise
    if policy == "stub":
        Recorder(session).add_tool(name, arguments, result=None)
        return None
    return _EXECUTE


def _active(name: str) -> Session | None:
    """The session that should capture this tool, if any.

    Live mode is included on purpose. It means "do not *serve* from the
    recording", not "do not *observe*": a Tier-2 behaviour check has to build a
    trajectory from the real run in order to compare it with the golden. Only
    the replay branch is skipped, and nothing is persisted, so the golden is
    left untouched.
    """
    session = current_session()
    if session is None:
        return None
    if not session.config.should_record(f"tools.{name}"):
        return None
    return session


def tool(fn: F) -> F:
    """Record and replay a tool function."""
    name = getattr(fn, "__name__", "tool")

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            session = _active(name)
            if session is None:
                return await fn(*args, **kwargs)

            arguments = _bind(fn, args, kwargs)
            if session.is_replaying:
                try:
                    return Replayer(session).tool(name, arguments)
                except NoMatchError:
                    fallback = _unmatched_fallback(session, name, arguments)
                    if fallback is not _EXECUTE:
                        return fallback

            started = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                Recorder(session).add_tool(
                    name,
                    arguments,
                    error=ErrorInfo(type=type(exc).__name__, message=str(exc)),
                    duration_ms=_elapsed_ms(started),
                )
                raise
            Recorder(session).add_tool(
                name,
                arguments,
                result=_json_safe(result),
                duration_ms=_elapsed_ms(started),
            )
            return result

        return cast(F, async_wrapper)

    @functools.wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        session = _active(name)
        if session is None:
            return fn(*args, **kwargs)

        arguments = _bind(fn, args, kwargs)
        if session.is_replaying:
            try:
                return Replayer(session).tool(name, arguments)
            except NoMatchError:
                fallback = _unmatched_fallback(session, name, arguments)
                if fallback is not _EXECUTE:
                    return fallback

        started = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            Recorder(session).add_tool(
                name,
                arguments,
                error=ErrorInfo(type=type(exc).__name__, message=str(exc)),
                duration_ms=_elapsed_ms(started),
            )
            raise
        Recorder(session).add_tool(
            name,
            arguments,
            result=_json_safe(result),
            duration_ms=_elapsed_ms(started),
        )
        return result

    return cast(F, sync_wrapper)
