"""Compatibility with the real OpenAI and Anthropic SDKs.

Everything else in the suite drives a hand-written fake provider, which proves
the engine works but assumes the request/response *shapes* are right. These
tests remove that assumption: the genuine SDK builds the request and parses the
reply, so the provider parsers are validated against real SDK behaviour.

No API calls and no keys are needed - the SDK's own ``http_client`` is pointed at
an ``httpx.MockTransport``, which is a documented, supported way to use it. The
interception layer hooks ``httpx.Client.send``, so the SDK is exercised exactly
as a user's would be.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import driftcatch as drift
from driftcatch.errors import NoMatchError

anthropic = pytest.importorskip("anthropic")
openai = pytest.importorskip("openai")

SYSTEM = "Use available tools to find the order."
CHANGED_SYSTEM = "Search the web for the order."

TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Look up an order",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "integer"}},
                "required": ["order_id"],
            },
        },
    }
]

TOOLS_ANTHROPIC = [
    {
        "name": "lookup_order",
        "description": "Look up an order",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "integer"}},
            "required": ["order_id"],
        },
    }
]


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path) -> Iterator[None]:
    drift.reset_config()
    drift.config(recordings_dir=tmp_path / "recordings")
    yield
    drift.reset_config()


class Counter:
    """Counts how many requests actually reached the wire."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content or b"{}"))
        return self.handler(request)

    @property
    def count(self) -> int:
        return len(self.requests)


# ------------------------------------------------------------------ OpenAI


def _openai_reply(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1_763_000_000,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_RealSdk000000000001",
                                "type": "function",
                                "function": {
                                    "name": "lookup_order",
                                    "arguments": '{"order_id": 1182}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 131, "completion_tokens": 17, "total_tokens": 148},
        },
        request=request,
    )


def _openai_client(counter: Counter) -> Any:
    return openai.OpenAI(
        api_key="sk-test-not-a-real-key-000000",
        http_client=httpx.Client(transport=httpx.MockTransport(counter)),
    )


def _call_openai(client: Any, system: str = SYSTEM) -> Any:
    return client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": "refund order 1182"},
        ],
        tools=TOOLS_OPENAI,
        temperature=0,
    )


def test_openai_sdk_records_and_replays() -> None:
    """The real SDK's request is parsed, and its reply round-trips untouched."""
    counter = Counter(_openai_reply)

    with drift.take("openai_basic") as take:
        completion = _call_openai(_openai_client(counter))

    # The SDK parsed our recorded body into its own typed objects.
    assert completion.choices[0].message.tool_calls[0].function.name == "lookup_order"
    assert counter.count == 1

    event = take.events[0]
    assert event.request.model == "gpt-4o-mini"
    assert event.request.tools == [{"name": "lookup_order", "schema": TOOLS_OPENAI[0]}]
    assert event.response is not None
    assert event.response.tool_calls[0]["name"] == "lookup_order"
    assert event.usage.input_tokens == 131
    assert event.usage.output_tokens == 17

    drift.approve("openai_basic", by="test")

    replay_counter = Counter(_openai_reply)
    with drift.take("openai_basic"):
        replayed = _call_openai(_openai_client(replay_counter))

    assert replay_counter.count == 0, "replay must not reach the provider"
    assert replayed.choices[0].message.tool_calls[0].function.arguments == '{"order_id": 1182}'
    assert replayed.usage.prompt_tokens == 131


