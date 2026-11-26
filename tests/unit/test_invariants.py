"""Invariant mining.

The mining itself is the easy half. The tests that matter are the ones proving
it *refuses* to report nonsense: a rule learned from too little evidence, or a
constant dressed up as a relationship, would be worse than no rule at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from driftcatch.invariants import (
    DEFAULT_MIN_SUPPORT,
    Invariant,
    InvariantKind,
    check,
    invariants_path,
    load_invariants,
    merge,
    mine,
    render,
    render_violations,
    save_invariants,
)
from driftcatch.recording.schema import (
    LLMEvent,
    LLMRequest,
    LLMResponse,
    Recording,
    Status,
    ToolEvent,
)
from driftcatch.trajectory import build


def rec(
    name: str,
    tools: list[tuple[str, dict[str, Any], Any]],
    *,
    declared: list[str] | None = None,
    approved: bool = True,
) -> Recording:
    """A recording with one model call that declares tools, then tool calls."""
    events: list[Any] = [
        LLMEvent(
            index=0,
            request=LLMRequest(
                model="m",
                messages=[{"role": "system", "content": "do the thing"}],
                tools=[{"name": t} for t in (declared or [t for t, _, _ in tools])],
            ),
            response=LLMResponse(tool_calls=[{"name": t} for t, _, _ in tools]),
        )
    ]
    for index, (tool_name, args, result) in enumerate(tools, start=1):
        events.append(ToolEvent(index=index, name=tool_name, args=args, result=result))

    return Recording(
        name=name,
        status=Status.APPROVED if approved else Status.PENDING,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        events=events,
        trajectory=build(events),
    )


def happy(name: str, order_id: int, total: int) -> Recording:
    """The canonical shape: look up, then refund the amount you looked up."""
    return rec(
        name,
        [
            ("lookup_order", {"order_id": order_id}, {"id": order_id, "total": total}),
            ("issue_refund", {"order_id": order_id, "amount": total}, {"ok": True}),
        ],
        declared=["lookup_order", "issue_refund", "web_search"],
    )


def corpus(n: int = 6) -> list[Recording]:
    return [happy(f"r{i}", 1000 + i, 10 + i * 5) for i in range(n)]


def kinds(invariants: list[Invariant]) -> set[tuple[str, str, str | None]]:
    return {(i.kind.value, i.subject, i.other) for i in invariants}


# ------------------------------------------------------- refuses nonsense


def test_mines_nothing_from_too_few_recordings() -> None:
    """Two observations are a coincidence, not a rule."""
    assert mine(corpus(2)) == []


def test_ignores_unapproved_recordings() -> None:
    """Learning from unapproved runs would launder a bug into a specification."""
    unapproved = [
        rec(f"r{i}", [("a", {}, 1)], approved=False) for i in range(DEFAULT_MIN_SUPPORT + 2)
    ]
    assert mine(unapproved) == []


def test_constant_values_are_not_reported_as_relationships() -> None:
    """If two fields are 40 in every run, 'a equals b' is true and meaningless."""
    fixed = [
        rec(
            f"r{i}",
            [("lookup_order", {"id": 1}, {"total": 40}), ("issue_refund", {"amount": 40}, {})],
        )
        for i in range(6)
    ]
    found = mine(fixed)
    assert not [i for i in found if i.kind is InvariantKind.DATA_EQUALITY]


def test_a_rule_broken_even_once_is_not_mined() -> None:
    runs = corpus(5)
    runs.append(
        rec(
            "rogue",
            [
                ("issue_refund", {"order_id": 1, "amount": 5}, {"ok": True}),
                ("lookup_order", {"order_id": 1}, {"id": 1, "total": 5}),
            ],
            declared=["lookup_order", "issue_refund", "web_search"],
        )
    )
    found = mine(runs)
    assert ("always_precedes", "lookup_order", "issue_refund") not in kinds(found)


def test_confidence_rises_with_evidence() -> None:
    assert Invariant(kind=InvariantKind.ALWAYS_CALLED, subject="a", support=1).confidence == 0.5
    assert Invariant(
        kind=InvariantKind.ALWAYS_CALLED, subject="a", support=5
    ).confidence == pytest.approx(0.96875)
    assert Invariant(kind=InvariantKind.ALWAYS_CALLED, subject="a", support=0).confidence == 0.0


# ------------------------------------------------------------ what it finds


def test_mines_ordering_rule() -> None:
    assert ("always_precedes", "lookup_order", "issue_refund") in kinds(mine(corpus()))


def test_mines_always_called() -> None:
    assert ("always_called", "lookup_order", None) in kinds(mine(corpus()))


def test_mines_never_called_for_a_declared_but_unused_tool() -> None:
    """web_search is offered to the model every time and never chosen."""
    assert ("never_called", "web_search", None) in kinds(mine(corpus()))


def test_mines_always_terminal() -> None:
    assert ("always_terminal", "issue_refund", None) in kinds(mine(corpus()))


def test_mines_a_call_bound() -> None:
    bounds = [i for i in mine(corpus()) if i.kind is InvariantKind.MAX_TOOL_CALLS]
    assert bounds and bounds[0].value == 2


def test_mines_the_data_relationship_nobody_wrote_down() -> None:
    """The showstopper: refund amount always equals the looked-up total."""
    found = [i for i in mine(corpus()) if i.kind is InvariantKind.DATA_EQUALITY]
    pairs = {(i.subject, i.other) for i in found}
    assert ("issue_refund.args.amount", "lookup_order.result.total") in pairs or (
        "lookup_order.result.total",
        "issue_refund.args.amount",
    ) in pairs


def test_every_mined_invariant_has_a_human_readable_description() -> None:
    for invariant in mine(corpus()):
        assert invariant.description
        assert invariant.key


def test_mined_invariants_start_unapproved() -> None:
    """Nothing is enforced until a human says so."""
    assert all(not i.approved for i in mine(corpus()))


# --------------------------------------------------------------- checking


def _approved(invariants: list[Invariant]) -> list[Invariant]:
    for invariant in invariants:
        invariant.approved = True
    return invariants


def test_a_conforming_run_breaks_nothing() -> None:
    learned = _approved(mine(corpus()))
    good = happy("new", 2000, 99)
    assert check(good.trajectory, good.events, learned) == []


def test_catches_a_reversed_order() -> None:
    learned = _approved(mine(corpus()))
    bad = rec(
        "bad",
        [
            ("issue_refund", {"order_id": 1, "amount": 7}, {"ok": True}),
            ("lookup_order", {"order_id": 1}, {"id": 1, "total": 7}),
        ],
    )
    violations = check(bad.trajectory, bad.events, learned)
    assert any(v.invariant.kind is InvariantKind.ALWAYS_PRECEDES for v in violations)


def test_catches_a_tool_used_for_the_first_time() -> None:
    """The scenario nobody wrote a test for - guarded anyway."""
    learned = _approved(mine(corpus()))
    bad = rec("bad", [("web_search", {"q": "x"}, {"hits": 0})])
    violations = check(bad.trajectory, bad.events, learned)
    assert any(v.invariant.kind is InvariantKind.NEVER_CALLED for v in violations)


def test_catches_a_broken_data_relationship() -> None:
    """A refund for the wrong amount - caught with no hand-written assertion."""
    learned = _approved(mine(corpus()))
    bad = rec(
        "bad",
        [
            ("lookup_order", {"order_id": 1}, {"id": 1, "total": 40}),
            ("issue_refund", {"order_id": 1, "amount": 4000}, {"ok": True}),
        ],
    )
    violations = check(bad.trajectory, bad.events, learned)
    assert any(v.invariant.kind is InvariantKind.DATA_EQUALITY for v in violations)
    assert "4000" in " ".join(v.detail for v in violations)


def test_catches_exceeding_the_call_bound() -> None:
    learned = _approved(mine(corpus()))
    bad = rec(
        "bad",
        [
            ("lookup_order", {"order_id": 1}, {"id": 1, "total": 5}),
            ("lookup_order", {"order_id": 1}, {"id": 1, "total": 5}),
            ("issue_refund", {"order_id": 1, "amount": 5}, {"ok": True}),
            ("issue_refund", {"order_id": 1, "amount": 5}, {"ok": True}),
        ],
    )
    violations = check(bad.trajectory, bad.events, learned)
    assert any(v.invariant.kind is InvariantKind.MAX_TOOL_CALLS for v in violations)


def test_unapproved_invariants_are_not_enforced() -> None:
    learned = mine(corpus())  # left unapproved
    bad = rec("bad", [("web_search", {"q": "x"}, {})])
    assert check(bad.trajectory, bad.events, learned) == []
    assert check(bad.trajectory, bad.events, learned, approved_only=False)


# ---------------------------------------------------------------- storage


def test_round_trip(tmp_path: Path) -> None:
    learned = _approved(mine(corpus()))
    path = invariants_path(tmp_path)
    save_invariants(learned, path)
    assert [i.key for i in load_invariants(path)] == [i.key for i in learned]


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_invariants(invariants_path(tmp_path)) == []


def test_merge_preserves_approval_across_remining() -> None:
    """Re-mining must not silently un-enforce rules you already approved."""
    first = _approved(mine(corpus()))
    second = merge(mine(corpus(7)), first)
    carried = [i for i in second if i.approved]
    assert carried, "approvals should survive a re-mine"


# -------------------------------------------------------------- rendering


def test_render_explains_what_to_do_with_no_corpus() -> None:
    assert "at least" in render([])


def test_render_lists_rules_and_prompts_for_approval() -> None:
    text = render(mine(corpus()), corpus=6)
    assert "always runs before" in text
    assert "drift learn --approve" in text


def test_render_violations_cites_prior_evidence() -> None:
    learned = _approved(mine(corpus()))
    bad = rec("bad", [("web_search", {"q": "x"}, {})])
    text = render_violations(check(bad.trajectory, bad.events, learned))
    assert "previous runs" in text


def test_render_violations_when_clean() -> None:
    assert "hold" in render_violations([])
