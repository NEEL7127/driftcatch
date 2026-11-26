"""Tool contracts inferred from the approved corpus.

A tool has two descriptions. The one you *declare* to the model lives in the
JSON schema you send with every request. The one that is *real* is whatever the
model and your code actually did, run after run. They drift apart silently, and
neither your type checker nor your tests can see the gap, because the model is
the thing choosing which half of the schema to use.

This module reads the second description out of recordings that a human
approved, and puts the two side by side. Three things fall out of that
comparison, each of which costs something today:

* **Dead schema surface.** A parameter you declare and the model has never
  once used is paid for on every single request, in tokens, forever.
* **Invented arguments.** A parameter the model passes that you never declared
  is the model filling in a gap - usually a sign the schema is unclear, and
  occasionally a sign your tool is silently ignoring input.
* **Shape instability.** A result whose keys differ between runs is a contract
  your downstream code is relying on by luck.

Deliberately an *analysis* command, not a gate. ``drift invariants`` enforces;
this reports. Keeping the two apart means a contract report can be noisy and
exploratory without ever failing somebody's build.

Only approved recordings are read. Inferring a contract from runs nobody
confirmed were correct would describe a bug as if it were the specification -
the same reasoning that governs invariant mining.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from driftcatch.recording.schema import LLMEvent, Recording, ToolEvent

__all__ = [
    "MIN_CORPUS_FOR_REMOVAL",
    "FieldShape",
    "ToolContract",
    "extract",
    "render",
    "to_json",
]

#: Below this many distinct values, a field is reported as an enumeration
#: rather than as a free type. Chosen so an obvious status field reads as
#: ``"pending" | "shipped"`` while a free-text field does not turn into an
#: unreadable wall of examples.
_ENUM_LIMIT = 8

#: How many example values to keep for a field that is not enum-like.
_EXAMPLES = 3

#: Findings that advise *removing* something need a corpus big enough for the
#: absence to mean anything. "This parameter was never used" across one run is
#: not evidence, it is a sample size of one, and acting on it deletes a working
#: parameter. Pinned to the same threshold invariant mining uses, for the same
#: reason: below it, absence of evidence is not evidence of absence.
MIN_CORPUS_FOR_REMOVAL = 5


def _type_name(value: Any) -> str:
    """A stable, JSON-flavoured type name for a recorded value."""
    if value is None:
        return "null"
    if isinstance(value, bool):  # before int: bool is a subclass of int
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


class FieldShape(BaseModel):
    """What one argument or result field has actually looked like."""

    name: str
    types: list[str] = Field(default_factory=list)
    #: Calls in which this field was present.
    present_in: int = 0
    #: Calls observed in total, so optionality is a ratio not a guess.
    total: int = 0
    #: Distinct values, kept only while the field looks enum-like.
    values: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)

    @property
    def required(self) -> bool:
        """Present in every observed call."""
        return self.total > 0 and self.present_in == self.total

    @property
    def is_enum(self) -> bool:
        return bool(self.values)

    @property
    def summary(self) -> str:
        """One line describing the field as it really behaves."""
        kind = " | ".join(self.types) or "unknown"
        if self.is_enum:
            kind = " | ".join(repr(v) for v in self.values)
        optional = "" if self.required else f"  (optional, {self.present_in}/{self.total})"
        return f"{self.name}: {kind}{optional}"


class ToolContract(BaseModel):
    """The observed behaviour of one tool, next to what was declared."""

    name: str
    #: Calls seen across the corpus.
    calls: int = 0
    #: Runs in which the tool was called at least once.
    runs: int = 0
    #: Runs in which it was offered to the model.
    offered_in: int = 0
    arguments: list[FieldShape] = Field(default_factory=list)
    result_type: list[str] = Field(default_factory=list)
    result_fields: list[FieldShape] = Field(default_factory=list)
    errors: int = 0
    error_types: list[str] = Field(default_factory=list)
    #: Parameters in the declared JSON schema.
    declared_parameters: list[str] = Field(default_factory=list)
    durations_ms: list[int] = Field(default_factory=list)

    @property
    def error_rate(self) -> float:
        return self.errors / self.calls if self.calls else 0.0

    @property
    def unused_parameters(self) -> list[str]:
        """Declared, but never once supplied.

        Every request pays for these in prompt tokens. A parameter that has
        survived an entire approved corpus without being used is either dead
        or described badly enough that the model cannot tell when to use it.
        """
        used = {field.name for field in self.arguments}
        return sorted(set(self.declared_parameters) - used)

    @property
    def undeclared_parameters(self) -> list[str]:
        """Supplied, but never declared.

        The model invented an argument name, or the schema sent at request time
        disagrees with the function your code actually calls.
        """
        declared = set(self.declared_parameters)
        if not declared:
            return []
        return sorted({f.name for f in self.arguments} - declared)

    @property
    def never_called(self) -> bool:
        """Offered to the model in some run, and never chosen in any."""
        return self.offered_in > 0 and self.calls == 0

    @property
    def unstable_result(self) -> list[str]:
        """Result fields that appear in some calls and not others."""
        return [f.name for f in self.result_fields if not f.required]

    @property
    def median_ms(self) -> int | None:
        if not self.durations_ms:
            return None
        ordered = sorted(self.durations_ms)
        return ordered[len(ordered) // 2]


class _ShapeBuilder:
    """Accumulates one field's observed values across many calls."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.types: set[str] = set()
        self.present = 0
        self.values: set[str] = set()
        self.enum_like = True

    def observe(self, value: Any) -> None:
        self.present += 1
        self.types.add(_type_name(value))
        if not self.enum_like:
            return
        if isinstance(value, (dict, list)):
            # Containers are never reported as an enumeration: the rendering
            # would be unreadable and the cardinality is unbounded.
            self.enum_like = False
            return
        self.values.add(json.dumps(value, default=str))
        if len(self.values) > _ENUM_LIMIT:
            self.enum_like = False

    def build(self, total: int) -> FieldShape:
        decoded = sorted(json.loads(v) if v else v for v in self.values) if self.enum_like else []
        rendered = [str(v) for v in decoded]
        return FieldShape(
            name=self.name,
            types=sorted(self.types),
            present_in=self.present,
            total=total,
            values=rendered if self.enum_like and len(rendered) > 1 else [],
            examples=rendered[:_EXAMPLES],
        )


