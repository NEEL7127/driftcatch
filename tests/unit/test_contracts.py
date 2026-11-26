"""Tool contract extraction.

The claim under test is narrow: what a contract report says about a corpus must
be true of that corpus. A report that over-claims is worse than no report,
because its whole purpose is to send someone to go and delete a parameter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from driftcatch.contracts import extract, render, to_json
from driftcatch.recording.schema import (
    ErrorInfo,
    LLMEvent,
    LLMRequest,
    Recording,
    Status,
    ToolEvent,
)


def tool_schema(name: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "parameters": {"type": "object", "properties": properties},
    }


def openai_schema(name: str, properties: dict[str, Any]) -> dict[str, Any]:
    """The wrapped shape the OpenAI SDK sends."""
    return {"type": "function", "function": tool_schema(name, properties)}


def recording(
    name: str,
    events: list[Any],
    *,
    approved: bool = True,
) -> Recording:
    return Recording(
        name=name,
        status=Status.APPROVED if approved else Status.PENDING,
        created_at=datetime.now(UTC),
        approved_by="test" if approved else None,
        approved_at=datetime.now(UTC) if approved else None,
        events=events,
    )


def llm(index: int, tools: list[dict[str, Any]]) -> LLMEvent:
    return LLMEvent(index=index, request=LLMRequest(model="m", tools=tools))


# ------------------------------------------------------- the basic shape


def test_argument_types_and_requiredness_are_observed() -> None:
    runs = [
        recording(
            f"r{i}",
            [
                llm(0, [tool_schema("lookup", {"order_id": {}, "verbose": {}})]),
                ToolEvent(index=1, name="lookup", args={"order_id": 100 + i}, result={"ok": True}),
            ],
        )
        for i in range(4)
    ]
    found = extract(runs)
    assert len(found) == 1
    contract = found[0]

    assert contract.name == "lookup"
    assert contract.calls == 4
    assert contract.runs == 4

    args = {f.name: f for f in contract.arguments}
    assert args["order_id"].types == ["integer"]
    assert args["order_id"].required, "present in every call"


def test_an_optional_argument_is_reported_as_a_ratio_not_a_guess() -> None:
    runs = [
        recording(
            "with",
            [ToolEvent(index=0, name="search", args={"q": "a", "limit": 10})],
        ),
        recording("without1", [ToolEvent(index=0, name="search", args={"q": "b"})]),
        recording("without2", [ToolEvent(index=0, name="search", args={"q": "c"})]),
    ]
    args = {f.name: f for f in extract(runs)[0].arguments}
    assert args["q"].required
    assert not args["limit"].required
    assert (args["limit"].present_in, args["limit"].total) == (1, 3)
    assert "optional, 1/3" in args["limit"].summary


def test_a_low_cardinality_field_reads_as_an_enumeration() -> None:
    statuses = ["pending", "shipped", "pending", "shipped"]
    runs = [
        recording(f"r{i}", [ToolEvent(index=0, name="set_status", args={"status": s})])
        for i, s in enumerate(statuses)
    ]
    field = extract(runs)[0].arguments[0]
    assert field.is_enum
    assert set(field.values) == {"pending", "shipped"}
    assert "'pending' | 'shipped'" in field.summary


def test_a_high_cardinality_field_does_not_become_an_enumeration() -> None:
    runs = [
        recording(f"r{i}", [ToolEvent(index=0, name="ask", args={"q": f"question {i}"})])
        for i in range(20)
    ]
    field = extract(runs)[0].arguments[0]
    assert not field.is_enum, "free text must not be rendered as an enum"
    assert field.types == ["string"]


def test_booleans_are_not_reported_as_integers() -> None:
    """bool subclasses int in Python; a contract that says 'integer' would be
    actively misleading to anyone regenerating a schema from this report."""
    runs = [
        recording(f"r{i}", [ToolEvent(index=0, name="t", args={"flag": True})]) for i in range(3)
    ]
    assert extract(runs)[0].arguments[0].types == ["boolean"]


# ------------------------------------------------------- the findings


def test_a_declared_but_unused_parameter_is_reported() -> None:
    """The headline finding: schema surface paid for on every request and
    never once exercised."""
    runs = [
        recording(
            f"r{i}",
            [
                llm(0, [tool_schema("lookup", {"order_id": {}, "include_history": {}})]),
                ToolEvent(index=1, name="lookup", args={"order_id": 1}, result={"ok": True}),
            ],
        )
        for i in range(5)
    ]
    contract = extract(runs)[0]
    assert contract.unused_parameters == ["include_history"]
    assert "include_history" in render(extract(runs), corpus=5)


def test_the_openai_wrapped_schema_shape_is_understood_too() -> None:
    """Providers disagree on the envelope. A report that handled only one would
    silently claim every parameter was undeclared."""
    runs = [
        recording(
            f"r{i}",
            [
                llm(0, [openai_schema("lookup", {"order_id": {}, "unused": {}})]),
                ToolEvent(index=1, name="lookup", args={"order_id": 1}, result=1),
            ],
        )
        for i in range(3)
    ]
    contract = extract(runs)[0]
    assert contract.declared_parameters == ["order_id", "unused"]
    assert contract.unused_parameters == ["unused"]


def test_an_undeclared_argument_is_reported() -> None:
    runs = [
        recording(
            f"r{i}",
            [
                llm(0, [tool_schema("lookup", {"order_id": {}})]),
                ToolEvent(index=1, name="lookup", args={"order_id": 1, "invented": "x"}, result=1),
            ],
        )
        for i in range(3)
    ]
    assert extract(runs)[0].undeclared_parameters == ["invented"]


def test_no_declared_schema_means_no_undeclared_findings() -> None:
    """A tool used purely in Python, never offered to the model, declares
    nothing. Reporting every argument as 'undeclared' would be noise."""
    runs = [
        recording(f"r{i}", [ToolEvent(index=0, name="helper", args={"x": 1})]) for i in range(3)
    ]
    contract = extract(runs)[0]
    assert contract.declared_parameters == []
    assert contract.undeclared_parameters == []
    assert contract.unused_parameters == []


def test_a_tool_offered_but_never_called_is_flagged() -> None:
    runs = [
        recording(
            f"r{i}",
            [
                llm(0, [tool_schema("used", {"a": {}}), tool_schema("ignored", {"b": {}})]),
                ToolEvent(index=1, name="used", args={"a": 1}, result=1),
            ],
        )
        for i in range(4)
    ]
    by_name = {c.name: c for c in extract(runs)}
    assert by_name["ignored"].never_called
    assert by_name["ignored"].offered_in == 4
    assert not by_name["used"].never_called


def test_unstable_result_fields_are_flagged() -> None:
    runs = [
        recording("full", [ToolEvent(index=0, name="t", args={}, result={"a": 1, "b": 2})]),
        recording("partial1", [ToolEvent(index=0, name="t", args={}, result={"a": 1})]),
        recording("partial2", [ToolEvent(index=0, name="t", args={}, result={"a": 1})]),
    ]
    contract = extract(runs)[0]
    assert contract.unstable_result == ["b"]
    assert "b" not in [f.name for f in contract.result_fields if f.required]


def test_error_rate_is_counted() -> None:
    runs = [
        recording("ok1", [ToolEvent(index=0, name="flaky", args={"x": 1}, result=1)]),
        recording("ok2", [ToolEvent(index=0, name="flaky", args={"x": 2}, result=2)]),
        recording(
            "bad",
            [
                ToolEvent(
                    index=0,
                    name="flaky",
                    args={"x": 3},
                    error=ErrorInfo(type="TimeoutError", message="slow"),
                )
            ],
        ),
    ]
    contract = extract(runs)[0]
    assert contract.calls == 3
    assert contract.errors == 1
    assert contract.error_types == ["TimeoutError"]
    assert abs(contract.error_rate - 1 / 3) < 1e-9


def test_a_failed_call_still_contributes_its_arguments() -> None:
    """What a tool was given when it blew up is exactly what you want to see."""
    runs = [
        recording(
            "bad",
            [
                ToolEvent(
                    index=0,
                    name="t",
                    args={"x": 1},
                    error=ErrorInfo(type="ValueError", message="no"),
                )
            ],
        )
    ]
    assert [f.name for f in extract(runs)[0].arguments] == ["x"]


def test_median_latency_is_reported_when_measured() -> None:
    runs = [
        recording(
            f"r{i}",
            [ToolEvent(index=0, name="t", args={}, result=1, duration_ms=ms)],
        )
        for i, ms in enumerate([10, 20, 30])
    ]
    assert extract(runs)[0].median_ms == 20


def test_unmeasured_durations_report_no_median() -> None:
    runs = [recording("r", [ToolEvent(index=0, name="t", args={}, result=1)])]
    assert extract(runs)[0].median_ms is None


# ------------------------------------------------- the approval boundary


def test_unapproved_recordings_are_ignored() -> None:
    """The same rule as invariant mining: inferring a contract from runs nobody
    confirmed would describe a bug as the specification."""
    runs = [
        recording(f"r{i}", [ToolEvent(index=0, name="t", args={"x": 1})], approved=False)
        for i in range(5)
    ]
    assert extract(runs) == []


def test_an_empty_corpus_renders_an_instruction_not_a_crash() -> None:
    out = render([])
    assert "No tool contracts yet" in out
    assert "drift approve" in out


# ------------------------------------------------------------ rendering


def test_render_lists_tools_arguments_and_findings() -> None:
    runs = [
        recording(
            f"r{i}",
            [
                llm(0, [tool_schema("lookup", {"order_id": {}, "dead": {}})]),
                ToolEvent(index=1, name="lookup", args={"order_id": 1}, result={"ok": True}),
            ],
        )
        for i in range(6)
    ]
    out = render(extract(runs), corpus=6)
    assert "Tool contracts from 6 approved recording(s)" in out
    assert "lookup" in out
    assert "order_id: integer" in out
    assert "declared but was never supplied" in out
    assert "finding(s)" in out


def test_render_says_so_when_everything_agrees() -> None:
    runs = [
        recording(
            f"r{i}",
            [
                llm(0, [tool_schema("lookup", {"order_id": {}})]),
                ToolEvent(index=1, name="lookup", args={"order_id": 1}, result=1),
            ],
        )
        for i in range(3)
    ]
    assert "No findings" in render(extract(runs))


def test_json_is_complete_and_serialisable() -> None:
    import json

    runs = [
        recording(
            f"r{i}",
            [
                llm(0, [tool_schema("lookup", {"order_id": {}, "dead": {}})]),
                ToolEvent(index=1, name="lookup", args={"order_id": 1}, result={"ok": True}),
            ],
        )
        for i in range(6)
    ]
    data = to_json(extract(runs), corpus=6)
    round_tripped = json.loads(json.dumps(data))

    assert round_tripped["corpus"] == 6
    assert round_tripped["removal_findings_enabled"] is True
    tool = round_tripped["tools"][0]
    assert tool["name"] == "lookup"
    assert tool["unused_parameters"] == ["dead"]
    assert tool["arguments"][0]["required"] is True
    assert tool["findings"], "findings must reach the JSON form too"


# --------------------------------------------- evidence before advice


def test_removal_advice_is_withheld_on_a_thin_corpus() -> None:
    """One run proving a parameter unused is a sample size of one.

    Acting on it deletes a working parameter, so the finding is suppressed
    rather than hedged - the same threshold invariant mining uses.
    """
    runs = [
        recording(
            f"r{i}",
            [
                llm(0, [tool_schema("lookup", {"order_id": {}, "dead": {}})]),
                ToolEvent(index=1, name="lookup", args={"order_id": 1}, result=1),
            ],
        )
        for i in range(2)
    ]
    out = render(extract(runs), corpus=2)
    assert "dead" not in out.split("too few")[0], "removal advice leaked on a thin corpus"
    assert "too few to call anything unused" in out
    assert "at least 5" in out


def test_a_thin_corpus_does_not_claim_everything_agrees() -> None:
    """Saying 'no findings' when findings were suppressed is a lie by omission."""
    runs = [
        recording(
            f"r{i}",
            [
                llm(0, [tool_schema("lookup", {"order_id": {}, "dead": {}})]),
                ToolEvent(index=1, name="lookup", args={"order_id": 1}, result=1),
            ],
        )
        for i in range(2)
    ]
    out = render(extract(runs), corpus=2)
    assert "declared schemas and observed use agree" not in out
    assert "No findings from the evidence available." in out


def test_removal_advice_appears_once_the_corpus_is_big_enough() -> None:
    runs = [
        recording(
            f"r{i}",
            [
                llm(0, [tool_schema("lookup", {"order_id": {}, "dead": {}})]),
                ToolEvent(index=1, name="lookup", args={"order_id": 1}, result=1),
            ],
        )
        for i in range(5)
    ]
    out = render(extract(runs), corpus=5)
    assert "'dead' is declared but was never supplied" in out
    assert "too few" not in out


def test_evidence_based_findings_survive_a_thin_corpus() -> None:
    """An undeclared argument or a failure *happened*; one run proves it.

    Only the findings that reason from absence need a large corpus.
    """
    runs = [
        recording(
            "r0",
            [
                llm(0, [tool_schema("lookup", {"order_id": {}})]),
                ToolEvent(
                    index=1,
                    name="lookup",
                    args={"order_id": 1, "invented": True},
                    error=ErrorInfo(type="ValueError", message="no"),
                ),
            ],
        )
    ]
    out = render(extract(runs), corpus=1)
    assert "'invented' was supplied but is not in the declared schema" in out
    assert "ValueError" in out


def test_json_reports_whether_removal_findings_were_enabled() -> None:
    runs = [
        recording(f"r{i}", [ToolEvent(index=0, name="t", args={"x": 1}, result=1)])
        for i in range(2)
    ]
    data = to_json(extract(runs), corpus=2)
    assert data["removal_findings_enabled"] is False
    assert data["min_corpus_for_removal"] == 5


# ------------------------------------- the shape that is really on disk


def recorded_request(tools: list[dict[str, Any]]) -> LLMRequest:
    """Build the request the way recording actually builds it.

    The tests above construct `LLMRequest(tools=[...])` directly, which skips
    DriftCatch's own normalisation. `parse_request` rewrites every tool to
    `{"name": ..., "schema": <original>}` so the matcher sees one shape - and
    that wrapper is what is on disk.

    Not peeling it was a real bug: the tools were found, every parameter was
    reported as undeclared, and only a live run against a real SDK caught it.
    These tests go through `parse_request` so the fixture cannot drift from
    reality again.
    """
    from driftcatch.providers import parse_request

    return parse_request({"model": "m", "messages": [], "tools": tools})


def test_declared_parameters_survive_the_real_normalisation() -> None:
    body = openai_schema("lookup", {"order_id": {}, "unused": {}})
    runs = [
        recording(
            f"r{i}",
            [
                LLMEvent(index=0, request=recorded_request([body])),
                ToolEvent(index=1, name="lookup", args={"order_id": 1}, result=1),
            ],
        )
        for i in range(5)
    ]
    contract = extract(runs)[0]
    assert contract.declared_parameters == ["order_id", "unused"], (
        "the tool schema was not read through DriftCatch's own normalisation"
    )
    assert contract.unused_parameters == ["unused"]
    assert contract.undeclared_parameters == []


def test_the_flat_schema_shape_also_survives_normalisation() -> None:
    body = tool_schema("lookup", {"order_id": {}})
    runs = [
        recording(
            f"r{i}",
            [
                LLMEvent(index=0, request=recorded_request([body])),
                ToolEvent(index=1, name="lookup", args={"order_id": 1}, result=1),
            ],
        )
        for i in range(3)
    ]
    assert extract(runs)[0].declared_parameters == ["order_id"]


def test_anthropic_input_schema_survives_normalisation() -> None:
    body = {
        "name": "lookup",
        "input_schema": {"type": "object", "properties": {"order_id": {}, "dead": {}}},
    }
    runs = [
        recording(
            f"r{i}",
            [
                LLMEvent(index=0, request=recorded_request([body])),
                ToolEvent(index=1, name="lookup", args={"order_id": 1}, result=1),
            ],
        )
        for i in range(5)
    ]
    contract = extract(runs)[0]
    assert contract.declared_parameters == ["dead", "order_id"]
    assert contract.unused_parameters == ["dead"]


def test_offered_counts_survive_normalisation() -> None:
    body = openai_schema("never_used", {"a": {}})
    runs = [
        recording(f"r{i}", [LLMEvent(index=0, request=recorded_request([body]))]) for i in range(5)
    ]
    contract = extract(runs)[0]
    assert contract.name == "never_used"
    assert contract.offered_in == 5
    assert contract.never_called
