"""Scenario suites.

The suite's whole value is that the policy in ``check`` reaches every case,
including the one somebody adds next month. These tests pin that, plus the
pytest-integration details that would otherwise fail in confusing ways: fixture
resolution, case ids, and one recording per case.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import driftcatch as drift
from driftcatch.errors import ConfigError, InvalidRecordingName
from driftcatch.suite import Suite

BASE = "https://suite.test/v1"
MODEL = "suite-model"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path) -> Iterator[None]:
    drift.reset_config()
    drift.config(recordings_dir=tmp_path / "recordings")
    yield
    drift.reset_config()


def agent(question: str) -> str:
    from openai import OpenAI

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "c",
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": f"answered: {question}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 3},
            },
        )

    http = OpenAI(
        base_url=BASE,
        api_key="k",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with http:
        reply = http.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": question}], temperature=0
        )
    return reply.choices[0].message.content or ""


# ------------------------------------------------- construction errors


def test_an_empty_suite_is_refused() -> None:
    """Zero cases collects zero tests and reports green, which is
    indistinguishable from a suite that actually passed."""
    with pytest.raises(ConfigError, match="no cases"):
        Suite("empty", {})


def test_an_unusable_case_name_is_caught_at_construction() -> None:
    """Not on the twelfth case of a live run."""
    with pytest.raises(InvalidRecordingName):
        Suite("ok", {"../escape": "x"})


def test_an_unusable_suite_name_is_caught() -> None:
    with pytest.raises(InvalidRecordingName):
        Suite("../bad", {"a": "x"})


def test_a_body_without_take_and_case_is_refused() -> None:
    suite = Suite("sig", {"a": "x"})
    with pytest.raises(ConfigError, match="must accept"):

        @suite.parametrize
        def _body(only_one: Any) -> None:  # pragma: no cover - never runs
            pass


def test_recording_names_are_derived_from_the_case() -> None:
    suite = Suite("refund", {"small": 1, "large": 2})
    assert suite.names == ["refund_small", "refund_large"]
    assert suite.recording_name("small") == "refund_small"
    assert "2 cases" in repr(suite)


# ------------------------------------------------------ the real thing

prompts = Suite(
    "prompts",
    {
        "greeting": "say hello",
        "farewell": "say goodbye",
    },
    mode="record",
)


@prompts.parametrize
def test_each_case_runs_with_its_own_take(take: drift.Take, case: str) -> None:
    reply = agent(case)
    assert reply == f"answered: {case}"
    take.assert_output().contains(case)


def test_each_case_wrote_its_own_recording(tmp_path: Path) -> None:
    """Run the suite body by hand so this assertion can inspect the result.

    The parametrized test above proves the body runs; this proves the goldens
    stay separate, which is what keeps a diff pointing at one scenario.
    """
    drift.config(recordings_dir=tmp_path / "sep")
    suite = Suite("sep", {"a": "say a", "b": "say b"}, mode="record")

    for case, prompt in suite.cases.items():
        with drift.take(suite.recording_name(case), mode="record"):
            agent(prompt)

    names = {r.name for r in drift.list_recordings(tmp_path / "sep")}
    assert names == {"sep_a", "sep_b"}


# ------------------------------------------------- the shared policy


def test_the_check_runs_for_every_case(tmp_path: Path) -> None:
    """The policy reaches every case, including the one added next month.

    Both cases are driven from inside this one test rather than from a
    parametrized body plus a separate assertion. Collecting results in module
    state and checking them in a later test looks fine serially and breaks
    under ``pytest -n``, where the two cases land in different workers and the
    list is empty in both.
    """
    drift.config(recordings_dir=tmp_path / "policed")
    checked: list[str] = []

    suite = Suite(
        "policed",
        {"one": "say one", "two": "say two"},
        mode="record",
        check=lambda take: checked.append(take.name),
    )

    @suite.parametrize
    def body(take: drift.Take, case: str) -> None:
        agent(case)

    for case in suite.cases:
        body(**{"_driftcatch_case": case})

    assert checked == ["policed_one", "policed_two"]


policed = Suite(
    "policed_collected",
    {"one": "say one", "two": "say two"},
    mode="record",
)


@policed.parametrize
def test_a_suite_body_collects_as_one_test_per_case(take: drift.Take, case: str) -> None:
    """The collection path itself, exercised by pytest rather than by hand."""
    agent(case)
    take.assert_output().not_empty()


def test_a_failing_check_fails_the_case(tmp_path: Path) -> None:
    """The policy has to be able to fail, or it is decoration."""
    drift.config(recordings_dir=tmp_path / "fail")

    def refuse(take: drift.Take) -> None:
        take.assert_output().contains("this is never present")

    suite = Suite("failing", {"a": "say a"}, mode="record", check=refuse)

    @suite.parametrize
    def body(take: drift.Take, case: str) -> None:
        agent(case)

    with pytest.raises(AssertionError, match="expected output to contain"):
        body(**{"_driftcatch_case": "a"})


def test_the_check_sees_a_finished_trajectory(tmp_path: Path) -> None:
    """A check that ran inside the take would assert against a half-built run."""
    drift.config(recordings_dir=tmp_path / "fin")
    seen: list[int] = []

    suite = Suite(
        "finished",
        {"a": "say a"},
        mode="record",
        check=lambda take: seen.append(len(take.events)),
    )

    @suite.parametrize
    def body(take: drift.Take, case: str) -> None:
        agent(case)

    body(**{"_driftcatch_case": "a"})
    assert seen == [1], "the check must see the completed event list"


# -------------------------------------------------- pytest integration


def test_extra_fixtures_still_resolve(tmp_path: Path) -> None:
    """A body may declare ordinary fixtures after (take, case).

    The wrapper rebuilds the signature by hand, so this is the test that
    catches it rebuilding the signature wrongly.
    """
    import inspect

    suite = Suite("fx", {"a": "say a"}, mode="record")

    @suite.parametrize
    def body(take: drift.Take, case: str, capsys: Any) -> None:  # pragma: no cover
        pass

    params = inspect.signature(body).parameters
    assert "_driftcatch_case" in params
    assert "capsys" in params, "a declared fixture was dropped from the signature"
    assert "take" not in params, "the take must not leak out as a fixture request"
    assert "case" not in params


def test_the_body_keeps_its_name_for_test_reporting() -> None:
    suite = Suite("named", {"a": "x"}, mode="record")

    @suite.parametrize
    def test_something_specific(take: drift.Take, case: str) -> None:  # pragma: no cover
        pass

    assert test_something_specific.__name__ == "test_something_specific"


def test_case_ids_are_the_case_names() -> None:
    suite = Suite("ids", {"alpha": 1, "beta": 2}, mode="record")

    @suite.parametrize
    def body(take: drift.Take, case: Any) -> None:  # pragma: no cover
        pass

    marks = [m for m in body.pytestmark if m.name == "parametrize"]
    assert marks, "the body was not parametrized"
    assert marks[0].kwargs["ids"] == ["alpha", "beta"]


def test_a_case_payload_can_be_any_shape(tmp_path: Path) -> None:
    """The table is passed through untouched; only the body knows what a case is."""
    drift.config(recordings_dir=tmp_path / "shape")
    received: list[Any] = []

    suite = Suite("shape", {"dict_case": {"q": "hello", "retries": 2}}, mode="record")

    @suite.parametrize
    def body(take: drift.Take, case: dict[str, Any]) -> None:
        received.append(case)
        agent(case["q"])

    body(**{"_driftcatch_case": "dict_case"})
    assert received == [{"q": "hello", "retries": 2}]
