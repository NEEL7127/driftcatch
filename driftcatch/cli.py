"""The ``drift`` command line interface.

Commands split into two groups, and the difference is honest rather than hidden:

* **Read-only commands** (``status``, ``coverage``, ``diff``, ``bisect``,
  ``audit``, ``scan``, ``review``) work entirely on recording files.
* **Commands that need to run your agent** (``record``, ``check``) delegate to
  pytest, because the agent lives in your test code - the CLI has no way to call
  it directly, and pretending otherwise would just produce a broken command.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from driftcatch._name import CLI_NAME, ENV_PREFIX
from driftcatch.agentio import (
    AGENTS_SECTION,
    ExitCode,
    capabilities,
    emit,
    fail,
    payload,
)
from driftcatch.bisect import bisect as run_bisect
from driftcatch.bisect import render as render_bisect
from driftcatch.config import Config, get_config
from driftcatch.contracts import extract as extract_contracts
from driftcatch.contracts import render as render_contracts
from driftcatch.contracts import to_json as contracts_to_json
from driftcatch.coverage import analyse as analyse_coverage
from driftcatch.coverage import render as render_coverage
from driftcatch.errors import DriftCatchError, RecordingNotApproved, RecordingNotFound
from driftcatch.invariants import (
    DEFAULT_MIN_SUPPORT,
    invariants_path,
    load_invariants,
    save_invariants,
)
from driftcatch.invariants import merge as merge_invariants
from driftcatch.invariants import mine as mine_invariants
from driftcatch.invariants import render as render_invariants
from driftcatch.match.keys import event_payload, key_for
from driftcatch.match.normalizers import apply_normalizers
from driftcatch.recording.io import (
    list_recordings,
    load_recording,
    previous_path,
    recording_path,
    restore_previous,
    save_recording,
)
from driftcatch.recording.schema import LLMEvent, Recording, Status, ToolEvent
from driftcatch.redact import find_secrets
from driftcatch.review import approve as approve_recording
from driftcatch.review import reject as reject_recording
from driftcatch.trajectory import diff as diff_trajectory
from driftcatch.trajectory import render as render_trajectory

app = typer.Typer(
    name=CLI_NAME,
    help="Catch the moment your AI agent's behaviour changes.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_STATUS_STYLE = {
    Status.APPROVED: "green",
    Status.PENDING: "yellow",
    Status.REJECTED: "red",
    Status.STALE: "magenta",
}

DirOption = typer.Option(None, "--recordings-dir", "-d", help="Directory of recording files.")
JsonOption = typer.Option(
    False,
    "--json",
    help="Emit machine-readable JSON on stdout. For coding agents and CI.",
)


def _config(recordings_dir: Path | None) -> Config:
    cfg = get_config()
    if recordings_dir is not None:
        cfg = cfg.merged(recordings_dir=recordings_dir)
    return cfg


def _load(name: str, cfg: Config) -> Recording:
    return load_recording(recording_path(name, cfg.recordings_dir))


#: Exceptions whose exit code the CLI documents. Mapping here rather than at
#: each call site is what keeps the contract consistent: every command that
#: funnels a DriftCatchError through `_die` gets the right code for free, and
#: a command cannot quietly report "not found" as a generic error.
_EXIT_FOR: dict[type[BaseException], ExitCode] = {
    RecordingNotFound: ExitCode.NOT_FOUND,
    RecordingNotApproved: ExitCode.NOT_APPROVED,
}


def _exit_code_for(exc: BaseException) -> ExitCode:
    for kind, code in _EXIT_FOR.items():
        if isinstance(exc, kind):
            return code
    return ExitCode.ERROR


def _die(message: str, *, code: ExitCode = ExitCode.ERROR) -> None:
    console.print(f"[red]error[/red] {message}")
    raise typer.Exit(code=int(code))


def _die_from(exc: BaseException) -> None:
    """Report a library error with the exit code its type promises."""
    _die(str(exc), code=_exit_code_for(exc))


# --------------------------------------------------------------- version


@app.command()
def version() -> None:
    """Print the installed version."""
    from driftcatch import __version__

    console.print(f"{CLI_NAME} {__version__}")


@app.command()
def explain(as_json: bool = JsonOption) -> None:
    """Describe this tool to a coding agent: commands, exit codes, workflows.

    A single discovery entrypoint, so an agent can work correctly without
    having read any documentation.
    """
    manifest = capabilities()
    if as_json:
        emit(payload("explain", manifest))
        return

    console.print(f"[bold]{manifest['name']}[/bold] - {manifest['purpose']}\n")
    console.print("[bold]Key facts[/bold]")
    for fact in manifest["key_facts"]:
        console.print(f"  - {fact}")
    console.print("\n[bold]Not for[/bold]")
    for item in manifest["not_for"]:
        console.print(f"  - {item}")
    console.print("\n[bold]Cautions[/bold]")
    for caution in manifest["cautions"]:
        console.print(f"  [yellow]![/yellow] {caution}")
    console.print(
        f"\nRun [bold]{CLI_NAME} explain --json[/bold] for the full machine-readable manifest."
    )


@app.command()
def mcp(recordings_dir: Path | None = DirOption) -> None:
    """Run as an MCP server, so a coding agent can call DriftCatch as tools.

    Exposes reading and analysis only. Approving recordings, re-recording and
    live runs are deliberately not available to an agent - approval means a
    human confirmed the behaviour is correct, and that judgement is theirs.
    """
    cfg = _config(recordings_dir)
    try:
        from driftcatch.mcp_server import serve as serve_mcp
    except ImportError:
        fail(
            "mcp",
            'the MCP server needs extra packages: pip install "driftcatch[mcp]"',
            code=ExitCode.ERROR,
            hint='pip install "driftcatch[mcp]"',
        )
    serve_mcp(str(cfg.recordings_dir))


@app.command()
def init(recordings_dir: Path | None = DirOption) -> None:
    """Set up DriftCatch in this project and print a starter test."""
    cfg = _config(recordings_dir)
    cfg.recordings_dir.mkdir(parents=True, exist_ok=True)

    gitignore = cfg.recordings_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# The archived copy of a re-recorded golden is a local safety net,\n"
            "# not something to commit. Recordings themselves SHOULD be committed.\n"
            ".previous/\n",
            encoding="utf-8",
        )

    # Teach whatever coding agent works in this repo how to use the tool.
    # AGENTS.md is read by Cursor, Claude Code, Codex and others; appending
    # rather than overwriting means an existing file is never clobbered.
    agents_file = Path.cwd() / "AGENTS.md"
    existing = agents_file.read_text(encoding="utf-8") if agents_file.exists() else ""
    if "DriftCatch" not in existing:
        separator = "\n\n" if existing.strip() else ""
        agents_file.write_text(existing + separator + AGENTS_SECTION, encoding="utf-8")
        console.print(f"[green]wrote[/green] agent instructions to {agents_file}")
    else:
        console.print("[dim]AGENTS.md already mentions DriftCatch, left alone[/dim]")

    console.print(f"[green]ready[/green] recordings will live in {cfg.recordings_dir}\n")
    snippet = """import driftcatch as drift


