"""Durability and honest-failure regressions.

Each test corresponds to a bug found by adversarial testing, and each failure
mode was *silent* - the framework kept working while doing the wrong thing,
which is the only kind of bug a testing tool cannot afford.
"""

from __future__ import annotations

import json  # noqa: F401
import warnings
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest

import driftcatch as drift
from driftcatch.errors import PrunedRecording, UnapprovedReplayWarning
from driftcatch.recording.io import load_recording, recording_path, save_recording

BASE = "https://dur.test/v1"
MODEL = "dur-model"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path) -> Iterator[None]:
    drift.reset_config()
    drift.config(recordings_dir=tmp_path / "recordings")
    yield
    drift.reset_config()


def reply(content: str = "ok") -> dict[str, Any]:
    return {
        "id": "c",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5},
    }


def call(question: str = "hi") -> Any:
    from openai import OpenAI

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=reply())

    http = OpenAI(
        base_url=BASE, api_key="k", http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with http:
        return http.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": question}], temperature=0
        )


# ------------------------------------------- pruned replay must refuse


def _prune(name: str) -> None:
    path = recording_path(name, drift.get_config().recordings_dir)
    recording = load_recording(path)
    for event in recording.events:
        if event.type == "llm":
            event.raw_response = None
            event.stream_chunks = None
    save_recording(recording, path)


def test_replaying_a_pruned_recording_refuses_rather_than_returning_nothing() -> None:
    """Serving `{}` gave the caller an empty completion with no error - a
    silent false green, which is the worst outcome available."""
    with drift.take("pruned"):
        call()
    drift.approve("pruned", by="test")
    _prune("pruned")

    with pytest.raises(PrunedRecording, match="cannot be replayed"), drift.take("pruned"):
        call()


def test_pruned_recording_keeps_its_trajectory_for_assertions() -> None:
    """Pruning is still useful: behaviour survives, only replay is lost."""

    @drift.tool
    def lookup() -> int:
        return 1

    with drift.take("pruned_traj"):
        lookup()
    drift.approve("pruned_traj", by="test")
    _prune("pruned_traj")

    recording = load_recording(recording_path("pruned_traj", drift.get_config().recordings_dir))
    assert recording.trajectory.tools_called == ["lookup"]


# ----------------------------------------------- concurrent durability


def test_concurrent_writes_to_one_name_never_corrupt_the_file() -> None:
    """A fixed temp filename made concurrent writers collide.

    Four writers, because this suite itself runs under ``pytest -n`` and the
    point is correctness, not a load test. The heavy version (32 threads) lives
    in the testbed, where it is not competing with the rest of the suite.
    """
    errors: list[Exception] = []

    def one(_: int) -> None:
        try:
            with drift.take("contended", mode="record"):
                call("identical")
        except Exception as exc:  # noqa: BLE001 - collected for reporting
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, range(8)))

    assert not errors, f"concurrent writes raised: {errors[:3]}"
    directory = drift.get_config().recordings_dir
    loaded = load_recording(recording_path("contended", directory))
    assert len(loaded.events) == 1
    assert not list(directory.glob("*.tmp")), "temp files left behind"


def test_framework_reads_and_writes_survive_contention() -> None:
    """On Windows, ``os.replace`` fails when the destination is open, and so
    does opening a file mid-swap. Both of DriftCatch's own paths retry.

    Scope: this guarantees *DriftCatch's* reads and writes. A third-party raw
    read of the file (an editor, a ``git add``) during a swap is outside the
    library's control on Windows and is not claimed to be safe.
    """
    with drift.take("hot", mode="record"):
        call()

    path = recording_path("hot", drift.get_config().recordings_dir)
    problems: list[str] = []

    def writer(_: int) -> None:
        try:
            save_recording(load_recording(path), path)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"write: {exc}")

    def reader(_: int) -> None:
        try:
            loaded = load_recording(path)
            assert loaded.name == "hot"
        except Exception as exc:  # noqa: BLE001
            problems.append(f"read: {exc}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: writer(i) if i % 2 else reader(i), range(32)))

    assert not problems, problems[:3]


# ------------------------------------------- warnings must be truthful


def test_recording_over_an_unapproved_recording_does_not_claim_to_replay() -> None:
    """The warning fired during `mode="record"`, where nothing is replayed."""
    with drift.take("unapproved"):
        call()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with drift.take("unapproved", mode="record"):
            call()

    replay_warnings = [w for w in caught if issubclass(w.category, UnapprovedReplayWarning)]
    assert not replay_warnings, "a record run must not warn about replaying"


def test_replaying_an_unapproved_recording_still_warns() -> None:
    """The real case must keep working."""
    with drift.take("unapproved2"):
        call()
    with pytest.warns(UnapprovedReplayWarning), drift.take("unapproved2", mode="replay"):
        call()
