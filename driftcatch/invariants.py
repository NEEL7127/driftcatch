"""Invariant mining: learning the rules your agent already follows.

Instead of making a developer think of every rule and write it as an assertion,
this reads the corpus of **approved** recordings and infers the rules the agent
has always obeyed - then enforces them on every future run, including scenarios
nobody wrote a test for.

The technique is specification mining, which is twenty-five years old in
conventional software (Daikon, Perracotta) and, as far as I can establish, has
never been pointed at agent trajectories. Two lessons are taken directly from
that prior work:

* **Daikon's confidence gate.** A pattern seen twice is a coincidence; seen
  twenty times it is a rule. Confidence is ``1 - 0.5**support``, the probability
  that the observation is not chance, and anything below the limit is withheld.
  Daikon's own evaluation found roughly a third of mined invariants were wrong
  or irrelevant, which is exactly why nothing here is enforced until a human
  approves it.
* **Perracotta's fixed templates.** Mining is done against a closed set of
  patterns rather than open-ended inference, which keeps results explainable
  and the search bounded.

Two anti-spurious rules of our own:

* An invariant must have had the **opportunity to be violated** (``applicable``),
  so "always precedes" is only counted over runs where the consequent occurred.
* A data equality is only reported when the value **varies across runs**.
  ``refund.amount == order.total`` is a real rule; both being 40 in every
  recording is a constant, and constants masquerading as relationships are the
  easiest way to generate confident nonsense.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from driftcatch.recording.schema import Event, Outcome, Recording, ToolEvent, Trajectory

__all__ = [
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_MIN_SUPPORT",
    "Invariant",
    "InvariantKind",
    "Violation",
    "check",
    "invariants_path",
    "load_invariants",
    "merge",
    "mine",
    "render",
    "render_violations",
    "save_invariants",
]

#: A pattern seen fewer times than this is a coincidence, not a rule.
#: Set to agree with the confidence gate below: 1 - 0.5**5 ~= 0.969 clears 0.95,
#: while four observations give only 0.9375 and would be filtered anyway. The
#: two thresholds must agree or one of them is silently dead.
DEFAULT_MIN_SUPPORT = 5
#: Daikon's idea: report only what the evidence statistically justifies.
DEFAULT_MIN_CONFIDENCE = 0.95
#: Bound the pairwise search for data relationships.
_MAX_PATHS_PER_RUN = 60


class InvariantKind(StrEnum):
    """The closed set of patterns we mine (Perracotta's template approach)."""

    ALWAYS_PRECEDES = "always_precedes"
    ALWAYS_CALLED = "always_called"
    NEVER_CALLED = "never_called"
    ALWAYS_TERMINAL = "always_terminal"
    NEVER_CONSECUTIVE = "never_consecutive"
    MAX_TOOL_CALLS = "max_tool_calls"
    DATA_EQUALITY = "data_equality"


class Invariant(BaseModel):
    """A rule the agent has always followed, with the evidence for it."""

    kind: InvariantKind
    subject: str
    other: str | None = None
    value: int | None = None
    #: Runs that confirmed the rule.
    support: int = 0
    #: Runs in which the rule could have been violated but was not.
    applicable: int = 0
    approved: bool = False

    @property
    def confidence(self) -> float:
        """Daikon-style: the chance this is not a coincidence."""
        if self.support <= 0:
            return 0.0
        return 1.0 - math.pow(0.5, self.support)

    @property
    def key(self) -> str:
        """Stable identity, so approval survives re-mining."""
        return f"{self.kind.value}:{self.subject}:{self.other or ''}:{self.value or ''}"

    @property
    def description(self) -> str:
        """One line a human can judge without reading the code."""
        match self.kind:
            case InvariantKind.ALWAYS_PRECEDES:
                return f"{self.subject} always runs before {self.other}"
            case InvariantKind.ALWAYS_CALLED:
                return f"{self.subject} is called in every run"
            case InvariantKind.NEVER_CALLED:
                return f"{self.subject} is offered but never called"
            case InvariantKind.ALWAYS_TERMINAL:
                return f"{self.subject} is always the last tool"
            case InvariantKind.NEVER_CONSECUTIVE:
                return f"{self.subject} is never called twice in a row"
            case InvariantKind.MAX_TOOL_CALLS:
                return f"a run never makes more than {self.value} tool calls"
            case InvariantKind.DATA_EQUALITY:
                return f"{self.subject} always equals {self.other}"
        return self.key  # pragma: no cover


class Violation(BaseModel):
    """A rule the current run broke."""

    invariant: Invariant
    detail: str

    @property
    def message(self) -> str:
        return f"{self.invariant.description} - {self.detail}"


# --------------------------------------------------------------- helpers


def _tool_steps(trajectory: Trajectory) -> list[str]:
    return list(trajectory.tools_called)


def _tool_events(events: list[Event]) -> list[ToolEvent]:
    return [e for e in events if isinstance(e, ToolEvent)]


def _declared_tools(recording: Recording) -> set[str]:
    """Every tool the agent was offered, from the recorded request."""
    from driftcatch.recording.schema import LLMEvent

    names: set[str] = set()
    for event in recording.events:
        if isinstance(event, LLMEvent):
            for tool in event.request.tools:
                name = tool.get("name")
                if name:
                    names.add(str(name))
    return names


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    """Flatten nested data into scalar paths, for data-relationship mining."""
    if len(out) >= _MAX_PATHS_PER_RUN:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}", item, out)
    elif (
        isinstance(value, (int, float, str, bool))
        and not isinstance(value, bool)
        or isinstance(value, bool)
    ):
        out[prefix] = value


