"""Typed, validated configuration profiles.

Two isolated profiles exist (`mode` discriminates them):
  - FixtureConfig: synthetic-only, no market decisions required.
  - ResearchConfig: the draft market profile; every "open" registry field
    left unset is reported as a named blocker instead of silently defaulting.

Fixture and research profiles are deliberately kept as separate models
(profile isolation) so a fixture run can never inherit an unresolved
market setting, and a research run can never silently fall back to the
fixture's zero-cost test simplifications.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    ValidationError,
)

from rsys.application.errors import ConfigError
from rsys.application.models import (
    Blocker,
    ConfigValidationResult,
    FixtureConfig,
    ReplayConfig,
    ResearchConfig,
    RsysConfig,
)
from rsys.application.registry import REGISTRY

_PROFILES: dict[str, type[BaseModel]] = {
    "fixture": FixtureConfig,
    "research": ResearchConfig,
    "replay": ReplayConfig,
}


def load_raw(path: str | Path) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read config file ({exc})") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: file is not valid UTF-8") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = (
            f" at line {mark.line + 1}, column {mark.column + 1}"
            if mark is not None
            else ""
        )
        raise ConfigError(f"{path}: malformed YAML{location}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level YAML must be a mapping")
    return data


def _format_validation_error(exc: ValidationError) -> str:
    """Field paths and messages only; never echoes raw input values (may contain secrets)."""
    parts = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in err["loc"])
        parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    return "; ".join(parts)


def parse_config(data: dict[str, Any]) -> RsysConfig:
    mode = data.get("mode")
    if not isinstance(mode, str):
        raise ConfigError(f"'mode' field must be a string; got {type(mode).__name__}")
    model = _PROFILES.get(mode)
    if model is None:
        raise ConfigError(
            f"unknown mode: {mode!r}; expected one of {sorted(_PROFILES)}"
        )
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc


def load_config(path: str | Path) -> RsysConfig:
    config = parse_config(load_raw(path))
    if isinstance(config, ReplayConfig):
        source = Path(config.input)
        if not source.is_absolute():
            config.input = str((Path(path).resolve().parent / source).resolve())
    return config


def _get_by_path(tree: dict[str, Any], dotted_path: str) -> Any:
    node: Any = tree
    for part in dotted_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def validate_config(config: RsysConfig) -> ConfigValidationResult:
    """Run registry-driven blocker/range checks. Fixture and replay profiles are offline
    synthetic by construction and have no registry-tracked fields."""
    if isinstance(config, (FixtureConfig, ReplayConfig)):
        return ConfigValidationResult(ok=True, blockers=[])

    tree = config.model_dump(by_alias=True, mode="json")
    blockers: list[Blocker] = []
    for spec in REGISTRY.values():
        value = _get_by_path(tree, spec.field_path)
        if spec.is_unresolved(value):
            blockers.append(
                Blocker(
                    field_path=spec.field_path,
                    decision_id=spec.decision_id,
                    reason=spec.rationale,
                )
            )
            continue
        error = spec.validate(value)
        if error:
            blockers.append(
                Blocker(
                    field_path=spec.field_path,
                    decision_id=spec.decision_id,
                    reason=error,
                )
            )
    blockers.sort(key=lambda b: b.field_path)
    return ConfigValidationResult(ok=len(blockers) == 0, blockers=blockers)
