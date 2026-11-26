"""The demo: a one-word prompt change silently breaks tool use, caught in CI.

Run it:

    pytest examples/refund_agent -q                 # records, then replays
    drift review --yes -d examples/refund_agent/recordings
    pytest examples/refund_agent -q                 # free, offline, deterministic
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import driftcatch as drift
from driftcatch.errors import NoMatchError
from examples.refund_agent.agent import refund_agent

RECORDINGS = Path(__file__).parent / "recordings"

# The change a teammate makes in a hurry. It reads fine. It is not fine.
REWORDED_PROMPT = "Search for the order information, then refund it."


@pytest.fixture(autouse=True)
def _recordings_dir() -> Iterator[None]:
    drift.reset_config()
    drift.config(recordings_dir=RECORDINGS, require_approved=False)
    yield
    drift.reset_config()


def test_refund_uses_the_database_not_web_search() -> None:
    """Tier 1: free, deterministic, runs on every commit."""
    with drift.take("refund_1182") as take:
        result = refund_agent("refund order 1182")

    assert "Refunded $40" in result
    take.assert_tools().called("lookup_order").never("web_search")
    take.assert_tools().called_in_order("lookup_order", "issue_refund")
    take.assert_cost().under(tokens=8_000)


def test_reworded_prompt_is_caught_and_explained() -> None:
    """The headline: the regression is caught, and DriftCatch names the cause.

    Skips until the golden exists, so a fresh clone records first and then this
    assertion has something to compare against.
    """
    if not (RECORDINGS / "refund_1182.json").exists():
        pytest.skip("record the golden first by running the test above")

    with pytest.raises(NoMatchError) as excinfo, drift.take("refund_1182") as take:
        refund_agent("refund order 1182", system_prompt=REWORDED_PROMPT)

    message = str(excinfo.value)
    assert "messages[0].content" in message  # it points at the prompt
    assert "Search for the order" in message  # it shows the new wording
    assert take.changed_inputs == ["prompt_hash"]  # and names the input that moved


def test_order_not_found_escalates() -> None:
    """Closing a coverage gap with what-if: no re-recording, no mocks."""
    if not (RECORDINGS / "refund_1182.json").exists():
        pytest.skip("record the golden first by running the first test")

    with drift.take("refund_1182").what_if(
        tool="lookup_order", returns={"id": 1182, "total": 40, "status": "paid"}
    ) as take:
        refund_agent("refund order 1182")

    take.assert_tools().called("lookup_order")
