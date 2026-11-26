"""Machine-readable output, for when the caller is a coding agent.

Cursor, Claude Code and friends drive tools by running shell commands and
reading stdout. A table drawn with box characters is good for a human and
hostile to a parser, so every read-only command can emit JSON instead.

The conventions here follow what the agent-CLI guidance has converged on:

* one uniform ``--json`` flag across every command, never a mix of spellings;
* results on **stdout**, diagnostics on **stderr**, so stdout is a clean contract;
* **errors are JSON too** in JSON mode - an agent should never have to parse a
  stack trace to learn that a recording is missing;
* a **documented exit-code taxonomy**, so an agent can branch on the number
  before it reads anything;
* nothing interactive, ever, when ``--json`` is set.
"""

from __future__ import annotations

import json
import sys
from enum import IntEnum
from typing import Any, NoReturn

__all__ = ["AGENTS_SECTION", "ExitCode", "capabilities", "emit", "fail", "payload"]


class ExitCode(IntEnum):
    """What an agent can branch on without reading output at all."""

    OK = 0
    #: Something went wrong that isn't one of the specific cases below.
    ERROR = 1
    #: Reserved by the CLI framework for bad usage.
    USAGE = 2
    #: Behaviour changed: a trajectory diff or a learned invariant broke.
    REGRESSION = 3
    #: A quality gate failed, e.g. coverage below --fail-under.
    GATE_FAILED = 4
    #: Credential-shaped values were found in recordings.
    SECRETS_FOUND = 5
    #: The named recording does not exist.
    NOT_FOUND = 6
    #: A recording was used as golden without human approval.
    NOT_APPROVED = 7


#: Written into the user's project by ``drift init`` so that any coding agent
#: working in that repo knows the workflow without being told.
AGENTS_SECTION = """\
## Agent testing with DriftCatch

This project uses DriftCatch to pin agent behaviour. Read this before changing
any prompt, tool, or agent code.

### The loop
1. `pytest` - replays recorded runs. Free, offline, deterministic.
2. If it fails with `NoMatchError`, behaviour changed.
   **Do not re-record to make it pass.**
   Read `changed_inputs` first and decide whether it is a bug.
3. `drift bisect <name> --previous` - says which input caused the change.
4. Only if the change is intended: `drift record --drift-update`, then
   `drift approve <name> --by <who>`. The previous version is archived; undo
   with `drift restore <name>`.

### Rules
- Never edit files under the recordings directory by hand.
- Never approve a recording you have not inspected; approval means "this
  behaviour is correct", and it is how wrong behaviour gets locked in.
- A changed **user question** is a NEW scenario - give it a new name. A changed
  **system prompt, model, or tool schema** is drift - re-record that recording.
- Live runs cost money. Never add `DRIFTCATCH_ALLOW_LIVE=1` without being asked.
- `drift contracts` reports where a tool's declared schema and its real use
  disagree. It is analysis, never a gate, and never fails a build.

### Machine-readable output
Every read command takes `--json`. Prefer it; parse stdout, ignore stderr.

```bash
drift status --json       # recordings and their approval state
drift coverage --json     # which behaviours were never tested
drift invariants --json   # learned rules and whether they are enforced
drift contracts --json    # declared tool schemas vs how they are really used
drift learn --json        # mine rules from approved recordings
drift scan --json         # credential audit
drift explain --json      # this contract, as data
```

`drift status --require-approved` exits 7 when any recording is still pending,
for use as a CI gate.

### Exit codes
`0` ok · `1` error · `2` usage · `3` behaviour regression · `4` gate failed
(e.g. coverage) · `5` secrets found · `6` recording not found · `7` not approved
"""


def payload(command: str, data: dict[str, Any], *, ok: bool = True) -> dict[str, Any]:
    """Wrap a result in the envelope every JSON response shares.

    A stable envelope means an agent can check ``ok`` and ``command`` without
    knowing anything about the specific command it called.
    """
    return {"ok": ok, "command": command, **data}


def emit(data: dict[str, Any]) -> None:
    """Write one JSON object to stdout and nothing else."""
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")


