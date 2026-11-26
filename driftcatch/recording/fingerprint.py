"""Fingerprinting: how DriftCatch knows a recording has gone stale.

Each input that can change an agent's behaviour is hashed *separately*, so that
drift detection can name which input changed, and ``drift bisect`` can attribute
a behaviour change to one of them.

Design reference: DESIGN.md sections 4.2 and 4.3.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from driftcatch.recording.schema import Fingerprint

__all__ = ["canonical", "compare", "compute_fingerprint", "hash_str"]


def canonical(obj: Any) -> str:
    """Serialise ``obj`` to a stable string.

    Key order and insignificant whitespace must never affect a hash, otherwise
    semantically identical inputs would look like drift.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def hash_str(text: str) -> str:
    """Hash a string to a prefixed digest, e.g. ``sha256:ab12...``."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_fingerprint(
    *,
    model_id: str,
    system_prompt: str,
    tool_schemas: list[dict[str, Any]],
    matcher_config: dict[str, Any],
) -> Fingerprint:
    """Build a fingerprint from the four behaviour-determining inputs."""
    prompt_hash = hash_str(canonical(system_prompt))
    tool_schema_hash = hash_str(canonical(tool_schemas))
    matcher_hash = hash_str(canonical(matcher_config))
    value = hash_str(
        canonical(
            {
                "model_id": model_id,
                "prompt_hash": prompt_hash,
                "tool_schema_hash": tool_schema_hash,
                "matcher_hash": matcher_hash,
            }
        )
    )
    return Fingerprint(
        value=value,
        model_id=model_id,
        prompt_hash=prompt_hash,
        tool_schema_hash=tool_schema_hash,
        matcher_hash=matcher_hash,
    )


def compare(approved: Fingerprint, current: Fingerprint) -> list[str]:
    """Return the names of the components that differ.

    An empty list means the recording still reflects its recorded inputs.
    """
    return [
        component
        for component in Fingerprint.COMPONENTS
        if getattr(approved, component) != getattr(current, component)
    ]
