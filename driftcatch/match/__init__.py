"""Matching: normalizers, strategies and the resolution pipeline.

Importing this package registers every built-in normalizer and strategy.
"""

from __future__ import annotations

from driftcatch.match import normalizers as _normalizers  # noqa: F401  (registers built-ins)
from driftcatch.match import strategies as _strategies  # noqa: F401  (registers built-ins)
from driftcatch.match.base import (
    Confidence,
    Kind,
    Match,
    MatchContext,
    Matcher,
    MatchQuery,
    MatchRecord,
    MatchResult,
    MatchState,
    NoMatch,
)
from driftcatch.match.keys import event_payload, key_for, llm_query, tool_query
from driftcatch.match.normalizers import apply_normalizers
from driftcatch.match.registry import (
    get_matcher,
    get_normalizer,
    list_matchers,
    list_normalizers,
    matcher,
    normalizer,
)
from driftcatch.match.resolve import NormalizationContext, resolve
from driftcatch.match.strategies import chain

__all__ = [
    "Confidence",
    "Kind",
    "Match",
    "MatchContext",
    "MatchQuery",
    "MatchRecord",
    "MatchResult",
    "MatchState",
    "Matcher",
    "NoMatch",
    "NormalizationContext",
    "apply_normalizers",
    "chain",
    "event_payload",
    "get_matcher",
    "get_normalizer",
    "key_for",
    "list_matchers",
    "list_normalizers",
    "llm_query",
    "matcher",
    "normalizer",
    "resolve",
    "tool_query",
]