@drift.tool                      # one line per tool you want captured
def lookup_order(order_id: int) -> dict:
    return {"id": order_id, "total": 40}


def test_my_agent():
    with drift.take("first_scenario") as take:
        result = my_agent("do the thing")

    take.assert_tools().called("lookup_order")
    take.assert_trajectory().matches_golden()
"""
    console.print(Panel(Syntax(snippet, "python", theme="ansi_dark"), expand=False))
    console.print("Then:")
    console.print("  1. [bold]pytest[/bold]                 records a pending recording")
    console.print(f"  2. [bold]{CLI_NAME} review[/bold]          approve it -> golden")
    console.print("  3. [bold]pytest[/bold]                 replays: free, offline, deterministic")
    console.print(f"  4. [bold]{CLI_NAME} coverage[/bold]        see which behaviours are untested")


# ---------------------------------------------------------------- status


@app.command()
def status(
    recordings_dir: Path | None = DirOption,
    as_json: bool = JsonOption,
    require_approved: bool = typer.Option(
        False,
        "--require-approved",
        help="Exit 7 if any recording is still pending. For CI.",
    ),
) -> None:
    """List every recording with its approval status and drift state."""
    cfg = _config(recordings_dir)
    recordings = list_recordings(cfg.recordings_dir)
    pending = [r.name for r in recordings if r.status is Status.PENDING]

    if as_json:
        emit(
            payload(
                "status",
                {
                    "recordings_dir": str(cfg.recordings_dir),
                    "total": len(recordings),
                    "counts": {
                        st.value: sum(1 for r in recordings if r.status is st) for st in Status
                    },
                    "pending": pending,
                    "recordings": [
                        {
                            "name": r.name,
                            "status": r.status.value,
                            "trajectory": r.trajectory.tools_called,
                            "events": len(r.events),
                            "tokens": r.meta.usage.total_tokens,
                            "approved_by": r.approved_by,
                        }
                        for r in recordings
                    ],
                },
            )
        )
        if require_approved and pending:
            raise typer.Exit(code=int(ExitCode.NOT_APPROVED))
        return

    if not recordings:
        console.print(f"No recordings in [cyan]{cfg.recordings_dir}[/cyan].")
        console.print(f"Record one by running your tests: [bold]{CLI_NAME} record[/bold]")
        return

    table = Table(title=f"Recordings in {cfg.recordings_dir}")
    table.add_column("NAME")
    table.add_column("STATUS")
    table.add_column("BEHAVIOUR")
    table.add_column("EVENTS", justify="right")
    table.add_column("TOKENS", justify="right")
    table.add_column("APPROVED BY")

    for recording in recordings:
        style = _STATUS_STYLE.get(recording.status, "white")
        table.add_row(
            recording.name,
            f"[{style}]{recording.status.value}[/{style}]",
            " -> ".join(recording.trajectory.tools_called) or "-",
            str(len(recording.events)),
            str(recording.meta.usage.total_tokens),
            recording.approved_by or "-",
        )
    console.print(table)

    stale = [r for r in recordings if r.status is Status.STALE]
    if pending:
        console.print(
            f"\n[yellow]{len(pending)} recording(s) awaiting approval.[/yellow] "
            f"Review them: [bold]{CLI_NAME} review[/bold]"
        )
    if stale:
        console.print(
            f"[magenta]{len(stale)} recording(s) stale.[/magenta] "
            f"Re-verify: [bold]{CLI_NAME} check --live <name>[/bold]"
        )

    if require_approved and pending:
        console.print(
            f"\n[red]{len(pending)} recording(s) have not been approved: {', '.join(pending)}[/red]"
        )
        raise typer.Exit(code=int(ExitCode.NOT_APPROVED))


# ------------------------------------------------------------------ show


@app.command()
def show(name: str, recordings_dir: Path | None = DirOption) -> None:
    """Print a recording: its trajectory, then every event in order."""
    cfg = _config(recordings_dir)
    try:
        recording = _load(name, cfg)
    except DriftCatchError as exc:
        _die_from(exc)
        return

    style = _STATUS_STYLE.get(recording.status, "white")
    header = [
        f"status:     [{style}]{recording.status.value}[/{style}]",
        f"recorded:   {recording.created_at:%Y-%m-%d %H:%M}",
        f"approved:   {recording.approved_by or '-'}",
        f"trajectory: {' -> '.join(recording.trajectory.tools_called) or '(no tools)'}",
        f"usage:      {recording.meta.usage.total_tokens} tokens",
    ]
    console.print(Panel("\n".join(header), title=f"recording: {name}", expand=False))

    for event in recording.events:
        if isinstance(event, LLMEvent):
            decided = ", ".join(event.decided) or "(text reply)"
            console.print(f"  [cyan]#{event.index} llm[/cyan]  -> {decided}")
            for message in event.request.messages:
                role = str(message.get("role", "?"))
                content = str(message.get("content", ""))[:100]
                console.print(f"       {role:<9} {content}")
            if event.response and event.response.content:
                console.print(f"       [dim]reply[/dim]     {event.response.content[:100]}")
        elif isinstance(event, ToolEvent):
            outcome = "[red]error[/red]" if event.error else "[green]ok[/green]"
            console.print(f"  [magenta]#{event.index} tool[/magenta] {event.name} {outcome}")
            console.print(f"       args      {event.args}")
            if event.error:
                console.print(f"       error     {event.error.type}: {event.error.message}")
            else:
                console.print(f"       result    {str(event.result)[:100]}")


# ---------------------------------------------------------------- review


@app.command()
def review(
    name: str | None = typer.Argument(None, help="Recording to review; omit for all pending."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve without prompting (CI)."),
    by: str = typer.Option("cli", "--by", help="Who is approving."),
    recordings_dir: Path | None = DirOption,
) -> None:
    """Inspect recordings and approve or reject them.

    Nothing becomes golden without passing through here: recording states what
    happened, a human states what is correct.
    """
    cfg = _config(recordings_dir)
    if name:
        targets = [_load(name, cfg)]
    else:
        targets = [
            r
            for r in list_recordings(cfg.recordings_dir)
            if r.status in {Status.PENDING, Status.STALE}
        ]

    if not targets:
        console.print("Nothing to review. Every recording is approved or rejected.")
        return

    for recording in targets:
        show(recording.name, cfg.recordings_dir)
        if yes:
            approve_recording(recording.name, by=by, config=cfg)
            console.print(f"[green]approved[/green] {recording.name}\n")
            continue

        choice = (
            typer.prompt(f"[{recording.name}] (a)pprove / (r)eject / (s)kip", default="s")
            .strip()
            .lower()
        )
        if choice.startswith("a"):
            approve_recording(recording.name, by=by, config=cfg)
            console.print(f"[green]approved[/green] {recording.name}\n")
        elif choice.startswith("r"):
            reject_recording(recording.name, config=cfg)
            console.print(f"[red]rejected[/red] {recording.name} - fix the agent, then re-record\n")
        else:
            console.print(f"[dim]skipped[/dim] {recording.name}\n")


# ------------------------------------------------------------------ diff


@app.command()
def diff(
    name: str,
    against: str | None = typer.Option(None, "--against", help="Compare with another recording."),
    tolerance: str = typer.Option("tools+order", "--tolerance", "-t"),
    recordings_dir: Path | None = DirOption,
) -> None:
    """Show the behaviour difference between two recordings."""
    cfg = _config(recordings_dir)
    try:
        golden = _load(name, cfg)
    except DriftCatchError as exc:
        _die_from(exc)
        return

    if against is None:
        console.print(
            Panel(
                " -> ".join(golden.trajectory.tools_called) or "(no tools)",
                title=f"{name} behaviour",
                expand=False,
            )
        )
        console.print("Pass --against <other> to compare two recordings.")
        return

    candidate = _load(against, cfg)
    result = diff_trajectory(golden.trajectory, candidate.trajectory, tolerance)
    console.print(render_trajectory(result))
    if not result.matched:
        raise typer.Exit(code=1)


# -------------------------------------------------------------- coverage


@app.command()
def coverage(
    fail_under: float | None = typer.Option(
        None, "--fail-under", help="Exit non-zero below this percentage (CI gate)."
    ),
    badge: bool = typer.Option(False, "--badge", help="Print a README badge instead."),
    recordings_dir: Path | None = DirOption,
    as_json: bool = JsonOption,
) -> None:
    """Behaviour coverage: which of the agent's paths were ever tested."""
    cfg = _config(recordings_dir)
    recordings = list_recordings(cfg.recordings_dir)
    if not recordings:
        fail(
            "coverage",
            f"no recordings in {cfg.recordings_dir}",
            code=ExitCode.NOT_FOUND,
            json_mode=as_json,
            hint=f"{CLI_NAME} record",
        )

    report = analyse_coverage(recordings)

    if as_json:
        passed = fail_under is None or report.percent >= fail_under
        emit(
            payload(
                "coverage",
                {
                    **report.model_dump(mode="json"),
                    "fail_under": fail_under,
                    "passed": passed,
                },
                ok=passed,
            )
        )
        raise typer.Exit(code=0 if passed else int(ExitCode.GATE_FAILED))

    if badge:
        colour = (
            "brightgreen" if report.percent >= 80 else "yellow" if report.percent >= 50 else "red"
        )
        label = "behaviour%20coverage"
        url = f"https://img.shields.io/badge/{label}-{report.percent:.0f}%25-{colour}"
        console.print(f"![behaviour coverage]({url})")
        return

    console.print(render_coverage(report))

    if fail_under is not None and report.percent < fail_under:
        console.print(
            f"\n[red]coverage {report.percent:.1f}% is below the required {fail_under:.1f}%[/red]"
        )
        raise typer.Exit(code=int(ExitCode.GATE_FAILED))