def capabilities() -> dict[str, Any]:
    """A self-describing manifest, so an agent needs no external docs.

    This is the single discovery entrypoint: one call tells an agent what the
    tool can do, which commands are safe to run unattended, what the exit codes
    mean, and what the correct workflow is.
    """
    return {
        "name": "driftcatch",
        "purpose": (
            "Behaviour regression testing for AI agents. Records agent runs, "
            "replays them offline for free, detects when behaviour or its inputs "
            "change, and learns the rules the agent always follows."
        ),
        "not_for": [
            "production use - this is a test tool, never a response cache",
            "scoring answer quality - that is evaluation, a different discipline",
            "production monitoring - that is observability",
        ],
        "key_facts": [
            "Replay is free and offline; it catches changes to YOUR prompt, "
            "model id, tool schemas and code.",
            "Replay CANNOT detect what the model would decide today, because the "
            "model does not run. That needs a deliberate live check.",
            "A recording is not trusted until a human approves it.",
            "Live runs cost money and are refused unless DRIFTCATCH_ALLOW_LIVE=1.",
        ],
        "exit_codes": {int(c): c.name.lower() for c in ExitCode},
        "commands": {
            "status": {
                "reads_only": True,
                "json": True,
                "purpose": "recordings and approval state",
            },
            "show": {"reads_only": True, "json": False, "purpose": "one recording in full"},
            "coverage": {
                "reads_only": True,
                "json": True,
                "purpose": "which behaviours were never tested",
                "gate": "--fail-under N exits 4",
            },
            "suggest": {"reads_only": True, "json": False, "purpose": "scenarios worth adding"},
            "learn": {
                "reads_only": False,
                "json": True,
                "purpose": "mine rules from approved recordings",
                "note": "mined rules are not enforced until --approve",
            },
            "invariants": {"reads_only": True, "json": True, "purpose": "learned rules"},
            "diff": {"reads_only": True, "json": False, "purpose": "behaviour diff"},
            "bisect": {
                "reads_only": True,
                "json": False,
                "purpose": "which input caused a behaviour change",
            },
            "scan": {
                "reads_only": True,
                "json": True,
                "purpose": "credential audit",
                "gate": "exits 5 on findings",
            },
            "audit": {"reads_only": True, "json": False, "purpose": "how each request keys"},
            "review": {
                "reads_only": False,
                "json": False,
                "purpose": "approve or reject",
                "warning": "approval means a human confirmed the behaviour is correct",
            },
            "record": {"reads_only": False, "json": False, "purpose": "re-record via pytest"},
            "restore": {"reads_only": False, "json": False, "purpose": "undo a re-record"},
            "check": {
                "reads_only": False,
                "json": False,
                "purpose": "live verification",
                "warning": "--live spends real money",
            },
        },
        "workflows": {
            "tests_failed_with_nomatch": [
                "read changed_inputs on the take",
                "drift bisect <name> --previous",
                "decide: bug -> fix the code; intended -> drift record",
                "drift approve <name> --by <who>",
            ],
            "improve_coverage": [
                "drift coverage --json",
                "drift suggest",
                "add a what_if test for each uncovered error path",
            ],
            "adopt_in_a_new_project": [
                "drift init",
                "decorate tools with @drift.tool",
                "wrap one test in `with drift.take(name) as take:`",
                "pytest, then drift review",
            ],
        },
        "cautions": [
            "Never hand-edit recording files.",
            "Never approve a recording without inspecting it.",
            "Never re-record merely to make a failing test pass.",
            "A changed user question is a new scenario, not drift.",
        ],
    }


def fail(
    command: str,
    message: str,
    *,
    code: ExitCode = ExitCode.ERROR,
    json_mode: bool = False,
    hint: str | None = None,
    **extra: Any,
) -> NoReturn:
    """Exit with a structured error.

    In JSON mode the error goes to stdout as data, because for an agent a
    failure is a result like any other. ``hint`` carries the next command to
    run, which is usually the only thing the agent actually needs.
    """
    import typer

    if json_mode:
        emit(
            payload(
                command,
                {"error": message, "hint": hint, "exit_code": int(code), **extra},
                ok=False,
            )
        )
    else:
        sys.stderr.write(f"error: {message}\n")
        if hint:
            sys.stderr.write(f"hint: {hint}\n")
    raise typer.Exit(code=int(code))
