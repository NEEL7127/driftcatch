"""MCP server, so a coding agent can call DriftCatch as tools.

Optional: ``pip install "driftcatch[mcp]"``, then ``drift mcp``.

The most important decision in this file is what is **not** here.

An agent's reflex when a test goes red is to make it green. In DriftCatch the
green button is ``approve`` - and approving a recording means "a human has
confirmed this behaviour is correct". If an agent could call that, it would
eventually approve a regression to clear a failing test, and the entire safety
model would quietly collapse. The approval gate only works if a human is the
one holding it.

So this server exposes **reading and analysis only**. The tools that change what
is trusted - ``approve``, ``reject``, ``record``, ``restore``, ``check --live`` -
are deliberately absent. Instead, :func:`pending_approvals` tells the agent
exactly what needs a human and which command the human should run, so the agent
can surface the decision without making it.

``learn`` is included because mining is read-only in effect: it proposes rules
and writes them as *pending*. It can never enforce one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from driftcatch._name import CLI_NAME
from driftcatch.agentio import capabilities
from driftcatch.bisect import bisect as run_bisect
from driftcatch.config import Config, get_config
from driftcatch.contracts import extract as extract_contracts
from driftcatch.contracts import to_json as contracts_to_json
from driftcatch.coverage import analyse as analyse_coverage
from driftcatch.errors import DriftCatchError
from driftcatch.invariants import (
    DEFAULT_MIN_SUPPORT,
    invariants_path,
    load_invariants,
    save_invariants,
)
from driftcatch.invariants import merge as merge_invariants
from driftcatch.invariants import mine as mine_invariants
from driftcatch.match.keys import event_payload, key_for
from driftcatch.match.normalizers import apply_normalizers
from driftcatch.recording.io import (
    list_recordings,
    load_recording,
    previous_path,
    recording_path,
)
from driftcatch.recording.schema import LLMEvent, Recording, Status, ToolEvent
from driftcatch.redact import find_secrets
from driftcatch.trajectory import diff as diff_trajectory

__all__ = ["build_server", "serve"]

INSTRUCTIONS = """\
DriftCatch pins an AI agent's behaviour so changes to it are caught in CI.

Read `explain` first. Three things govern how you should use these tools:

1. This is a TEST tool. It is never a production cache and never scores answer
   quality.
2. Replay is free and offline. It catches changes to the prompt, model id, tool
   schemas and the surrounding code. It CANNOT tell you what the model would
   decide today, because the model does not run.
3. When a test fails with NoMatchError, the behaviour changed. Do NOT try to
   make it pass by re-recording. Diagnose first with `bisect`, then ask the
   human to decide.

