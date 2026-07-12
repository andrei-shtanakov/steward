"""RiskModel: versioned risk-model.yaml loader (WS-006, REQ-601, REQ-603).

The model is data, not code: lookup tables and path rules reviewed via PR.
``version_sha`` (sha256 of the file bytes) travels into every classification
and verdict-record so any tier can be reproduced post-mortem (DESIGN-607).
Structural problems raise :class:`RiskModelError` — the CLI maps it to exit 2,
mirroring gate-check's config-error semantics.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

__all__ = ["PathRule", "RiskModel", "RiskModelError", "TIERS", "load_risk_model", "tier_max"]

TIERS = ("low", "medium", "high", "critical")

# Fail-closed floor for unmapped paths (REQ-603): `unknown` may never sit below this.
_UNKNOWN_MIN = "medium"


class RiskModelError(Exception):
    """risk-model.yaml is missing, unparsable, or structurally invalid."""


@dataclass(frozen=True)
class PathRule:
    """One path->value rule; first match wins inside a section."""

    glob: str
    value: str


@dataclass(frozen=True)
class RiskModel:
    """Parsed risk-model.yaml plus its provenance hash."""

    version_sha: str
    profile_floors: dict[str, str]
    class_tiers: dict[str, str]
    blast_tiers: dict[str, str]
    trust_tiers: dict[str, str]
    tier_gates: dict[str, list[str]]
    waiver_policy: dict[str, str]
    path_class: dict[str, list[PathRule]]
    generic_class: list[PathRule]
    trust_rules: list[PathRule]
    declared_flags: list[str] = field(default_factory=list)
    consumer_registry: dict[str, list[str]] = field(default_factory=dict)


def tier_max(*tiers: str) -> str:
    """Highest of the given tiers (the monotone combinator core, DESIGN-604)."""
    return TIERS[max(TIERS.index(t) for t in tiers)]


def load_risk_model(path: Path) -> RiskModel:
    """Load and validate risk-model.yaml; raise :class:`RiskModelError` on any defect."""
    try:
        raw = yaml.safe_load(path.read_bytes())
    except OSError as exc:
        raise RiskModelError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RiskModelError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RiskModelError(f"{path}: top level must be a mapping")

    version_sha = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    class_tiers = _tier_table(raw, "class_tiers")
    unknown = class_tiers.get("unknown")
    if unknown is None:
        raise RiskModelError("class_tiers must define 'unknown' (fail-closed default, REQ-603)")
    if TIERS.index(unknown) < TIERS.index(_UNKNOWN_MIN):
        raise RiskModelError(f"class_tiers.unknown must be >= {_UNKNOWN_MIN}, got '{unknown}'")

    tier_gates = _mapping(raw, "tier_gates")
    missing = [t for t in TIERS if t not in tier_gates]
    if missing:
        raise RiskModelError(f"tier_gates must cover every tier; missing: {missing}")
    gates = {t: _str_list(v, f"tier_gates.{t}") for t, v in tier_gates.items()}

    path_class = {
        str(repo): _rules(rules, f"path_class.{repo}", key="class")
        for repo, rules in _mapping(raw, "path_class").items()
    }

    return RiskModel(
        version_sha=version_sha,
        profile_floors=_tier_table(raw, "profile_floors"),
        class_tiers=class_tiers,
        blast_tiers=_tier_table(raw, "blast_tiers"),
        trust_tiers=_tier_table(raw, "trust_tiers"),
        tier_gates=gates,
        waiver_policy={str(k): str(v) for k, v in _mapping(raw, "waiver_policy").items()},
        path_class=path_class,
        generic_class=_rules(raw.get("_generic", []), "_generic", key="class"),
        trust_rules=_rules(raw.get("trust_rules", []), "trust_rules", key="boundary"),
        declared_flags=_str_list(raw.get("declared_flags", []), "declared_flags"),
        consumer_registry={
            str(k): _str_list(v, f"consumer_registry.{k}")
            for k, v in _mapping(raw, "consumer_registry", optional=True).items()
        },
    )


def _mapping(raw: dict, key: str, *, optional: bool = False) -> dict:
    value = raw.get(key)
    if value is None and optional:
        return {}
    if not isinstance(value, dict):
        raise RiskModelError(f"'{key}' must be a mapping, got {type(value).__name__}")
    return value


def _tier_table(raw: dict, key: str) -> dict[str, str]:
    table = {}
    for name, tier in _mapping(raw, key).items():
        if tier not in TIERS:
            raise RiskModelError(f"{key}.{name}: unknown tier '{tier}' (expected one of {TIERS})")
        table[str(name)] = str(tier)
    return table


def _str_list(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RiskModelError(f"'{where}' must be a list of strings")
    return list(value)


def _rules(value: object, where: str, *, key: str) -> list[PathRule]:
    if not isinstance(value, list):
        raise RiskModelError(f"'{where}' must be a list of rules")
    rules = []
    for i, item in enumerate(value):
        if not isinstance(item, dict) or "glob" not in item or key not in item:
            raise RiskModelError(f"{where}[{i}]: rule needs 'glob' and '{key}'")
        rules.append(PathRule(glob=str(item["glob"]), value=str(item[key])))
    return rules
