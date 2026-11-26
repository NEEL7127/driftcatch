"""Configuration.

The golden path uses no configuration at all. Everything here is progressive
disclosure: defaults that work, overridable per process, per project or per
recording, in that order of specificity.
"""

from __future__ import annotations

import os
import tomllib
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from driftcatch._name import ENV_PREFIX
from driftcatch.errors import ConfigError

__all__ = ["Config", "config", "get_config", "reset_config"]

Mode = Literal["auto", "record", "replay", "live"]
DriftPolicy = Literal["warn", "fail", "ignore"]
StreamTiming = Literal["instant", "recorded"]

DEFAULT_NORMALIZERS = [
    "drop_timestamps",
    "mask_uuids",
    "normalize_tool_call_ids",
]


class Config(BaseModel):
    """Resolved settings for a recording session."""

    recordings_dir: Path = Path("tests/recordings")
    matcher: str = "field"
    normalizers: list[str] = Field(default_factory=lambda: list(DEFAULT_NORMALIZERS))
    strict: bool = True
    drift_policy: DriftPolicy = "warn"
    require_approved: bool = False
    record: list[str] = Field(default_factory=lambda: ["llm.*", "tools.*"])
    passthrough: list[str] = Field(default_factory=list)
    redactors: list[str] = Field(default_factory=lambda: ["default"])
    stream_timing: StreamTiming = "instant"
    max_recording_bytes: int = 2_000_000
    default_tolerance: str = "tools+order"
    #: What to do with a tool call that has no recorded match during a what-if
    #: run. A counterfactual branch was never recorded, so there is nothing to
    #: replay past the mutation point: ``execute`` runs the real tool (the
    #: default, since the point of what-if is to explore that branch),
    #: ``stub`` returns ``None`` to let control flow proceed without side
    #: effects, and ``error`` refuses.
    what_if_unmatched: Literal["execute", "stub", "error"] = "execute"

    def should_record(self, target: str) -> bool:
        """Whether a boundary should be captured.

        ``target`` is a dotted name such as ``"llm.chat"`` or
        ``"tools.get_time"``. ``passthrough`` wins over ``record``, so a single
        noisy tool can be let through while everything else is pinned.
        """
        for pattern in self.passthrough:
            if fnmatch(target, pattern):
                return False
        return any(fnmatch(target, pattern) for pattern in self.record)

    def merged(self, **overrides: Any) -> Config:
        """Return a copy with ``overrides`` applied, ignoring ``None`` values."""
        clean = {key: value for key, value in overrides.items() if value is not None}
        if not clean:
            return self.model_copy(deep=True)
        try:
            return self.model_copy(deep=True).model_validate({**self.model_dump(), **clean})
        except ValidationError as exc:  # pragma: no cover - defensive
            raise ConfigError(f"invalid configuration override: {exc}") from exc


_process_config: Config | None = None


def find_config_file(start: Path | None = None) -> Path | None:
    """Locate ``driftcatch.toml`` or a ``pyproject.toml`` with our section."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        standalone = directory / "driftcatch.toml"
        if standalone.is_file():
            return standalone
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            try:
                with pyproject.open("rb") as handle:
                    data = tomllib.load(handle)
            except (OSError, tomllib.TOMLDecodeError):
                return None
            if isinstance(data.get("tool", {}).get("driftcatch"), dict):
                return pyproject
            return None
    return None


def _from_pyproject(start: Path | None = None) -> dict[str, Any]:
    """Read settings from ``driftcatch.toml`` or ``[tool.driftcatch]``.

    A standalone ``driftcatch.toml`` is preferred and is what the UI writes, so
    editing settings never has to rewrite a project's build file.
    """
    path = find_config_file(start)
    if path is None:
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    if path.name == "driftcatch.toml":
        return {k: v for k, v in data.items() if not isinstance(v, dict)} or dict(data)
    section = data.get("tool", {}).get("driftcatch", {})
    return dict(section) if isinstance(section, dict) else {}


def _from_env() -> dict[str, Any]:
    """Environment overrides, e.g. ``DRIFTCATCH_RECORDINGS_DIR``."""
    env: dict[str, Any] = {}
    mapping = {
        f"{ENV_PREFIX}_RECORDINGS_DIR": "recordings_dir",
        f"{ENV_PREFIX}_MATCHER": "matcher",
        f"{ENV_PREFIX}_DRIFT_POLICY": "drift_policy",
    }
    for variable, field in mapping.items():
        value = os.environ.get(variable)
        if value:
            env[field] = value
    strict = os.environ.get(f"{ENV_PREFIX}_STRICT")
    if strict is not None:
        env["strict"] = strict.lower() not in {"0", "false", "no"}
    return env


def config(**kwargs: Any) -> Config:
    """Set the process-wide configuration and return it."""
    global _process_config
    base = Config(**_from_pyproject())
    try:
        _process_config = base.model_validate({**base.model_dump(), **kwargs})
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration: {exc}") from exc
    return _process_config


def get_config() -> Config:
    """Resolve configuration: process override > pyproject > env > defaults."""
    if _process_config is not None:
        return _process_config.model_copy(deep=True)
    data = {**_from_pyproject(), **_from_env()}
    try:
        return Config(**data)
    except ValidationError as exc:
        raise ConfigError(f"invalid [tool.driftcatch] configuration: {exc}") from exc


def reset_config() -> None:
    """Forget any process-wide configuration. Used by tests."""
    global _process_config
    _process_config = None