def test_openai_sdk_tool_call_ids_do_not_break_matching() -> None:
    """Providers mint a fresh tool_call id per request; it must not look like drift."""
    counter = Counter(_openai_reply)
    with drift.take("openai_ids") as take:
        client = _openai_client(counter)
        first = _call_openai(client)
        # Feed the reply back, the way an agent loop does.
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": "refund order 1182"},
                first.choices[0].message.model_dump(exclude_none=True),
                {
                    "role": "tool",
                    "tool_call_id": first.choices[0].message.tool_calls[0].id,
                    "content": '{"total": 40}',
                },
            ],
            tools=TOOLS_OPENAI,
            temperature=0,
        )
    assert len(take.events) == 2
    drift.approve("openai_ids", by="test")

    replay_counter = Counter(_openai_reply)
    with drift.take("openai_ids"):
        client = _openai_client(replay_counter)
        first = _call_openai(client)
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": "refund order 1182"},
                first.choices[0].message.model_dump(exclude_none=True),
                {
                    "role": "tool",
                    "tool_call_id": first.choices[0].message.tool_calls[0].id,
                    "content": '{"total": 40}',
                },
            ],
            tools=TOOLS_OPENAI,
            temperature=0,
        )
    assert replay_counter.count == 0


def test_openai_sdk_prompt_change_is_caught() -> None:
    counter = Counter(_openai_reply)
    with drift.take("openai_drift"):
        _call_openai(_openai_client(counter))
    drift.approve("openai_drift", by="test")

    with pytest.raises(NoMatchError) as excinfo, drift.take("openai_drift") as take:
        _call_openai(_openai_client(Counter(_openai_reply)), system=CHANGED_SYSTEM)

    assert "messages[0].content" in str(excinfo.value)
    assert take.changed_inputs == ["prompt_hash"]


def test_openai_sdk_http_error_is_recorded_and_replayed() -> None:
    """A 429 must come back as a 429, so retry handling is testable."""

    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "rate limit", "type": "rate_limit_error"}},
            request=request,
        )

    counter = Counter(rate_limited)
    with drift.take("openai_429"), pytest.raises(openai.RateLimitError):
        _call_openai(_openai_client(counter), system=SYSTEM)

    # The SDK retries rate limits internally, so several requests reach the wire.
    assert counter.count >= 1
    drift.approve("openai_429", by="test")

    replay_counter = Counter(rate_limited)
    with drift.take("openai_429"), pytest.raises(openai.RateLimitError):
        _call_openai(_openai_client(replay_counter), system=SYSTEM)
    assert replay_counter.count == 0


def test_openai_sdk_streaming_round_trips() -> None:
    frames = [
        {"choices": [{"delta": {"role": "assistant", "content": "Refunded "}}]},
        {"choices": [{"delta": {"content": "$40"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"

    def stream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    def collect(client: Any) -> str:
        chunks = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "refund order 1182"}],
            stream=True,
        )
        return "".join(c.choices[0].delta.content or "" for c in chunks)

    counter = Counter(stream)
    with drift.take("openai_stream") as take:
        recorded = collect(_openai_client(counter))

    assert recorded == "Refunded $40"
    assert take.events[0].stream_chunks, "SSE frames must be captured in order"
    drift.approve("openai_stream", by="test")

    replay_counter = Counter(stream)
    with drift.take("openai_stream"):
        replayed = collect(_openai_client(replay_counter))

    assert replayed == recorded, "the SDK must parse the replayed stream identically"
    assert replay_counter.count == 0


@pytest.mark.anyio
async def test_openai_async_sdk_records_and_replays() -> None:
    """Most production agents are async, so AsyncClient must work too."""

    def make_client(counter: Counter) -> Any:
        return openai.AsyncOpenAI(
            api_key="sk-test-not-a-real-key-000000",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(counter)),
        )

    async def call(client: Any) -> Any:
        return await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": "refund order 1182"},
            ],
            tools=TOOLS_OPENAI,
            temperature=0,
        )

    counter = Counter(_openai_reply)
    with drift.take("openai_async") as take:
        await call(make_client(counter))
    assert counter.count == 1
    assert take.events[0].request.model == "gpt-4o-mini"
    drift.approve("openai_async", by="test")

    replay_counter = Counter(_openai_reply)
    with drift.take("openai_async"):
        result = await call(make_client(replay_counter))
    assert replay_counter.count == 0
    assert result.choices[0].message.tool_calls[0].function.name == "lookup_order"


