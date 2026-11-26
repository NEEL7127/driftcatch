"""Determinism measurement and the Tier 2 live behaviour check.

Replay cannot tell you what the model would decide today, because the model
isn't running. Only a live run can. That costs money and is genuinely
non-deterministic, so it is deliberate, guarded, and thresholded rather than
exact - and it reports *how* non-deterministic the agent is, so you know which
tests are safe to gate on.
"""

from __future__ import annotations

import functools
import math
import os
from collections.abc import Callable
from typing import Any, TypeVar, cast

from pydantic import BaseModel, Field

from driftcatch._name import ENV_PREFIX
from driftcatch.config import Config, get_config
from driftcatch.errors import DriftCatchError
from driftcatch.recording.io import load_recording, recording_path
from driftcatch.recording.schema import Recording, Trajectory
from driftcatch.trajectory import diff as trajectory_diff
from driftcatch.trajectory import render as render_diff

__all__ = [
    "DeterminismReport",
    "LiveResult",
    "TrajectoryGroup",
    "analyse",
    "live_allowed",
    "live_check",
    "render",
    "run_live",
]

F = TypeVar("F", bound=Callable[..., Any])


# ------------------------------------------------------------------ guard


def live_allowed() -> bool:
    """Live runs spend real money, so they never happen by accident."""
    if os.environ.get(f"{ENV_PREFIX}_ALLOW_LIVE") == "1":
        return True
    in_ci = any(os.environ.get(name) for name in ("CI", "GITHUB_ACTIONS", "GITLAB_CI"))
    return not in_ci


def _require_live() -> None:
    if not live_allowed():
        raise DriftCatchError(
            "live behaviour checks make real API calls and are disabled here. "
            f"Set {ENV_PREFIX}_ALLOW_LIVE=1 to permit them."
        )


# ------------------------------------------------------------- statistics


class TrajectoryGroup(BaseModel):
    """One distinct behaviour observed across repeated live runs."""

    path: str
    count: int
    share: float
    matches_golden: bool = False


class DeterminismReport(BaseModel):
    runs: int = 0
    distinct: int = 0
    groups: list[TrajectoryGroup] = Field(default_factory=list)
    score: float = 0.0
    recommended_pass_k: tuple[int, int] | None = None
    golden_share: float | None = None

    @property
    def is_deterministic(self) -> bool:
        return self.distinct <= 1