def _tool_spec(declared: Any) -> dict[str, Any] | None:
    """Unwrap a recorded tool entry down to the object holding its schema.

    Three layers can be present, and all three must be peeled or the report is
    silently empty:

    1. DriftCatch's own normalisation. ``parse_request`` rewrites every tool to
       ``{"name": ..., "schema": <the original entry>}`` so the matcher and the
       fingerprint see one shape. This is what is actually on disk, and missing
       it was a real bug: the tools were found, every parameter was reported as
       undeclared, and only a live run against a real SDK revealed it.
    2. The OpenAI envelope, ``{"type": "function", "function": {...}}``.
    3. The flat form, ``{"name": ..., "parameters": {...}}``, and Anthropic's
       ``input_schema`` equivalent.
    """
    if not isinstance(declared, dict):
        return None
    inner = declared.get("schema")
    if isinstance(inner, dict):
        declared = inner
    body = declared.get("function")
    spec = body if isinstance(body, dict) else declared
    return spec if isinstance(spec, dict) else None


def _declared_parameters(recordings: list[Recording]) -> dict[str, list[str]]:
    """Parameter names from the tool schemas sent with each request."""
    found: dict[str, set[str]] = defaultdict(set)
    for recording in recordings:
        for event in recording.events:
            if not isinstance(event, LLMEvent):
                continue
            for declared in event.request.tools:
                spec = _tool_spec(declared)
                if spec is None:
                    continue
                name = spec.get("name") or (
                    declared.get("name") if isinstance(declared, dict) else None
                )
                if not name:
                    continue
                schema = spec.get("parameters") or spec.get("input_schema") or {}
                properties = schema.get("properties") if isinstance(schema, dict) else None
                if isinstance(properties, dict):
                    found[str(name)] |= set(properties)
                else:
                    found[str(name)] |= set()
    return {name: sorted(params) for name, params in found.items()}


