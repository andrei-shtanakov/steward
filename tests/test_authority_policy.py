"""Structural checks for profiles/authority.yaml (RD-006 M2).

Catalog conformance (harness exists, routable coverage, retired pins) runs on
the arbiter side at vendoring, where the vendored agents-catalog lives; this
suite guards the structure and closed vocabularies at the SSOT.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CANONICAL = Path(__file__).parent.parent / "profiles" / "authority.yaml"

ROLES = {"decompose", "implement", "review", "benchmark"}
PHASES = {"authoring", "execution", "merge", "pr"}


def _load() -> dict:
    data = yaml.safe_load(CANONICAL.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"authority.yaml must be a mapping, got {type(data)}"
    return data


def _valid_pattern(pattern: str) -> bool:
    if "@" not in pattern:
        return False
    harness, _, model = pattern.partition("@")
    if not harness or "*" in harness or not model:
        return False
    return model == "*" or "*" not in model


def test_policy_parses_with_version_1() -> None:
    data = _load()
    assert data["version"] == 1
    assert data["unknown_context"] in ("deny", "allow")


def test_rules_use_closed_vocabularies() -> None:
    for rule in _load()["rules"]:
        assert rule["role"] in ROLES, rule
        assert rule["phase"] in PHASES, rule


def test_patterns_are_exact_or_harness_wildcard_only() -> None:
    for rule in _load()["rules"]:
        assert rule["agents"], f"empty allowlist rule: {rule}"
        for pattern in rule["agents"]:
            assert _valid_pattern(pattern), pattern


def test_default_is_fail_closed() -> None:
    # The canon ships deny; flipping to allow is a deliberate reviewed change.
    assert _load()["unknown_context"] == "deny"
