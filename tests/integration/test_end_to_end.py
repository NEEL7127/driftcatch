"""The whole loop, offline: record -> approve -> replay -> catch the regression.

This is the behaviour the README promises. If these fail, the product does not
work, whatever the unit tests say.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import driftcatch as drift
from driftcatch.errors import (
    DriftDetected,
    InvalidTransition,
    NoMatchError,
    RecordingNotFound,
)
from driftcatch.recording import Status, load_recording, recording_path
from tests.integration.fake_provider import (
    FakeProvider,
    chat_body,
    first_tool_call,
    post_chat,
    sse_stream,
)

SYSTEM = "Use available tools to find the order."
CHANGED_SYSTEM = "Search for the order information."


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path) -> Iterator[None]:
    """Every test gets its own recordings directory and clean config."""
    drift.reset_config()
    drift.config(recordings_dir=tmp_path / "recordings")
    yield
    drift.reset_config()


# ----------------------------------------------------------- the agent


def build_agent(provider: FakeProvider) -> Any:
    """A tiny refund agent: ask the model, call the tool it picks, reply."""
    calls: dict[str, int] = {"lookup_order": 0, "web_search": 0}

    @drift.tool
    def lookup_order(order_id: int) -> dict[str, Any]:
        calls["lookup_order"] += 1
        return {"id": order_id, "total": 40}

    @drift.tool
    def web_search(query: str) -> dict[str, Any]:
        calls["web_search"] += 1
        return {"hits": []}

    @drift.tool
    def issue_refund(order_id: int, amount: int) -> dict[str, Any]:
        return {"ok": True}

    def agent(system: str = SYSTEM) -> str:
        with provider.client() as client:
            body = post_chat(client, system=system)
        chosen = first_tool_call(body)
        if chosen == "web_search":
            web_search("order 1182")
            order = {"id": 1182, "total": 40}
        else:
            order = lookup_order(1182)
        issue_refund(order["id"], order["total"])
        return f"Refunded ${order['total']} to order {order['id']}"

    agent.calls = calls  # type: ignore[attr-defined]
    return agent


def good_provider() -> FakeProvider:
    return FakeProvider(
        [
            chat_body(tool_name="lookup_order", tool_args={"id": 1182}),
            chat_body(content="Refunded $40 to order 1182"),
        ]
    )


# --------------------------------------------------------------- record


def test_first_run_records_a_pending_recording() -> None:
    provider = good_provider()
    agent = build_agent(provider)

    with drift.take("refund") as take:
        result = agent()

    assert "Refunded $40" in result
    assert take.mode == "record"
    assert provider.call_count == 1

    path = recording_path("refund", drift.get_config().recordings_dir)
    recording = load_recording(path)
    assert recording.status is Status.PENDING, "nothing is golden until a human approves it"
    assert recording.trajectory.tools_called == ["lookup_order", "issue_refund"]
    assert recording.fingerprint is not None


def test_recorded_usage_is_normalised() -> None:
    provider = good_provider()
    agent = build_agent(provider)
    with drift.take("refund") as take:
        agent()
    assert take.usage.input_tokens == 100
    assert take.usage.output_tokens == 20


# --------------------------------------------------------------- replay


def _record_and_approve(name: str = "refund") -> FakeProvider:
    provider = good_provider()
    agent = build_agent(provider)
    with drift.take(name):
        agent()
    drift.approve(name, by="tester")
    return provider


def test_replay_makes_no_network_calls() -> None:
    """The core promise: replayed tests are free and offline."""
    _record_and_approve()

    provider = good_provider()
    agent = build_agent(provider)
    with drift.take("refund") as take:
        result = agent()

    assert take.mode == "replay"
    assert provider.call_count == 0, "replay must never reach the provider"
    assert "Refunded $40" in result
    take.assert_tools().called("lookup_order").never("web_search")


def test_replay_serves_recorded_tool_results_without_executing_them() -> None:
    _record_and_approve()

    provider = good_provider()
    agent = build_agent(provider)
    with drift.take("refund"):
        agent()

    assert agent.calls["lookup_order"] == 0, "the tool body must not run during replay"


def test_replay_matches_golden_trajectory() -> None:
    _record_and_approve()
    agent = build_agent(good_provider())
    with drift.take("refund") as take:
        agent()
    take.assert_trajectory().matches_golden()


def test_replay_without_a_recording_is_an_actionable_error() -> None:
    agent = build_agent(good_provider())
    with (
        pytest.raises(RecordingNotFound, match="no recording named"),
        drift.take("never_recorded", mode="replay"),
    ):
        agent()


# ------------------------------------------------ the headline regression


def test_prompt_change_is_caught_and_named() -> None:
    """The money shot: a reworded prompt is caught, and drift says which input moved."""
    _record_and_approve()

    agent = build_agent(good_provider())
    with pytest.raises(NoMatchError) as excinfo, drift.take("refund"):
        agent(system=CHANGED_SYSTEM)

    message = str(excinfo.value)
    assert "messages[0].content" in message
    assert CHANGED_SYSTEM in message
    # The hint must name commands that exist. This asserted `drift record
    # --update` for a while, which pytest rejects outright - an error message
    # that sends you to a failing command is worse than one that says nothing.
    assert "drift record" in message
    assert "drift approve" in message
    assert "--update" not in message


def test_prompt_change_sets_stale_and_names_the_component() -> None:
    _record_and_approve()
    agent = build_agent(good_provider())

    with pytest.raises(NoMatchError), drift.take("refund") as take:
        agent(system=CHANGED_SYSTEM)

    assert take.stale is True
    assert take.changed_inputs == ["prompt_hash"]


def test_drift_policy_fail_raises_before_matching() -> None:
    _record_and_approve()
    drift.config(recordings_dir=drift.get_config().recordings_dir, drift_policy="fail")
    agent = build_agent(good_provider())

    with pytest.raises(DriftDetected, match="prompt_hash changed"), drift.take("refund"):
        agent(system=CHANGED_SYSTEM)


def test_tool_switch_fails_the_behaviour_assertion() -> None:
    """A different tool choice must fail `never(...)`, not slip through."""
    _record_and_approve("switch")

    switched = FakeProvider(
        [
            chat_body(tool_name="web_search", tool_args={"q": "order 1182"}),
            chat_body(content="Refunded $40 to order 1182"),
        ]
    )
    agent = build_agent(switched)

    # Record a second, different behaviour under its own name.
    with drift.take("switch_after") as take:
        agent()

    with pytest.raises(AssertionError, match="web_search"):
        take.assert_tools().never("web_search")


# ---------------------------------------------------- errors and retries


def test_recorded_tool_failure_is_reraised_on_replay() -> None:
    """Retry paths only get tested if recorded failures come back as failures."""
    attempts = {"n": 0}

    @drift.tool
    def flaky(order_id: int) -> dict[str, Any]:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise TimeoutError("upstream timed out")
        return {"ok": True}

    def agent() -> str:
        try:
            flaky(1)
        except TimeoutError:
            flaky(1)
        return "done"

    with drift.take("retry"):
        agent()
    drift.approve("retry", by="tester")

    attempts["n"] = 0
    with drift.take("retry") as take:
        agent()

    assert attempts["n"] == 0, "tool bodies must not run on replay"
    assert take.trajectory.tools_called == ["flaky", "flaky"]
    outcomes = [step.outcome for step in take.trajectory.steps if step.kind == "tool"]
    assert outcomes[0] is drift.Outcome.ERROR
    assert outcomes[1] is drift.Outcome.OK


def test_same_request_twice_gets_each_recorded_reply() -> None:
    """Polling: identical request, pending then done."""
    provider = FakeProvider([chat_body(content="pending"), chat_body(content="done")])

    def agent() -> list[str | None]:
        out: list[str | None] = []
        with provider.client() as client:
            for _ in range(2):
                body = post_chat(client)
                out.append(body["choices"][0]["message"]["content"])
        return out

    with drift.take("poll"):
        recorded = agent()
    assert recorded == ["pending", "done"]
    drift.approve("poll", by="tester")

    provider.calls.clear()
    with drift.take("poll"):
        replayed = agent()
    assert replayed == ["pending", "done"]
    assert provider.call_count == 0


# -------------------------------------------------------------- streaming


def test_streaming_is_captured_and_replayed() -> None:
    provider = FakeProvider()
    provider.stream = sse_stream("Refunded forty dollars")

    def agent() -> str:
        with provider.client() as client:
            response = client.post(
                "https://fake.openai.test/v1/chat/completions",
                json={"model": "gpt-fake-1", "messages": [{"role": "user", "content": "hi"}]},
            )
            return response.text

    with drift.take("stream") as take:
        recorded_text = agent()

    event = take.events[0]
    assert event.stream_chunks, "chunks must be captured in order"
    assert "Refunded" in (event.response.content or "")
    drift.approve("stream", by="tester")

    provider.calls.clear()
    with drift.take("stream"):
        replayed_text = agent()

    assert replayed_text == recorded_text, "replay must reproduce the stream verbatim"
    assert provider.call_count == 0


# -------------------------------------------------------- approval gate


def test_approval_transitions_are_enforced() -> None:
    _record_and_approve("gate")
    with pytest.raises(InvalidTransition, match="can only move to"):
        drift.approve("gate", by="tester")


def test_require_approved_blocks_an_unapproved_recording() -> None:
    provider = good_provider()
    agent = build_agent(provider)
    with drift.take("ungated"):
        agent()

    drift.config(recordings_dir=drift.get_config().recordings_dir, require_approved=True)
    with (
        pytest.raises(drift.RecordingNotApproved, match="drift review"),
        drift.take("ungated", mode="replay"),
    ):
        agent()


# ------------------------------------------------------------- what-if


def test_what_if_exercises_an_untested_branch() -> None:
    """Close a coverage gap without re-recording or writing a mock."""
    _record_and_approve("whatif")

    agent = build_agent(good_provider())
    with (
        drift.take("whatif").what_if(tool="lookup_order", raises="TimeoutError") as take,
        pytest.raises(TimeoutError),
    ):
        agent()

    assert take.mode == "replay"


def test_what_if_leaves_the_recording_untouched() -> None:
    _record_and_approve("immutable")
    path = recording_path("immutable", drift.get_config().recordings_dir)
    before = path.read_bytes()

    agent = build_agent(good_provider())
    with drift.take("immutable").what_if(tool="lookup_order", returns={"id": 1, "total": 7}):
        agent()

    assert path.read_bytes() == before


def test_what_if_on_an_unknown_tool_is_a_clear_error() -> None:
    _record_and_approve("unknown_tool")
    agent = build_agent(good_provider())
    with (
        pytest.raises(drift.DriftCatchError, match="never calls tool"),
        drift.take("unknown_tool").what_if(tool="nope", returns={}),
    ):
        agent()


# ------------------------------------------------------------ coverage


def test_behaviour_coverage_reports_untested_tools() -> None:
    _record_and_approve("cov")
    recordings = drift.list_recordings(drift.get_config().recordings_dir)
    report = drift.analyse_coverage(recordings)

    assert report.tools["lookup_order"].covered is True
    assert report.tools["web_search"].covered is False
    assert report.tools["lookup_order"].error_path_covered is False
    assert any("web_search" in item for item in report.uncovered)
    assert 0 < report.percent < 100