def _offered_counts(recordings: list[Recording]) -> dict[str, int]:
    """Runs in which each tool was offered to the model."""
    counts: dict[str, int] = defaultdict(int)
    for recording in recordings:
        offered: set[str] = set()
        for event in recording.events:
            if not isinstance(event, LLMEvent):
                continue
            for declared in event.request.tools:
                spec = _tool_spec(declared)
                name = (spec or {}).get("name") or (
                    declared.get("name") if isinstance(declared, dict) else None
                )
                if name:
                    offered.add(str(name))
        for name in offered:
            counts[name] += 1
    return counts


def extract(recordings: list[Recording]) -> list[ToolContract]:
    """Infer a contract per tool from the approved recordings.

    Unapproved runs are ignored, so a contract always describes behaviour a
    human signed off on.
    """
    corpus = [r for r in recordings if r.is_golden]
    if not corpus:
        return []

    declared = _declared_parameters(corpus)
    offered = _offered_counts(corpus)

    calls: dict[str, int] = defaultdict(int)
    runs: dict[str, int] = defaultdict(int)
    errors: dict[str, int] = defaultdict(int)
    error_types: dict[str, set[str]] = defaultdict(set)
    result_types: dict[str, set[str]] = defaultdict(set)
    durations: dict[str, list[int]] = defaultdict(list)
    arg_fields: dict[str, dict[str, _ShapeBuilder]] = defaultdict(dict)
    result_fields: dict[str, dict[str, _ShapeBuilder]] = defaultdict(dict)
    #: Calls whose result was an object, so result-field optionality is a
    #: ratio over the calls where fields were even possible.
    object_results: dict[str, int] = defaultdict(int)

    for recording in corpus:
        seen_here: set[str] = set()
        for event in recording.events:
            if not isinstance(event, ToolEvent):
                continue
            name = event.name
            calls[name] += 1
            seen_here.add(name)
            if event.duration_ms is not None:
                durations[name].append(event.duration_ms)

            for key, value in (event.args or {}).items():
                builder = arg_fields[name].setdefault(key, _ShapeBuilder(key))
                builder.observe(value)

            if event.error is not None:
                errors[name] += 1
                error_types[name].add(event.error.type)
                continue

            result_types[name].add(_type_name(event.result))
            if isinstance(event.result, dict):
                object_results[name] += 1
                for key, value in event.result.items():
                    builder = result_fields[name].setdefault(key, _ShapeBuilder(key))
                    builder.observe(value)

        for name in seen_here:
            runs[name] += 1

    contracts: list[ToolContract] = []
    for name in sorted(set(calls) | set(offered)):
        total_calls = calls[name]
        # Argument optionality is measured over successful *and* failed calls,
        # because a tool that raises still tells you what it was given.
        contracts.append(
            ToolContract(
                name=name,
                calls=total_calls,
                runs=runs[name],
                offered_in=offered.get(name, 0),
                arguments=[b.build(total_calls) for b in arg_fields[name].values()],
                result_type=sorted(result_types[name]),
                result_fields=[b.build(object_results[name]) for b in result_fields[name].values()],
                errors=errors[name],
                error_types=sorted(error_types[name]),
                declared_parameters=declared.get(name, []),
                durations_ms=durations[name],
            )
        )
    return contracts


# ------------------------------------------------------------- reporting


def _findings(contract: ToolContract, corpus: int = 0) -> list[str]:
    """The lines worth acting on, in descending order of cost.

    ``corpus`` gates the findings that recommend deleting something. With too
    few recordings those are guesses dressed as advice, so they are withheld
    rather than hedged - a report nobody can act on confidently is worse than a
    shorter one.
    """
    notes: list[str] = []
    enough = corpus >= MIN_CORPUS_FOR_REMOVAL

    if contract.never_called and enough:
        notes.append(
            f"offered in {contract.offered_in} run(s) but never called - "
            f"its schema costs tokens on every request"
        )
    if enough:
        for param in contract.unused_parameters:
            notes.append(f"parameter {param!r} is declared but was never supplied")
    for param in contract.undeclared_parameters:
        notes.append(f"parameter {param!r} was supplied but is not in the declared schema")
    unstable = contract.unstable_result
    if unstable:
        notes.append(
            f"result fields appear only sometimes: {', '.join(unstable)} - "
            f"downstream code reading them is relying on luck"
        )
    if contract.errors:
        notes.append(
            f"failed {contract.errors}/{contract.calls} calls "
            f"({contract.error_rate:.0%}): {', '.join(contract.error_types)}"
        )
    return notes


