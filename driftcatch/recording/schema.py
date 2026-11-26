"""The recording data model.

A *recording* is one captured agent run: an ordered list of boundary events
(model calls, tool calls) plus the trajectory derived from them, a fingerprint
of the inputs that produced it, and approval metadata.

Design reference: DESIGN.md section 5.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from driftcatch._name import RECORDING_VERSION

__all__ = [
    "ErrorInfo",
    "Event",
    "Fingerprint",
    "LLMEvent",
    "LLMRequest",
    "LLMResponse",
    "Outcome",
    "Recording",
    "RecordingMeta",
    "Status",
    "Step",
    "ToolEvent",
    "Trajectory",
    "Usage",
]


class Outcome(StrEnum):
    """Whether a step succeeded or raised."""

    OK = "ok"
    ERROR = "error"


class Status(StrEnum):
    """Approval state of a recording.

    A recording is never trusted as golden until a human moves it to APPROVED.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"


class ErrorInfo(BaseModel):
    """A captured failure. Errors are behaviour worth pinning, not noise."""

    type: str
    message: str
    status: int | None = None


class Usage(BaseModel):
    """Token and cost accounting, normalised across providers."""

    input_tokens: int = 0
    output_tokens: int = 0
    #: Thinking tokens, reported separately by reasoning models. Already counted
    #: inside ``output_tokens``; broken out because it is often most of the bill.
    reasoning_tokens: int = 0
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMRequest(BaseModel):
    """The provider-agnostic shape of an outgoing model call."""

    model: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    """The provider-agnostic shape of a model reply."""

    content: str | None = None
    #: Chain-of-thought, where the provider separates it from the answer.
    #: Reasoning models (o1-style, DeepSeek-R1, Nemotron, QwQ) return their
    #: working in ``reasoning_content`` and frequently leave ``content`` null,
    #: so a parser that only reads ``content`` sees an empty reply.
    reasoning: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    finish_reason: str | None = None

    @property
    def text(self) -> str:
        """The answer if there is one, otherwise the reasoning."""
        return self.content or self.reasoning or ""


class _EventBase(BaseModel):
    """Shared validation: an event records a success or a failure, never both."""

    index: int

    #: Wall-clock time this step took, in milliseconds, or ``None`` when it was
    #: not measured. Only ever populated on a *real* call - record mode and live
    #: mode. A replayed step serves this value verbatim from the recording, so
    #: it describes the original call and never the replay, which is why
    #: ``Take.assert_latency`` refuses to run during replay.
    duration_ms: int | None = None

    @model_validator(mode="after")
    def _exactly_one_outcome(self) -> _EventBase:
        success = self._success_payload()
        error = getattr(self, "error", None)
        if success is not None and error is not None:
            raise ValueError(
                "an event cannot carry both a success payload and an error; record one or the other"
            )
        return self

    def _success_payload(self) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError


class LLMEvent(_EventBase):
    """One model call and its reply (or the error it raised).

    Two copies of the reply are kept on purpose:

    * ``response`` is the normalised, provider-agnostic view. Assertions,
      trajectories and coverage read this.
    * ``raw_response`` is the verbatim provider body. Replay returns *this*, so
      the caller's SDK parses exactly what it parsed when recording. Rebuilding
      a provider-shaped body from the normalised view would break on every SDK
      quirk and schema change.
    """

    type: Literal["llm"] = "llm"
    request: LLMRequest
    response: LLMResponse | None = None
    error: ErrorInfo | None = None
    raw_response: dict[str, Any] | None = None
    status_code: int = 200
    stream_chunks: list[str] | None = None
    usage: Usage = Field(default_factory=Usage)

    def _success_payload(self) -> Any:
        return self.response

    @property
    def outcome(self) -> Outcome:
        return Outcome.ERROR if self.error is not None else Outcome.OK

    @property
    def decided(self) -> list[str]:
        """Tool names the model chose in this reply."""
        if self.response is None:
            return []
        return [str(call.get("name", "")) for call in self.response.tool_calls]


class ToolEvent(_EventBase):
    """One tool invocation and its result (or the error it raised)."""

    type: Literal["tool"] = "tool"
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: Any | None = None
    error: ErrorInfo | None = None

    def _success_payload(self) -> Any:
        return self.result

    @property
    def outcome(self) -> Outcome:
        return Outcome.ERROR if self.error is not None else Outcome.OK


Event = Annotated[LLMEvent | ToolEvent, Field(discriminator="type")]


class Step(BaseModel):
    """One node of the trajectory tree."""

    kind: Literal["llm", "tool"]
    index: int
    name: str | None = None
    decided: list[str] = Field(default_factory=list)
    args_digest: str | None = None
    outcome: Outcome | None = None
    parent: int | None = None


class Trajectory(BaseModel):
    """The behaviour of a run: what the agent decided and did, in order.

    This is the object that gets diffed and the input to behaviour coverage.
    """

    steps: list[Step] = Field(default_factory=list)
    tools_called: list[str] = Field(default_factory=list)
    parallel_groups: list[list[int]] = Field(default_factory=list)
    retries: list[int] = Field(default_factory=list)
    terminal: Outcome | None = None


class Fingerprint(BaseModel):
    """Hashes of the inputs that determine whether a recording still holds.

    Components are hashed separately so drift can name *which* input changed and
    so ``drift bisect`` can attribute a behaviour change to one of them.
    """

    value: str
    model_id: str
    prompt_hash: str
    tool_schema_hash: str
    matcher_hash: str

    COMPONENTS: ClassVar[tuple[str, ...]] = (
        "model_id",
        "prompt_hash",
        "tool_schema_hash",
        "matcher_hash",
    )


class RecordingMeta(BaseModel):
    """Non-behavioural bookkeeping about a run."""

    usage: Usage = Field(default_factory=Usage)
    duration_ms: int = 0
    sdk: dict[str, str] = Field(default_factory=dict)
    redacted_fields: list[str] = Field(default_factory=list)


class Recording(BaseModel):
    """A complete captured agent run."""

    version: int = RECORDING_VERSION
    name: str
    status: Status = Status.PENDING
    created_at: datetime
    approved_at: datetime | None = None
    approved_by: str | None = None
    fingerprint: Fingerprint | None = None
    events: list[Event] = Field(default_factory=list)
    trajectory: Trajectory = Field(default_factory=Trajectory)
    meta: RecordingMeta = Field(default_factory=RecordingMeta)

    @property
    def is_golden(self) -> bool:
        return self.status is Status.APPROVED
