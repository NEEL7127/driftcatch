"""Provider body parsers.

The only provider-specific code in DriftCatch. Each parser is a small, pure
function from a request/response body to the normalised view; everything else in
the codebase works on that view.

Interception happens at the httpx transport layer, so one hook already covers
OpenAI, Anthropic, LiteLLM, Mistral, Cohere, Google and any gateway that speaks
one of their wire formats. These parsers only decide how to *read* the bodies.
"""

from __future__ import annotations

from typing import Any

from driftcatch.recording.schema import LLMRequest, LLMResponse, Usage

__all__ = [
    "LLM_PATH_MARKERS",
    "is_llm_path",
    "parse_request",
    "parse_response",
    "parse_usage",
]

#: URL path fragments that indicate a model call. Gateways (LiteLLM, OpenRouter,
#: Azure) reuse these paths, which is why matching on the path works broadly.
LLM_PATH_MARKERS = (
    "/chat/completions",
    "/v1/messages",
    "/messages",
    "/responses",
    "/completions",
    ":generatecontent",
    ":streamgeneratecontent",
    "/converse",
    "/invoke",
    "/predict",
)

_REQUEST_META_KEYS = {"model", "messages", "tools", "system", "stream", "input", "contents"}


def is_llm_path(path: str) -> bool:
    """True when a URL path looks like a model call."""
    lowered = path.lower()
    return any(marker in lowered for marker in LLM_PATH_MARKERS)


def _tool_name(tool: Any) -> str:
    """Pull a tool's name out of any of the common schema shapes."""
    if isinstance(tool, dict):
        if "name" in tool:
            return str(tool["name"])
        function = tool.get("function")
        if isinstance(function, dict) and "name" in function:
            return str(function["name"])
        declarations = tool.get("functionDeclarations") or tool.get("function_declarations")
        if isinstance(declarations, list) and declarations:
            first = declarations[0]
            if isinstance(first, dict) and "name" in first:
                return str(first["name"])
    return ""


def parse_request(body: dict[str, Any]) -> LLMRequest:
    """Normalise a provider request body.

    Anthropic carries the system prompt in a top-level ``system`` field while
    OpenAI puts it in ``messages``. Both are projected into ``messages`` so the
    fingerprint and the matcher see the same thing either way.
    """
    messages: list[dict[str, Any]] = []

    system = body.get("system")
    if isinstance(system, str) and system:
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        for block in system:
            text = block.get("text") if isinstance(block, dict) else None
            messages.append({"role": "system", "content": text if text else str(block)})

    raw_messages = body.get("messages") or body.get("contents") or body.get("input") or []
    if isinstance(raw_messages, list):
        for message in raw_messages:
            messages.append(message if isinstance(message, dict) else {"content": message})
    elif isinstance(raw_messages, str):
        messages.append({"role": "user", "content": raw_messages})

    raw_tools = body.get("tools") or []
    tools: list[dict[str, Any]] = []
    if isinstance(raw_tools, list):
        for tool in raw_tools:
            name = _tool_name(tool)
            tools.append({"name": name, "schema": tool} if name else {"schema": tool})

    params = {
        key: value
        for key, value in body.items()
        if key not in _REQUEST_META_KEYS and not key.startswith("_")
    }

    return LLMRequest(
        model=str(body.get("model", "unknown")),
        messages=messages,
        tools=tools,
        params=params,
    )


def _openai_response(body: dict[str, Any]) -> LLMResponse | None:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") or first.get("delta") or {}
    tool_calls: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        tool_calls.append(
            {
                "name": function.get("name") or call.get("name") or "",
                "args": function.get("arguments") or call.get("args") or {},
            }
        )
    return LLMResponse(
        content=message.get("content"),
        reasoning=message.get("reasoning_content") or message.get("reasoning"),
        tool_calls=tool_calls,
        finish_reason=first.get("finish_reason"),
    )


def _anthropic_response(body: dict[str, Any]) -> LLMResponse | None:
    content = body.get("content")
    if not isinstance(content, list):
        return None
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and block.get("text"):
            text_parts.append(str(block["text"]))
        elif kind == "thinking" and block.get("thinking"):
            # Anthropic's extended thinking arrives as its own block type.
            thinking_parts.append(str(block["thinking"]))
        elif kind == "tool_use":
            tool_calls.append({"name": block.get("name", ""), "args": block.get("input", {})})
    return LLMResponse(
        content="".join(text_parts) or None,
        reasoning="".join(thinking_parts) or None,
        tool_calls=tool_calls,
        finish_reason=body.get("stop_reason"),
    )


def _gemini_response(body: dict[str, Any]) -> LLMResponse | None:
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    first = candidates[0] if isinstance(candidates[0], dict) else {}
    parts = (first.get("content") or {}).get("parts") or []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if "text" in part:
            text_parts.append(str(part["text"]))
        call = part.get("functionCall") or part.get("function_call")
        if isinstance(call, dict):
            tool_calls.append({"name": call.get("name", ""), "args": call.get("args", {})})
    return LLMResponse(
        content="".join(text_parts) or None,
        tool_calls=tool_calls,
        finish_reason=first.get("finishReason"),
    )


def parse_response(body: dict[str, Any]) -> LLMResponse:
    """Normalise a provider response body, whatever its shape."""
    for parser in (_openai_response, _anthropic_response, _gemini_response):
        parsed = parser(body)
        if parsed is not None:
            return parsed
    # Unknown provider: keep whatever text we can find rather than losing it.
    text = body.get("output_text") or body.get("text") or body.get("completion")
    return LLMResponse(content=str(text) if text is not None else None)


def parse_usage(body: dict[str, Any]) -> Usage:
    """Normalise token accounting across providers."""
    usage = body.get("usage") or body.get("usageMetadata") or {}
    if not isinstance(usage, dict):
        return Usage()
    input_tokens = (
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("promptTokenCount")
        or 0
    )
    output_tokens = (
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("candidatesTokenCount")
        or 0
    )
    # Reasoning models report thinking tokens in a nested detail object. They
    # are already inside output_tokens, but they are usually the bulk of the
    # cost, so a cost assertion is far more useful when they are visible.
    details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    reasoning_tokens = 0
    if isinstance(details, dict):
        reasoning_tokens = int(details.get("reasoning_tokens") or 0)
    if not reasoning_tokens:
        reasoning_tokens = int(usage.get("reasoning_tokens") or 0)

    return Usage(
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        reasoning_tokens=reasoning_tokens,
    )