def render(contracts: list[ToolContract], *, corpus: int = 0) -> str:
    """A human-readable contract report."""
    if not contracts:
        return (
            "No tool contracts yet.\n\n"
            "Contracts are read from approved recordings. Record a few runs, "
            "approve them with 'drift approve <name>', then run this again."
        )

    lines: list[str] = []
    if corpus:
        lines.append(f"Tool contracts from {corpus} approved recording(s)\n")

    total_findings = 0
    for contract in contracts:
        header = f"{contract.name}  -  {contract.calls} call(s) in {contract.runs} run(s)"
        if contract.median_ms is not None:
            header += f", median {contract.median_ms}ms"
        lines.append(header)

        if contract.arguments:
            lines.append("  arguments:")
            lines.extend(f"    {field.summary}" for field in contract.arguments)
        elif contract.calls:
            lines.append("  arguments: (none)")

        if contract.result_type:
            lines.append(f"  returns: {' | '.join(contract.result_type)}")
        for field in contract.result_fields:
            lines.append(f"    {field.summary}")

        notes = _findings(contract, corpus)
        total_findings += len(notes)
        lines.extend(f"  ! {note}" for note in notes)
        lines.append("")

    withheld = bool(corpus) and corpus < MIN_CORPUS_FOR_REMOVAL

    if total_findings:
        lines.append(f"{total_findings} finding(s). These are observations, not failures.")
    elif withheld:
        # Saying "no findings" here would be a lie by omission: the findings
        # that recommend deleting something were suppressed for lack of
        # evidence, not evaluated and cleared.
        lines.append("No findings from the evidence available.")
    else:
        lines.append("No findings: declared schemas and observed use agree.")

    if withheld:
        lines.append(
            f"\n{corpus} approved recording(s) is too few to call anything unused. "
            f"Findings that recommend removing a tool or a parameter need at least "
            f"{MIN_CORPUS_FOR_REMOVAL} and are withheld until then - with this few runs, "
            f"'never used' means 'not yet seen'. Record and approve more runs to see them."
        )
    return "\n".join(lines)


def to_json(contracts: list[ToolContract], *, corpus: int = 0) -> dict[str, Any]:
    """The machine-readable form, for CI and for coding agents."""
    return {
        "corpus": corpus,
        "removal_findings_enabled": corpus >= MIN_CORPUS_FOR_REMOVAL,
        "min_corpus_for_removal": MIN_CORPUS_FOR_REMOVAL,
        "tools": [
            {
                "name": c.name,
                "calls": c.calls,
                "runs": c.runs,
                "offered_in": c.offered_in,
                "never_called": c.never_called,
                "error_rate": round(c.error_rate, 4),
                "error_types": c.error_types,
                "median_ms": c.median_ms,
                "returns": c.result_type,
                "arguments": [
                    {
                        "name": f.name,
                        "types": f.types,
                        "required": f.required,
                        "present_in": f.present_in,
                        "total": f.total,
                        "values": f.values,
                    }
                    for f in c.arguments
                ],
                "result_fields": [
                    {"name": f.name, "types": f.types, "required": f.required}
                    for f in c.result_fields
                ],
                "declared_parameters": c.declared_parameters,
                "unused_parameters": c.unused_parameters,
                "undeclared_parameters": c.undeclared_parameters,
                "unstable_result_fields": c.unstable_result,
                "findings": _findings(c, corpus),
            }
            for c in contracts
        ],
    }


def contracts_path(recordings_dir: Path) -> Path:
    """Where a saved contract snapshot lives, beside the recordings."""
    return Path(recordings_dir) / "contracts.json"
