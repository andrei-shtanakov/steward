"""RiskModel loader tests (TASK-604): parse, validate, provenance."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from steward.riskclassify.model import RiskModel, RiskModelError, load_risk_model

CANONICAL = Path(__file__).parents[2] / "profiles" / "risk-model.yaml"

MINIMAL = """
version: 1
tiers: [low, medium, high, critical]
profile_floors: {lite: low, team: medium}
class_tiers: {docs: low, code: medium, unknown: medium}
blast_tiers: {single-repo: low, cross-repo: high, ecosystem-contract: critical}
trust_tiers: {none: low, secrets: critical, external-api: high}
tier_gates:
  low: []
  medium: [steward.gate_check]
  high: [steward.gate_check]
  critical: [steward.gate_check]
waiver_policy: {critical: forbidden}
path_class:
  demo:
    - {glob: "src/**", class: code}
_generic:
  - {glob: "**/*.md", class: docs}
trust_rules:
  - {glob: "**/.env*", boundary: secrets}
declared_flags: [external-api]
consumer_registry:
  "demo/contracts/thing": [other]
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "risk-model.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_loads_canonical_model() -> None:
    model = load_risk_model(CANONICAL)
    assert isinstance(model, RiskModel)
    assert model.class_tiers["unknown"] == "medium"
    assert "Maestro" in model.path_class


def test_version_is_sha256_of_file(tmp_path: Path) -> None:
    p = _write(tmp_path, MINIMAL)
    model = load_risk_model(p)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    assert model.version_sha == f"sha256:{digest}"


def test_missing_unknown_class_tier_is_config_error(tmp_path: Path) -> None:
    bad = MINIMAL.replace(
        "class_tiers: {docs: low, code: medium, unknown: medium}",
        "class_tiers: {docs: low, code: medium}",
    )
    with pytest.raises(RiskModelError, match="unknown"):
        load_risk_model(_write(tmp_path, bad))


def test_unknown_class_tier_below_medium_is_config_error(tmp_path: Path) -> None:
    bad = MINIMAL.replace("unknown: medium", "unknown: low")
    with pytest.raises(RiskModelError, match="unknown"):
        load_risk_model(_write(tmp_path, bad))


def test_unknown_tier_name_is_config_error(tmp_path: Path) -> None:
    bad = MINIMAL.replace("docs: low", "docs: mild")
    with pytest.raises(RiskModelError, match="mild"):
        load_risk_model(_write(tmp_path, bad))


def test_structural_garbage_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(RiskModelError):
        load_risk_model(_write(tmp_path, "just: [a, string\n"))
    with pytest.raises(RiskModelError):
        load_risk_model(_write(tmp_path, "- a\n- list\n"))


def test_missing_file_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(RiskModelError):
        load_risk_model(tmp_path / "nope.yaml")


def test_tier_gates_must_cover_all_tiers(tmp_path: Path) -> None:
    bad = MINIMAL.replace("  critical: [steward.gate_check]\n", "")
    with pytest.raises(RiskModelError, match="tier_gates"):
        load_risk_model(_write(tmp_path, bad))


def test_rule_class_missing_from_class_tiers_is_config_error(tmp_path: Path) -> None:
    bad = MINIMAL.replace('{glob: "src/**", class: code}', '{glob: "src/**", class: cod}')
    with pytest.raises(RiskModelError, match="cod"):
        load_risk_model(_write(tmp_path, bad))


def test_missing_required_blast_key_is_config_error(tmp_path: Path) -> None:
    bad = MINIMAL.replace("single-repo: low, ", "")
    with pytest.raises(RiskModelError, match="single-repo"):
        load_risk_model(_write(tmp_path, bad))


def test_trust_rule_boundary_missing_from_trust_tiers_is_config_error(tmp_path: Path) -> None:
    bad = MINIMAL.replace("boundary: secrets", "boundary: sekrets")
    with pytest.raises(RiskModelError, match="sekrets"):
        load_risk_model(_write(tmp_path, bad))


def test_declared_flag_missing_from_trust_tiers_is_config_error(tmp_path: Path) -> None:
    bad = MINIMAL.replace("declared_flags: [external-api]", "declared_flags: [ext-api]")
    with pytest.raises(RiskModelError, match="ext-api"):
        load_risk_model(_write(tmp_path, bad))
