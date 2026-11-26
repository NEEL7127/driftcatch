"""HTTP client interception.

Python LLM SDKs issue their requests through an httpx-style client, so one hook
there covers OpenAI, Anthropic, LiteLLM, Mistral, Cohere, Google and any gateway
speaking their wire formats. Hooking each SDK instead would mean an unbounded
number of adapters that break on every version bump.

**There is more than one such library.** The OpenAI SDK uses ``httpx``; the
Anthropic SDK has moved to ``httpx2``, which is a separate distribution with the
same API. Patching only one silently misses every SDK built on the other - the
calls simply are not recorded, and a replay test passes while testing nothing.
So every known client library present in the environment is patched, and the
response for a replayed event is built with *that* library's ``Response`` class.

Safety rules:

* when there is no active session the original client runs untouched, so
  importing DriftCatch costs production nothing;
* anything that cannot be parsed is passed through rather than guessed at;
* during replay no request ever reaches the network.
"""

from __future__ import annotations

import importlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import Any, cast

from driftcatch.errors import PrunedRecording
from driftcatch.providers import is_llm_path, parse_request, parse_response, parse_usage
from driftcatch.record import Recorder
from driftcatch.recording.schema import ErrorInfo, LLMEvent, LLMRequest
from driftcatch.replay import Replayer, raise_recorded
from driftcatch.session import Session, current_session

__all__ = [
    "CLIENT_LIBRARIES",
    "assemble_stream",
    "client_libraries",
    "intercept_http",
    "response_from_event",
]

_LOCK = threading.Lock()
_depth = 0
#: Saved originals, keyed by (library name, "sync" | "async").
_originals: dict[tuple[str, str], Callable[..., Any]] = {}

_SSE_SEPARATOR = "\n\n"

#: Client libraries DriftCatch knows how to hook. Add a name here and any SDK
#: built on it is covered; nothing else in the codebase needs to change.
CLIENT_LIBRARIES = ("httpx", "httpx2")


def client_libraries() -> list[ModuleType]:
    """Every known client library actually installed, in priority order."""
    found: list[ModuleType] = []
    for name in CLIENT_LIBRARIES:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(module, "Client") and hasattr(module, "Response"):
            found.append(module)
    return found


# --------------------------------------------------------------- streaming