@app.command()
def suggest(recordings_dir: Path | None = DirOption) -> None:
    """Propose scenarios that would close the biggest coverage gaps."""
    cfg = _config(recordings_dir)
    recordings = list_recordings(cfg.recordings_dir)
    if not recordings:
        _die(f"no recordings in {cfg.recordings_dir}")
        return

    report = analyse_coverage(recordings)
    if not report.uncovered:
        console.print("[green]Every declared behaviour already has coverage.[/green]")
        return

    example = recordings[0].name
    console.print("[bold]Suggested next scenarios[/bold]\n")
    for tool_name, tool in sorted(report.tools.items()):
        if not tool.covered:
            console.print(f"  [yellow]{tool_name}[/yellow] has never been called.")
            console.print("    Write a scenario that makes the agent choose it, then record it.\n")
        elif not tool.error_path_covered:
            console.print(f"  [yellow]{tool_name}[/yellow] has never failed in a test.")
            console.print(
                f"    No re-recording needed:\n"
                f"      [bold]{CLI_NAME} what-if {example} --fail-tool {tool_name}[/bold]\n"
            )


# -------------------------------------------------------------- what-if


@app.command(name="what-if")
def what_if(
    name: str,
    fail_tool: str | None = typer.Option(None, "--fail-tool", help="Make this tool raise."),
    recordings_dir: Path | None = DirOption,
) -> None:
    """Explain how to run a counterfactual against a recording.

    The mutation has to run inside your test, because only your test knows how
    to invoke the agent. This prints the snippet to drop in.
    """
    cfg = _config(recordings_dir)
    try:
        _load(name, cfg)
    except DriftCatchError as exc:
        _die_from(exc)
        return

    tool_name = fail_tool or "<tool>"
    snippet = f'''import driftcatch as drift

def test_{name}_handles_{tool_name}_failure():
    with drift.take("{name}").what_if(
        tool="{tool_name}", raises="TimeoutError"
    ) as take:
        run_my_agent(...)

    # assert the agent handled it, e.g.
    take.assert_tools().called("escalate_to_human")
'''
    console.print(
        Panel(Syntax(snippet, "python", theme="ansi_dark"), title="what-if", expand=False)
    )


