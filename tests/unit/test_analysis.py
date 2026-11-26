"""Redaction, trajectory diffing, determinism scoring and bisect attribution."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from driftcatch.bisect import bisect
from driftcatch.bisect import render as render_bisect
from driftcatch.config import Config
from driftcatch.determinism import analyse as analyse_determinism
from driftcatch.determinism import live_allowed
from driftcatch.determinism import render as render_determinism
from driftcatch.errors import RedactionError
from driftcatch.recording.schema import (
    LLMEvent,
    LLMRequest,
    LLMResponse,
    Outcome,
    Recording,
    Status,
    ToolEvent,
    Trajectory,
)
from driftcatch.redact import REDACTED, assert_clean, find_secrets, redact
from driftcatch.trajectory import build, diff, render

# ------------------------------------------------------------- redaction


def test_redaction_strips_denied_headers() -> None:
    cleaned, paths = redact({"headers": {"Authorization": "Bearer abc"}})
    assert cleaned["headers"]["Authorization"] == REDACTED
    assert "headers.Authorization" in paths


def test_redaction_strips_credential_patterns_anywhere() -> None:
    cleaned, paths = redact({"messages": [{"content": "my key is sk-abcdefghijklmnop12345"}]})
    assert REDACTED in cleaned["messages"][0]["content"]
    assert paths


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijklmnop12345",
        "sk-ant-abcdefghijklmnop12345",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_abcdefghijklmnopqrstuvwxyz12",
        "AIzaSyAbcdefghijklmnopqrstuvwxyz12345678",
    ],
)
def test_known_credential_shapes_are_detected(secret: str) -> None:
    assert find_secrets({"v": secret}) == ["v"]


def test_assert_clean_refuses_surviving_secrets() -> None:
    """Fail closed: a recording with a live key must never be written."""
    with pytest.raises(RedactionError, match="refusing to write"):
        assert_clean({"token": "sk-abcdefghijklmnop12345"})


def test_assert_clean_passes_for_redacted_payloads() -> None:
    cleaned, _ = redact({"api_key": "sk-abcdefghijklmnop12345"})
    assert_clean(cleaned)


def test_disabling_redactors_is_explicit() -> None:
    payload = {"headers": {"authorization": "Bearer abc"}}
    cleaned, paths = redact(payload, [])
    assert cleaned == payload
    assert paths == []


# ------------------------------------------------------------ trajectory


def _tool(index: int, name: str, args: dict[str, object], *, ok: bool = True) -> ToolEvent:
    return ToolEvent(
        index=index,
        name=name,
        args=args,
        result={"ok": True} if ok else None,
        error=None if ok else {"type": "TimeoutError", "message": "boom"},  # type: ignore[arg-type]
    )


def _llm(index: int, decided: list[str]) -> LLMEvent:
    return LLMEvent(
        index=index,
        request=LLMRequest(model="m"),
        response=LLMResponse(tool_calls=[{"name": n} for n in decided]),
    )


def test_build_links_tools_to_the_deciding_model_call() -> None:
    trajectory = build([_llm(0, ["a"]), _tool(1, "a", {"x": 1})])
    assert trajectory.tools_called == ["a"]
    assert trajectory.steps[1].parent == 0
    assert trajectory.terminal is Outcome.OK


def test_build_detects_parallel_groups() -> None:
    """Two tools between the same pair of model calls ran concurrently."""
    trajectory = build([_llm(0, ["a", "b"]), _tool(1, "a", {}), _tool(2, "b", {})])
    assert trajectory.parallel_groups == [[1, 2]]


def test_build_detects_retries() -> None:
    trajectory = build([_tool(0, "a", {"x": 1}, ok=False), _tool(1, "a", {"x": 1})])
    assert trajectory.retries == [1]


def test_build_marks_a_failing_final_step() -> None:
    trajectory = build([_tool(0, "a", {}, ok=False)])
    assert trajectory.terminal is Outcome.ERROR


@pytest.mark.parametrize("tolerance", ["tools", "tools+order", "tools+order+args", "exact"])
def test_identical_trajectories_match_at_every_tolerance(tolerance: str) -> None:
    events = [_llm(0, ["a"]), _tool(1, "a", {"x": 1})]
    t = build(events)
    assert diff(t, t, tolerance).matched


def test_tools_tolerance_ignores_order() -> None:
    first = build([_tool(0, "a", {}), _tool(1, "b", {})])
    second = build([_tool(0, "b", {}), _tool(1, "a", {})])
    assert diff(first, second, "tools").matched
    assert not diff(first, second, "tools+order").matched


def test_order_tolerance_reports_the_divergence_position() -> None:
    golden = build([_tool(0, "lookup_order", {}), _tool(1, "issue_refund", {})])
    current = build([_tool(0, "web_search", {}), _tool(1, "issue_refund", {})])
    result = diff(golden, current, "tools+order")
    assert not result.matched
    assert result.first_divergence == 0
    assert result.missing == ["lookup_order"]
    assert result.unexpected == ["web_search"]


def test_args_tolerance_catches_changed_arguments() -> None:
    golden = build([_tool(0, "a", {"x": 1})])
    current = build([_tool(0, "a", {"x": 2})])
    assert diff(golden, current, "tools+order").matched
    assert not diff(golden, current, "tools+order+args").matched


def test_exact_tolerance_catches_a_changed_outcome() -> None:
    golden = build([_tool(0, "a", {})])
    current = build([_tool(0, "a", {}, ok=False)])
    assert diff(golden, current, "tools+order+args").matched
    assert not diff(golden, current, "exact").matched


def test_unknown_tolerance_is_rejected() -> None:
    t = Trajectory()
    with pytest.raises(ValueError, match="unknown tolerance"):
        diff(t, t, "nonsense")


def test_render_shows_both_paths_and_the_problem() -> None:
    golden = build([_tool(0, "lookup_order", {}), _tool(1, "issue_refund", {})])
    current = build([_tool(0, "web_search", {}), _tool(1, "issue_refund", {})])
    text = render(diff(golden, current, "tools+order"))
    assert "golden:" in text and "current:" in text
    assert 'expected "lookup_order", never called' in text
    assert 'unexpected "web_search"' in text


# ----------------------------------------------------------- determinism


def _trajectory(tools: list[str]) -> Trajectory:
    return build([_tool(i, name, {}) for i, name in enumerate(tools)])


def test_determinism_of_identical_runs_is_one() -> None:
    runs = [_trajectory(["a", "b"]) for _ in range(10)]
    report = analyse_determinism(runs)
    assert report.score == 1.0
    assert report.is_deterministic
    assert report.recommended_pass_k == (10, 10)


def test_determinism_groups_and_scores_mixed_runs() -> None:
    runs = [_trajectory(["a"]) for _ in range(17)] + [_trajectory(["b"]) for _ in range(3)]
    report = analyse_determinism(runs, golden=_trajectory(["a"]))
    assert report.runs == 20
    assert report.distinct == 2
    assert report.score == 0.85
    assert report.recommended_pass_k == (17, 20)
    assert report.golden_share == 0.85
    assert report.groups[0].matches_golden is True


def test_determinism_render_warns_when_golden_is_a_minority() -> None:
    runs = [_trajectory(["a"]) for _ in range(5)] + [_trajectory(["b"]) for _ in range(5)]
    text = render_determinism(analyse_determinism(runs, golden=_trajectory(["a"])))
    assert "DETERMINISM SCORE" in text
    assert "warning" in text


def test_determinism_of_no_runs_is_empty() -> None:
    assert analyse_determinism([]).runs == 0
    assert render_determinism(analyse_determinism([])) == "  no runs to analyse"


def test_live_is_refused_inside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live runs cost money; they never happen by accident in CI."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("DRIFTCATCH_ALLOW_LIVE", raising=False)
    assert live_allowed() is False
    monkeypatch.setenv("DRIFTCATCH_ALLOW_LIVE", "1")
    assert live_allowed() is True


# --------------------------------------------------------------- bisect


def _recording(name: str, *, prompt: str, model: str, tools: list[str]) -> Recording:
    from driftcatch.record import fingerprint_for

    events = [
        LLMEvent(
            index=0,
            request=LLMRequest(
                model=model,
                messages=[{"role": "system", "content": prompt}],
                tools=[{"name": t} for t in tools],
            ),
            response=LLMResponse(tool_calls=[{"name": tools[0]}]),
        ),
        _tool(1, tools[0], {}),
    ]
    return Recording(
        name=name,
        status=Status.APPROVED,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        fingerprint=fingerprint_for(events, Config()),
        events=events,
        trajectory=build(events),
    )


def test_bisect_attributes_a_prompt_change_with_confidence() -> None:
    golden = _recording("g", prompt="Use available tools", model="m1", tools=["lookup_order"])
    candidate = _recording("c", prompt="Search the web", model="m1", tools=["web_search"])

    report = bisect(golden, candidate)
    assert report.behaviour_changed is True
    assert "prompt_hash" in report.changed_inputs
    assert report.prompt_diff


def test_bisect_is_confident_when_exactly_one_input_moved() -> None:
    golden = _recording("g", prompt="same", model="m1", tools=["a"])
    candidate = _recording("c", prompt="same", model="m2", tools=["a"])

    report = bisect(golden, candidate)
    assert report.changed_inputs == ["model_id"]
    assert report.confident is True
    assert report.root_cause == "the model version"
    assert "ROOT CAUSE" in render_bisect(report)


def test_bisect_declines_to_guess_when_several_inputs_moved() -> None:
    golden = _recording("g", prompt="one", model="m1", tools=["a"])
    candidate = _recording("c", prompt="two", model="m2", tools=["a"])

    report = bisect(golden, candidate)
    assert len(report.changed_inputs) == 2
    assert report.confident is False
    assert "not isolated" in render_bisect(report)


def test_bisect_blames_non_determinism_when_no_input_changed() -> None:
    """Same inputs, different behaviour: that is the model, not a code change."""
    golden = _recording("g", prompt="same", model="m1", tools=["a"])
    candidate = _recording("c", prompt="same", model="m1", tools=["a"])
    candidate.trajectory = build([_tool(0, "b", {})])

    report = bisect(golden, candidate)
    assert report.behaviour_changed is True
    assert report.changed_inputs == []
    text = render_bisect(report)
    assert "non-determinism" in text
    assert "drift determinism" in text
