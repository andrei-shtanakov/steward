"""Waiver tests (TASK-607, REQ-609, DESIGN-608/609): parse, validate, consume."""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.riskclassify.model import load_risk_model
from steward.riskclassify.waivers import (
    Waiver,
    WaiverFinding,
    find_waiver,
    load_waivers,
    validate_waivers,
)

CANONICAL = Path(__file__).parents[2] / "profiles" / "risk-model.yaml"

HEAD = "c" * 40

VALID = f"""---
gate_id: steward.gate_check
sha: {HEAD}
tier: medium
waived_by: andrei-shtanakov
reason: known-flaky presence check, tracked as ISSUE-12
---

# Waiver: steward.gate_check @ {HEAD[:7]}
"""


@pytest.fixture(scope="module")
def model():
    return load_risk_model(CANONICAL)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    d = tmp_path / "waivers"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(text, encoding="utf-8")
    return d


# --- parsing ---


def test_loads_valid_waiver(tmp_path: Path) -> None:
    d = _write(tmp_path, "steward.gate_check-ccccccc.md", VALID)
    waivers = load_waivers(d)
    assert len(waivers) == 1
    w = waivers[0]
    assert isinstance(w, Waiver)
    assert w.gate_id == "steward.gate_check"
    assert w.sha == HEAD
    assert w.tier == "medium"
    assert w.waived_by == "andrei-shtanakov"


def test_missing_dir_is_empty(tmp_path: Path) -> None:
    assert load_waivers(tmp_path / "nope") == []


def test_file_without_frontmatter_is_a_finding(tmp_path: Path) -> None:
    d = _write(tmp_path, "broken.md", "no frontmatter here\n")
    waivers = load_waivers(d)
    assert waivers == []


def test_missing_required_field_is_invalid(tmp_path: Path, model) -> None:
    bad = VALID.replace("waived_by: andrei-shtanakov\n", "")
    d = _write(tmp_path, "steward.gate_check-ccccccc.md", bad)
    findings = validate_waivers(load_waivers(d), model, head_sha=HEAD)
    assert findings == []  # unparsable/missing-field files never become Waivers
    # ...but the loader surfaces them via strict mode:
    with pytest.raises(ValueError, match="waived_by"):
        load_waivers(d, strict=True)


# --- validation (REQ-609) ---


def test_valid_waiver_produces_no_findings(tmp_path: Path, model) -> None:
    d = _write(tmp_path, "steward.gate_check-ccccccc.md", VALID)
    assert validate_waivers(load_waivers(d), model, head_sha=HEAD) == []


def test_stale_sha_is_error(tmp_path: Path, model) -> None:
    # DESIGN-608: a new commit kills every waiver — a stale file must be flagged.
    d = _write(tmp_path, "steward.gate_check-ccccccc.md", VALID)
    findings = validate_waivers(load_waivers(d), model, head_sha="d" * 40)
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, WaiverFinding)
    assert f.rule_id == "waiver-stale-sha"
    assert f.severity == "error"


def test_critical_tier_waiver_is_forbidden(tmp_path: Path, model) -> None:
    # OQ-3 (2026-07-12): waiver_policy.critical == forbidden.
    bad = VALID.replace("tier: medium", "tier: critical")
    d = _write(tmp_path, "steward.gate_check-ccccccc.md", bad)
    findings = validate_waivers(load_waivers(d), model, head_sha=HEAD)
    assert [f.rule_id for f in findings] == ["waiver-forbidden-tier"]
    assert findings[0].severity == "error"


def test_unknown_tier_in_waiver_is_error(tmp_path: Path, model) -> None:
    bad = VALID.replace("tier: medium", "tier: mild")
    d = _write(tmp_path, "steward.gate_check-ccccccc.md", bad)
    findings = validate_waivers(load_waivers(d), model, head_sha=HEAD)
    assert [f.rule_id for f in findings] == ["waiver-bad-tier"]


# --- consumption (guard side) ---


def test_find_waiver_matches_gate_and_sha(tmp_path: Path) -> None:
    d = _write(tmp_path, "steward.gate_check-ccccccc.md", VALID)
    waivers = load_waivers(d)
    assert find_waiver(waivers, "steward.gate_check", HEAD) is not None
    assert find_waiver(waivers, "steward.gate_check", "d" * 40) is None
    assert find_waiver(waivers, "tests.passed", HEAD) is None


def test_invalid_yaml_frontmatter_strict_message_is_accurate(tmp_path: Path) -> None:
    # Regression (Copilot, PR #8): split_frontmatter returns None for broken
    # YAML too — strict mode must not claim the frontmatter is absent.
    d = _write(tmp_path, "broken.md", "---\ngate_id: [unclosed\n---\n\nbody\n")
    assert load_waivers(d) == []
    with pytest.raises(ValueError, match="not parseable"):
        load_waivers(d, strict=True)


def test_waiver_with_short_sha_is_finding(tmp_path: Path, model) -> None:
    # Regression (Copilot, PR #8): consumers compare full 40-hex SHAs; a short
    # sha can only ever produce a misleading result.
    bad = VALID.replace(HEAD, "abc123")
    d = _write(tmp_path, "steward.gate_check-abc123.md", bad)
    findings = validate_waivers(load_waivers(d), model, head_sha="abc123")
    assert [f.rule_id for f in findings] == ["waiver-bad-sha"]
