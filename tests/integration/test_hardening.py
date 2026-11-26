"""Hardening: the failure modes found by testing against real SDKs.

Each test here corresponds to a bug that actually shipped in an earlier version
of this framework and would have silently produced passing, meaningless tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import driftcatch as drift
from driftcatch.errors import (
    DriftSignal,
    NoMatchError,
    RecordingNotFound,
    UnapprovedReplayWarning,
)
from driftcatch.intercept.http import CLIENT_LIBRARIES, client_libraries
from driftcatch.recording.io import previous_path, recording_path
from tests.integration.fake_provider import FakeProvider, chat_body, post_chat

SYSTEM = "Use available tools to find the order."


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path) -> Iterator[None]:
    drift.reset_config()
    drift.config(recordings_dir=tmp_path / "recordings")
    yield
    drift.reset_config()


def _provider() -> FakeProvider:
    return FakeProvider([chat_body(content="ok")])


def _agent(provider: FakeProvider, system: str = SYSTEM) -> None:
    with provider.client() as client:
        post_chat(client, system=system)


# ------------------------------------------------- multi-library hooking


def test_every_known_client_library_is_discovered() -> None:
    """httpx and httpx2 both exist; patching only one misses whole SDKs."""
    names = [lib.__name__ for lib in client_libraries()]
    assert "httpx" in names
    assert set(names) <= set(CLIENT_LIBRARIES)


def test_patches_are_restored_for_every_library() -> None:
    before = {lib.__name__: (lib.Client.send, lib.AsyncClient.send) for lib in client_libraries()}
    with drift.take("restore_check"):
        _agent(_provider())
        during = {lib.__name__: lib.Client.send for lib in client_libraries()}
    after = {lib.__name__: (lib.Client.send, lib.AsyncClient.send) for lib in client_libraries()}

    for name in before:
        assert during[name] is not before[name][0], f"{name} was not patched"
    assert after == before, "originals must be restored exactly"


def test_nested_sessions_do_not_unpatch_early() -> None:
    """Reference counting: an inner session ending must not unhook the outer."""
    with drift.take("outer"):
        patched_outer = httpx.Client.send
        with drift.take("inner"):
            pass
        assert httpx.Client.send is patched_outer
    assert httpx.Client.send is not patched_outer


# ----------------------------------------- signals must not be swallowed


def test_no_match_error_survives_a_broad_except() -> None:
    """A replay mismatch swallowed by the agent would be a false green.

    This is the worst possible failure for a testing tool: the regression is
    real, the test passes, and the bug ships.
    """
    provider = _provider()
    with drift.take("signal"):
        _agent(provider)
    drift.approve("signal", by="test")

    def careless_agent() -> str:
        try:
            _agent(_provider(), system="a completely different prompt")
            return "no error seen"
        except Exception:  # noqa: BLE001 - deliberately careless
            return "swallowed"

    with pytest.raises(NoMatchError), drift.take("signal"):
        careless_agent()


def test_drift_signals_are_not_exceptions() -> None:
    assert issubclass(NoMatchError, DriftSignal)
    assert issubclass(drift.DriftDetected, DriftSignal)
    assert not issubclass(NoMatchError, Exception)
    # Ordinary errors stay ordinary.
    assert issubclass(drift.ConfigError, Exception)


# ------------------------------------------- re-recording keeps the golden


def test_rerecording_archives_the_previous_golden() -> None:
    """Overwriting an approved recording used to destroy it outright."""
    directory = drift.get_config().recordings_dir
    with drift.take("archive_me"):
        _agent(_provider())
    drift.approve("archive_me", by="test")

    original = recording_path("archive_me", directory).read_bytes()

    with (
        pytest.warns(UserWarning, match="re-recorded"),
        drift.take("archive_me", mode="record"),
    ):
        _agent(_provider())

    archived = previous_path("archive_me", directory)
    assert archived.exists(), "the replaced golden must be kept"
    assert archived.read_bytes() == original


def test_rerecording_returns_the_recording_to_pending() -> None:
    with drift.take("regress"):
        _agent(_provider())
    drift.approve("regress", by="test")

    with pytest.warns(UserWarning), drift.take("regress", mode="record"):
        _agent(_provider())

    reloaded = drift.load_recording(recording_path("regress", drift.get_config().recordings_dir))
    assert reloaded.status is drift.Status.PENDING, "a re-record needs fresh approval"


def test_restore_undoes_a_rerecord() -> None:
    from driftcatch.recording.io import restore_previous

    directory = drift.get_config().recordings_dir
    with drift.take("undo_me"):
        _agent(_provider())
    drift.approve("undo_me", by="test")

    with pytest.warns(UserWarning), drift.take("undo_me", mode="record"):
        _agent(_provider())

    restore_previous("undo_me", directory)
    reloaded = drift.load_recording(recording_path("undo_me", directory))
    assert reloaded.status is drift.Status.APPROVED
    assert reloaded.approved_by == "test"


def test_restore_without_an_archive_is_an_actionable_error() -> None:
    from driftcatch.recording.io import restore_previous

    with pytest.raises(RecordingNotFound, match="no previous version"):
        restore_previous("never_existed", drift.get_config().recordings_dir)


def test_archived_copies_are_not_listed_as_recordings() -> None:
    """`.previous/` must not pollute status, coverage or scan."""
    with drift.take("hidden"):
        _agent(_provider())
    drift.approve("hidden", by="test")
    with pytest.warns(UserWarning), drift.take("hidden", mode="record"):
        _agent(_provider())

    names = [r.name for r in drift.list_recordings(drift.get_config().recordings_dir)]
    assert names == ["hidden"]


# --------------------------------------------- unapproved replay warning


def test_replaying_an_unapproved_recording_warns() -> None:
    """Silence here would re-create the problem the approval gate solves."""
    with drift.take("unapproved"):
        _agent(_provider())

    with (
        pytest.warns(UnapprovedReplayWarning, match="not approved"),
        drift.take("unapproved", mode="replay"),
    ):
        _agent(_provider())


def test_approved_replay_does_not_warn(recwarn: pytest.WarningsRecorder) -> None:
    with drift.take("quiet"):
        _agent(_provider())
    drift.approve("quiet", by="test")

    with drift.take("quiet", mode="replay"):
        _agent(_provider())

    assert not [w for w in recwarn if issubclass(w.category, UnapprovedReplayWarning)]


# --------------------------------------------------- async tool capture


@pytest.mark.anyio
async def test_async_tools_record_and_replay() -> None:
    calls = {"n": 0}

    @drift.tool
    async def fetch_order(order_id: int) -> dict[str, Any]:
        calls["n"] += 1
        return {"id": order_id, "total": 40}

    async def agent() -> dict[str, Any]:
        return await fetch_order(1182)

    with drift.take("async_tool") as take:
        recorded = await agent()
    assert calls["n"] == 1
    assert recorded == {"id": 1182, "total": 40}
    assert take.trajectory.tools_called == ["fetch_order"]
    drift.approve("async_tool", by="test")

    calls["n"] = 0
    with drift.take("async_tool") as take:
        replayed = await agent()
    assert calls["n"] == 0, "the async tool body must not run on replay"
    assert replayed == {"id": 1182, "total": 40}


@pytest.mark.anyio
async def test_async_tool_errors_are_reraised() -> None:
    attempts = {"n": 0}

    @drift.tool
    async def flaky(order_id: int) -> dict[str, Any]:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise TimeoutError("slow")
        return {"ok": True}

    async def agent() -> str:
        try:
            await flaky(1)
        except TimeoutError:
            await flaky(1)
        return "done"

    with drift.take("async_retry"):
        await agent()
    drift.approve("async_retry", by="test")

    attempts["n"] = 0
    with drift.take("async_retry") as take:
        assert await agent() == "done"
    assert attempts["n"] == 0
    assert take.trajectory.tools_called == ["flaky", "flaky"]


# ------------------------------------------------------ matcher caching


def test_candidate_payloads_are_cached_across_resolutions() -> None:
    """Without the session-level cache this is O(n^2) normalizations per run."""
    provider = FakeProvider([chat_body(content=f"reply {i}") for i in range(6)])

    def agent() -> None:
        with provider.client() as client:
            for index in range(6):
                post_chat(client, user=f"message {index}")

    with drift.take("cache_me"):
        agent()
    drift.approve("cache_me", by="test")

    with drift.take("cache_me") as take:
        agent()

    state = take._session.match_state
    assert state.payload_cache, "normalized payloads should be cached"
    # Each candidate is normalized once, not once per request.
    assert len(state.payload_cache) <= len(take.events)
