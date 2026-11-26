"""A fake OpenAI-compatible provider, served through a stub httpx transport.

Unit and integration tests must never touch the network. This stands in for a
real provider so the whole record / replay / drift loop can be exercised
offline, deterministically and for free.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

BASE_URL = "https://fake.openai.test/v1/chat/completions"


def chat_body(
    *,
    content: str | None = None,
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
    finish_reason: str = "stop",
    input_tokens: int = 100,
    output_tokens: int = 20,
) -> dict[str, Any]:
    """An OpenAI chat-completions response body."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_name:
        message["tool_calls"] = [
            {
                "id": "call_abcdef1234567890",
                "type": "function",
                "function": {"name": tool_name, "arguments": tool_args or {}},
            }
        ]
        finish_reason = "tool_calls"
    return {
        "id": "chatcmpl-fake",
        "created": 1_700_000_000,
        "model": "gpt-fake-1",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
    }


def sse_stream(text: str, *, tool_name: str | None = None) -> str:
    """An OpenAI-style SSE body, token by token."""
    frames: list[str] = []
    if tool_name:
        frames.append(
            json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"function": {"name": tool_name, "arguments": "{}"}}]
                            }
                        }
                    ]
                }
            )
        )
    for piece in text.split(" "):
        frames.append(json.dumps({"choices": [{"delta": {"content": piece + " "}}]}))
    frames.append(json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}))
    body = "".join(f"data: {frame}\n\n" for frame in frames)
    return body + "data: [DONE]\n\n"


class FakeProvider:
    """Scripted responses plus a call counter, so tests can prove replay is offline."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[dict[str, Any]] = []
        self.stream: str | None = None
        self.status_code = 200
        self.raise_exc: Exception | None = None

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.raise_exc is not None:
            raise self.raise_exc
        self.calls.append(json.loads(request.content or b"{}"))

        if self.stream is not None:
            return httpx.Response(
                self.status_code,
                content=self.stream.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
                request=request,
            )

        index = min(len(self.calls) - 1, len(self.responses) - 1)
        body = self.responses[index] if self.responses else chat_body(content="ok")
        return httpx.Response(self.status_code, json=body, request=request)

    def client(self) -> httpx.Client:
        """A client whose transport is this fake provider."""
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def post_chat(
    client: httpx.Client,
    *,
    system: str = "Use available tools to find the order.",
    user: str = "refund order 1182",
    tools: list[dict[str, Any]] | None = None,
    model: str = "gpt-fake-1",
) -> dict[str, Any]:
    """Issue a chat-completions call the way an SDK would."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "tools": tools
        if tools is not None
        else [
            {"type": "function", "function": {"name": "lookup_order"}},
            {"type": "function", "function": {"name": "web_search"}},
        ],
    }
    response = client.post(BASE_URL, json=payload)
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    return data


def first_tool_call(body: dict[str, Any]) -> str | None:
    """Pull the chosen tool name out of a provider reply."""
    choices = body.get("choices") or []
    if not choices:
        return None
    calls = (choices[0].get("message") or {}).get("tool_calls") or []
    if not calls:
        return None
    name: str = calls[0]["function"]["name"]
    return name


AgentFn = Callable[..., str]
