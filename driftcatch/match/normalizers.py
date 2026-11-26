"""Built-in normalizers: deciding what counts as noise.

A normalizer strips values that vary run to run but do not change behaviour.
Without them, a fresh timestamp or a random tool-call id would look like drift.
"""

from __future__ import annotations

import re
from typing import Any

from driftcatch.match.registry import normalizer
from driftcatch.redact import redact

__all__ = ["apply_normalizers"]

_TIMESTAMP_KEY = re.compile(r"(created|timestamp|_at$|^time$|^date$)", re.IGNORECASE)
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
#: Provider-generated correlation ids for tool calls.
_TOOL_CALL_ID = re.compile(r"^(call_|toolu_|fc_|tooluse_)[A-Za-z0-9_\-]{6,}$")


@normalizer("drop_timestamps")
def drop_timestamps(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove keys that carry wall-clock values."""

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: strip(value)
                for key, value in node.items()
                if not _TIMESTAMP_KEY.search(str(key))
            }
        if isinstance(node, list):
            return [strip(item) for item in node]
        return node

    result = strip(payload)
    return result if isinstance(result, dict) else payload


@normalizer("mask_uuids")
def mask_uuids(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace UUID-shaped strings with a stable placeholder."""

    def mask(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: mask(value) for key, value in node.items()}
        if isinstance(node, list):
            return [mask(item) for item in node]
        if isinstance(node, str):
            return _UUID.sub("<uuid>", node)
        return node

    result = mask(payload)
    return result if isinstance(result, dict) else payload


@normalizer("normalize_tool_call_ids")
def normalize_tool_call_ids(payload: dict[str, Any]) -> dict[str, Any]:
    """Rewrite provider tool-call ids to positional placeholders.

    Mandatory in the default set. Providers mint a fresh random id for every
    request, so without this every single match would miss.
    """
    seen: dict[str, str] = {}

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if (
                    key in {"id", "tool_call_id", "tool_use_id"}
                    and isinstance(value, str)
                    and _TOOL_CALL_ID.match(value)
                    and value not in seen
                ):
                    seen[value] = f"<tc:{len(seen)}>"
                collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    def rewrite(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: rewrite(value) for key, value in node.items()}
        if isinstance(node, list):
            return [rewrite(item) for item in node]
        if isinstance(node, str) and node in seen:
            return seen[node]
        return node

    collect(payload)
    if not seen:
        return payload
    result = rewrite(payload)
    return result if isinstance(result, dict) else payload


@normalizer("json_canonical")
def json_canonical(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively sort dictionary keys so key order cannot look like drift."""

    def sort(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: sort(node[key]) for key in sorted(node, key=str)}
        if isinstance(node, list):
            return [sort(item) for item in node]
        return node

    result = sort(payload)
    return result if isinstance(result, dict) else payload


@normalizer("redact_secrets")
def redact_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip credentials so they never influence a match key."""
    cleaned, _ = redact(payload, ["default"])
    return cleaned if isinstance(cleaned, dict) else payload


def apply_normalizers(payload: dict[str, Any], names: list[str]) -> dict[str, Any]:
    """Apply the named normalizers in order."""
    from driftcatch.match.registry import get_normalizer

    result = payload
    for name in names:
        result = get_normalizer(name)(result)
    return result
