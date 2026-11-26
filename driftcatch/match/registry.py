"""Name-based registry for matchers and normalizers.

Built-ins and user-supplied implementations live in the same registry, so a
custom matcher is a first-class citizen selectable by name from config.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from driftcatch.errors import ConfigError
from driftcatch.match.base import Matcher

__all__ = [
    "Normalizer",
    "get_matcher",
    "get_normalizer",
    "list_matchers",
    "list_normalizers",
    "matcher",
    "normalizer",
]

Normalizer = Callable[[dict[str, Any]], dict[str, Any]]

_MATCHERS: dict[str, Matcher] = {}
_NORMALIZERS: dict[str, Normalizer] = {}

M = TypeVar("M", bound=Matcher)
N = TypeVar("N", bound=Normalizer)


def matcher(name: str) -> Callable[[type[M]], type[M]]:
    """Register a matcher class under ``name``."""

    def register(cls: type[M]) -> type[M]:
        instance = cls()
        instance.name = name
        _MATCHERS[name] = instance
        return cls

    return register


def normalizer(name: str) -> Callable[[N], N]:
    """Register a normalizer function under ``name``."""

    def register(fn: N) -> N:
        _NORMALIZERS[name] = fn
        return fn

    return register


def register_matcher(name: str, instance: Matcher) -> None:
    """Register an already-built matcher instance (used by ``chain``)."""
    _MATCHERS[name] = instance


def get_matcher(name: str) -> Matcher:
    try:
        return _MATCHERS[name]
    except KeyError:
        known = ", ".join(sorted(_MATCHERS)) or "none"
        raise ConfigError(f"unknown matcher {name!r}. Registered matchers: {known}") from None


def get_normalizer(name: str) -> Normalizer:
    try:
        return _NORMALIZERS[name]
    except KeyError:
        known = ", ".join(sorted(_NORMALIZERS)) or "none"
        raise ConfigError(f"unknown normalizer {name!r}. Registered normalizers: {known}") from None


def list_matchers() -> list[str]:
    return sorted(_MATCHERS)


def list_normalizers() -> list[str]:
    return sorted(_NORMALIZERS)
