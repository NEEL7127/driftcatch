"""Reading and writing recording files.

Two properties matter more than convenience here:

* **Atomic writes.** A crash mid-write must never leave a corrupt recording,
  because a corrupt golden is worse than no golden.
* **Readable diffs.** Status, fingerprint and trajectory stay as plaintext JSON
  so that a behaviour change shows up legibly in a pull request. Only the bulky
  event bodies are compressed.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from driftcatch._name import RECORDING_VERSION
from driftcatch.errors import (
    InvalidRecordingName,
    RecordingNotFound,
    RecordingVersionError,
    RecordingWriteError,
)
from driftcatch.recording.schema import Recording

__all__ = [
    "archive_previous",
    "list_recordings",
    "load_recording",
    "previous_path",
    "recording_path",
    "restore_previous",
    "save_recording",
    "validate_name",
]

#: Where the prior version of a re-recorded golden is kept.
PREVIOUS_DIR = ".previous"

T = TypeVar("T")

_EVENTS_KEY = "events"
_EVENTS_PACKED_KEY = "events_gz"


#: A recording name must be a plain identifier. Names become filenames and
#: arrive from CLI arguments, config and MCP tool calls, so anything that could
#: climb out of the recordings directory is refused rather than sanitised -
#: silently rewriting a name would make two different names collide.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def validate_name(name: str) -> str:
    """Return ``name`` if it is a safe filename component, else raise.

    Rejects path separators, parent references, absolute paths, null bytes,
    leading dots and empty strings.
    """
    if not isinstance(name, str) or not name:
        raise InvalidRecordingName("a recording name must be a non-empty string")
    if not _SAFE_NAME.match(name) or ".." in name:
        raise InvalidRecordingName(
            f"invalid recording name {name!r}. Use letters, digits, dot, dash or "
            f"underscore, starting with a letter or digit (max 120 characters). "
            f"Path separators and parent references are refused because names "
            f"become filenames."
        )
    return name


def recording_path(name: str, recordings_dir: Path) -> Path:
    """Resolve the on-disk path for a recording name."""
    return Path(recordings_dir) / f"{validate_name(name)}.json"


def previous_path(name: str, recordings_dir: Path) -> Path:
    """Where the version replaced by a re-record is kept."""
    return Path(recordings_dir) / PREVIOUS_DIR / f"{validate_name(name)}.json"


def archive_previous(name: str, recordings_dir: Path) -> Path | None:
    """Copy the current recording aside before it is overwritten.

    Re-recording a golden used to destroy it, which is unacceptable for two
    reasons: the old behaviour is exactly what you want to diff the new one
    against, and a re-record done by mistake should be recoverable. The previous
    version is kept so ``drift bisect --previous`` and ``drift restore`` work.
    """
    source = recording_path(name, recordings_dir)
    if not source.exists():
        return None
    target = previous_path(name, recordings_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    # This reads the live recording, so it contends with a concurrent save the
    # same way the other file operations do and needs the same tolerance.
    _retry_on_contention(lambda: shutil.copy2(source, target), "archive", source)
    return target


def restore_previous(name: str, recordings_dir: Path) -> Path:
    """Put the archived version back, undoing a re-record."""
    source = previous_path(name, recordings_dir)
    if not source.exists():
        raise RecordingNotFound(
            f"no previous version of {name!r} to restore. One is kept only after a "
            f"re-record replaces an existing recording."
        )
    target = recording_path(name, recordings_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def _pack(recording: Recording) -> dict[str, Any]:
    """Model -> on-disk dict, compressing event bodies only."""
    data: dict[str, Any] = recording.model_dump(mode="json")
    events = data.pop(_EVENTS_KEY, [])
    raw = json.dumps(events, separators=(",", ":")).encode("utf-8")
    data[_EVENTS_PACKED_KEY] = base64.b64encode(gzip.compress(raw)).decode("ascii")
    return data


def _unpack(data: dict[str, Any]) -> Recording:
    """On-disk dict -> model, transparently decompressing event bodies."""
    payload = dict(data)
    packed = payload.pop(_EVENTS_PACKED_KEY, None)
    if packed is not None:
        raw = gzip.decompress(base64.b64decode(packed))
        payload[_EVENTS_KEY] = json.loads(raw)
    payload.setdefault(_EVENTS_KEY, [])
    return Recording.model_validate(payload)


#: Windows refuses to replace a file another handle has open, so a concurrent
#: reader can make an otherwise-valid write fail. Retrying is bounded by a
#: *deadline* rather than an attempt count: under heavy CPU load a fixed number
#: of attempts can elapse in far less wall-clock time than intended, which made
#: contention failures depend on how busy the machine happened to be.
_CONTENTION_DEADLINE = 5.0
_CONTENTION_BACKOFF = 0.01
_CONTENTION_BACKOFF_MAX = 0.2


def _retry_on_contention(operation: Callable[[], T], describe: str, path: Path) -> T:
    """Run ``operation``, retrying while Windows reports a sharing violation.

    Both the swap and the open can fail transiently while another thread is
    replacing the same file. The swap itself is atomic, so a retry always lands
    on one whole version or another - never on a half-written file.
    """
    deadline = time.monotonic() + _CONTENTION_DEADLINE
    backoff = _CONTENTION_BACKOFF
    last: OSError | None = None
    while True:
        try:
            return operation()
        except PermissionError as exc:  # pragma: no cover - platform specific
            last = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, _CONTENTION_BACKOFF_MAX)
    raise RecordingWriteError(  # pragma: no cover - platform specific
        f"could not {describe} {path} within {_CONTENTION_DEADLINE:.0f}s; another "
        f"process or thread is holding it open. Avoid recording the same scenario "
        f"name concurrently."
    ) from last


def _read_with_retry(path: Path) -> str:
    """Read a recording, tolerating a concurrent replace."""
    return _retry_on_contention(lambda: path.read_text(encoding="utf-8"), "read", path)


def _replace_with_retry(tmp: Path, path: Path) -> None:
    """``os.replace`` with a deadline-bounded retry for sharing violations."""
    _retry_on_contention(lambda: os.replace(tmp, path), "replace", path)


def save_recording(recording: Recording, path: Path) -> None:
    """Write a recording atomically.

    The payload is written to a sibling temporary file, flushed and fsynced,
    then moved into place with ``os.replace`` which is atomic on POSIX and
    Windows alike. An interrupted write therefore leaves the previous file
    untouched rather than truncated.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # A unique temp name per write, not a fixed "<name>.tmp". Two threads or
    # processes recording the same scenario would otherwise share one temp file
    # and race: on Windows that raises PermissionError, and elsewhere one writer
    # can replace the file out from under the other.
    handle_fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(_pack(recording), handle, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, path)
    finally:
        # Best-effort cleanup. After a successful replace the temp file is gone;
        # if removing it ever fails we must not turn a completed write into an
        # error, so a stray temp file is preferable to a spurious exception.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def load_recording(path: Path) -> Recording:
    """Load a recording, refusing formats this version cannot understand."""
    path = Path(path)
    if not path.exists():
        raise RecordingNotFound(
            f"no recording at {path}. Record one first: drift record {path.stem}"
        )
    data = json.loads(_read_with_retry(path))

    version = data.get("version")
    if version != RECORDING_VERSION:
        raise RecordingVersionError(
            f"{path} was written in recording format v{version}, but this build "
            f"understands v{RECORDING_VERSION}. Re-record it, or install a matching "
            f"version of DriftCatch."
        )
    return _unpack(data)


def list_recordings(recordings_dir: Path) -> list[Recording]:
    """Load every recording in a directory, sorted by name.

    Files that cannot be parsed are skipped rather than aborting the listing, so
    one bad file never hides the rest of the suite.
    """
    directory = Path(recordings_dir)
    if not directory.is_dir():
        return []
    found: list[Recording] = []
    # glob("*.json") deliberately does not descend into .previous/
    for file in sorted(directory.glob("*.json")):
        try:
            found.append(load_recording(file))
        except (RecordingVersionError, ValueError, KeyError):
            continue
    return found
