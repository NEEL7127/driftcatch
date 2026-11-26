"""Recording persistence: atomicity, round-tripping and readable diffs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from driftcatch.errors import RecordingNotFound, RecordingVersionError
from driftcatch.recording import (
    Recording,
    Status,
    list_recordings,
    load_recording,
    recording_path,
    save_recording,
)
from tests.conftest import PROMPT_MARKER


def test_recording_path_uses_name(tmp_path: Path) -> None:
    assert recording_path("refund_1182", tmp_path) == tmp_path / "refund_1182.json"


def test_round_trip_preserves_everything(tmp_path: Path, sample_recording: Recording) -> None:
    path = recording_path(sample_recording.name, tmp_path)
    save_recording(sample_recording, path)
    loaded = load_recording(path)
    assert loaded.model_dump(mode="json") == sample_recording.model_dump(mode="json")


def test_metadata_is_plaintext_but_bodies_are_compressed(
    tmp_path: Path, sample_recording: Recording
) -> None:
    """A behaviour change must be legible in a pull request diff.

    Status, name and trajectory stay as readable JSON; the bulky event bodies
    are gzipped so long prompts do not bloat the repo or the diff.
    """
    path = recording_path(sample_recording.name, tmp_path)
    save_recording(sample_recording, path)
    raw = path.read_text(encoding="utf-8")

    assert '"status": "approved"' in raw
    assert '"refund_1182"' in raw
    assert "lookup_order" in raw  # trajectory.tools_called is plaintext
    assert PROMPT_MARKER not in raw  # the prompt body is inside the gzipped blob
    assert "events_gz" in raw


def test_save_is_atomic_and_leaves_no_temp_file(
    tmp_path: Path, sample_recording: Recording
) -> None:
    path = recording_path(sample_recording.name, tmp_path)
    save_recording(sample_recording, path)
    save_recording(sample_recording, path)
    assert list(tmp_path.glob("*.tmp")) == []
    assert path.exists()


def test_save_creates_missing_directories(tmp_path: Path, sample_recording: Recording) -> None:
    path = tmp_path / "nested" / "deeper" / "r.json"
    save_recording(sample_recording, path)
    assert path.exists()


def test_overwrite_replaces_content(tmp_path: Path, sample_recording: Recording) -> None:
    path = recording_path(sample_recording.name, tmp_path)
    save_recording(sample_recording, path)

    sample_recording.status = Status.STALE
    save_recording(sample_recording, path)

    assert load_recording(path).status is Status.STALE


def test_load_missing_file_raises_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(RecordingNotFound, match="drift record"):
        load_recording(tmp_path / "nope.json")


def test_load_rejects_unknown_version(tmp_path: Path) -> None:
    """Recordings are committed artifacts, so a format mismatch must be loud."""
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"version": 99, "name": "x"}), encoding="utf-8")
    with pytest.raises(RecordingVersionError, match="v99"):
        load_recording(path)


def test_load_handles_recording_without_events(tmp_path: Path) -> None:
    recording = Recording(name="empty", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    path = recording_path("empty", tmp_path)
    save_recording(recording, path)
    assert load_recording(path).events == []


def test_list_recordings_is_sorted_and_skips_bad_files(
    tmp_path: Path, sample_recording: Recording
) -> None:
    for name in ("b_second", "a_first"):
        sample_recording.name = name
        save_recording(sample_recording, recording_path(name, tmp_path))
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "wrong_version.json").write_text(json.dumps({"version": 99}), encoding="utf-8")

    names = [r.name for r in list_recordings(tmp_path)]
    assert names == ["a_first", "b_second"]


def test_list_recordings_on_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert list_recordings(tmp_path / "does_not_exist") == []