def assemble_stream(chunks: list[str]) -> dict[str, Any]:
    """Collapse recorded SSE chunks into a single response body.

    The verbatim chunks are what replay serves, so fidelity is never lost. This
    assembled view exists only so assertions, trajectories and coverage can read
    a streamed reply the same way they read a buffered one.
    """
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    finish_reason: str | None = None
    usage: dict[str, Any] = {}

    for chunk in chunks:
        for line in chunk.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue

            # OpenAI-style deltas
            for choice in data.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or choice.get("message") or {}
                if isinstance(delta, dict):
                    if delta.get("content"):
                        text_parts.append(str(delta["content"]))
                    # Reasoning models stream their thinking on a separate key;
                    # without this the assembled view of a streamed reply from
                    # o1-style, DeepSeek-R1 or Nemotron models comes back empty.
                    if delta.get("reasoning_content"):
                        reasoning_parts.append(str(delta["reasoning_content"]))
                    for call in delta.get("tool_calls") or []:
                        if isinstance(call, dict):
                            function = call.get("function") or {}
                            tool_calls.append(
                                {
                                    "name": function.get("name") or call.get("name") or "",
                                    "args": function.get("arguments") or {},
                                }
                            )
                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])

            # Anthropic-style events
            delta = data.get("delta")
            if isinstance(delta, dict):
                if delta.get("text"):
                    text_parts.append(str(delta["text"]))
                if delta.get("thinking"):
                    reasoning_parts.append(str(delta["thinking"]))
                if delta.get("stop_reason"):
                    finish_reason = str(delta["stop_reason"])
            block = data.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_calls.append({"name": block.get("name", ""), "args": block.get("input", {})})

            if isinstance(data.get("usage"), dict):
                usage.update(data["usage"])

    return {
        "choices": [
            {
                "message": {
                    "content": "".join(text_parts) or None,
                    "reasoning_content": "".join(reasoning_parts) or None,
                    "tool_calls": tool_calls,
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }


def _split_sse(text: str) -> list[str]:
    return [part + _SSE_SEPARATOR for part in text.split(_SSE_SEPARATOR) if part.strip()]


# ------------------------------------------------------------------ replay


def response_from_event(event: LLMEvent, request: Any, lib: ModuleType) -> Any:
    """Rebuild a response from a recorded event, using ``lib``'s Response class.

    The class matters: an ``httpx.Response`` handed to an SDK built on ``httpx2``
    is rejected outright, so the response must come from the same library that
    issued the request.
    """
    # A transport-level failure was recorded (no HTTP response ever arrived).
    if event.error is not None and event.status_code == 0:
        raise_recorded(event.error)

    if event.stream_chunks:
        content = "".join(event.stream_chunks).encode("utf-8")
        return lib.Response(
            event.status_code,
            content=content,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    if event.raw_response is None:
        # A pruned recording has had its bodies stripped. Serving `{}` here
        # would hand the caller's SDK an empty completion with no error, which
        # is a silent false green - the exact failure this project exists to
        # prevent. Refuse instead and say why.
        raise PrunedRecording(
            f"event #{event.index} has no recorded body, so it cannot be replayed. "
            f"This recording was pruned, which keeps the trajectory for behaviour "
            f"assertions but discards the bodies needed to replay. Re-record it to "
            f"restore replay."
        )

    return lib.Response(event.status_code, json=event.raw_response, request=request)


# ----------------------------------------------------------------- capture


def _elapsed_ms(started: float) -> int:
    """Milliseconds since ``started``, never negative."""
    return max(0, round((time.perf_counter() - started) * 1000))


def _parse_body(request: Any) -> LLMRequest | None:
    try:
        body = json.loads(request.content or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    return parse_request(body)


def _record_response(
    session: Session,
    parsed: LLMRequest,
    response: Any,
    duration_ms: int | None = None,
) -> Any:
    recorder = Recorder(session)
    content_type = response.headers.get("content-type", "")

    if "text/event-stream" in content_type:
        chunks = _split_sse(response.text)
        body = assemble_stream(chunks)
        recorder.add_llm(
            parsed,
            response=parse_response(body),
            raw_response=body,
            status_code=response.status_code,
            usage=parse_usage(body),
            stream_chunks=chunks,
            duration_ms=duration_ms,
        )
        return response

    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        body = {}
    if not isinstance(body, dict):
        body = {"value": body}

    error: ErrorInfo | None = None
    if response.status_code >= 400:
        error = ErrorInfo(
            type="HTTPStatusError",
            message=f"{response.status_code} from {response.request.url}",
            status=response.status_code,
        )

    recorder.add_llm(
        parsed,
        response=None if error else parse_response(body),
        error=error,
        raw_response=body,
        status_code=response.status_code,
        usage=parse_usage(body),
        duration_ms=duration_ms,
    )
    return response


# ---------------------------------------------------------------- handlers


def _should_handle(session: Session | None, request: Any) -> bool:
    """Whether this request is ours to observe.

    Live mode is included: it must still be *observed* so a Tier-2 check can
    build a trajectory from the real run. The handlers branch on
    ``session.is_replaying`` for whether to *serve* from the recording, which is
    false in live mode, so the real call always goes out.
    """
    return (
        session is not None
        and is_llm_path(request.url.path)
        and session.config.should_record("llm.chat")
    )


def _make_sync_handler(lib: ModuleType, original: Callable[..., Any]) -> Callable[..., Any]:
    """Build a ``Client.send`` replacement bound to one client library."""

    def handler(self: Any, request: Any, **kwargs: Any) -> Any:
        session = current_session()
        if not _should_handle(session, request):
            return original(self, request, **kwargs)
        assert session is not None

        parsed = _parse_body(request)
        if parsed is None:
            return original(self, request, **kwargs)

        if session.is_replaying:
            event = Replayer(session).llm(parsed)
            return response_from_event(event, request, lib)

        started = time.perf_counter()
        try:
            response = original(self, request, **kwargs)
        except Exception as exc:
            Recorder(session).add_llm(
                parsed,
                error=ErrorInfo(type=type(exc).__name__, message=str(exc)),
                status_code=0,
                duration_ms=_elapsed_ms(started),
            )
            raise
        # Reading is part of the call: a streamed reply is not complete until
        # the last chunk arrives, so timing only the headers would understate a
        # streamed request by most of its duration.
        response.read()
        return _record_response(session, parsed, response, _elapsed_ms(started))

    return handler


def _make_async_handler(lib: ModuleType, original: Callable[..., Any]) -> Callable[..., Any]:
    """Build an ``AsyncClient.send`` replacement bound to one client library."""

    async def handler(self: Any, request: Any, **kwargs: Any) -> Any:
        session = current_session()
        if not _should_handle(session, request):
            return await original(self, request, **kwargs)
        assert session is not None

        parsed = _parse_body(request)
        if parsed is None:
            return await original(self, request, **kwargs)

        if session.is_replaying:
            event = Replayer(session).llm(parsed)
            return response_from_event(event, request, lib)

        started = time.perf_counter()
        try:
            response = await original(self, request, **kwargs)
        except Exception as exc:
            Recorder(session).add_llm(
                parsed,
                error=ErrorInfo(type=type(exc).__name__, message=str(exc)),
                status_code=0,
                duration_ms=_elapsed_ms(started),
            )
            raise
        await response.aread()
        return _record_response(session, parsed, response, _elapsed_ms(started))

    return handler


# -------------------------------------------------------------- patching


@contextmanager
def intercept_http() -> Iterator[None]:
    """Patch ``Client.send`` on every installed client library.

    Hooking ``send`` rather than a concrete transport is deliberate: it catches
    every transport, including ``MockTransport`` and any custom one a user plugs
    in, so interception does not quietly stop working depending on how the client
    was constructed.

    Reference counted so nested sessions are safe. The handlers read the session
    from a ``ContextVar``, so a patch held open by an outer session never leaks
    into unrelated threads or tasks.
    """
    global _depth
    with _LOCK:
        if _depth == 0:
            for lib in client_libraries():
                sync_original = lib.Client.send
                async_original = lib.AsyncClient.send
                _originals[(lib.__name__, "sync")] = sync_original
                _originals[(lib.__name__, "async")] = async_original
                lib.Client.send = cast(Any, _make_sync_handler(lib, sync_original))
                lib.AsyncClient.send = cast(Any, _make_async_handler(lib, async_original))
        _depth += 1
    try:
        yield
    finally:
        with _LOCK:
            _depth -= 1
            if _depth == 0:
                for lib in client_libraries():
                    sync_original = _originals.pop((lib.__name__, "sync"), None)
                    async_original = _originals.pop((lib.__name__, "async"), None)
                    if sync_original is not None:
                        lib.Client.send = cast(Any, sync_original)
                    if async_original is not None:
                        lib.AsyncClient.send = cast(Any, async_original)
                _originals.clear()
