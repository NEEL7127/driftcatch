"""End-to-end smoke check for the Phase 1 foundation.

Writes a real recording to disk, reloads it, mutates one behaviour-determining
input, and shows that drift is detected and attributed. Run with:

    python scripts/smoke_roundtrip.py
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from driftcatch.recording import (
    LLMEvent,
    LLMRequest,
    LLMResponse,
    Outcome,
    Recording,
    Status,
    Step,
    ToolEvent,
    Trajectory,
    Usage,
    compare,
    compute_fingerprint,
    load_recording,
    recording_path,
    save_recording,
)

SYSTEM_PROMPT = "Use available tools to find the order."
TOOL_SCHEMAS = [{"name": "lookup_order"}, {"name": "web_search"}]
MATCHER_CONFIG = {"matcher": "field", "normalizers": ["drop_timestamps"]}


def build() -> Recording:
    return Recording(
        name="refund_1182",
        status=Status.APPROVED,
        created_at=datetime.now(UTC),
        approved_at=datetime.now(UTC),
        approved_by="neel",
        fingerprint=compute_fingerprint(
            model_id="gpt-5.2-2026-03",
            system_prompt=SYSTEM_PROMPT,
            tool_schemas=TOOL_SCHEMAS,
            matcher_config=MATCHER_CONFIG,
        ),
        events=[
            LLMEvent(
                index=0,
                request=LLMRequest(
                    model="gpt-5.2-2026-03",
                    messages=[{"role": "system", "content": SYSTEM_PROMPT}],
                    tools=TOOL_SCHEMAS,
                ),
                response=LLMResponse(
                    tool_calls=[{"name": "lookup_order", "args": {"id": 1182}}],
                    finish_reason="tool_calls",
                ),
                usage=Usage(input_tokens=910, output_tokens=40, cost_usd=0.0041),
            ),
            ToolEvent(index=1, name="lookup_order", args={"id": 1182}, result={"total": 40}),
        ],
        trajectory=Trajectory(
            steps=[
                Step(kind="llm", index=0, decided=["lookup_order"]),
                Step(kind="tool", index=1, name="lookup_order", outcome=Outcome.OK, parent=0),
            ],
            tools_called=["lookup_order"],
            terminal=Outcome.OK,
        ),
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = recording_path("refund_1182", Path(tmp))

        original = build()
        save_recording(original, path)
        size_kb = path.stat().st_size / 1024
        print(f"[1] wrote recording          {path.name}  ({size_kb:.1f} KB)")

        loaded = load_recording(path)
        identical = loaded.model_dump(mode="json") == original.model_dump(mode="json")
        print(f"[2] reloaded identical        {identical}")
        print(f"[3] status / approved_by      {loaded.status.value} / {loaded.approved_by}")
        print(f"[4] trajectory                {' -> '.join(loaded.trajectory.tools_called)}")

        raw = path.read_text(encoding="utf-8")
        # Kept as a variable: nesting the same quote inside an f-string is only
        # legal from Python 3.12, and this project supports 3.11.
        status_marker = '"status": "approved"'
        print(f"[5] metadata plaintext        {status_marker in raw}")
        print(f"[6] prompt body compressed    {SYSTEM_PROMPT not in raw}")

        # Someone rewords the prompt. Drift must notice, and say which input moved.
        current = compute_fingerprint(
            model_id="gpt-5.2-2026-03",
            system_prompt="Search for the order information.",
            tool_schemas=TOOL_SCHEMAS,
            matcher_config=MATCHER_CONFIG,
        )
        assert loaded.fingerprint is not None
        changed = compare(loaded.fingerprint, current)
        print(f"[7] drift detected            {bool(changed)}  -> changed: {changed}")

        unchanged = compare(loaded.fingerprint, original.fingerprint)  # type: ignore[arg-type]
        print(f"[8] no false positive         {unchanged == []}")


if __name__ == "__main__":
    main()
