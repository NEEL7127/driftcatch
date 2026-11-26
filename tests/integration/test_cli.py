"""The ``drift`` CLI."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import driftcatch as drift
from driftcatch.agentio import ExitCode
from driftcatch.cli import app
from tests.integration.fake_provider import FakeProvider, chat_body, post_chat

runner = CliRunner()


@pytest.fixture
def recordings(tmp_path: Path) -> Iterator[Path]:
    """A directory holding one recorded run."""
    directory = tmp_path / "recordings"
    drift.reset_config()
    drift.config(recordings_dir=directory)

    provider = FakeProvider(
        [
            chat_body(tool_name="lookup_order", tool_args={"id": 1182}),
            chat_body(content="Refunded $40"),
        ]
    )

    @drift.tool
    def lookup_order(order_id: int) -> dict[str, Any]:
        return {"id": order_id, "total": 40}

    with drift.take("refund"):
        with provider.client() as http:
            post_chat(http)
        lookup_order(1182)

    yield directory
    drift.reset_config()


def run(*args: str) -> Any:
    return runner.invoke(app, list(args))


def test_version() -> None:
    result = run("version")
    assert result.exit_code == 0
    assert "drift" in result.stdout


def test_status_lists_recordings(recordings: Path) -> None:
    result = run("status", "-d", str(recordings))
    assert result.exit_code == 0
    assert "refund" in result.stdout
    assert "pending" in result.stdout


def test_status_on_empty_dir_suggests_recording(tmp_path: Path) -> None:
    result = run("status", "-d", str(tmp_path / "none"))
    assert result.exit_code == 0
    assert "No recordings" in result.stdout


def test_show_renders_events(recordings: Path) -> None:
    result = run("show", "refund", "-d", str(recordings))
    assert result.exit_code == 0
    assert "lookup_order" in result.stdout


def test_show_missing_recording_exits_not_found(recordings: Path) -> None:
    """Specifically 6, not merely non-zero.

    `drift init` writes the exit-code table into a project's AGENTS.md, so a
    coding agent branches on 6 meaning "record it first" rather than 1 meaning
    "something broke". Returning the generic code made that branch unreachable.
    """
    from driftcatch.agentio import ExitCode

    result = run("show", "nope", "-d", str(recordings))
    assert result.exit_code == ExitCode.NOT_FOUND


def test_review_yes_approves(recordings: Path) -> None:
    result = run("review", "refund", "--yes", "-d", str(recordings))
    assert result.exit_code == 0
    assert "approved" in result.stdout

    after = run("status", "-d", str(recordings))
    assert "approved" in after.stdout


def test_coverage_reports_and_gates(recordings: Path) -> None:
    result = run("coverage", "-d", str(recordings))
    assert result.exit_code == 0
    assert "BEHAVIOUR COVERAGE" in result.stdout

    gated = run("coverage", "--fail-under", "99", "-d", str(recordings))
    # A distinct code, not a generic 1, so an agent or CI step can tell a failed
    # quality gate apart from the tool erroring out.
    assert gated.exit_code == int(ExitCode.GATE_FAILED)


def test_coverage_badge(recordings: Path) -> None:
    result = run("coverage", "--badge", "-d", str(recordings))
    assert result.exit_code == 0
    assert "img.shields.io" in result.stdout


def test_suggest_points_at_what_if(recordings: Path) -> None:
    result = run("suggest", "-d", str(recordings))
    assert result.exit_code == 0
    assert "what-if" in result.stdout


def test_audit_shows_match_keys(recordings: Path) -> None:
    result = run("audit", "refund", "-d", str(recordings))
    assert result.exit_code == 0
    assert "match keys" in result.stdout


def test_scan_is_clean_for_a_redacted_recording(recordings: Path) -> None:
    result = run("scan", "-d", str(recordings))
    assert result.exit_code == 0
    assert "clean" in result.stdout


def test_diff_against_itself_matches(recordings: Path) -> None:
    result = run("diff", "refund", "--against", "refund", "-d", str(recordings))
    assert result.exit_code == 0
    assert "matches golden" in result.stdout


def test_bisect_reports_no_change_against_itself(recordings: Path) -> None:
    result = run("bisect", "refund", "refund", "-d", str(recordings))
    assert result.exit_code == 0
    assert "unchanged" in result.stdout


def test_what_if_prints_a_snippet(recordings: Path) -> None:
    result = run("what-if", "refund", "--fail-tool", "lookup_order", "-d", str(recordings))
    assert result.exit_code == 0
    assert "what_if" in result.stdout


def test_prune_shrinks_the_recording(recordings: Path) -> None:
    before = (recordings / "refund.json").stat().st_size
    result = run("prune", "refund", "-d", str(recordings))
    assert result.exit_code == 0
    assert (recordings / "refund.json").stat().st_size <= before
