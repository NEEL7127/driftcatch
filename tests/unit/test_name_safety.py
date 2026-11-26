"""Recording names must not escape the recordings directory.

Names become filenames, and they do not only come from test code. They arrive
from CLI arguments, from config, from UI routes, and - most importantly - from
MCP tool calls made by a coding agent that may be acting on content it read
somewhere. Before validation, ``drift.take("../../../../etc/passwd")`` wrote
outside the recordings directory, and an absolute name escaped entirely.

Names are **refused**, never sanitised: quietly rewriting one would make two
different names collide on the same file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from driftcatch.errors import InvalidRecordingName
from driftcatch.invariants import invariants_path
from driftcatch.recording.io import previous_path, recording_path, validate_name

TRAVERSAL = [
    "../escape",
    "../../etc/passwd",
    "../../../../etc/passwd",
    "a/b",
    "a\\b",
    "/absolute",
    "C:/Windows/evil",
    "C:\\Windows\\evil",
    "..",
    "./x",
    "x/../y",
]

MALFORMED = ["", " ", ".hidden", "-leading", "_leading", "a b", "a\tb", "a\nb", "x" * 121]


@pytest.mark.parametrize("name", TRAVERSAL)
def test_traversal_is_refused(name: str) -> None:
    with pytest.raises(InvalidRecordingName):
        validate_name(name)


@pytest.mark.parametrize("name", MALFORMED)
def test_malformed_names_are_refused(name: str) -> None:
    with pytest.raises(InvalidRecordingName):
        validate_name(name)


@pytest.mark.parametrize(
    "name", ["a", "A", "1", "refund_1182", "a.b-c_1", "x" * 120, "Order.2026-01"]
)
def test_reasonable_names_are_accepted(name: str) -> None:
    assert validate_name(name) == name


@pytest.mark.parametrize("name", TRAVERSAL)
def test_path_builders_refuse_traversal(tmp_path: Path, name: str) -> None:
    for build in (recording_path, previous_path):
        with pytest.raises(InvalidRecordingName):
            build(name, tmp_path)


def test_every_resolved_path_stays_inside_the_directory(tmp_path: Path) -> None:
    base = (tmp_path / "recordings").resolve()
    for name in ["a", "refund_1182", "a.b-c"]:
        resolved = recording_path(name, base).resolve()
        assert resolved.parent == base
        assert previous_path(name, base).resolve().parent == base / ".previous"


def test_null_byte_is_refused() -> None:
    with pytest.raises(InvalidRecordingName):
        validate_name("a\0b")


def test_non_string_is_refused() -> None:
    with pytest.raises(InvalidRecordingName):
        validate_name(None)  # type: ignore[arg-type]


def test_invariants_path_is_fixed_and_not_name_derived(tmp_path: Path) -> None:
    """There is one invariants file per directory, so no name reaches it."""
    assert invariants_path(tmp_path).parent == tmp_path


def test_error_message_explains_the_rule() -> None:
    with pytest.raises(InvalidRecordingName) as excinfo:
        validate_name("../evil")
    message = str(excinfo.value)
    assert "invalid recording name" in message
    assert "become filenames" in message
