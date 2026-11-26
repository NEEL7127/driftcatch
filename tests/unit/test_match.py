"""The matcher: the critical path. These are the adversarial tests.

Rule under test throughout: one input change must produce exactly one clear
error, and a no-match must never silently serve a nearby response.
"""

from __future__ import annotations

from typing import Any

import pytest

from driftcatch.config import Config
from driftcatch.errors import ConfigError, NoMatchError
from driftcatch.match import (
    Confidence,
    MatchState,
    chain,
    get_matcher,
    list_matchers,
    list_normalizers,
    llm_query,
    matcher,
    resolve,
    tool_query,
)
from driftcatch.match.base import Match, MatchQuery, NoMatch
from driftcatch.recording.schema import (
    ErrorInfo,
    Event,
    LLMEvent,
    LLMRequest,
    LLMResponse,
    ToolEvent,
)

PROMPT = "Use available tools to find the order."


# Custom matchers are registered at import time, not inside a test. Registering
# inside one test and relying on it from another is a hidden inter-test
# dependency that only shows up under `pytest -n`, which is precisely the class
# of bug this project exists to catch.
@matcher("always_fuzzy")
class AlwaysFuzzy:
    """A deliberately low-confidence matcher, for exercising strict mode."""

    name = "always_fuzzy"

    def match(self, query, candidates, state, ctx):  # type: ignore[no-untyped-def]
        return Match(event=candidates[0], confidence=Confidence.FUZZY, score=0.5)


@matcher("always_first")
class AlwaysFirst:
    """Proves a user-supplied matcher is a first-class citizen."""

    name = "always_first"

    def match(self, query, candidates, state, ctx):  # type: ignore[no-untyped-def]
        return Match(event=candidates[0], confidence=Confidence.EXACT)


def llm_event(
    index: int,
    *,
    prompt: str = PROMPT,
    model: str = "gpt-5.2",
    tools: list[dict[str, Any]] | None = None,
    reply: str = "ok",
    extra: dict[str, Any] | None = None,
) -> LLMEvent:
    messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}]
    if extra:
        messages[0].update(extra)
    return LLMEvent(
        index=index,
        request=LLMRequest(
            model=model,
            messages=messages,
            tools=tools if tools is not None else [{"name": "lookup_order"}],
        ),
        response=LLMResponse(content=reply),
    )


def llm_request(
    *,
    prompt: str = PROMPT,
    model: str = "gpt-5.2",
    tools: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> LLMRequest:
    messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}]
    if extra:
        messages[0].update(extra)
    return LLMRequest(
        model=model,
        messages=messages,
        tools=tools if tools is not None else [{"name": "lookup_order"}],
    )


def cfg(**overrides: Any) -> Config:
    return Config(**overrides)


# ---------------------------------------------------------------- 1. exact


def test_identical_request_matches_exactly() -> None:
    events: list[Event] = [llm_event(0)]
    match = resolve(llm_query(llm_request()), events, MatchState(), cfg())
    assert match.confidence is Confidence.EXACT
    assert match.event.index == 0


# --------------------------------------------------- 2. normalized by noise


def test_timestamp_noise_still_matches() -> None:
    """A fresh timestamp must not look like drift."""
    events: list[Event] = [llm_event(0, extra={"created": 111})]
    query = llm_query(llm_request(extra={"created": 999}))
    match = resolve(query, events, MatchState(), cfg())
    assert match.confidence is Confidence.NORMALIZED


# ------------------------------------------- 3. mandatory tool-call id fix


def test_tool_call_ids_are_normalized() -> None:
    """Providers mint a fresh tool-call id per request; without normalizing
    this, every single match would miss."""
    events: list[Event] = [llm_event(0, extra={"tool_call_id": "call_abc123456789"})]
    query = llm_query(llm_request(extra={"tool_call_id": "call_zzz987654321"}))
    match = resolve(query, events, MatchState(), cfg())
    assert match.confidence is Confidence.NORMALIZED


# -------------------------------------------------- 4. repeated requests


