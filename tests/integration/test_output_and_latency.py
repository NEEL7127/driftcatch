"""Output assertions and latency capture.

Latency has a trap that these tests pin down: a replayed run serves bytes from
disk, so the recorded durations describe the *original* call. Checking them
during replay would pass unconditionally and look like a verified performance
guarantee. The assertion is skipped and warned instead, and that behaviour is
tested here rather than left to documentation.
"""

from __future__ import annotations

import json
import time
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import driftcatch as drift
from driftcatch.errors import LatencyNotMeasured

BASE = "https://lat.test/v1"
MODEL = "lat-model"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path) -> Iterator[None]:
    drift.reset_config()
    drift.config(recordings_dir=tmp_path / "recordings")
    yield
    drift.reset_config()


def reply(content: str = "ok") -> dict[str, Any]:
    return {
        "id": "c",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5},
    }


def call(content: str = "ok", *, delay: float = 0.0) -> Any:
    from openai import OpenAI

    def handler(_: httpx.Request) -> httpx.Response:
        if delay:
            time.sleep(delay)
        return httpx.Response(200, json=reply(content))

    http = OpenAI(
        base_url=BASE,
        api_key="k",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with http:
        return http.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": "hi"}], temperature=0
        )


# ------------------------------------------------------------- output


def test_output_reads_the_final_reply() -> None:
    with drift.take("out", mode="record") as take:
        call("the answer is 42")
    assert take.output == "the answer is 42"
    take.assert_output().not_empty().contains("42").excludes("error")


def test_output_assertions_fail_on_a_mismatch() -> None:
    with drift.take("out2", mode="record") as take:
        call("hello world")
    with pytest.raises(AssertionError, match="expected output to contain"):
        take.assert_output().contains("goodbye")
    with pytest.raises(AssertionError, match="expected output not to contain"):
        take.assert_output().excludes("hello")


def test_output_case_insensitive_matching() -> None:
    with drift.take("out3", mode="record") as take:
        call("Refund Approved")
    take.assert_output().contains("refund approved", case_sensitive=False)
    with pytest.raises(AssertionError):
        take.assert_output().contains("refund approved")


def test_output_regex_and_equality() -> None:
    with drift.take("out4", mode="record") as take:
        call("order 1182 shipped")
    take.assert_output().matches(r"order \d+ shipped").equals("order 1182 shipped")


def test_output_json_shape() -> None:
    payload = json.dumps({"decision": "refund", "amount": 20})
    with drift.take("out5", mode="record") as take:
        call(payload)
    take.assert_output().is_json().json_has("decision", "amount")
    with pytest.raises(AssertionError, match="missing"):
        take.assert_output().json_has("decision", "currency")


def test_output_json_rejects_prose() -> None:
    with drift.take("out6", mode="record") as take:
        call("sure, here you go!")
    with pytest.raises(AssertionError, match="not valid JSON"):
        take.assert_output().is_json()


def test_output_satisfies_a_custom_predicate() -> None:
    with drift.take("out7", mode="record") as take:
        call("a short answer")
    take.assert_output().satisfies(lambda text: len(text) < 100, description="is concise")
    with pytest.raises(AssertionError, match="is concise"):
        take.assert_output().satisfies(lambda text: len(text) > 100, description="is concise")


def test_outputs_collects_every_turn() -> None:
    with drift.take("out8", mode="record") as take:
        call("first")
        call("second")
    assert take.outputs == ["first", "second"]
    assert take.output == "second", "output is the final reply"


def test_empty_output_is_reported_as_empty_not_crashing() -> None:
    with drift.take("out9", mode="record") as take:
        pass
    assert take.output == ""
    with pytest.raises(AssertionError, match="empty response"):
        take.assert_output().not_empty()


# ------------------------------------------------------------ latency


def test_recording_captures_per_event_latency() -> None:
    with drift.take("lat", mode="record") as take:
        call(delay=0.05)
    measured = take.latencies
    assert len(measured) == 1, measured
    label, ms = measured[0]
    assert label == "llm[0]"
    assert ms >= 40, f"a 50ms call was measured as {ms}ms"
    assert take.latency_ms == ms


def test_tool_latency_is_captured() -> None:
    @drift.tool
    def slow_lookup() -> int:
        time.sleep(0.05)
        return 1

    with drift.take("lat_tool", mode="record") as take:
        slow_lookup()
    measured = dict(take.latencies)
    assert "slow_lookup" in measured
    assert measured["slow_lookup"] >= 40


def test_latency_assertion_passes_under_budget() -> None:
    with drift.take("lat2", mode="record") as take:
        call()
    take.assert_latency().under(total_ms=10_000, per_call_ms=10_000)


def test_latency_assertion_fails_over_budget() -> None:
    with drift.take("lat3", mode="record") as take:
        call(delay=0.05)
    with pytest.raises(AssertionError, match="expected the run under"):
        take.assert_latency().under(total_ms=1)
    with pytest.raises(AssertionError, match="expected every call under"):
        take.assert_latency().under(per_call_ms=1)


def test_latency_is_skipped_and_warned_during_replay() -> None:
    """The headline property: a replayed run must not report a performance pass.

    Durations on a replayed event came off disk, so checking them would compare
    a recorded number against itself. The assertion no-ops and says so.
    """
    with drift.take("lat4", mode="record"):
        call(delay=0.05)
    drift.approve("lat4", by="test")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with drift.take("lat4", mode="replay") as take:
            call()
        # A budget of 0ms would fail on any real call. It passes here only
        # because the check was correctly skipped.
        take.assert_latency().under(total_ms=0, per_call_ms=0)

    skipped = [w for w in caught if issubclass(w.category, LatencyNotMeasured)]
    assert skipped, "replay must warn that latency was not checked"
    assert "no real call to time" in str(skipped[0].message)


def test_replayed_events_report_no_measured_latency() -> None:
    with drift.take("lat5", mode="record"):
        call(delay=0.05)
    drift.approve("lat5", by="test")

    with drift.take("lat5", mode="replay") as take:
        call()
    assert take.latencies == [], "a replayed step was never timed and must not claim to be"
    assert take.latency_ms == 0


def test_latency_survives_a_round_trip_through_the_recording() -> None:
    """The recorded duration is kept on disk, so `drift show` can report it."""
    from driftcatch.recording.io import load_recording, recording_path

    with drift.take("lat6", mode="record"):
        call(delay=0.05)

    loaded = load_recording(recording_path("lat6", drift.get_config().recordings_dir))
    assert loaded.events[0].duration_ms is not None
    assert loaded.events[0].duration_ms >= 40


def test_failed_call_still_records_its_duration() -> None:
    """A timeout is exactly the case where the duration matters most."""

    def handler(_: httpx.Request) -> httpx.Response:
        time.sleep(0.05)
        raise httpx.ConnectError("boom")

    def failing() -> Any:
        from openai import OpenAI

        http = OpenAI(
            base_url=BASE,
            api_key="k",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            max_retries=0,
        )
        with http:
            return http.chat.completions.create(
                model=MODEL, messages=[{"role": "user", "content": "hi"}]
            )

    from openai import APIConnectionError

    # Named rather than blind: the SDK wraps the transport error, and asserting
    # the specific type is what proves the recorder ran on the real failure path
    # instead of on some unrelated exception.
    with drift.take("lat7", mode="record") as take, pytest.raises(APIConnectionError):
        failing()

    assert take.latencies, "a failed call must still report how long it took to fail"
    assert take.latencies[0][1] >= 40