def _scalar_paths(events: list[Event]) -> dict[str, Any]:
    """`tool.arg.x` and `tool.result.y` paths with scalar values."""
    paths: dict[str, Any] = {}
    seen: dict[str, int] = defaultdict(int)
    for event in _tool_events(events):
        occurrence = seen[event.name]
        seen[event.name] += 1
        # Only the first call of each tool, so repeated calls do not create
        # ambiguous paths.
        if occurrence > 0:
            continue
        _flatten(f"{event.name}.args", event.args, paths)
        if event.result is not None:
            _flatten(f"{event.name}.result", event.result, paths)
    return paths


# ---------------------------------------------------------------- mining


def _mine_ordering(runs: list[list[str]]) -> list[Invariant]:
    """a always precedes b, counted only over runs where b actually occurred."""
    tools = sorted({t for run in runs for t in run})
    found: list[Invariant] = []

    for before in tools:
        for after in tools:
            if before == after:
                continue
            applicable = 0
            holds = 0
            for run in runs:
                if after not in run:
                    continue  # no opportunity to violate
                applicable += 1
                first_after = run.index(after)
                if before in run and run.index(before) < first_after:
                    holds += 1
            if applicable and holds == applicable:
                found.append(
                    Invariant(
                        kind=InvariantKind.ALWAYS_PRECEDES,
                        subject=before,
                        other=after,
                        support=holds,
                        applicable=applicable,
                    )
                )
    return found


def _mine_occurrence(runs: list[list[str]], declared: set[str]) -> list[Invariant]:
    total = len(runs)
    found: list[Invariant] = []
    called = {t for run in runs for t in run}

    for tool in sorted(called):
        hits = sum(1 for run in runs if tool in run)
        if hits == total:
            found.append(
                Invariant(
                    kind=InvariantKind.ALWAYS_CALLED,
                    subject=tool,
                    support=hits,
                    applicable=total,
                )
            )

    for tool in sorted(declared - called):
        found.append(
            Invariant(
                kind=InvariantKind.NEVER_CALLED,
                subject=tool,
                support=total,
                applicable=total,
            )
        )
    return found


def _mine_terminal(runs: list[list[str]]) -> list[Invariant]:
    found: list[Invariant] = []
    tools = sorted({t for run in runs for t in run})
    for tool in tools:
        applicable = sum(1 for run in runs if tool in run)
        holds = sum(1 for run in runs if run and run[-1] == tool and tool in run)
        # "always terminal" means: whenever it appears, it appears last.
        strict = sum(1 for run in runs if tool in run and run[-1] == tool)
        if applicable and strict == applicable:
            found.append(
                Invariant(
                    kind=InvariantKind.ALWAYS_TERMINAL,
                    subject=tool,
                    support=holds,
                    applicable=applicable,
                )
            )
    return found


def _mine_consecutive(runs: list[list[str]]) -> list[Invariant]:
    found: list[Invariant] = []
    for tool in sorted({t for run in runs for t in run}):
        applicable = sum(1 for run in runs if run.count(tool) >= 1)
        repeated = any(
            run[i] == tool and run[i + 1] == tool for run in runs for i in range(len(run) - 1)
        )
        if applicable and not repeated:
            found.append(
                Invariant(
                    kind=InvariantKind.NEVER_CONSECUTIVE,
                    subject=tool,
                    support=applicable,
                    applicable=applicable,
                )
            )
    return found


def _mine_bounds(runs: list[list[str]]) -> list[Invariant]:
    if not runs:
        return []
    longest = max(len(run) for run in runs)
    return [
        Invariant(
            kind=InvariantKind.MAX_TOOL_CALLS,
            subject="run",
            value=longest,
            support=len(runs),
            applicable=len(runs),
        )
    ]


