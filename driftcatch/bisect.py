"""Behaviour bisect: attributing a change to the input that caused it.

Every other tool stops at "the behaviour differs". The question a developer
actually has is *why*. Because the fingerprint hashes each behaviour-determining
input separately, comparing two recordings isolates the candidates - and when
exactly one input moved, that is the root cause with certainty.

Scope note: this attributes by comparison, which needs no API spend. Proving
causation by reverting one input at a time and re-running live is a strictly
larger feature and is not claimed here.
"""

from __future__ import annotations

import difflib

from pydantic import BaseModel, Field

from driftcatch.recording.fingerprint import compare as fp_compare
from driftcatch.recording.schema import LLMEvent, Recording
from driftcatch.trajectory import diff as trajectory_diff

__all__ = ["BisectReport", "bisect", "render"]

_COMPONENT_LABELS = {
    "model_id": "the model version",
    "prompt_hash": "the system prompt",
    "tool_schema_hash": "the tool schemas",
    "matcher_hash": "the matcher configuration",
}


class BisectReport(BaseModel):
    name: str
    behaviour_changed: bool = False
    golden_path: str = ""
    candidate_path: str = ""
    changed_inputs: list[str] = Field(default_factory=list)
    root_cause: str | None = None
    confident: bool = False
    prompt_diff: list[str] = Field(default_factory=list)
    tool_diff: list[str] = Field(default_factory=list)


def _system_prompt(recording: Recording) -> str:
    for event in recording.events:
        if isinstance(event, LLMEvent):
            return "\n".join(
                str(message.get("content", ""))
                for message in event.request.messages
                if message.get("role") == "system"
            )
    return ""


def _tool_names(recording: Recording) -> list[str]:
    for event in recording.events:
        if isinstance(event, LLMEvent):
            return [str(tool.get("name", "")) for tool in event.request.tools]
    return []


def _word_diff(before: str, after: str) -> list[str]:
    """A readable before/after of the prompt, the usual culprit."""
    lines: list[str] = []
    matcher = difflib.SequenceMatcher(None, before.split(), after.split())
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed = " ".join(before.split()[i1:i2])
        added = " ".join(after.split()[j1:j2])
        if removed:
            lines.append(f"- {removed}")
        if added:
            lines.append(f"+ {added}")
    return lines


def bisect(
    golden: Recording, candidate: Recording, *, tolerance: str = "tools+order"
) -> BisectReport:
    """Attribute the behaviour difference between two recordings."""
    report = BisectReport(
        name=golden.name,
        golden_path=" -> ".join(golden.trajectory.tools_called) or "(no tools)",
        candidate_path=" -> ".join(candidate.trajectory.tools_called) or "(no tools)",
    )

    result = trajectory_diff(golden.trajectory, candidate.trajectory, tolerance)
    report.behaviour_changed = not result.matched

    if golden.fingerprint and candidate.fingerprint:
        report.changed_inputs = fp_compare(golden.fingerprint, candidate.fingerprint)

    # Exactly one changed input means the attribution is certain.
    if len(report.changed_inputs) == 1:
        component = report.changed_inputs[0]
        report.root_cause = _COMPONENT_LABELS.get(component, component)
        report.confident = True
    elif len(report.changed_inputs) > 1:
        report.root_cause = None
        report.confident = False

    if "prompt_hash" in report.changed_inputs:
        report.prompt_diff = _word_diff(_system_prompt(golden), _system_prompt(candidate))

    if "tool_schema_hash" in report.changed_inputs:
        before, after = set(_tool_names(golden)), set(_tool_names(candidate))
        report.tool_diff = [f"- {name}" for name in sorted(before - after)] + [
            f"+ {name}" for name in sorted(after - before)
        ]

    return report


def render(report: BisectReport) -> str:
    lines: list[str] = []

    if not report.behaviour_changed:
        lines.append("  behaviour is unchanged between these recordings")
    else:
        lines.extend(
            [
                "  Behaviour changed:",
                f"    golden:    {report.golden_path}",
                f"    candidate: {report.candidate_path}",
            ]
        )

    lines.append("")
    if not report.changed_inputs:
        lines.append("  No behaviour-determining input changed.")
        if report.behaviour_changed:
            lines.append("  The difference is model non-determinism, not a code change.")
            lines.append("  Measure it: drift determinism <recording> -n 20")
        return "\n".join(lines)

    labels = [_COMPONENT_LABELS.get(c, c) for c in report.changed_inputs]
    lines.append(f"  Changed inputs: {', '.join(labels)}")

    if report.confident and report.root_cause:
        lines.extend(["", f"  ROOT CAUSE: {report.root_cause}."])
    else:
        lines.extend(
            [
                "",
                "  Several inputs changed, so the cause is not isolated.",
                "  Revert one at a time and re-record to narrow it down.",
            ]
        )

    if report.prompt_diff:
        lines.append("")
        lines.extend(f"    {line}" for line in report.prompt_diff)

    if report.tool_diff:
        lines.append("")
        lines.extend(f"    {line}" for line in report.tool_diff)

    return "\n".join(lines)