These tools only read and analyse. Approving recordings, re-recording,
restoring and live runs are intentionally not available to you: approval means
a human confirmed the behaviour is correct, and that judgement is theirs. Use
`pending_approvals` to tell the human what is waiting and which command to run.
"""


def _config(recordings_dir: str | None) -> Config:
    cfg = get_config()
    if recordings_dir:
        cfg = cfg.merged(recordings_dir=Path(recordings_dir))
    return cfg


def _load(name: str, cfg: Config) -> Recording:
    return load_recording(recording_path(name, cfg.recordings_dir))


def _summary(recording: Recording) -> dict[str, Any]:
    return {
        "name": recording.name,
        "status": recording.status.value,
        "trajectory": recording.trajectory.tools_called,
        "events": len(recording.events),
        "tokens": recording.meta.usage.total_tokens,
        "approved_by": recording.approved_by,
    }


def build_server(recordings_dir: str | None = None) -> Any:
    """Construct the MCP server.

    Separated from :func:`serve` so the tools can be exercised in tests without
    standing up a transport.
    """
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover - guarded by the CLI
        raise DriftCatchError(
            'the MCP server needs extra packages: pip install "driftcatch[mcp]"'
        ) from exc

    server = MCPServer(
        name="driftcatch",
        title="DriftCatch",
        description="Behaviour regression testing for AI agents.",
        instructions=INSTRUCTIONS,
    )

    # Every tool here reads. `open_world_hint=False` says the tool touches only
    # local project state, which lets a client treat these as safe to call.
    read_only = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

    @server.tool(
        description=(
            "What DriftCatch is, what it is not, the exit codes, and the correct "
            "workflows. Call this before anything else."
        ),
        annotations=read_only,
    )
    def explain() -> dict[str, Any]:
        return capabilities()

    @server.tool(
        description="List recordings with their approval status and behaviour.",
        annotations=read_only,
    )
    def status(recordings_dir: str | None = None) -> dict[str, Any]:
        cfg = _config(recordings_dir)
        recordings = list_recordings(cfg.recordings_dir)
        return {
            "recordings_dir": str(cfg.recordings_dir),
            "total": len(recordings),
            "counts": {s.value: sum(1 for r in recordings if r.status is s) for s in Status},
            "recordings": [_summary(r) for r in recordings],
        }

    @server.tool(
        description=(
            "Behaviour coverage: which of the agent's tools and error paths have "
            "never been exercised by any recording."
        ),
        annotations=read_only,
    )
    def coverage(recordings_dir: str | None = None) -> dict[str, Any]:
        cfg = _config(recordings_dir)
        recordings = list_recordings(cfg.recordings_dir)
        if not recordings:
            return {"error": "no recordings", "hint": f"{CLI_NAME} record"}
        return analyse_coverage(recordings).model_dump(mode="json")

    @server.tool(
        description="Show one recording: its trajectory and every captured event.",
        annotations=read_only,
    )
    def show(name: str, recordings_dir: str | None = None) -> dict[str, Any]:
        cfg = _config(recordings_dir)
        try:
            recording = _load(name, cfg)
        except DriftCatchError as exc:
            return {"error": str(exc)}

        events: list[dict[str, Any]] = []
        for event in recording.events:
            if isinstance(event, LLMEvent):
                events.append(
                    {
                        "index": event.index,
                        "type": "llm",
                        "model": event.request.model,
                        "messages": event.request.messages,
                        "tools_offered": [t.get("name") for t in event.request.tools],
                        "decided": event.decided,
                        "content": event.response.content if event.response else None,
                    }
                )
            elif isinstance(event, ToolEvent):
                events.append(
                    {
                        "index": event.index,
                        "type": "tool",
                        "name": event.name,
                        "args": event.args,
                        "result": event.result,
                        "error": event.error.model_dump() if event.error else None,
                    }
                )
        return {
            **_summary(recording),
            "fingerprint": recording.fingerprint.model_dump() if recording.fingerprint else None,
            "events_detail": events,
        }

    @server.tool(
        description=(
            "Compare the behaviour of two recordings and attribute any change to "
            "the input that caused it. Use this when a test fails, BEFORE "
            "suggesting any fix."
        ),
        annotations=read_only,
    )
    def bisect(
        golden: str,
        candidate: str | None = None,
        use_previous: bool = False,
        tolerance: str = "tools+order",
        recordings_dir: str | None = None,
    ) -> dict[str, Any]:
        cfg = _config(recordings_dir)
        try:
            if use_previous:
                archived = previous_path(golden, cfg.recordings_dir)
                if not archived.exists():
                    return {"error": f"no previous version of {golden!r}"}
                left, right = load_recording(archived), _load(golden, cfg)
            elif candidate:
                left, right = _load(golden, cfg), _load(candidate, cfg)
            else:
                return {"error": "pass candidate=<name> or use_previous=true"}
        except DriftCatchError as exc:
            return {"error": str(exc)}

        result = diff_trajectory(left.trajectory, right.trajectory, tolerance)
        report = run_bisect(left, right, tolerance=tolerance)
        return {
            "matched": result.matched,
            "golden_tools": result.golden_tools,
            "current_tools": result.current_tools,
            "missing": result.missing,
            "unexpected": result.unexpected,
            "attribution": report.model_dump(mode="json"),
        }

    @server.tool(
        description=(
            "Mine the rules the agent always follows from APPROVED recordings. "
            "Rules are proposed as pending and are never enforced here - a human "
            "must run `drift learn --approve`."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True
        ),
    )
    def learn(
        min_support: int = DEFAULT_MIN_SUPPORT,
        recordings_dir: str | None = None,
    ) -> dict[str, Any]:
        cfg = _config(recordings_dir)
        recordings = list_recordings(cfg.recordings_dir)
        golden = [r for r in recordings if r.is_golden]

        path = invariants_path(cfg.recordings_dir)
        mined = merge_invariants(
            mine_invariants(recordings, min_support=min_support), load_invariants(path)
        )
        if mined:
            save_invariants(mined, path)

        return {
            "corpus": len(golden),
            "mined": len(mined),
            "enforced": sum(1 for i in mined if i.approved),
            "note": (
                "Nothing new is enforced. Ask the human to review these and run "
                f"`{CLI_NAME} learn --approve` if they agree."
            ),
            "invariants": [
                {
                    "description": i.description,
                    "kind": i.kind.value,
                    "support": i.support,
                    "confidence": round(i.confidence, 4),
                    "approved": i.approved,
                }
                for i in mined
            ],
        }

    @server.tool(
        description=(
            "Compare each tool's declared JSON schema with how it is actually used "
            "across approved recordings. Reports dead schema surface, arguments the "
            "model invented, unstable result shapes and per-tool error rates. "
            "Analysis only - this never fails a build."
        ),
        annotations=read_only,
    )
    def contracts(recordings_dir: str | None = None) -> dict[str, Any]:
        cfg = _config(recordings_dir)
        recordings = list_recordings(cfg.recordings_dir)
        approved = [r for r in recordings if r.is_golden]
        return contracts_to_json(extract_contracts(recordings), corpus=len(approved))

    @server.tool(
        description="The rules learned so far and whether each is enforced.",
        annotations=read_only,
    )
    def invariants(recordings_dir: str | None = None) -> dict[str, Any]:
        cfg = _config(recordings_dir)
        learned = load_invariants(invariants_path(cfg.recordings_dir))
        return {
            "total": len(learned),
            "enforced": sum(1 for i in learned if i.approved),
            "invariants": [
                {
                    "description": i.description,
                    "kind": i.kind.value,
                    "support": i.support,
                    "confidence": round(i.confidence, 4),
                    "approved": i.approved,
                }
                for i in learned
            ],
        }

    @server.tool(
        description="Audit recordings for credential-shaped values.",
        annotations=read_only,
    )
    def scan(recordings_dir: str | None = None) -> dict[str, Any]:
        cfg = _config(recordings_dir)
        recordings = list_recordings(cfg.recordings_dir)
        offenders = {
            r.name: found
            for r in recordings
            for found in [find_secrets([e.model_dump(mode="json") for e in r.events])]
            if found
        }
        return {"scanned": len(recordings), "clean": not offenders, "offenders": offenders}

    @server.tool(
        description=(
            "Show how each recorded request is keyed for matching. Use this to "
            "diagnose a matcher that is too loose or too strict."
        ),
        annotations=read_only,
    )
    def audit(name: str, recordings_dir: str | None = None) -> dict[str, Any]:
        cfg = _config(recordings_dir)
        try:
            recording = _load(name, cfg)
        except DriftCatchError as exc:
            return {"error": str(exc)}

        keys: dict[str, list[int]] = {}
        rows = []
        for event in recording.events:
            key = key_for(apply_normalizers(event_payload(event), cfg.normalizers))
            keys.setdefault(key, []).append(event.index)
            rows.append({"index": event.index, "type": event.type, "key": key[7:19]})
        duplicates = {k[7:19]: v for k, v in keys.items() if len(v) > 1}
        return {"matcher": cfg.matcher, "events": rows, "duplicate_keys": duplicates}

    @server.tool(
        description=(
            "What is waiting on a human decision, and the exact command they "
            "should run. Use this instead of trying to approve anything yourself."
        ),
        annotations=read_only,
    )
    def pending_approvals(recordings_dir: str | None = None) -> dict[str, Any]:
        cfg = _config(recordings_dir)
        recordings = list_recordings(cfg.recordings_dir)
        pending = [r.name for r in recordings if r.status is Status.PENDING]
        stale = [r.name for r in recordings if r.status is Status.STALE]
        learned = load_invariants(invariants_path(cfg.recordings_dir))
        unenforced = [i.description for i in learned if not i.approved]

        actions: list[dict[str, str]] = []
        if pending:
            actions.append(
                {
                    "what": f"{len(pending)} recording(s) awaiting approval",
                    "human_runs": f"{CLI_NAME} review",
                }
            )
        if stale:
            actions.append(
                {
                    "what": f"{len(stale)} recording(s) stale - inputs changed since approval",
                    "human_runs": f"{CLI_NAME} check --live <name>",
                }
            )
        if unenforced:
            actions.append(
                {
                    "what": f"{len(unenforced)} learned rule(s) not yet enforced",
                    "human_runs": f"{CLI_NAME} learn --approve",
                }
            )
        return {
            "pending_recordings": pending,
            "stale_recordings": stale,
            "unenforced_invariants": unenforced,
            "actions_for_the_human": actions,
            "note": "You cannot perform these. Report them and let the human decide.",
        }

    return server


def serve(recordings_dir: str | None = None, transport: str = "stdio") -> None:
    """Run the MCP server. stdio is what local editors speak."""
    build_server(recordings_dir).run(transport=transport)
