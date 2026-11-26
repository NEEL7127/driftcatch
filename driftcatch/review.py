"""The approval gate.

Recording states *what happened*. Only a human states what is *correct*. Without
this gate, a run that was wrong the first time would be preserved as golden
forever and every later test would faithfully protect the bug.
"""

from __future__ import annotations

import warnings
from datetime import UTC, datetime
from pathlib import Path

from driftcatch.config import Config
from driftcatch.errors import InvalidTransition, RecordingNotApproved, UnapprovedReplayWarning
from driftcatch.recording.io import load_recording, recording_path, save_recording
from driftcatch.recording.schema import Recording, Status

__all__ = ["ALLOWED_TRANSITIONS", "approve", "mark_stale", "reject", "require_approved"]

#: A recording may only move along these edges.
ALLOWED_TRANSITIONS: dict[Status, set[Status]] = {
    Status.PENDING: {Status.APPROVED, Status.REJECTED},
    Status.APPROVED: {Status.STALE},
    Status.STALE: {Status.APPROVED, Status.REJECTED},
    Status.REJECTED: {Status.PENDING},
}


def _transition(recording: Recording, to: Status) -> None:
    allowed = ALLOWED_TRANSITIONS.get(recording.status, set())
    if to not in allowed:
        permitted = ", ".join(sorted(s.value for s in allowed)) or "nothing"
        raise InvalidTransition(
            f"recording {recording.name!r} is {recording.status.value}; it can only move to "
            f"{permitted}, not {to.value}"
        )
    recording.status = to


def _load(name: str, config: Config) -> tuple[Recording, Path]:
    path = recording_path(name, config.recordings_dir)
    return load_recording(path), path


def approve(name: str, *, by: str = "unknown", config: Config | None = None) -> Recording:
    """Bless a recording as the golden, verified behaviour."""
    from driftcatch.config import get_config

    cfg = config or get_config()
    recording, path = _load(name, cfg)
    _transition(recording, Status.APPROVED)
    recording.approved_at = datetime.now(UTC)
    recording.approved_by = by
    save_recording(recording, path)
    return recording


def reject(name: str, *, config: Config | None = None) -> Recording:
    """Mark a recording as wrong. The fix belongs in the agent, not the file."""
    from driftcatch.config import get_config

    cfg = config or get_config()
    recording, path = _load(name, cfg)
    _transition(recording, Status.REJECTED)
    save_recording(recording, path)
    return recording


def mark_stale(name: str, changed: list[str], *, config: Config | None = None) -> Recording:
    """Flag that a recording's inputs have moved on."""
    from driftcatch.config import get_config

    cfg = config or get_config()
    recording, path = _load(name, cfg)
    if recording.status is Status.APPROVED:
        _transition(recording, Status.STALE)
    recording.meta.sdk["drift_changed"] = ",".join(changed)
    save_recording(recording, path)
    return recording


def require_approved(recording: Recording, config: Config) -> None:
    """Gate on human approval.

    Hard-fails when ``require_approved`` is set. Otherwise it still **warns**,
    because replaying an unapproved recording means the test is being measured
    against behaviour nobody has confirmed is correct - quiet success there is
    exactly the "garbage recorded once, protected forever" failure the approval
    gate exists to prevent.
    """
    if recording.status is Status.APPROVED:
        return

    message = (
        f"replaying recording {recording.name!r} which is {recording.status.value}, "
        f"not approved - nobody has confirmed this behaviour is correct. "
        f"Review it: drift review {recording.name}"
    )
    if config.require_approved:
        raise RecordingNotApproved(message)
    warnings.warn(message, UnapprovedReplayWarning, stacklevel=3)