# --------------------------------------------------------------- bisect


@app.command()
def bisect(
    golden: str,
    candidate: str | None = typer.Argument(None, help="Recording to compare against."),
    previous: bool = typer.Option(
        False, "--previous", help="Compare against the version replaced by the last re-record."
    ),
    recordings_dir: Path | None = DirOption,
) -> None:
    """Attribute a behaviour change to the input that caused it."""
    cfg = _config(recordings_dir)

    if previous:
        archived = previous_path(golden, cfg.recordings_dir)
        if not archived.exists():
            _die(
                f"no previous version of {golden!r}. One is kept only after a re-record "
                f"replaces an existing recording."
            )
            return
        try:
            # The archived copy is the old golden; the file on disk is the new take.
            left = load_recording(archived)
            right = _load(golden, cfg)
        except DriftCatchError as exc:
            _die_from(exc)
            return
    elif candidate is None:
        _die("pass a second recording name, or use --previous")
        return
    else:
        try:
            left = _load(golden, cfg)
            right = _load(candidate, cfg)
        except DriftCatchError as exc:
            _die_from(exc)
            return

    console.print(render_bisect(run_bisect(left, right)))


@app.command()
def learn(
    approve: bool = typer.Option(False, "--approve", help="Enforce every mined invariant."),
    min_support: int = typer.Option(
        DEFAULT_MIN_SUPPORT, "--min-support", help="Runs a pattern needs before it counts."
    ),
    recordings_dir: Path | None = DirOption,
    as_json: bool = JsonOption,
) -> None:
    """Learn the rules your agent always follows, from approved recordings.

    Mined rules are evidence, not truth - Daikon's own evaluation found about a
    third of inferred invariants were wrong or irrelevant - so nothing is
    enforced until a human approves it.
    """
    cfg = _config(recordings_dir)
    recordings = list_recordings(cfg.recordings_dir)
    golden = [r for r in recordings if r.is_golden]

    mined = mine_invariants(recordings, min_support=min_support)
    path = invariants_path(cfg.recordings_dir)
    mined = merge_invariants(mined, load_invariants(path))

    if approve:
        for invariant in mined:
            invariant.approved = True

    if mined:
        save_invariants(mined, path)

    if as_json:
        emit(
            payload(
                "learn",
                {
                    "corpus": len(golden),
                    "mined": len(mined),
                    "enforced": sum(1 for i in mined if i.approved),
                    "path": str(path),
                    "invariants": [
                        {
                            "key": i.key,
                            "kind": i.kind.value,
                            "description": i.description,
                            "support": i.support,
                            "applicable": i.applicable,
                            "confidence": round(i.confidence, 4),
                            "approved": i.approved,
                        }
                        for i in mined
                    ],
                },
            )
        )
        return

    console.print(render_invariants(mined, corpus=len(golden)))
    if mined and not approve:
        console.print(f"\n  saved to [cyan]{path}[/cyan]")