def _mine_data_equality(per_run_paths: list[dict[str, Any]]) -> list[Invariant]:
    """Find `a == b` relationships whose value actually varies across runs.

    The variance requirement is the whole defence against nonsense here: if two
    fields are 40 in every recording, "a equals b" is confidently true and
    completely meaningless.
    """
    found: list[Invariant] = []
    if len(per_run_paths) < 2:
        return found

    common = set(per_run_paths[0])
    for paths in per_run_paths[1:]:
        common &= set(paths)
    ordered = sorted(common)

    for i, left in enumerate(ordered):
        for right in ordered[i + 1 :]:
            if left.split(".")[0] == right.split(".")[0]:
                continue  # same tool; uninteresting
            values = [(p[left], p[right]) for p in per_run_paths]
            if not all(a == b for a, b in values):
                continue
            distinct = {a for a, _ in values}
            if len(distinct) < 2:
                continue  # a constant, not a relationship
            found.append(
                Invariant(
                    kind=InvariantKind.DATA_EQUALITY,
                    subject=left,
                    other=right,
                    support=len(per_run_paths),
                    applicable=len(per_run_paths),
                )
            )
    return found


def _drop_redundant(invariants: list[Invariant]) -> list[Invariant]:
    """Remove rules that are implied by other rules.

    Daikon treats redundancy as its own filter category, and for good reason:
    a human has to read this list and approve it. Reporting every implied fact
    turns six real rules into nineteen, and a list nobody reads is a list
    nobody enforces.

    Two reductions:

    * **Transitive ordering.** Given a<b and b<c, the rule a<c adds nothing.
    * **Transitive equality.** Fields that are all equal form one group; report
      a spanning chain (n-1 rules), not every pair (n*(n-1)/2).
    """
    ordering = {(i.subject, i.other) for i in invariants if i.kind is InvariantKind.ALWAYS_PRECEDES}
    implied_order = {
        (a, c)
        for (a, b) in ordering
        for (b2, c) in ordering
        if b == b2 and (a, c) in ordering and a != c
    }

    # Group equal fields, then keep only a spanning chain per group.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        parent[find(x)] = find(y)

    equalities = [i for i in invariants if i.kind is InvariantKind.DATA_EQUALITY]
    groups: dict[str, list[str]] = defaultdict(list)
    for inv in equalities:
        union(inv.subject, inv.other or "")
    for inv in equalities:
        for field in (inv.subject, inv.other or ""):
            if field not in groups[find(field)]:
                groups[find(field)].append(field)

    keep_equality: set[tuple[str, str]] = set()
    for members in groups.values():
        ordered = sorted(members)
        for left, right in zip(ordered, ordered[1:], strict=False):
            keep_equality.add((left, right))

    result: list[Invariant] = []
    for inv in invariants:
        if inv.kind is InvariantKind.ALWAYS_PRECEDES:
            if (inv.subject, inv.other) in implied_order:
                continue
        elif inv.kind is InvariantKind.DATA_EQUALITY:
            pair = (inv.subject, inv.other or "")
            if pair not in keep_equality and tuple(reversed(pair)) not in keep_equality:
                continue
        result.append(inv)
    return result


