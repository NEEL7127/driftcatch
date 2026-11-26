"""Trajectory construction and diffing.

The trajectory is *behaviour*: what the agent decided and did, in order. It is
what gets diffed and what behaviour coverage is computed from. Diffing it -
rather than the final text - is how DriftCatch catches the regression that
answer-quality evals miss.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from driftcatch.recording.fingerprint import canonical, hash_str
from driftcatch.recording.schema import (
    Event,
    LLMEvent,
    Outcome,
    Step,
    ToolEvent,
    Trajectory,
)

__all__ = ["TOLERANCES", "TrajectoryDiff", "build", "diff", "render"]

#: Increasingly strict comparison levels.
TOLERANCES = ("tools", "tools+order", "tools+order+args", "exact")


def build(events: list[Event]) -> Trajectory:
    """Derive the trajectory from an ordered list of events."""
    steps: list[Step] = []
    tools_called: list[str] = []
    parallel_groups: list[list[int]] = []
    retries: list[int] = []

    current_group: list[int] = []
    last_llm: int | None = None
    seen_tool_keys: set[str] = set()

    for event in events:
        if isinstance(event, LLMEvent):
            if len(current_group) > 1:
                parallel_groups.append(current_group)
            current_group = []
            last_llm = event.index
            steps.append(
                Step(
                    kind="llm",
                    index=event.index,
                    decided=event.decided,
                    outcome=event.outcome,
                )
            )
        elif isinstance(event, ToolEvent):
            key = f"{event.name}:{canonical(event.args)}"
            if key in seen_tool_keys:
                retries.append(event.index)
            seen_tool_keys.add(key)

            current_group.append(event.index)
            tools_called.append(event.name)
            steps.append(
                Step(
                    kind="tool",
                    index=event.index,
                    name=event.name,
                    args_digest=hash_str(canonical(event.args)),
                    outcome=event.outcome,
                    parent=last_llm,
                )
            )

    if len(current_group) > 1:
        parallel_groups.append(current_group)

    terminal: Outcome | None = None
    if steps:
        terminal = steps[-1].outcome or Outcome.OK

    return Trajectory(
        steps=steps,
        tools_called=tools_called,
        parallel_groups=parallel_groups,
        retries=retries,
        terminal=terminal,
    )


@dataclass
class TrajectoryDiff:
    """The result of comparing a run against its golden."""

    matched: bool
    tolerance: str
    golden_tools: list[str] = field(default_factory=list)
    current_tools: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    first_divergence: int | None = None
    notes: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.matched


def _tool_steps(trajectory: Trajectory) -> list[Step]:
    return [step for step in trajectory.steps if step.kind == "tool"]


def diff(golden: Trajectory, current: Trajectory, tolerance: str = "tools+order") -> TrajectoryDiff:
    """Compare two trajectories at the requested tolerance."""
    if tolerance not in TOLERANCES:
        raise ValueError(f"unknown tolerance {tolerance!r}; expected one of {TOLERANCES}")

    golden_tools = list(golden.tools_called)
    current_tools = list(current.tools_called)

    result = TrajectoryDiff(
        matched=True,
        tolerance=tolerance,
        golden_tools=golden_tools,
        current_tools=current_tools,
    )

    missing = [name for name in golden_tools if name not in current_tools]
    unexpected = [name for name in current_tools if name not in golden_tools]
    result.missing = sorted(set(missing))
    result.unexpected = sorted(set(unexpected))

    if tolerance == "tools":
        result.matched = not result.missing and not result.unexpected
        return result

    # Ordered comparison from here on.
    if golden_tools != current_tools:
        result.matched = False
        for position in range(max(len(golden_tools), len(current_tools))):
            left = golden_tools[position] if position < len(golden_tools) else None
            right = current_tools[position] if position < len(current_tools) else None
            if left != right:
                result.first_divergence = position
                break
        if tolerance == "tools+order":
            return result

    if tolerance in {"tools+order+args", "exact"}:
        golden_steps = _tool_steps(golden)
        current_steps = _tool_steps(current)
        pairs = zip(golden_steps, current_steps, strict=False)
        for position, (left_step, right_step) in enumerate(pairs):
            if left_step.args_digest != right_step.args_digest:
                result.matched = False
                result.first_divergence = result.first_divergence or position
                result.notes.append(
                    f"arguments differ for {left_step.name!r} at position {position}"
                )
                break

    if tolerance == "exact":
        if len(golden.steps) != len(current.steps):
            result.matched = False
            result.notes.append(
                f"step count differs: golden {len(golden.steps)}, current {len(current.steps)}"
            )
        else:
            step_pairs = zip(golden.steps, current.steps, strict=False)
            for position, (left_step, right_step) in enumerate(step_pairs):
                if (
                    left_step.kind != right_step.kind
                    or left_step.name != right_step.name
                    or left_step.outcome != right_step.outcome
                    or left_step.decided != right_step.decided
                ):
                    result.matched = False
                    result.first_divergence = result.first_divergence or position
                    result.notes.append(f"step {position} differs")
                    break

    return result


def render(d: TrajectoryDiff) -> str:
    """Human-readable behaviour diff - the product's signature output."""
    golden = " -> ".join(d.golden_tools) or "(no tools)"
    current = " -> ".join(d.current_tools) or "(no tools)"

    if d.matched:
        return f"  behaviour matches golden ({d.tolerance})\n    {current}"

    lines = [
        f"  golden:   {golden}",
        f"  current:  {current}",
    ]

    if d.first_divergence is not None and d.first_divergence < len(d.current_tools):
        prefix = " -> ".join(d.current_tools[: d.first_divergence])
        pad = len(prefix) + (4 if prefix else 0)
        marker = "^" * len(d.current_tools[d.first_divergence])
        lines.append("  " + " " * (len("current:  ") + pad) + marker)

    for name in d.missing:
        lines.append(f'  x expected "{name}", never called')
    for name in d.unexpected:
        lines.append(f'  x unexpected "{name}"')
    for note in d.notes:
        lines.append(f"  x {note}")

    return "\n".join(lines)
