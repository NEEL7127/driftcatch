"""Behaviour coverage.

Code coverage reports which *lines* ran. Nothing reports which agent
*behaviours* were ever tested, which is why teams ship agents with whole
branches - the not-found case, the refund-failed case - never exercised once.

The denominator comes for free: every recorded model call declares the tools
available to the agent, so DriftCatch knows which behaviours *could* happen, not
merely which ones did.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from driftcatch.recording.schema import LLMEvent, Outcome, Recording, ToolEvent

__all__ = [
    "CoverageReport",
    "PathCoverage",
    "ToolCoverage",
    "analyse",
    "declared_tools",
    "render",
]


class ToolCoverage(BaseModel):
    """Coverage of one tool: was it called, and was its failure path tested?"""

    name: str
    calls: int = 0
    covered: bool = False
    error_path_covered: bool = False


class PathCoverage(BaseModel):
    """One distinct decision path the agent has been observed taking."""

    path: str
    recordings: int = 0
    terminal: Outcome | None = None


class CoverageReport(BaseModel):
    percent: float = 0.0
    covered_units: int = 0
    total_units: int = 0
    tools: dict[str, ToolCoverage] = Field(default_factory=dict)
    paths: list[PathCoverage] = Field(default_factory=list)
    uncovered: list[str] = Field(default_factory=list)
    recordings_analysed: int = 0

    @property
    def passing(self) -> bool:
        return self.total_units > 0 and self.covered_units == self.total_units


def declared_tools(recordings: list[Recording]) -> list[str]:
    """Every tool the agent was offered, across all recordings."""
    names: set[str] = set()
    for recording in recordings:
        for event in recording.events:
            if isinstance(event, LLMEvent):
                for tool in event.request.tools:
                    name = tool.get("name")
                    if name:
                        names.add(str(name))
            elif isinstance(event, ToolEvent):
                names.add(event.name)
    return sorted(names)


def analyse(recordings: list[Recording], known_tools: list[str] | None = None) -> CoverageReport:
    """Compute behaviour coverage across a corpus of recordings.

    Each tool contributes two units of behaviour: its success path and its
    failure path. Counting the failure path separately is the point - an agent
    whose tools have never failed in testing is an agent whose error handling
    has never run.
    """
    tools = known_tools if known_tools is not None else declared_tools(recordings)
    report = CoverageReport(recordings_analysed=len(recordings))
    report.tools = {name: ToolCoverage(name=name) for name in tools}

    path_counts: dict[str, PathCoverage] = {}

    for recording in recordings:
        for event in recording.events:
            if not isinstance(event, ToolEvent):
                continue
            entry = report.tools.setdefault(event.name, ToolCoverage(name=event.name))
            entry.calls += 1
            entry.covered = True
            if event.outcome is Outcome.ERROR:
                entry.error_path_covered = True

        path = " -> ".join(recording.trajectory.tools_called) or "(no tools)"
        existing = path_counts.get(path)
        if existing is None:
            path_counts[path] = PathCoverage(
                path=path, recordings=1, terminal=recording.trajectory.terminal
            )
        else:
            existing.recordings += 1

    report.paths = sorted(path_counts.values(), key=lambda p: (-p.recordings, p.path))

    report.total_units = len(report.tools) * 2
    report.covered_units = sum(
        int(tool.covered) + int(tool.error_path_covered) for tool in report.tools.values()
    )
    report.percent = (
        round(100 * report.covered_units / report.total_units, 1) if report.total_units else 0.0
    )

    uncovered: list[str] = []
    for tool in report.tools.values():
        if not tool.covered:
            uncovered.append(f"{tool.name} (never called)")
        elif not tool.error_path_covered:
            uncovered.append(f"{tool.name} error path")
    report.uncovered = uncovered

    return report


def _bar(percent: float, width: int = 20) -> str:
    filled = int(round(percent / 100 * width))
    return "#" * filled + "." * (width - filled)


def render(report: CoverageReport) -> str:
    """The report users will screenshot. Make the gaps impossible to miss."""
    lines = [
        f"  BEHAVIOUR COVERAGE   {report.percent:>5.1f}%  {_bar(report.percent)}",
        f"  {report.covered_units}/{report.total_units} behaviour units across "
        f"{report.recordings_analysed} recording(s)",
        "",
        f"  {'TOOL':<24}{'CALLED':>8}{'CALLS':>7}{'ERROR PATH':>13}",
        f"  {'-' * 52}",
    ]
    for name in sorted(report.tools):
        tool = report.tools[name]
        lines.append(
            f"  {name:<24}{('yes' if tool.covered else 'NO'):>8}{tool.calls:>7}"
            f"{('yes' if tool.error_path_covered else 'NO'):>13}"
        )

    if report.paths:
        lines.extend(["", "  DECISION PATHS"])
        for path in report.paths:
            lines.append(f"    {path.recordings:>3}x  {path.path}")

    if report.uncovered:
        lines.extend(["", f"  {len(report.uncovered)} behaviour(s) with no coverage:"])
        for item in report.uncovered:
            lines.append(f"    - {item}")
        lines.append("")
        lines.append("  Close a gap without re-recording:")
        lines.append("    drift what-if <recording> --fail-tool <tool>")
    else:
        lines.extend(["", "  every declared behaviour has coverage"])

    return "\n".join(lines)
