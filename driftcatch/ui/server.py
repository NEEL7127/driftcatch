"""The local UI server.

Design rules, in order of importance:

1. **Local only.** Recordings contain prompts and customer data. The server
   binds loopback and refuses anything else without an explicit override.
2. **No logic lives here.** Every route is a thin wrapper over the same engine
   the CLI uses, so the UI can never disagree with ``drift``.
3. **No build step.** The page ships as one template, so installing DriftCatch
   never requires Node and the wheel never carries a compiled bundle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from driftcatch._name import CLI_NAME
from driftcatch.bisect import bisect as run_bisect
from driftcatch.config import Config, find_config_file, get_config
from driftcatch.coverage import analyse as analyse_coverage
from driftcatch.errors import DriftCatchError
from driftcatch.match.keys import event_payload, key_for
from driftcatch.match.normalizers import apply_normalizers
from driftcatch.recording.io import list_recordings, load_recording, recording_path
from driftcatch.recording.schema import LLMEvent, Recording, Status, ToolEvent
from driftcatch.redact import find_secrets
from driftcatch.review import approve, reject
from driftcatch.trajectory import diff as diff_trajectory

__all__ = ["build_app", "serve"]

_TEMPLATE = Path(__file__).parent / "templates" / "app.html"

#: Settings the UI is allowed to change. Deliberately excludes anything that
#: could weaken a safety property (redactors, require_approved).
EDITABLE_SETTINGS = (
    "matcher",
    "normalizers",
    "strict",
    "drift_policy",
    "default_tolerance",
    "stream_timing",
    "what_if_unmatched",
)


def _recording_summary(recording: Recording) -> dict[str, Any]:
    return {
        "name": recording.name,
        "status": recording.status.value,
        "trajectory": recording.trajectory.tools_called,
        "events": len(recording.events),
        "tokens": recording.meta.usage.total_tokens,
        "cost_usd": recording.meta.usage.cost_usd,
        "approved_by": recording.approved_by,
        "created_at": recording.created_at.isoformat(),
        "redacted": len(recording.meta.redacted_fields),
    }


def _event_detail(event: Any, cfg: Config) -> dict[str, Any]:
    payload = apply_normalizers(event_payload(event), cfg.normalizers)
    base: dict[str, Any] = {
        "index": event.index,
        "type": event.type,
        "match_key": key_for(payload)[7:19],
    }
    if isinstance(event, LLMEvent):
        base.update(
            {
                "model": event.request.model,
                "messages": event.request.messages,
                "tools": [t.get("name") for t in event.request.tools],
                "decided": event.decided,
                "content": event.response.content if event.response else None,
                "status_code": event.status_code,
                "streamed": bool(event.stream_chunks),
                "tokens": event.usage.total_tokens,
                "error": event.error.model_dump() if event.error else None,
            }
        )
    elif isinstance(event, ToolEvent):
        base.update(
            {
                "name": event.name,
                "args": event.args,
                "result": event.result,
                "error": event.error.model_dump() if event.error else None,
            }
        )
    return base


def build_app(cfg: Config | None = None) -> Any:
    """Construct the FastAPI application."""
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import HTMLResponse
    except ImportError as exc:  # pragma: no cover - guarded by the CLI
        raise DriftCatchError('the UI needs extra packages: pip install "driftcatch[ui]"') from exc

    config = cfg or get_config()
    app = FastAPI(title="DriftCatch", docs_url=None, redoc_url=None)

    def _resolve(name: str) -> Recording:
        try:
            return load_recording(recording_path(name, config.recordings_dir))
        except DriftCatchError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _TEMPLATE.read_text(encoding="utf-8")

    @app.get("/api/summary")
    def summary() -> dict[str, Any]:
        recordings = list_recordings(config.recordings_dir)
        report = analyse_coverage(recordings)
        counts = {status.value: 0 for status in Status}
        for recording in recordings:
            counts[recording.status.value] += 1
        return {
            "recordings_dir": str(config.recordings_dir),
            "counts": counts,
            "total": len(recordings),
            "coverage": {
                "percent": report.percent,
                "covered_units": report.covered_units,
                "total_units": report.total_units,
                "uncovered": report.uncovered,
            },
        }

    @app.get("/api/recordings")
    def recordings() -> list[dict[str, Any]]:
        return [_recording_summary(r) for r in list_recordings(config.recordings_dir)]

    @app.get("/api/recordings/{name}")
    def recording_detail(name: str) -> dict[str, Any]:
        recording = _resolve(name)
        data = _recording_summary(recording)
        data["events_detail"] = [_event_detail(e, config) for e in recording.events]
        data["fingerprint"] = recording.fingerprint.model_dump() if recording.fingerprint else None
        data["steps"] = [step.model_dump() for step in recording.trajectory.steps]
        data["retries"] = recording.trajectory.retries
        data["secrets"] = find_secrets([e.model_dump(mode="json") for e in recording.events])
        return data

    @app.post("/api/recordings/{name}/approve")
    def approve_route(name: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        who = (body or {}).get("by", "ui")
        try:
            recording = approve(name, by=str(who), config=config)
        except DriftCatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _recording_summary(recording)

    @app.post("/api/recordings/{name}/reject")
    def reject_route(name: str) -> dict[str, Any]:
        try:
            recording = reject(name, config=config)
        except DriftCatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _recording_summary(recording)

    @app.get("/api/coverage")
    def coverage_route() -> dict[str, Any]:
        recordings = list_recordings(config.recordings_dir)
        report = analyse_coverage(recordings)
        return report.model_dump()

    @app.get("/api/compare")
    def compare(golden: str, candidate: str, tolerance: str = "tools+order") -> dict[str, Any]:
        left, right = _resolve(golden), _resolve(candidate)
        result = diff_trajectory(left.trajectory, right.trajectory, tolerance)
        report = run_bisect(left, right, tolerance=tolerance)
        return {
            "matched": result.matched,
            "golden_tools": result.golden_tools,
            "current_tools": result.current_tools,
            "missing": result.missing,
            "unexpected": result.unexpected,
            "first_divergence": result.first_divergence,
            "attribution": report.model_dump(),
        }

    @app.get("/api/config")
    def read_config() -> dict[str, Any]:
        found = find_config_file()
        return {
            "values": json.loads(config.model_dump_json()),
            "editable": list(EDITABLE_SETTINGS),
            "file": str(found) if found else None,
        }

    @app.post("/api/config")
    def write_config(body: dict[str, Any]) -> dict[str, Any]:
        """Persist editable settings to ``driftcatch.toml``.

        Written to a standalone file rather than pyproject.toml so the UI never
        rewrites a project's build configuration.
        """
        rejected = [key for key in body if key not in EDITABLE_SETTINGS]
        if rejected:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"not editable from the UI: {', '.join(sorted(rejected))}. "
                    "Safety settings are changed in code or config on purpose."
                ),
            )
        merged = config.merged(**body)
        target = Path.cwd() / "driftcatch.toml"
        lines = ["# Written by the DriftCatch UI", ""]
        for key in EDITABLE_SETTINGS:
            value = getattr(merged, key)
            lines.append(f"{key} = {json.dumps(value)}")
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"written": str(target), "values": json.loads(merged.model_dump_json())}

    return app


def serve(cfg: Config | None = None, *, host: str = "127.0.0.1", port: int = 7777) -> None:
    """Run the UI.

    Refuses to bind a non-loopback address: recordings hold prompts and customer
    data, and a tool that quietly exposed them on a shared network would be a
    liability rather than a convenience.
    """
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise DriftCatchError(
            f"refusing to bind {host}: recordings contain prompts and may contain "
            "customer data. The UI is loopback-only by design."
        )
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise DriftCatchError('the UI needs extra packages: pip install "driftcatch[ui]"') from exc

    config = cfg or get_config()
    print(f"{CLI_NAME} ui -> http://{host}:{port}  (recordings: {config.recordings_dir})")
    uvicorn.run(build_app(config), host=host, port=port, log_level="warning")
