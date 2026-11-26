"""Secret redaction.

Recordings hold full prompts and responses and are committed to git, so this
runs at record time, *before* anything reaches disk, and it fails closed: if a
credential-shaped string survives redaction the recording is not written at all.
"""

from __future__ import annotations

import re
from typing import Any

from driftcatch.errors import RedactionError

__all__ = [
    "DENY_HEADERS",
    "KEY_PATTERNS",
    "REDACTED",
    "assert_clean",
    "find_secrets",
    "redact",
]

REDACTED = "<redacted>"

DENY_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "api-key",
        "x-api-key",
        "x-goog-api-key",
        "openai-organization",
        "openai-project",
        "anthropic-api-key",
        "cookie",
        "set-cookie",
    }
)

#: Keys whose *value* is always sensitive, whatever the surrounding structure.
DENY_KEYS = frozenset({"api_key", "apikey", "access_token", "refresh_token", "password", "secret"})

KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
)


def _redact_string(text: str, found: list[str], path: str) -> str:
    cleaned = text
    for pattern in KEY_PATTERNS:
        if pattern.search(cleaned):
            cleaned = pattern.sub(REDACTED, cleaned)
            found.append(path)
    return cleaned


def _walk(node: Any, path: str, found: list[str]) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            lowered = str(key).lower()
            if lowered in DENY_HEADERS or lowered in DENY_KEYS:
                out[key] = REDACTED
                found.append(child)
            else:
                out[key] = _walk(value, child, found)
        return out
    if isinstance(node, list):
        return [_walk(item, f"{path}[{index}]", found) for index, item in enumerate(node)]
    if isinstance(node, str):
        return _redact_string(node, found, path)
    return node


def redact(obj: Any, redactors: list[str] | None = None) -> tuple[Any, list[str]]:
    """Return ``(cleaned, redacted_paths)``.

    ``redactors`` selects which rule sets to apply. ``"default"`` applies the
    header deny-list, the sensitive-key deny-list and the credential patterns.
    Passing an empty list disables redaction, which is never the default.
    """
    selected = ["default"] if redactors is None else redactors
    if "default" not in selected:
        return obj, []
    found: list[str] = []
    cleaned = _walk(obj, "", found)
    # Stable, de-duplicated report of what was removed.
    return cleaned, sorted(set(found))


def find_secrets(obj: Any) -> list[str]:
    """Return paths whose value still looks like a credential."""
    offenders: list[str] = []

    def scan(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                scan(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                scan(item, f"{path}[{index}]")
        elif isinstance(node, str) and any(p.search(node) for p in KEY_PATTERNS):
            offenders.append(path or "<root>")

    scan(obj, "")
    return sorted(set(offenders))


def assert_clean(obj: Any) -> None:
    """Raise :class:`RedactionError` if any credential survived redaction."""
    offenders = find_secrets(obj)
    if offenders:
        joined = ", ".join(offenders)
        raise RedactionError(
            "refusing to write a recording that still contains credential-shaped "
            f"values at: {joined}. Add a custom redactor or remove the secret from "
            "the request before recording."
        )