@app.command()
def invariants(
    recordings_dir: Path | None = DirOption,
    as_json: bool = JsonOption,
) -> None:
    """Show the learned rules and whether they are enforced."""
    cfg = _config(recordings_dir)
    learned = load_invariants(invariants_path(cfg.recordings_dir))

    if as_json:
        emit(
            payload(
                "invariants",
                {
                    "total": len(learned),
                    "enforced": sum(1 for i in learned if i.approved),
                    "invariants": [
                        {
                            "key": i.key,
                            "kind": i.kind.value,
                            "description": i.description,
                            "support": i.support,
                            "applicable": i.applicable,
                            "confidence": round(i.confidence, 4),
                            "approved": i.approved,
                        }
                        for i in learned
                    ],
                },
            )
        )
        return

    if not learned:
        console.print(f"No invariants learned yet. Run [bold]{CLI_NAME} learn[/bold].")
        return

    table = Table(title="learned invariants")
    table.add_column("ENFORCED")
    table.add_column("RULE")
    table.add_column("EVIDENCE", justify="right")
    table.add_column("CONF", justify="right")
    for invariant in learned:
        table.add_row(
            "[green]yes[/green]" if invariant.approved else "[yellow]no[/yellow]",
            invariant.description,
            f"{invariant.support}/{invariant.applicable}",
            f"{invariant.confidence:.2f}",
        )
    console.print(table)


