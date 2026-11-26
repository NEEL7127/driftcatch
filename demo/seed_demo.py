"""Build the sandbox the VHS tapes record.

The tapes film a real terminal running real commands, which only works if there
is something real to run. This script creates `demo/sandbox/` with a working
agent, a corpus of approved recordings, and a one-line way to break it.

    python demo/seed_demo.py

Everything it writes is disposable and gitignored. Run it again to reset.

No network and no API key: the agent talks to a stand-in provider through
httpx, which is the same seam DriftCatch hooks for real SDKs. The demo is
therefore honest about the mechanism while costing nothing to film.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "demo" / "sandbox"

#: The corpus size that makes mining worth filming. Below five, invariant
#: mining withholds its findings on purpose, so a smaller demo would show an
#: empty report and teach the viewer the wrong thing.
RUNS = 23


AGENT = '''"""A refund agent with a stand-in provider, so the demo runs offline."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

import driftcatch as drift

API_URL = "https://api.openai.com/v1/chat/completions"
PROMPT_FILE = Path(__file__).parent / "prompt.txt"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "integer"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_eligibility",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "integer"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_refund",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer"},
                    "amount": {"type": "integer"},
                    "currency": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
            },
        },
    },
]


@drift.tool
def lookup_order(order_id: int) -> dict[str, Any]:
    return {"id": order_id, "total": 40, "status": "paid"}


@drift.tool
def check_eligibility(order_id: int) -> dict[str, Any]:
    return {"order_id": order_id, "eligible": True}


@drift.tool
def issue_refund(order_id: int, amount: int) -> dict[str, Any]:
    return {"ok": True, "refunded": amount}


@drift.tool
def escalate_to_human(reason: str) -> dict[str, Any]:
    return {"ticket": "SUP-1"}


def _reply(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-demo",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_demo000001",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 18},
    }


def _handler(request: httpx.Request) -> httpx.Response:
    """Stand in for the model.

    The branch below is the whole point of the demo: the single word
    "carefully" in the prompt flips the agent from refunding to escalating.
    A real model does this for reasons nobody can inspect; here it is explicit
    so the demo is reproducible.
    """
    body = json.loads(request.content or b"{}")
    messages = body.get("messages", [])
    system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    calls = sum(1 for m in messages if m.get("role") == "tool")
    cautious = "carefully" in system

    # Carry the order id through every call. A stand-in that hardcoded one id
    # would silently break the rule the demo exists to show - that the order
    # you look up is the order you refund - because the ids would never match.
    found = re.search(r"\d+", user or "")
    order_id = int(found.group()) if found else 1182

    if calls == 0:
        return httpx.Response(200, json=_reply("lookup_order", {"order_id": order_id}))
    if calls == 1:
        return httpx.Response(200, json=_reply("check_eligibility", {"order_id": order_id}))
    if cautious:
        return httpx.Response(
            200, json=_reply("escalate_to_human", {"reason": "needs review"})
        )
    return httpx.Response(
        200, json=_reply("issue_refund", {"order_id": order_id, "amount": 40})
    )


DISPATCH = {
    "lookup_order": lookup_order,
    "check_eligibility": check_eligibility,
    "issue_refund": issue_refund,
    "escalate_to_human": escalate_to_human,
}


def refund_agent(request: str) -> str:
    """Drive the loop until the model stops asking for tools."""
    system = PROMPT_FILE.read_text(encoding="utf-8").strip()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": request},
    ]

    with httpx.Client(transport=httpx.MockTransport(_handler)) as client:
        for _ in range(6):
            response = client.post(
                API_URL,
                json={"model": "gpt-4o-mini", "messages": messages, "tools": TOOLS},
            )
            message = response.json()["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            if not calls:
                return message.get("content") or ""

            call = calls[0]["function"]
            name = call["name"]
            result = DISPATCH[name](**json.loads(call["arguments"]))
            messages.append(message)
            messages.append(
                {"role": "tool", "tool_call_id": calls[0]["id"], "content": json.dumps(result)}
            )

            if name in {"issue_refund", "escalate_to_human"}:
                return json.dumps(result)

    return ""
'''


TEST = '''"""The scenario the tapes film."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import driftcatch as drift
from agent import refund_agent

RECORDINGS = Path(__file__).parent / "recordings"


@pytest.fixture(autouse=True)
def _recordings() -> Iterator[None]:
    drift.reset_config()
    drift.config(recordings_dir=RECORDINGS)
    yield
    drift.reset_config()


def test_refund_1182() -> None:
    with drift.take("refund_1182") as take:
        refund_agent("refund order 1182")

    take.assert_tools().called_in_order("lookup_order", "issue_refund")
'''


PROMPT = "You are a refund agent. Resolve the customer's issue.\n"

CHANGE_PROMPT = '''"""Change one word in the prompt. That is the whole bug."""

from __future__ import annotations

from pathlib import Path

PROMPT = Path(__file__).parent / "prompt.txt"
text = PROMPT.read_text(encoding="utf-8")

if "carefully" in text:
    text = text.replace(" carefully", "")
    print("prompt reverted")
else:
    text = text.replace("issue.", "issue carefully.")
    print("prompt changed:  Resolve the customer's issue carefully.")

PROMPT.write_text(text, encoding="utf-8")
'''


SEED = '''"""Record and approve a corpus, so `drift learn` has something to mine."""

from __future__ import annotations

from pathlib import Path

import driftcatch as drift
from agent import refund_agent

RECORDINGS = Path(__file__).parent / "recordings"
RUNS = {runs}

drift.config(recordings_dir=RECORDINGS)

for index in range(RUNS):
    name = f"refund_{{1182 + index}}"
    with drift.take(name, mode="record"):
        refund_agent(f"refund order {{1182 + index}}")
    drift.approve(name, by="demo")

print(f"recorded and approved {{RUNS}} runs")
'''


def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    print(f"  {path.relative_to(ROOT)}")


def main() -> int:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    (SANDBOX / "recordings").mkdir(parents=True)

    print("writing sandbox:")
    write(SANDBOX / "agent.py", AGENT)
    write(SANDBOX / "test_refund.py", TEST)
    write(SANDBOX / "prompt.txt", PROMPT)
    write(SANDBOX / "change_prompt.py", CHANGE_PROMPT)
    write(SANDBOX / "_seed.py", SEED.format(runs=RUNS))

    print(f"\nrecording {RUNS} runs...")
    result = subprocess.run(
        [sys.executable, "_seed.py"],
        cwd=SANDBOX,
        capture_output=True,
        text=True,
    )
    print(result.stdout.strip() or result.stderr.strip())
    if result.returncode != 0:
        print("\nseeding failed; the tapes will not have a corpus to film.")
        return 1

    print(f"\nsandbox ready at {SANDBOX.relative_to(ROOT)}")
    print("  vhs demo/tapes/drift-catch.tape")
    print("  vhs demo/tapes/drift-learn.tape")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
