"""Live mode must OBSERVE without SERVING.

A Tier-2 behaviour check runs the real model and compares the resulting
trajectory with the golden. That only works if live mode still captures events.

The original implementation skipped interception entirely in live mode, which
meant every live run produced an empty trajectory and `LiveResult.matches()`
could never exceed zero whenever the golden used tools - so Tier-2 silently did
nothing. The unit tests passed because they fed synthetic trajectories straight
into `analyse()` and never drove `run_live` end to end.

Live mode therefore has exactly three properties, each tested below:
  1. it makes real calls (never served from the recording);
  2. it captures events, so a trajectory exists to compare;
  3. it never writes, so the golden survives.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import driftcatch as drift
from driftcatch.recording.io import load_recording, recording_path

BASE = "https://live.test/v1"
MODEL = "live-model"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path) -> Iterator[None]:
    drift.reset_config()
    drift.config(recordings_dir=tmp_path / "recordings")
    os.environ["DRIFTCATCH_ALLOW_LIVE"] = "1"
    yield
    os.environ.pop("DRIFTCATCH_ALLOW_LIVE", None)
    drift.reset_config()


def body(tool: str | None = None, content: str | None = "done") -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool:
        message["tool_calls"] = [
            {
                "id": "call_live0000000001",
                "type": "function",
                "function": {"name": tool, "arguments": "{}"},
            }
        ]
    return {
        "id": "c",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool else "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


class Provider:
    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = replies or (body(),)
        self.calls = 0

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            index = min(self.calls, len(self.replies) - 1)
            self.calls += 1
            return httpx.Response(200, json=self.replies[index], request=request)

        return httpx.MockTransport(handler)


tool_runs = {"n": 0}


@drift.tool
def lookup() -> dict[str, int]:
    tool_runs["n"] += 1
    return {"id": 1}


@drift.tool
def other_tool() -> dict[str, int]:
    tool_runs["n"] += 1
    return {"id": 2}


DISPATCH = {"lookup": lookup, "other_tool": other_tool}


def agent(provider: Provider) -> None:
    """Dispatch on the tool the model actually chose.

    It matters that this is driven by the reply rather than hard-coded: an
    agent that always calls the same tool has a constant trajectory, which
    would make a behaviour-change test vacuously pass.
    """
    from openai import OpenAI

    http = OpenAI(
        base_url=BASE, api_key="k", http_client=httpx.Client(transport=provider.transport())
    )
    with http:
        completion = http.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": "go"}], temperature=0
        )
    calls = completion.choices[0].message.tool_calls or []
    for call in calls:
        DISPATCH[call.function.name]()


def record_golden() -> None:
    provider = Provider(body(tool="lookup"))
    with drift.take("scenario"):
        agent(provider)
    drift.approve("scenario", by="test")


# -------------------------------------------------- 1. live really calls


def test_live_mode_makes_real_calls() -> None:
    record_golden()
    provider = Provider(body(tool="lookup"))
    with drift.take("scenario", mode="live"):
        agent(provider)
    assert provider.calls == 1, "live mode must not be served from the recording"


def test_live_mode_runs_the_real_tool_body() -> None:
    record_golden()
    tool_runs["n"] = 0
    provider = Provider(body(tool="lookup"))
    with drift.take("scenario", mode="live"):
        agent(provider)
    assert tool_runs["n"] == 1, "live mode must execute the real tool"


# ------------------------------------------------- 2. live still observes


def test_live_mode_captures_llm_events() -> None:
    record_golden()
    provider = Provider(body(tool="lookup"))
    with drift.take("scenario", mode="live") as take:
        agent(provider)
    assert [e for e in take.events if e.type == "llm"], "no model call captured in live mode"


def test_live_mode_builds_a_trajectory() -> None:
    """The regression: an empty trajectory made Tier-2 unable to ever match."""
    record_golden()
    provider = Provider(body(tool="lookup"))
    with drift.take("scenario", mode="live") as take:
        agent(provider)
    assert take.trajectory.tools_called == ["lookup"]


def test_live_trajectory_matches_the_golden_when_behaviour_is_unchanged() -> None:
    record_golden()
    golden = load_recording(recording_path("scenario", drift.get_config().recordings_dir))

    provider = Provider(body(tool="lookup"))
    with drift.take("scenario", mode="live") as take:
        agent(provider)

    from driftcatch.trajectory import diff

    assert diff(golden.trajectory, take.trajectory, "tools+order").matched


# ---------------------------------------------------- 3. live never writes


def test_live_mode_does_not_overwrite_the_golden() -> None:
    record_golden()
    path = recording_path("scenario", drift.get_config().recordings_dir)
    before = path.read_bytes()

    provider = Provider(body(tool="lookup"))
    with drift.take("scenario", mode="live"):
        agent(provider)

    assert path.read_bytes() == before, "a live run must leave the golden untouched"
    assert load_recording(path).status is drift.Status.APPROVED


def test_live_mode_without_a_recording_writes_nothing() -> None:
    provider = Provider(body(tool="lookup"))
    with drift.take("brand_new", mode="live") as take:
        agent(provider)
    assert take.trajectory.tools_called == ["lookup"]
    assert not recording_path("brand_new", drift.get_config().recordings_dir).exists()


# ------------------------------------------------------- run_live, wired


def test_run_live_produces_matching_trajectories() -> None:
    """End-to-end Tier-2, the path that was never exercised before."""
    record_golden()

    def runner() -> None:
        agent(Provider(body(tool="lookup")))

    result = drift.run_live("scenario", runner, runs=3)
    assert result.report.runs == 3
    assert result.matches() == 3, "identical behaviour should match on every run"
    assert result.report.score == 1.0
    result.assert_matches_golden(pass_k=(3, 3))


def test_run_live_detects_changed_behaviour() -> None:
    record_golden()

    def runner() -> None:
        # The model now picks a different tool.
        agent(Provider(body(tool="other_tool")))

    result = drift.run_live("scenario", runner, runs=3)
    assert result.matches() == 0
    with pytest.raises(AssertionError, match="live behaviour check failed"):
        result.assert_matches_golden(pass_k=(2, 3))


def test_run_live_scores_a_mixed_outcome() -> None:
    record_golden()
    sequence = [True, True, False]
    state = {"i": 0}

    def runner() -> None:
        same = sequence[state["i"] % len(sequence)]
        state["i"] += 1
        agent(Provider(body(tool="lookup" if same else "other_tool")))

    result = drift.run_live("scenario", runner, runs=3)
    assert result.matches() == 2
    assert result.report.distinct == 2
    assert result.report.recommended_pass_k == (2, 3)
