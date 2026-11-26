"""Demo: the framework learns your agent's rules, then catches a break.

Run it:

    python scripts/demo_invariants.py

Nothing is mocked away: it builds a real corpus of approved recordings, mines
them, and checks two new runs against what was learned.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from typing import Any

from driftcatch.invariants import check, mine, render, render_violations
from driftcatch.recording.schema import (
    LLMEvent,
    LLMRequest,
    LLMResponse,
    Recording,
    Status,
    ToolEvent,
)
from driftcatch.trajectory import build

SYSTEM = "Look up the order, then refund exactly what it is worth."
# Every tool the agent is offered. It only ever chooses three of them.
DECLARED = ["lookup_order", "issue_refund", "notify_customer", "web_search", "escalate"]


def run(name: str, calls: list[tuple[str, dict[str, Any], Any]]) -> Recording:
    events: list[Any] = [
        LLMEvent(
            index=0,
            request=LLMRequest(
                model="gpt-5.2",
                messages=[{"role": "system", "content": SYSTEM}],
                tools=[{"name": t} for t in DECLARED],
            ),
            response=LLMResponse(tool_calls=[{"name": c[0]} for c in calls]),
        )
    ]
    for index, (tool, args, result) in enumerate(calls, start=1):
        events.append(ToolEvent(index=index, name=tool, args=args, result=result))
    return Recording(
        name=name,
        status=Status.APPROVED,
        created_at=datetime.now(UTC),
        events=events,
        trajectory=build(events),
    )


def refund_run(name: str, order_id: int, total: int) -> Recording:
    """The shape the agent has always followed. Note: amount == total."""
    return run(
        name,
        [
            ("lookup_order", {"order_id": order_id}, {"id": order_id, "total": total}),
            ("issue_refund", {"order_id": order_id, "amount": total}, {"ok": True}),
            ("notify_customer", {"order_id": order_id}, {"sent": True}),
        ],
    )


def main() -> None:
    with tempfile.TemporaryDirectory():
        # ---- a corpus of approved runs, each with DIFFERENT amounts --------
        corpus = [
            refund_run("refund_1182", 1182, 40),
            refund_run("refund_2301", 2301, 125),
            refund_run("refund_3044", 3044, 18),
            refund_run("refund_4417", 4417, 260),
            refund_run("refund_5590", 5590, 75),
            refund_run("refund_6612", 6612, 9),
        ]

        print("=" * 72)
        print("STEP 1  Six approved recordings. Nobody wrote a single rule.")
        print("=" * 72)
        for recording in corpus:
            print(f"  {recording.name}: {' -> '.join(recording.trajectory.tools_called)}")

        # ---- learn ---------------------------------------------------------
        learned = mine(corpus)
        print()
        print("=" * 72)
        print("STEP 2  drift learn  -- the framework reads them and infers rules")
        print("=" * 72)
        print(render(learned, corpus=len(corpus)))

        # ---- a human approves ---------------------------------------------
        for invariant in learned:
            invariant.approved = True
        print()
        print("=" * 72)
        print("STEP 3  A human approves them. Now they are enforced.")
        print("=" * 72)

        # ---- a new run that breaks a rule nobody wrote a test for ----------
        broken = run(
            "refund_7000",
            [
                ("lookup_order", {"order_id": 7000}, {"id": 7000, "total": 50}),
                # the decimal slipped: refunds 5000 instead of 50
                ("issue_refund", {"order_id": 7000, "amount": 5000}, {"ok": True}),
                ("notify_customer", {"order_id": 7000}, {"sent": True}),
            ],
        )
        print()
        print("  New run, trajectory looks completely normal:")
        print(f"    {' -> '.join(broken.trajectory.tools_called)}")
        print("  A trajectory diff would pass this. Watch:")
        print()
        print(render_violations(check(broken.trajectory, broken.events, learned)))

        # ---- a second break: a tool used for the first time ----------------
        novel = run(
            "refund_8000",
            [
                ("web_search", {"q": "order 8000"}, {"hits": 0}),
                ("issue_refund", {"order_id": 8000, "amount": 30}, {"ok": True}),
            ],
        )
        print()
        print("=" * 72)
        print("STEP 4  A different break: a tool the agent has never chosen")
        print("=" * 72)
        print(f"  {' -> '.join(novel.trajectory.tools_called)}")
        print()
        print(render_violations(check(novel.trajectory, novel.events, learned)))

        # ---- and a good run still passes ------------------------------------
        good = refund_run("refund_9000", 9000, 310)
        violations = check(good.trajectory, good.events, learned)
        print()
        print("=" * 72)
        print("STEP 5  A correct run with values never seen before")
        print("=" * 72)
        print(f"  {' -> '.join(good.trajectory.tools_called)}  (amount 310, new)")
        print(f"  {render_violations(violations)}")


if __name__ == "__main__":
    main()