def analyse(trajectories: list[Trajectory], golden: Trajectory | None = None) -> DeterminismReport:
    """Group repeated runs by behaviour and score how stable the agent is."""
    runs = len(trajectories)
    if runs == 0:
        return DeterminismReport()

    counts: dict[str, int] = {}
    for trajectory in trajectories:
        path = " -> ".join(trajectory.tools_called) or "(no tools)"
        counts[path] = counts.get(path, 0) + 1

    golden_path = None
    if golden is not None:
        golden_path = " -> ".join(golden.tools_called) or "(no tools)"

    groups = [
        TrajectoryGroup(
            path=path,
            count=count,
            share=round(count / runs, 4),
            matches_golden=(path == golden_path),
        )
        for path, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    top = groups[0].count
    score = round(top / runs, 4)
    # Recommend a gate the agent can actually pass today, never rounding up into
    # a threshold that would make CI flaky.
    recommended = (max(1, math.floor(score * runs)), runs)

    golden_share = None
    if golden_path is not None:
        golden_share = round(counts.get(golden_path, 0) / runs, 4)

    return DeterminismReport(
        runs=runs,
        distinct=len(groups),
        groups=groups,
        score=score,
        recommended_pass_k=recommended,
        golden_share=golden_share,
    )


def render(report: DeterminismReport) -> str:
    if report.runs == 0:
        return "  no runs to analyse"
    lines = [
        f"  {report.runs} live run(s) - {report.distinct} distinct trajectory(ies)",
        "",
    ]
    for group in report.groups:
        marker = "  <- golden" if group.matches_golden else ""
        lines.append(f"  {group.share * 100:>5.1f}%  {group.path}{marker}")
    lines.extend(
        [
            "",
            f"  DETERMINISM SCORE  {report.score:.2f}",
        ]
    )
    if report.recommended_pass_k:
        k, n = report.recommended_pass_k
        lines.append(f"  recommended gate: pass_k=({k}, {n})")
    if report.golden_share is not None and report.golden_share < 1.0:
        lines.append(
            f"  warning: only {report.golden_share * 100:.0f}% of runs matched the golden behaviour"
        )
    return "\n".join(lines)


# ------------------------------------------------------------- live runner


class LiveResult:
    """Outcome of repeating a scenario against the real model."""

    def __init__(
        self,
        name: str,
        trajectories: list[Trajectory],
        golden: Recording | None,
        config: Config,
    ) -> None:
        self.name = name
        self.trajectories = trajectories
        self.golden = golden
        self.config = config
        self.report = analyse(trajectories, golden.trajectory if golden else None)

    def matches(self, tolerance: str | None = None) -> int:
        """How many runs matched the golden behaviour."""
        if self.golden is None:
            return 0
        level = tolerance or self.config.default_tolerance
        return sum(
            1
            for trajectory in self.trajectories
            if trajectory_diff(self.golden.trajectory, trajectory, level).matched
        )

    def assert_matches_golden(
        self,
        *,
        pass_k: tuple[int, int] | None = None,
        tolerance: str | None = None,
    ) -> LiveResult:
        """Assert that enough runs reproduced the approved behaviour.

        ``pass_k=(8, 10)`` means at least 8 of 10 runs must match. Exact match
        across live runs is not a goal; models are not deterministic and a test
        that pretends otherwise is a flaky test.
        """
        if self.golden is None:
            raise AssertionError(
                f"no approved recording for {self.name!r} to compare live runs against"
            )
        level = tolerance or self.config.default_tolerance
        required = pass_k[0] if pass_k else len(self.trajectories)
        matched = self.matches(level)
        if matched < required:
            worst = next(
                (
                    trajectory
                    for trajectory in self.trajectories
                    if not trajectory_diff(self.golden.trajectory, trajectory, level).matched
                ),
                None,
            )
            detail = ""
            if worst is not None:
                detail = "\n" + render_diff(trajectory_diff(self.golden.trajectory, worst, level))
            raise AssertionError(
                f"live behaviour check failed for {self.name!r}: {matched}/"
                f"{len(self.trajectories)} runs matched golden, needed {required}."
                f"{detail}\n\n{render(self.report)}"
            )
        return self


def run_live(
    name: str,
    runner: Callable[[], Any],
    *,
    runs: int = 5,
    config: Config | None = None,
) -> LiveResult:
    """Run a scenario against the real model ``runs`` times and compare behaviour.

    ``runner`` is a zero-argument callable that exercises the agent.
    """
    _require_live()
    if runs < 1:
        raise DriftCatchError("runs must be at least 1")

    from driftcatch.take import take as open_take

    cfg = config or get_config()
    path = recording_path(name, cfg.recordings_dir)
    golden = load_recording(path) if path.exists() else None

    trajectories: list[Trajectory] = []
    for _ in range(runs):
        with open_take(name, mode="live") as take_obj:
            runner()
        trajectories.append(take_obj.trajectory)

    return LiveResult(name, trajectories, golden, cfg)


def live_check(name: str, *, runs: int = 5) -> Callable[[F], F]:
    """Mark a test as a Tier 2 live behaviour check.

    Skips rather than fails when live runs are not permitted, so a normal test
    suite stays free and offline while the behaviour gate still exists in CI for
    the runs that opt in.
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not live_allowed():
                try:
                    import pytest

                    pytest.skip(
                        f"live behaviour check for {name!r} skipped; "
                        f"set {ENV_PREFIX}_ALLOW_LIVE=1 to run it"
                    )
                except ImportError:  # pragma: no cover
                    return None
            return fn(*args, **kwargs)

        wrapper.driftcatch_live = {"name": name, "runs": runs}  # type: ignore[attr-defined]
        return cast(F, wrapper)

    return decorator