def mine(
    recordings: list[Recording],
    *,
    min_support: int = DEFAULT_MIN_SUPPORT,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> list[Invariant]:
    """Infer the rules a corpus of approved recordings always obeyed.

    Only approved recordings are used. Learning rules from runs nobody
    confirmed were correct would launder a bug into a specification.
    """
    corpus = [r for r in recordings if r.is_golden]
    if len(corpus) < min_support:
        return []

    runs = [_tool_steps(r.trajectory) for r in corpus]
    declared: set[str] = set()
    for recording in corpus:
        declared |= _declared_tools(recording)

    candidates: list[Invariant] = []
    candidates += _mine_ordering(runs)
    candidates += _mine_occurrence(runs, declared)
    candidates += _mine_terminal(runs)
    candidates += _mine_consecutive(runs)
    candidates += _mine_bounds(runs)
    candidates += _mine_data_equality([_scalar_paths(r.events) for r in corpus])

    kept = [
        inv for inv in candidates if inv.support >= min_support and inv.confidence >= min_confidence
    ]
    kept = _drop_redundant(kept)
    return sorted(kept, key=lambda i: (-i.support, i.kind.value, i.subject))


def merge(mined: list[Invariant], existing: list[Invariant]) -> list[Invariant]:
    """Re-mine without losing approvals already given."""
    approved = {inv.key for inv in existing if inv.approved}
    for inv in mined:
        if inv.key in approved:
            inv.approved = True
    return mined


# -------------------------------------------------------------- checking


def check(
    trajectory: Trajectory,
    events: list[Event],
    invariants: list[Invariant],
    *,
    approved_only: bool = True,
) -> list[Violation]:
    """Test one run against the learned rules."""
    active = [i for i in invariants if i.approved] if approved_only else list(invariants)
    if not active:
        return []

    run = list(trajectory.tools_called)
    paths = _scalar_paths(events)
    violations: list[Violation] = []

    for inv in active:
        match inv.kind:
            case InvariantKind.ALWAYS_PRECEDES:
                other = inv.other or ""
                if other in run:
                    if inv.subject not in run:
                        violations.append(
                            Violation(
                                invariant=inv,
                                detail=f"{other} ran but {inv.subject} never did",
                            )
                        )
                    elif run.index(inv.subject) > run.index(other):
                        violations.append(
                            Violation(
                                invariant=inv,
                                detail=f"{other} ran before {inv.subject} this time",
                            )
                        )

            case InvariantKind.ALWAYS_CALLED:
                if inv.subject not in run:
                    violations.append(
                        Violation(invariant=inv, detail=f"{inv.subject} was not called")
                    )

            case InvariantKind.NEVER_CALLED:
                if inv.subject in run:
                    violations.append(
                        Violation(
                            invariant=inv,
                            detail=f"{inv.subject} was called for the first time",
                        )
                    )

            case InvariantKind.ALWAYS_TERMINAL:
                if inv.subject in run and run[-1] != inv.subject:
                    violations.append(
                        Violation(
                            invariant=inv,
                            detail=f"{run[-1]} ran after {inv.subject}",
                        )
                    )

            case InvariantKind.NEVER_CONSECUTIVE:
                for index in range(len(run) - 1):
                    if run[index] == inv.subject and run[index + 1] == inv.subject:
                        violations.append(
                            Violation(
                                invariant=inv,
                                detail=f"{inv.subject} ran twice in a row",
                            )
                        )
                        break

            case InvariantKind.MAX_TOOL_CALLS:
                limit = inv.value or 0
                if len(run) > limit:
                    violations.append(
                        Violation(
                            invariant=inv,
                            detail=f"{len(run)} tool calls, previously never above {limit}",
                        )
                    )

            case InvariantKind.DATA_EQUALITY:
                left, right = inv.subject, inv.other or ""
                if left in paths and right in paths and paths[left] != paths[right]:
                    violations.append(
                        Violation(
                            invariant=inv,
                            detail=f"{paths[left]!r} != {paths[right]!r}",
                        )
                    )

    return violations


# --------------------------------------------------------------- storage


def invariants_path(recordings_dir: Path) -> Path:
    return Path(recordings_dir) / "invariants.json"


def save_invariants(invariants: list[Invariant], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "invariants": [inv.model_dump(mode="json") for inv in invariants],
    }
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_invariants(path: Path) -> list[Invariant]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [Invariant.model_validate(item) for item in data.get("invariants", [])]


# ------------------------------------------------------------- rendering


_BADGE = {
    InvariantKind.ALWAYS_PRECEDES: "ALWAYS",
    InvariantKind.ALWAYS_CALLED: "ALWAYS",
    InvariantKind.NEVER_CALLED: "NEVER",
    InvariantKind.ALWAYS_TERMINAL: "ALWAYS",
    InvariantKind.NEVER_CONSECUTIVE: "NEVER",
    InvariantKind.MAX_TOOL_CALLS: "BOUND",
    InvariantKind.DATA_EQUALITY: "ALWAYS",
}


def render(invariants: list[Invariant], *, corpus: int = 0) -> str:
    if not invariants:
        return (
            "  No invariants mined yet.\n"
            f"  Mining needs at least {DEFAULT_MIN_SUPPORT} approved recordings; "
            "record and approve a few more scenarios, then run drift learn again."
        )
    lines = [f"  Mined {len(invariants)} invariant(s) from {corpus} approved recording(s):", ""]
    for inv in invariants:
        mark = "approved" if inv.approved else "pending "
        badge = _BADGE[inv.kind]
        lines.append(
            f"  [{mark}] {badge:<6} {inv.description:<52} "
            f"{inv.support}/{inv.applicable} runs  conf {inv.confidence:.2f}"
        )
    pending = sum(1 for i in invariants if not i.approved)
    if pending:
        lines += [
            "",
            f"  {pending} not yet enforced. Mined rules are evidence, not truth -",
            "  review them, then: drift learn --approve",
        ]
    return "\n".join(lines)


def render_violations(violations: list[Violation]) -> str:
    if not violations:
        return "  all learned invariants hold"
    lines = [f"  {len(violations)} learned invariant(s) broken:", ""]
    for violation in violations:
        lines.append(f"  x {violation.invariant.description}")
        lines.append(f"      {violation.detail}")
        lines.append(
            f"      (held in {violation.invariant.support}/"
            f"{violation.invariant.applicable} previous runs)"
        )
    return "\n".join(lines)


def terminal_outcomes(recordings: list[Recording]) -> dict[str, int]:  # pragma: no cover
    """Small helper used by reporting."""
    counts: dict[str, int] = defaultdict(int)
    for recording in recordings:
        counts[(recording.trajectory.terminal or Outcome.OK).value] += 1
    return dict(counts)
