"""DriftCatch - catch the moment your AI agent's behaviour changes.

Quick start::

    import driftcatch as drift

    @drift.tool
    def lookup_order(order_id: int) -> dict: ...

    def test_refund_uses_the_database():
        with drift.take("refund_1182") as take:
            result = refund_agent("refund order 1182")
        take.assert_tools().called("lookup_order").never("web_search")
        take.assert_trajectory().matches_golden()

Everything users need is re-exported here; internal modules are free to move.
"""

from __future__ import annotations

from driftcatch._name import CLI_NAME, NAME, RECORDING_VERSION
from driftcatch.bisect import bisect
from driftcatch.config import Config, config, get_config, reset_config
from driftcatch.contracts import FieldShape, ToolContract
from driftcatch.contracts import extract as extract_contracts
from driftcatch.coverage import CoverageReport
from driftcatch.coverage import analyse as analyse_coverage
from driftcatch.determinism import (
    DeterminismReport,
    LiveResult,
    live_allowed,
    live_check,
    run_live,
)
from driftcatch.errors import (
    AmbiguousMatchError,
    ConfigError,
    DriftCatchError,
    DriftDetected,
    DriftSignal,
    DriftWarning,
    InvalidRecordingName,
    InvalidTransition,
    LatencyNotMeasured,
    NoMatchError,
    PrunedRecording,
    RecordedError,
    RecordingNotApproved,
    RecordingNotFound,
    RecordingVersionError,
    RedactionError,
)
from driftcatch.intercept import tool
from driftcatch.invariants import Invariant, InvariantKind, Violation
from driftcatch.invariants import check as check_invariants
from driftcatch.invariants import mine as mine_invariants
from driftcatch.match import chain, matcher, normalizer
from driftcatch.recording import (
    ErrorInfo,
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
    list_recordings,
    load_recording,
)
from driftcatch.review import approve, reject
from driftcatch.suite import Suite
from driftcatch.take import LatencyAssertion, OutputAssertion, Take, take
from driftcatch.trajectory import TOLERANCES

__version__ = "0.1.0"

__all__ = [
    "CLI_NAME",
    "NAME",
    "RECORDING_VERSION",
    "TOLERANCES",
    "AmbiguousMatchError",
    "Config",
    "ConfigError",
    "CoverageReport",
    "DeterminismReport",
    "DriftCatchError",
    "DriftDetected",
    "DriftSignal",
    "DriftWarning",
    "FieldShape",
    "ToolContract",
    "extract_contracts",
    "LatencyAssertion",
    "LatencyNotMeasured",
    "OutputAssertion",
    "ErrorInfo",
    "InvalidRecordingName",
    "InvalidTransition",
    "mine_invariants",
    "check_invariants",
    "Violation",
    "InvariantKind",
    "Invariant",
    "LLMEvent",
    "LLMRequest",
    "LLMResponse",
    "LiveResult",
    "NoMatchError",
    "PrunedRecording",
    "Outcome",
    "RecordedError",
    "Recording",
    "RecordingNotApproved",
    "RecordingNotFound",
    "RecordingVersionError",
    "RedactionError",
    "Status",
    "Step",
    "Suite",
    "Take",
    "ToolEvent",
    "Trajectory",
    "Usage",
    "__version__",
    "analyse_coverage",
    "approve",
    "bisect",
    "chain",
    "config",
    "get_config",
    "list_recordings",
    "live_allowed",
    "live_check",
    "load_recording",
    "matcher",
    "normalizer",
    "reject",
    "reset_config",
    "run_live",
    "take",
    "tool",
]