def test_same_request_twice_serves_each_recorded_reply_in_turn() -> None:
    """The retry / polling case: identical request, two different answers."""
    events: list[Event] = [
        llm_event(0, reply="pending"),
        llm_event(1, reply="done"),
    ]
    state = MatchState()
    first = resolve(llm_query(llm_request()), events, state, cfg())
    second = resolve(llm_query(llm_request()), events, state, cfg())

    assert first.event.index == 0
    assert second.event.index == 1
    assert first.event.response.content == "pending"
    assert second.event.response.content == "done"


def test_a_third_identical_request_diverges() -> None:
    """Doing more work than was recorded is drift, not a free pass."""
    events: list[Event] = [llm_event(0), llm_event(1)]
    state = MatchState()
    resolve(llm_query(llm_request()), events, state, cfg())
    resolve(llm_query(llm_request()), events, state, cfg())

    with pytest.raises(NoMatchError, match="no further llm events"):
        resolve(llm_query(llm_request()), events, state, cfg())


# -------------------------------------------------- 5. parallel tool calls


def test_parallel_tool_calls_match_in_either_order() -> None:
    """Concurrency reorders calls; content matching must not care."""
    events: list[Event] = [
        ToolEvent(index=0, name="lookup_order", args={"id": 1}, result={"ok": True}),
        ToolEvent(index=1, name="web_search", args={"q": "x"}, result={"hits": 0}),
    ]
    state = MatchState()
    second = resolve(tool_query("web_search", {"q": "x"}), events, state, cfg())
    first = resolve(tool_query("lookup_order", {"id": 1}), events, state, cfg())

    assert second.event.index == 1
    assert first.event.index == 0


# ------------------------------------------------- 6. exactly one error


def test_loop_with_more_calls_than_recorded_reports_once() -> None:
    events: list[Event] = [llm_event(0)]
    state = MatchState()
    resolve(llm_query(llm_request()), events, state, cfg())

    with pytest.raises(NoMatchError) as excinfo:
        resolve(llm_query(llm_request()), events, state, cfg())

    message = str(excinfo.value)
    assert message.count("diverged at event") <= 1


# ------------------------------------------------ 7. names what changed


def test_changed_prompt_names_the_field_and_shows_both_values() -> None:
    events: list[Event] = [llm_event(0, prompt=PROMPT)]
    query = llm_query(llm_request(prompt="Search for the order information."))

    with pytest.raises(NoMatchError) as excinfo:
        resolve(query, events, MatchState(), cfg())

    message = str(excinfo.value)
    assert "messages[0].content" in message
    assert PROMPT in message
    assert "Search for the order information." in message
    # The hint must name commands that exist. This asserted `drift record
    # --update` for a while, which pytest rejects outright - an error message
    # that sends you to a failing command is worse than one that says nothing.
    assert "drift record" in message
    assert "drift approve" in message
    assert "--update" not in message


def test_changed_model_is_reported() -> None:
    events: list[Event] = [llm_event(0, model="gpt-5.2")]
    query = llm_query(llm_request(model="claude-opus-5"))
    with pytest.raises(NoMatchError, match="model"):
        resolve(query, events, MatchState(), cfg())


def test_added_tool_is_reported() -> None:
    events: list[Event] = [llm_event(0, tools=[{"name": "lookup_order"}])]
    query = llm_query(llm_request(tools=[{"name": "lookup_order"}, {"name": "web_search"}]))
    with pytest.raises(NoMatchError, match="tools"):
        resolve(query, events, MatchState(), cfg())


# ------------------------------------------------------- 8. tool matcher


def test_tool_matcher_reports_an_unrecorded_tool_clearly() -> None:
    """The classic agent regression: it now calls a tool it never called."""
    events: list[Event] = [
        ToolEvent(index=0, name="lookup_order", args={"id": 1}, result={"ok": True})
    ]
    with pytest.raises(NoMatchError, match="'web_search' was never called"):
        resolve(
            tool_query("web_search", {"q": "x"}),
            events,
            MatchState(),
            cfg(matcher="tool_call"),
        )


def test_tool_matcher_reports_changed_arguments() -> None:
    events: list[Event] = [
        ToolEvent(index=0, name="lookup_order", args={"id": 1}, result={"ok": True})
    ]
    with pytest.raises(NoMatchError, match="different arguments"):
        resolve(
            tool_query("lookup_order", {"id": 999}),
            events,
            MatchState(),
            cfg(matcher="tool_call"),
        )


