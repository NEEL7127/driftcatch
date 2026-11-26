"""A tiny refund agent, with a stand-in provider so the demo runs offline.

Swap ``FakeLLM`` for a real client and nothing else changes: DriftCatch hooks
``httpx``, which is what every major Python LLM SDK uses underneath.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

import driftcatch as drift

API_URL = "https://api.openai.com/v1/chat/completions"

SYSTEM_PROMPT = "Use available tools to find the order, then refund it."

TOOLS = [
    {"type": "function", "function": {"name": "lookup_order"}},
    {"type": "function", "function": {"name": "web_search"}},
    {"type": "function", "function": {"name": "issue_refund"}},
    {"type": "function", "function": {"name": "escalate_to_human"}},
]


# --------------------------------------------------------------------- tools


@drift.tool
def lookup_order(order_id: int) -> dict[str, Any]:
    """Pretend to hit the orders database."""
    return {"id": order_id, "total": 40, "status": "paid"}


@drift.tool
def web_search(query: str) -> dict[str, Any]:
    """The tool the agent should *not* reach for when an order id is known."""
    return {"hits": []}


@drift.tool
def issue_refund(order_id: int, amount: int) -> dict[str, Any]:
    return {"ok": True, "refunded": amount}


@drift.tool
def escalate_to_human(reason: str) -> dict[str, Any]:
    return {"ticket": "SUP-1"}


# ------------------------------------------------------------- fake provider


def _fake_transport(prompt_bias: str) -> httpx.MockTransport:
    """A provider whose tool choice depends on the wording of the prompt.

    This is the whole point of the demo: a prompt that says "search" nudges the
    model towards ``web_search``, which is exactly the kind of silent behaviour
    change answer-quality evals miss.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        system = next((m["content"] for m in body["messages"] if m.get("role") == "system"), "")
        if calls["n"] == 1:
            tool = "web_search" if "search" in system.lower() else "lookup_order"
            args = {"query": "order 1182"} if tool == "web_search" else {"order_id": 1182}
            message: dict[str, Any] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_demo000000000001",
                        "type": "function",
                        "function": {"name": tool, "arguments": json.dumps(args)},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "Refunded $40 to order 1182."}
            finish = "stop"

        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-demo",
                "model": "gpt-demo-1",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 18},
            },
            request=request,
        )

    return httpx.MockTransport(handler)


# -------------------------------------------------------------------- agent


def refund_agent(message: str, *, system_prompt: str = SYSTEM_PROMPT) -> str:
    """Ask the model what to do, do it, then confirm."""
    transport = _fake_transport(system_prompt)

    with httpx.Client(transport=transport) as client:
        first = client.post(
            API_URL,
            json={
                "model": "gpt-demo-1",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                "tools": TOOLS,
            },
        ).json()

        call = first["choices"][0]["message"]["tool_calls"][0]["function"]
        args = json.loads(call["arguments"])

        if call["name"] == "lookup_order":
            order = lookup_order(args["order_id"])
        else:
            web_search(args.get("query", ""))
            order = {"id": 1182, "total": 40}

        issue_refund(order["id"], order["total"])

        final = client.post(
            API_URL,
            json={
                "model": "gpt-demo-1",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": "refund issued"},
                ],
                "tools": TOOLS,
            },
        ).json()

    content: str = final["choices"][0]["message"]["content"]
    return content