@app.command()
def contracts(
    recordings_dir: Path | None = DirOption,
    as_json: bool = JsonOption,
) -> None:
    """Compare each tool's declared schema with how it is really used.

    Reports dead schema surface, invented arguments and unstable result shapes.
    This never fails a build: it is an analysis of the approved corpus, while
    'drift invariants' is the gate.
    """
    cfg = _config(recordings_dir)
    recordings = list_recordings(cfg.recordings_dir)
    approved = [r for r in recordings if r.is_golden]
    found = extract_contracts(recordings)

    if as_json:
        emit(payload("contracts", contracts_to_json(found, corpus=len(approved))))
        return

    console.print(render_contracts(found, corpus=len(approved)))


@app.command()
def approve(
    name: str,
    by: str = typer.Option("cli", "--by", help="Who is approving."),
    recordings_dir: Path | None = DirOption,
) -> None:
    """Approve a recording, making it the golden for that scenario.

    The non-interactive half of `drift review`. Both exist because approving a
    known-good recording in a script and walking the pending queue by hand are
    different jobs, and making the scripted one go through a prompt is how
    people end up piping `yes` into it.
    """
    cfg = _config(recordings_dir)
    try:
        approve_recording(name, by=by, config=cfg)
    except RecordingNotFound as exc:
        fail("approve", str(exc), code=ExitCode.NOT_FOUND, hint=f"{CLI_NAME} status")
        return
    except DriftCatchError as exc:
        _die_from(exc)
        return
    console.print(f"[green]approved[/green] [cyan]{name}[/cyan] by {by}")


