"""Reasoning models: chain-of-thought must not be silently dropped.

Found by testing against a live reasoning model (NVIDIA Nemotron via an
OpenAI-compatible gateway). It returned ``content: null`` with the entire reply
in ``reasoning_content``, a field the OpenAI schema does not define. The parser
read only ``content``, so every assertion about what the model said saw ``None``
while the test still passed.

The same shape appears across o1-style models, DeepSeek-R1, QwQ and Anthropic's
extended thinking, so this is a family of providers, not one vendor's quirk.
"""

from __future__ import annotations

from typing import Any

import pytest

from driftcatch.intercept.http import assemble_stream
from driftcatch.providers import parse_response, parse_usage
from driftcatch.recording.schema import LLMResponse, Usage


def openai_style(
    *, content: str | None, reasoning: str | None = None, reasoning_tokens: int = 0
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content, "tool_calls": None}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    usage: dict[str, Any] = {"prompt_tokens": 21, "completion_tokens": 16}
    if reasoning_tokens:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}], "usage": usage}


# ----------------------------------------------------------- parsing


def test_reasoning_content_is_captured() -> None:
    parsed = parse_response(openai_style(content=None, reasoning="step one, step two"))
    assert parsed.reasoning == "step one, step two"


def test_content_and_reasoning_coexist() -> None:
    parsed = parse_response(openai_style(content="4", reasoning="2+2 is 4"))
    assert parsed.content == "4"
    assert parsed.reasoning == "2+2 is 4"


def test_text_falls_back_to_reasoning_when_content_is_null() -> None:
    """The real failure: a reasoning model often leaves content null."""
    parsed = parse_response(openai_style(content=None, reasoning="the answer is 4"))
    assert parsed.content is None
    assert parsed.text == "the answer is 4"


def test_text_prefers_content() -> None:
    parsed = parse_response(openai_style(content="4", reasoning="working"))
    assert parsed.text == "4"


def test_plain_models_are_unaffected() -> None:
    parsed = parse_response(openai_style(content="hello"))
    assert parsed.content == "hello"
    assert parsed.reasoning is None
    assert parsed.text == "hello"


def test_bare_reasoning_key_is_also_accepted() -> None:
    body = {"choices": [{"message": {"content": None, "reasoning": "alt spelling"}}]}
    assert parse_response(body).reasoning == "alt spelling"


# ---------------------------------------------------- anthropic thinking


def test_anthropic_thinking_blocks_are_captured() -> None:
    body = {
        "content": [
            {"type": "thinking", "thinking": "let me work through this"},
            {"type": "text", "text": "The answer is 4."},
        ],
        "stop_reason": "end_turn",
    }
    parsed = parse_response(body)
    assert parsed.content == "The answer is 4."
    assert parsed.reasoning == "let me work through this"


# ------------------------------------------------------------- usage


def test_reasoning_tokens_are_broken_out() -> None:
    usage = parse_usage(openai_style(content=None, reasoning="x", reasoning_tokens=16))
    assert usage.output_tokens == 16
    assert usage.reasoning_tokens == 16


def test_reasoning_tokens_default_to_zero() -> None:
    assert parse_usage(openai_style(content="hi")).reasoning_tokens == 0


def test_reasoning_tokens_are_already_inside_output_tokens() -> None:
    """Documented relationship: do not double-count them in a cost assertion."""
    usage = Usage(input_tokens=10, output_tokens=100, reasoning_tokens=80)
    assert usage.total_tokens == 110


# ----------------------------------------------------------- streaming


def test_streamed_reasoning_is_assembled() -> None:
    chunks = [
        'data: {"choices":[{"delta":{"reasoning_content":"think "}}]}\n\n',
        'data: {"choices":[{"delta":{"reasoning_content":"harder"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"4"},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    ]
    assembled = assemble_stream(chunks)
    message = assembled["choices"][0]["message"]
    assert message["reasoning_content"] == "think harder"
    assert message["content"] == "4"
    assert parse_response(assembled).text == "4"


def test_streamed_reasoning_only_still_yields_text() -> None:
    chunks = [
        'data: {"choices":[{"delta":{"reasoning_content":"all thought, no answer"}}]}\n\n',
        "data: [DONE]\n\n",
    ]
    parsed = parse_response(assemble_stream(chunks))
    assert parsed.content is None
    assert parsed.text == "all thought, no answer"


def test_streamed_anthropic_thinking_is_assembled() -> None:
    chunks = [
        'data: {"delta":{"thinking":"considering"}}\n\n',
        'data: {"delta":{"text":"done"}}\n\n',
    ]
    message = assemble_stream(chunks)["choices"][0]["message"]
    assert message["reasoning_content"] == "considering"
    assert message["content"] == "done"


# ------------------------------------------------------- serialisation


@pytest.mark.parametrize("reasoning", [None, "some thinking"])
def test_response_round_trips_through_json(reasoning: str | None) -> None:
    original = LLMResponse(content="4", reasoning=reasoning)
    assert LLMResponse.model_validate(original.model_dump()) == original
