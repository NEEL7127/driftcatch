"""The surfaces a coding agent uses.

Cursor, Claude Code and friends drive tools by running shell commands and
parsing stdout. These tests hold that contract: valid JSON on stdout, nothing
else mixed in, documented exit codes, and no prompt that could block.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import driftcatch as drift
from driftcatch.agentio import ExitCode, capabilities
from driftcatch.cli import app
from tests.integration.fake_provider import FakeProvider, chat_body, post_chat

runner = CliRunner()


@pytest.fixture
def recordings(tmp_path: Path) -> Iterator[Path]:
    directory = tmp_path / "recordings"
    drift.reset_config()
    drift.config(recordings_dir=directory)

    @drift.tool
    def lookup_order(order_id: int) -> dict[str, Any]:
        return {"id": order_id, "total": 40}

    # Six, because invariant mining needs DEFAULT_MIN_SUPPORT clean observations
    # before it will claim a pattern is a rule.
    for index in range(6):
        provider = FakeProvider([chat_body(content="ok")])
        with drift.take(f"case_{index}"):
            with provider.client() as http:
                post_chat(http)
            lookup_order(1000 + index)
        drift.approve(f"case_{index}", by="test")

    yield directory
    drift.reset_config()


def run_json(*args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the CLI and parse stdout as JSON - the agent's contract."""
    result = runner.invoke(app, list(args))
    return result.exit_code, json.loads(result.stdout)


# ------------------------------------------------------------ discovery


def test_explain_json_is_a_complete_manifest() -> None:
    """One call should tell an agent everything, with no docs to read."""
    code, data = run_json("explain", "--json")
    assert code == 0
    assert data["ok"] is True
    for key in ("purpose", "not_for", "key_facts", "exit_codes", "commands", "workflows"):
        assert key in data, key


def test_manifest_warns_against_the_dangerous_misuses() -> None:
    """An agent must not reach for this as a production cache, or re-record to
    make a failing test pass."""
    manifest = capabilities()
    assert "never a response cache" in json.dumps(manifest["not_for"]).lower()
    assert "never re-record" in json.dumps(manifest["cautions"]).lower()
    assert any("does not run" in fact for fact in manifest["key_facts"])


def test_manifest_marks_which_commands_mutate() -> None:
    commands = capabilities()["commands"]
    assert commands["status"]["reads_only"] is True
    assert commands["review"]["reads_only"] is False
    assert "money" in commands["check"]["warning"]


def test_explain_human_mode_still_works() -> None:
    result = runner.invoke(app, ["explain"])
    assert result.exit_code == 0
    assert "driftcatch" in result.stdout


# ----------------------------------------------------------- json output


def test_status_json(recordings: Path) -> None:
    code, data = run_json("status", "--json", "-d", str(recordings))
    assert code == 0
    assert data["total"] == 6
    assert data["counts"]["approved"] == 6
    assert {r["name"] for r in data["recordings"]} == {f"case_{i}" for i in range(6)}


def test_coverage_json(recordings: Path) -> None:
    code, data = run_json("coverage", "--json", "-d", str(recordings))
    assert code == 0
    assert "percent" in data
    assert data["passed"] is True


def test_coverage_json_gate_uses_its_own_exit_code(recordings: Path) -> None:
    """An agent should branch on the number before parsing anything."""
    code, data = run_json("coverage", "--json", "--fail-under", "99", "-d", str(recordings))
    assert code == int(ExitCode.GATE_FAILED)
    assert data["ok"] is False
    assert data["passed"] is False


def test_learn_json(recordings: Path) -> None:
    code, data = run_json("learn", "--json", "-d", str(recordings))
    assert code == 0
    assert data["corpus"] == 6
    assert data["mined"] >= 1
    assert data["enforced"] == 0, "nothing is enforced until a human approves"
    for invariant in data["invariants"]:
        assert {"key", "kind", "description", "support", "confidence", "approved"} <= set(invariant)


def test_learn_approve_json_marks_rules_enforced(recordings: Path) -> None:
    run_json("learn", "--json", "-d", str(recordings))
    code, data = run_json("learn", "--json", "--approve", "-d", str(recordings))
    assert code == 0
    assert data["enforced"] == data["mined"]

    code, listed = run_json("invariants", "--json", "-d", str(recordings))
    assert listed["enforced"] == listed["total"]


def test_invariants_json_when_nothing_learned(tmp_path: Path) -> None:
    code, data = run_json("invariants", "--json", "-d", str(tmp_path / "empty"))
    assert code == 0
    assert data["total"] == 0


def test_scan_json_clean(recordings: Path) -> None:
    code, data = run_json("scan", "--json", "-d", str(recordings))
    assert code == 0
    assert data["ok"] is True
    assert data["offenders"] == {}


def test_missing_recordings_fails_as_json_not_a_traceback(tmp_path: Path) -> None:
    """A failure is a result too; an agent must not have to parse a stack trace."""
    code, data = run_json("coverage", "--json", "-d", str(tmp_path / "nope"))
    assert code == int(ExitCode.NOT_FOUND)
    assert data["ok"] is False
    assert "error" in data
    assert data["hint"]


# --------------------------------------------------------- the contract


@pytest.mark.parametrize(
    "args",
    [
        ("status", "--json"),
        ("coverage", "--json"),
        ("learn", "--json"),
        ("invariants", "--json"),
        ("scan", "--json"),
        ("explain", "--json"),
    ],
)
def test_json_mode_emits_exactly_one_parseable_object(
    recordings: Path, args: tuple[str, ...]
) -> None:
    """stdout is the API contract: one JSON object, no decorations."""
    extra = [] if args[0] == "explain" else ["-d", str(recordings)]
    result = runner.invoke(app, [*args, *extra])
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    assert parsed["command"] == args[0]
    assert "ok" in parsed


def test_json_output_has_no_ansi_escapes(recordings: Path) -> None:
    result = runner.invoke(app, ["status", "--json", "-d", str(recordings)])
    assert "\x1b[" not in result.stdout


def test_every_exit_code_is_documented() -> None:
    documented = capabilities()["exit_codes"]
    assert set(documented) == {int(c) for c in ExitCode}


# ------------------------------------------------------------ AGENTS.md


def test_init_writes_agent_instructions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "-d", str(tmp_path / "recordings")])
    assert result.exit_code == 0

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "DriftCatch" in agents
    assert "--json" in agents
    assert "Never edit files under the recordings directory" in agents
    assert "Do not re-record to make it pass" in agents


def test_init_appends_without_clobbering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# House rules\n\nUse tabs.\n", encoding="utf-8")

    runner.invoke(app, ["init", "-d", str(tmp_path / "recordings")])
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "House rules" in agents, "an existing AGENTS.md must never be clobbered"
    assert "DriftCatch" in agents


def test_init_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "-d", str(tmp_path / "recordings")])
    runner.invoke(app, ["init", "-d", str(tmp_path / "recordings")])
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count("## Agent testing with DriftCatch") == 1