# --------------------------------------------------------------- Anthropic


def _anthropic_http_lib() -> Any:
    """Whichever client library the installed Anthropic SDK is built on."""
    import importlib

    for name in ("httpx2", "httpx"):
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        try:  # the SDK rejects a client from the wrong library
            anthropic.Anthropic(api_key="x", http_client=module.Client())
            return module
        except TypeError:
            continue
    raise RuntimeError("no compatible client library for the Anthropic SDK")


def _anthropic_reply(request: Any) -> Any:
    lib = _anthropic_http_lib()
    return lib.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [
                {"type": "text", "text": "Looking that up."},
                {
                    "type": "tool_use",
                    "id": "toolu_RealSdk00000000001",
                    "name": "lookup_order",
                    "input": {"order_id": 1182},
                },
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 212, "output_tokens": 29},
        },
        request=request,
    )


def _anthropic_client(counter: Counter) -> Any:
    """Build an Anthropic client.

    The Anthropic SDK has moved to ``httpx2``, so its client must be given an
    ``httpx2`` transport — handing it an ``httpx`` one is rejected outright.
    This is precisely why DriftCatch patches both libraries.
    """
    lib = _anthropic_http_lib()
    return anthropic.Anthropic(
        api_key="sk-ant-test-not-a-real-key-0000",
        http_client=lib.Client(transport=lib.MockTransport(counter)),
    )


def _call_anthropic(client: Any, system: str = SYSTEM) -> Any:
    return client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=256,
        system=system,
        messages=[{"role": "user", "content": "refund order 1182"}],
        tools=TOOLS_ANTHROPIC,
    )


def test_anthropic_sdk_records_and_replays() -> None:
    """Anthropic puts the system prompt in a top-level field, not in messages."""
    counter = Counter(_anthropic_reply)

    with drift.take("anthropic_basic") as take:
        message = _call_anthropic(_anthropic_client(counter))

    assert message.content[1].name == "lookup_order"
    assert counter.count == 1

    event = take.events[0]
    # The top-level `system` field must be projected into messages, or the
    # fingerprint would never see the prompt at all.
    assert event.request.messages[0] == {"role": "system", "content": SYSTEM}
    assert event.response is not None
    assert event.response.content == "Looking that up."
    assert event.response.tool_calls[0]["name"] == "lookup_order"
    assert event.usage.input_tokens == 212
    assert event.usage.output_tokens == 29

    drift.approve("anthropic_basic", by="test")

    replay_counter = Counter(_anthropic_reply)
    with drift.take("anthropic_basic"):
        replayed = _call_anthropic(_anthropic_client(replay_counter))

    assert replay_counter.count == 0
    assert replayed.content[1].input == {"order_id": 1182}
    assert replayed.usage.input_tokens == 212


def test_anthropic_sdk_prompt_change_is_caught_in_the_system_field() -> None:
    """Drift must be detected even though the prompt is not inside `messages`."""
    counter = Counter(_anthropic_reply)
    with drift.take("anthropic_drift"):
        _call_anthropic(_anthropic_client(counter))
    drift.approve("anthropic_drift", by="test")

    with pytest.raises(NoMatchError), drift.take("anthropic_drift") as take:
        _call_anthropic(_anthropic_client(Counter(_anthropic_reply)), system=CHANGED_SYSTEM)

    assert take.changed_inputs == ["prompt_hash"]


def test_anthropic_sdk_tool_schema_feeds_coverage() -> None:
    """Coverage's denominator comes from the declared tools, whatever the SDK."""
    counter = Counter(_anthropic_reply)
    with drift.take("anthropic_cov"):
        _call_anthropic(_anthropic_client(counter))
    drift.approve("anthropic_cov", by="test")

    recordings = drift.list_recordings(drift.get_config().recordings_dir)
    report = drift.analyse_coverage(recordings)
    assert "lookup_order" in report.tools
    assert report.tools["lookup_order"].covered is False  # declared but never invoked


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
