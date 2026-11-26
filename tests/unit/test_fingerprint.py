"""Fingerprinting and drift attribution."""

from __future__ import annotations

from driftcatch.recording import canonical, compare, compute_fingerprint, hash_str


def _fp(**overrides: object) -> object:
    base: dict[str, object] = {
        "model_id": "gpt-5.2-2026-03",
        "system_prompt": "Use available tools to find the order.",
        "tool_schemas": [{"name": "lookup_order"}],
        "matcher_config": {"matcher": "field"},
    }
    base.update(overrides)
    return compute_fingerprint(**base)  # type: ignore[arg-type]


def test_canonical_is_key_order_insensitive() -> None:
    """Key order must never look like drift."""
    assert canonical({"a": 1, "b": 2}) == canonical({"b": 2, "a": 1})


def test_canonical_is_whitespace_insensitive() -> None:
    assert canonical({"a": [1, 2]}) == '{"a":[1,2]}'


def test_hash_str_is_prefixed_and_stable() -> None:
    first = hash_str("hello")
    assert first.startswith("sha256:")
    assert first == hash_str("hello")
    assert first != hash_str("world")


def test_identical_inputs_produce_identical_fingerprints() -> None:
    assert _fp().value == _fp().value  # type: ignore[attr-defined]


def test_compare_is_empty_when_nothing_changed() -> None:
    assert compare(_fp(), _fp()) == []  # type: ignore[arg-type]


def test_compare_names_a_changed_prompt() -> None:
    """Drift must say *which* input changed, not merely that something did."""
    changed = _fp(system_prompt="Search for the order information.")
    assert compare(_fp(), changed) == ["prompt_hash"]  # type: ignore[arg-type]


def test_compare_names_a_changed_model() -> None:
    changed = _fp(model_id="claude-opus-5")
    assert compare(_fp(), changed) == ["model_id"]  # type: ignore[arg-type]


def test_compare_names_a_changed_tool_schema() -> None:
    changed = _fp(tool_schemas=[{"name": "lookup_order"}, {"name": "web_search"}])
    assert compare(_fp(), changed) == ["tool_schema_hash"]  # type: ignore[arg-type]


def test_compare_names_multiple_changes() -> None:
    """Bisect relies on being able to see every changed component at once."""
    changed = _fp(model_id="claude-opus-5", system_prompt="different")
    assert compare(_fp(), changed) == ["model_id", "prompt_hash"]  # type: ignore[arg-type]


def test_fingerprint_value_depends_on_every_component() -> None:
    baseline = _fp().value  # type: ignore[attr-defined]
    for override in (
        {"model_id": "other"},
        {"system_prompt": "other"},
        {"tool_schemas": [{"name": "other"}]},
        {"matcher_config": {"matcher": "positional"}},
    ):
        assert _fp(**override).value != baseline  # type: ignore[attr-defined]