@app.command()
def reject(
    name: str,
    by: str = typer.Option("cli", "--by", help="Who is rejecting."),
    recordings_dir: Path | None = DirOption,
) -> None:
    """Reject a recording, so it is never used as a golden."""
    cfg = _config(recordings_dir)
    try:
        reject_recording(name, config=cfg)
    except RecordingNotFound as exc:
        fail("reject", str(exc), code=ExitCode.NOT_FOUND, hint=f"{CLI_NAME} status")
        return
    except DriftCatchError as exc:
        _die_from(exc)
        return
    console.print(
        f"[yellow]rejected[/yellow] [cyan]{name}[/cyan] by {by} - fix the agent, then re-record"
    )


@app.command()
def restore(name: str, recordings_dir: Path | None = DirOption) -> None:
    """Undo a re-record, putting the previous version back."""
    cfg = _config(recordings_dir)
    try:
        target = restore_previous(name, cfg.recordings_dir)
    except DriftCatchError as exc:
        _die_from(exc)
        return
    recording = load_recording(target)
    console.print(
        f"restored [cyan]{name}[/cyan] -> status [green]{recording.status.value}[/green], "
        f"behaviour: {' -> '.join(recording.trajectory.tools_called) or '-'}"
    )


# ---------------------------------------------------------------- audit


@app.command()
def audit(name: str, recordings_dir: Path | None = DirOption) -> None:
    """Show how each recorded request keys, so a loose matcher is visible."""
    cfg = _config(recordings_dir)
    try:
        recording = _load(name, cfg)
    except DriftCatchError as exc:
        _die_from(exc)
        return

    table = Table(title=f"match keys for {name} (matcher: {cfg.matcher})")
    table.add_column("#", justify="right")
    table.add_column("KIND")
    table.add_column("WHAT")
    table.add_column("KEY")

    keys: dict[str, list[int]] = {}
    for event in recording.events:
        payload = apply_normalizers(event_payload(event), cfg.normalizers)
        key = key_for(payload)
        keys.setdefault(key, []).append(event.index)
        what = event.name if isinstance(event, ToolEvent) else event.request.model
        table.add_row(str(event.index), event.type, what, key[7:19])

    console.print(table)

    duplicates = {key: indices for key, indices in keys.items() if len(indices) > 1}
    if duplicates:
        console.print(
            "\n[yellow]Identical keys found.[/yellow] These are served in order, which is "
            "what makes retries and polling work - but if they were meant to be distinct, "
            "tighten the matcher or reduce the normalizer set."
        )
        for key, indices in duplicates.items():
            console.print(f"  {key[7:19]}  events {indices}")
    else:
        console.print("\n[green]Every request keys uniquely.[/green]")


# ----------------------------------------------------------------- scan


@app.command()
def scan(
    recordings_dir: Path | None = DirOption,
    as_json: bool = JsonOption,
) -> None:
    """Audit recordings for credential-shaped values. Pre-commit friendly."""
    cfg = _config(recordings_dir)
    recordings = list_recordings(cfg.recordings_dir)
    if not recordings:
        console.print(f"No recordings in {cfg.recordings_dir}.")
        return

    offenders: dict[str, list[str]] = {}
    for recording in recordings:
        found = find_secrets([e.model_dump(mode="json") for e in recording.events])
        if found:
            offenders[recording.name] = found

    if as_json:
        emit(
            payload(
                "scan",
                {"scanned": len(recordings), "offenders": offenders},
                ok=not offenders,
            )
        )
        raise typer.Exit(code=0 if not offenders else int(ExitCode.SECRETS_FOUND))

    if not offenders:
        console.print(f"[green]clean[/green] - scanned {len(recordings)} recording(s).")
        return

    for name, paths in offenders.items():
        console.print(f"[red]{name}[/red]")
        for path in paths:
            console.print(f"    {path}")
    console.print(f"\n[red]{len(offenders)} recording(s) contain secrets.[/red]")
    raise typer.Exit(code=int(ExitCode.SECRETS_FOUND))


# ---------------------------------------------------------------- prune


