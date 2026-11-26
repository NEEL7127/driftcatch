"""pytest integration.

Loaded through the ``pytest11`` entry point, so it must import cleanly and do
nothing until a test actually opens a take.

Everything is kept out of module-level state: each worker, thread and task reads
its session from a ``ContextVar``, which is what makes ``pytest -n auto`` safe
for a tool whose whole value proposition is determinism.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from driftcatch._name import ENV_PREFIX
from driftcatch.config import Config, get_config
from driftcatch.recording.io import list_recordings
from driftcatch.recording.schema import Status

if TYPE_CHECKING:  # pragma: no cover
    from _pytest.terminal import TerminalReporter

_MODES = ("auto", "record", "replay", "live")


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register DriftCatch's command line options."""
    group = parser.getgroup("driftcatch", "DriftCatch agent behaviour testing")
    group.addoption(
        "--drift-mode",
        action="store",
        default=None,
        choices=list(_MODES),
        help=(
            "Recording mode. 'auto' records when a recording is missing and replays "
            f"otherwise. Overrides ${ENV_PREFIX}_MODE."
        ),
    )
    group.addoption(
        "--drift-recordings-dir",
        action="store",
        default=None,
        help="Directory holding recording files (default: tests/recordings).",
    )
    group.addoption(
        "--drift-policy",
        action="store",
        default=None,
        choices=["warn", "fail", "ignore"],
        help="What to do when a recording's inputs have changed.",
    )
    group.addoption(
        "--drift-update",
        action="store_true",
        default=False,
        help="Re-record every take, replacing existing recordings (they return to pending).",
    )
    parser.addini("drift_recordings_dir", "Directory holding recording files.", default="")


def pytest_configure(config: pytest.Config) -> None:
    """Translate pytest options into DriftCatch configuration."""
    config.addinivalue_line(
        "markers", "drift(recording): mark a test as a DriftCatch behaviour test"
    )

    mode = config.getoption("--drift-mode")
    if config.getoption("--drift-update"):
        mode = "record"
    if mode and mode != "auto":
        os.environ[f"{ENV_PREFIX}_MODE"] = str(mode)

    overrides: dict[str, Any] = {}
    directory = config.getoption("--drift-recordings-dir") or config.getini("drift_recordings_dir")
    if directory:
        overrides["recordings_dir"] = Path(str(directory))
    policy = config.getoption("--drift-policy")
    if policy:
        overrides["drift_policy"] = policy

    if overrides:
        from driftcatch.config import config as set_config

        set_config(**overrides)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Leave no trace in the environment between runs."""
    if config.getoption("--drift-mode") or config.getoption("--drift-update"):
        os.environ.pop(f"{ENV_PREFIX}_MODE", None)


@pytest.fixture
def drift_config() -> Config:
    """The resolved DriftCatch configuration for this test."""
    return get_config()


@pytest.fixture
def drift_recordings_dir(drift_config: Config) -> Path:
    """Where recordings for this run live."""
    return drift_config.recordings_dir


@pytest.fixture
def isolated_recordings(tmp_path: Path) -> Iterator[Path]:
    """Point DriftCatch at a throwaway recordings directory for one test.

    Useful when testing DriftCatch itself, or for tests that should always
    record fresh rather than reuse a committed recording.
    """
    from driftcatch.config import config as set_config
    from driftcatch.config import reset_config

    directory = tmp_path / "recordings"
    reset_config()
    set_config(recordings_dir=directory)
    try:
        yield directory
    finally:
        reset_config()


def pytest_terminal_summary(
    terminalreporter: TerminalReporter,
    exitstatus: int,
    config: pytest.Config,
) -> None:
    """Report recording health after the run.

    A test suite can be entirely green while every recording in it is stale, so
    the summary says so rather than letting a passing run imply current
    behaviour.
    """
    try:
        cfg = get_config()
        recordings = list_recordings(cfg.recordings_dir)
    except Exception:  # pragma: no cover - never break the summary
        return
    if not recordings:
        return

    counts = {status: 0 for status in Status}
    for recording in recordings:
        counts[recording.status] += 1

    parts = [f"{counts[Status.APPROVED]} approved"]
    if counts[Status.PENDING]:
        parts.append(f"{counts[Status.PENDING]} pending approval")
    if counts[Status.STALE]:
        parts.append(f"{counts[Status.STALE]} stale")
    if counts[Status.REJECTED]:
        parts.append(f"{counts[Status.REJECTED]} rejected")

    terminalreporter.write_sep("-", "driftcatch")
    terminalreporter.write_line(f"recordings: {', '.join(parts)}")

    if counts[Status.PENDING]:
        terminalreporter.write_line(
            "  pending recordings are not golden yet - approve them: drift review"
        )
    if counts[Status.STALE]:
        terminalreporter.write_line(
            "  stale recordings still replay, but no longer reflect current behaviour"
        )