def test_tool_matcher_delegates_llm_queries_to_field() -> None:
    events: list[Event] = [llm_event(0)]
    match = resolve(llm_query(llm_request()), events, MatchState(), cfg(matcher="tool_call"))
    assert match.event.index == 0


# ----------------------------------------------------- 9. strict / fuzzy


def test_strict_rejects_a_fuzzy_match() -> None:
    events: list[Event] = [llm_event(0)]
    with pytest.raises(NoMatchError, match="fuzzy match"):
        resolve(llm_query(llm_request()), events, MatchState(), cfg(matcher="always_fuzzy"))


def test_non_strict_accepts_a_fuzzy_match() -> None:
    events: list[Event] = [llm_event(0)]
    match = resolve(
        llm_query(llm_request()),
        events,
        MatchState(),
        cfg(matcher="always_fuzzy", strict=False),
    )
    assert match.confidence is Confidence.FUZZY


# ----------------------------------------------------------- 10. chain


def test_positional_matches_despite_content_change() -> None:
    events: list[Event] = [llm_event(0, prompt="totally different")]
    match = resolve(llm_query(llm_request()), events, MatchState(), cfg(matcher="positional"))
    assert match.event.index == 0


def test_chain_falls_through_to_the_next_strategy() -> None:
    composite = chain("field", "positional")
    events: list[Event] = [llm_event(0, prompt="totally different")]
    match = resolve(llm_query(llm_request()), events, MatchState(), cfg(matcher=composite.name))
    assert match.event.index == 0
    assert match.matcher == composite.name


def test_chain_rejects_unknown_strategy_names() -> None:
    with pytest.raises(ConfigError, match="unknown matcher"):
        chain("field", "does_not_exist")


# --------------------------------------------------------- 11. registry


def test_builtin_matchers_and_normalizers_are_registered() -> None:
    assert {"field", "positional", "tool_call"} <= set(list_matchers())
    assert {
        "drop_timestamps",
        "mask_uuids",
        "normalize_tool_call_ids",
        "json_canonical",
        "redact_secrets",
    } <= set(list_normalizers())


def test_custom_matcher_is_selectable_by_name() -> None:
    events: list[Event] = [llm_event(0), llm_event(1)]
    match = resolve(llm_query(llm_request()), events, MatchState(), cfg(matcher="always_first"))
    assert match.event.index == 0


def test_unknown_matcher_name_raises() -> None:
    with pytest.raises(ConfigError, match="unknown matcher"):
        get_matcher("nope")


# ------------------------------------------------------------- extras


def test_no_match_never_returns_a_nearby_response() -> None:
    """A false green is worse than a false red: resolve must raise, not guess."""
    events: list[Event] = [llm_event(0, prompt="recorded prompt")]
    with pytest.raises(NoMatchError):
        resolve(llm_query(llm_request(prompt="different prompt")), events, MatchState(), cfg())


def test_match_trail_records_confidence_for_audit() -> None:
    events: list[Event] = [llm_event(0)]
    state = MatchState()
    resolve(llm_query(llm_request()), events, state, cfg())
    assert len(state.trail) == 1
    assert state.trail[0].confidence is Confidence.EXACT
    assert state.trail[0].matcher == "field"


def test_error_only_event_is_matchable() -> None:
    """Recorded failures are behaviour and must be servable on replay."""
    events: list[Event] = [
        ToolEvent(
            index=0,
            name="issue_refund",
            args={"id": 1},
            error=ErrorInfo(type="TimeoutError", message="boom"),
        )
    ]
    match = resolve(tool_query("issue_refund", {"id": 1}), events, MatchState(), cfg())
    assert match.event.error.type == "TimeoutError"


def test_empty_recording_diverges_with_a_helpful_message() -> None:
    with pytest.raises(NoMatchError, match="no further llm events"):
        resolve(llm_query(llm_request()), [], MatchState(), cfg())


def test_no_match_object_is_distinct_from_match() -> None:
    assert isinstance(NoMatch(reason="x"), NoMatch)
    assert MatchQuery(kind="llm", payload={}).kind == "llm"