@app.command()
def prune(name: str, recordings_dir: Path | None = DirOption) -> None:
    """Drop raw response bodies, keeping the trajectory for behaviour tests."""
    cfg = _config(recordings_dir)
    path = recording_path(name, cfg.recordings_dir)
    try:
        recording = load_recording(path)
    except DriftCatchError as exc:
        _die_from(exc)
        return

    before = path.stat().st_size
    for event in recording.events:
        if isinstance(event, LLMEvent):
            event.raw_response = None
            event.stream_chunks = None
    save_recording(recording, path)
    after = path.stat().st_size
    console.print(
        f"pruned {name}: {before / 1024:.1f} KB -> {after / 1024:.1f} KB "
        f"(behaviour preserved, replay of raw bodies no longer possible)"
    )


# ----------------------------------------- commands that run your agent


#: pytest's own exit codes, translated into the ones this CLI documents so an
#: agent or a CI step can branch without parsing output. Two of these matter
#: more than the rest:
#:
#: * ``1`` (tests failed) becomes REGRESSION during a check, because that is
#:   what a failing behaviour replay means.
#: * ``5`` (no tests collected) must never become 0. "Verified nothing" and
#:   "verified everything" are the same exit code otherwise, which is the
#:   silent false green this project exists to prevent.
_PYTEST_FAILED = 1
_PYTEST_INTERRUPTED = 2
_PYTEST_INTERNAL_ERROR = 3
_PYTEST_USAGE_ERROR = 4
_PYTEST_NO_TESTS = 5


def _pytest(extra: list[str]) -> int:
    console.print(f"[dim]$ pytest {' '.join(extra)}[/dim]")
    return subprocess.call([sys.executable, "-m", "pytest", *extra])


def _translate_pytest(code: int, *, failure_means_regression: bool) -> int:
    """Map a pytest exit code onto this CLI's documented contract."""
    if code == 0:
        return int(ExitCode.OK)
    if code == _PYTEST_FAILED:
        return int(ExitCode.REGRESSION if failure_means_regression else ExitCode.ERROR)
    if code == _PYTEST_NO_TESTS:
        console.print(
            "[red]No tests were collected, so no behaviour was verified.[/red]\n"
            "This is reported as an error on purpose: exiting 0 here would be "
            "indistinguishable from a passing run."
        )
        return int(ExitCode.ERROR)
    if code == _PYTEST_USAGE_ERROR:
        return int(ExitCode.USAGE)
    if code in (_PYTEST_INTERRUPTED, _PYTEST_INTERNAL_ERROR):
        return int(ExitCode.ERROR)
    return code


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def record(ctx: typer.Context) -> None:
    """Record by running your tests. Extra arguments go to pytest."""
    code = _pytest(["--drift-mode=record", *ctx.args])
    # A failing test while recording is a failing test, not a regression:
    # there is no golden yet for the behaviour to have regressed from.
    raise typer.Exit(code=_translate_pytest(code, failure_means_regression=False))


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def check(
    ctx: typer.Context,
    live: bool = typer.Option(False, "--live", help="Run against the real model."),
) -> None:
    """Re-verify behaviour against the golden. Use --live for Tier 2 (costs money)."""
    if not live:
        code = _pytest(["--drift-mode=replay", *ctx.args])
        raise typer.Exit(code=_translate_pytest(code, failure_means_regression=True))
    console.print(
        f"[yellow]Live runs make real API calls.[/yellow] "
        f"Set {ENV_PREFIX}_ALLOW_LIVE=1 to permit them in CI."
    )
    code = _pytest(["--drift-mode=live", *ctx.args])
    raise typer.Exit(code=_translate_pytest(code, failure_means_regression=True))


# -------------------------------------------------------------------- ui


@app.command()
def ui(
    port: int = typer.Option(7777, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host"),
    recordings_dir: Path | None = DirOption,
) -> None:
    """Launch the local web UI."""
    cfg = _config(recordings_dir)
    try:
        from driftcatch.ui.server import serve
    except ImportError:
        _die('the UI needs extra packages. Install them with:\n    pip install "driftcatch[ui]"')
        return
    serve(cfg, host=host, port=port)


def main() -> Any:
    """Console-script entry point."""
    return app()


if __name__ == "__main__":  # pragma: no cover
    main()
