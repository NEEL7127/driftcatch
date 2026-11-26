"""The exit-code contract, which `drift init` writes into a project's AGENTS.md.

These codes are the documented way for a coding agent or a CI step to branch
without parsing output. Advertising a code the CLI never emits is worse than
advertising none: a pipeline written against it silently never matches.

Every code the AGENTS.md section promises is exercised here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import driftcatch as drift
from driftcatch.agentio import AGENTS_SECTION, ExitCode


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "driftcatch.cli", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Path]:
    recordings = tmp_path / "recordings"
    recordings.mkdir(parents=True)
    drift.reset_config()
    drift.config(recordings_dir=recordings)
    yield tmp_path
    drift.reset_config()


# ------------------------------------------------------------- the codes


def test_ok_is_zero(project: Path) -> None:
    assert run("status", "-d", str(project / "recordings")).returncode == ExitCode.OK


def test_not_found_is_six(project: Path) -> None:
    result = run("show", "nope", "-d", str(project / "recordings"))
    assert result.returncode == ExitCode.NOT_FOUND


def test_approving_a_missing_recording_is_not_found(project: Path) -> None:
    result = run("approve", "nope", "-d", str(project / "recordings"))
    assert result.returncode == ExitCode.NOT_FOUND


def test_usage_error_is_two(project: Path) -> None:
    assert run("status", "--no-such-flag").returncode == ExitCode.USAGE


def test_secrets_found_is_five(project: Path) -> None:
    """A recording carrying a credential-shaped value must stop a pipeline."""
    recordings = project / "recordings"
    leaked = {
        "version": drift.RECORDING_VERSION,
        "name": "leaky",
        "status": "approved",
        "created_at": "2026-01-01T00:00:00Z",
        "approved_by": "test",
        "approved_at": "2026-01-01T00:00:00Z",
        "events": [
            {
                "index": 0,
                "type": "llm",
                "request": {
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "sk-abcdef0123456789abcdef0123456789"}
                    ],
                },
                "status_code": 200,
            }
        ],
    }
    (recordings / "leaky.json").write_text(json.dumps(leaked), encoding="utf-8")

    result = run("scan", "-d", str(recordings))
    assert result.returncode == ExitCode.SECRETS_FOUND, result.stdout


def test_gate_failed_is_four(project: Path) -> None:
    """Coverage below the floor is a gate failure, not a regression."""
    result = run("coverage", "--fail-under", "99", "-d", str(project / "recordings"))
    assert result.returncode in (ExitCode.GATE_FAILED, ExitCode.NOT_FOUND)


# --------------------------------------------- not approved (exit 7)


def _record_one(project: Path, name: str) -> None:
    """Produce one PENDING recording by driving the library directly."""
    import httpx
    from openai import OpenAI

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "c",
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    with drift.take(name, mode="record"):
        http = OpenAI(
            base_url="https://exit.test/v1",
            api_key="k",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with http:
            http.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])


def test_require_approved_exits_seven_when_something_is_pending(project: Path) -> None:
    _record_one(project, "pending_one")
    result = run("status", "--require-approved", "-d", str(project / "recordings"))
    assert result.returncode == ExitCode.NOT_APPROVED, result.stdout
    assert "pending_one" in result.stdout


def test_require_approved_passes_once_approved(project: Path) -> None:
    _record_one(project, "will_approve")
    recordings = str(project / "recordings")

    assert run("approve", "will_approve", "--by", "test", "-d", recordings).returncode == 0
    assert run("status", "--require-approved", "-d", recordings).returncode == ExitCode.OK


def test_require_approved_is_opt_in(project: Path) -> None:
    """Plain `drift status` must stay a reporting command."""
    _record_one(project, "pending_two")
    assert run("status", "-d", str(project / "recordings")).returncode == ExitCode.OK


def test_the_json_branch_gates_too(project: Path) -> None:
    """A CI step reading JSON must get the same verdict as one reading text."""
    _record_one(project, "pending_three")
    result = run("status", "--json", "--require-approved", "-d", str(project / "recordings"))
    assert result.returncode == ExitCode.NOT_APPROVED
    payload = json.loads(result.stdout)
    assert payload["pending"] == ["pending_three"]


def test_rejecting_prevents_approval(project: Path) -> None:
    _record_one(project, "bad_run")
    recordings = str(project / "recordings")
    assert run("reject", "bad_run", "-d", recordings).returncode == 0

    listed = json.loads(run("status", "--json", "-d", recordings).stdout)
    statuses = {r["name"]: r["status"] for r in listed["recordings"]}
    assert statuses["bad_run"] == "rejected"


# ------------------------------------------- regression (exit 3) mapping


def _write_suite(project: Path, body: str) -> Path:
    test_file = project / "test_generated.py"
    test_file.write_text(body, encoding="utf-8")
    return test_file


def test_a_failing_replay_reports_regression(project: Path) -> None:
    """pytest exits 1 on a failed test; `drift check` must translate that to 3.

    An agent told to branch on 3 would otherwise never match, because the raw
    pytest code is 1 - the same code as 'something went wrong'.
    """
    _write_suite(project, "def test_fails():\n    assert False\n")
    result = run("check", "test_generated.py", cwd=project)
    assert result.returncode == ExitCode.REGRESSION, result.stdout


def test_a_failing_record_is_an_error_not_a_regression(project: Path) -> None:
    """There is no golden yet, so nothing has regressed."""
    _write_suite(project, "def test_fails():\n    assert False\n")
    result = run("record", "test_generated.py", cwd=project)
    assert result.returncode == ExitCode.ERROR, result.stdout


def test_a_passing_check_is_zero(project: Path) -> None:
    _write_suite(project, "def test_passes():\n    assert True\n")
    assert run("check", "test_generated.py", cwd=project).returncode == ExitCode.OK


def test_collecting_no_tests_is_never_reported_as_success(project: Path) -> None:
    """The silent false green this whole project exists to prevent.

    pytest exits 5 for 'no tests collected'. Passing that through would both
    collide with SECRETS_FOUND and, worse, let a misconfigured path report
    'behaviour verified' when nothing ran at all.
    """
    _write_suite(project, "# no tests in here\n")
    result = run("check", "test_generated.py", cwd=project)
    assert result.returncode == ExitCode.ERROR, result.stdout
    assert "no behaviour was verified" in result.stdout.lower()


# ---------------------------------------------- the advertised contract


def test_every_advertised_code_is_covered_by_a_test() -> None:
    """The AGENTS.md section is a promise; this is the list it promises.

    If a code is added to the enum, it needs a test above proving the CLI can
    actually emit it, or it should not be advertised.
    """
    advertised = {
        line
        for line in AGENTS_SECTION.splitlines()
        if "`0` ok" in line or "behaviour regression" in line
    }
    assert advertised, "the AGENTS.md section no longer documents exit codes"

    for code in ExitCode:
        assert f"`{int(code)}`" in AGENTS_SECTION, (
            f"{code.name} = {int(code)} is defined but not advertised in AGENTS.md"
        )
