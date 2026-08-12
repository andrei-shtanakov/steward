"""proposal-intake admission check (steward#64).

Fixtures mirror the live PP-101 bundle shape (impresario
``pilot/forconcept/pp-101/``): proposal.yaml + decisions/*.yaml. The
acceptance contract is mutation-based: the clean bundle admits, every
named mutation rejects with a specific finding.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from steward.proposalintake import IntakeConfigError, check_intake
from steward.riskclassify.cli import app

runner = CliRunner()


def _proposal(**overrides: object) -> dict:
    doc: dict = {
        "proposal_id": "PP-101",
        "idea_ref": "idea://IDEA-101",
        "version": 8,
        "status": "approved",
        "iteration": 2,
        "refs": {"exchange_log": "exchange-log://XL-101"},
        "created_at": "2026-08-12T02:08:53Z",
        "updated_at": "2026-08-12T04:12:30Z",
    }
    doc.update(overrides)
    return doc


def _decision(decision_id: str, gate_id: str, version: int, **overrides: object) -> dict:
    doc: dict = {
        "decision_id": decision_id,
        "gate_id": gate_id,
        "subject": {"kind": "product_proposal", "ref": "proposal://PP-101", "version": version},
        "decision": "approve",
        "decided_by": {"kind": "human", "id": "andrei", "role": "business_owner"},
        "decided_at": "2026-08-12T04:09:21Z",
        "reason": "инвестируем: ценность доказана, риски закрыты",
    }
    doc.update(overrides)
    return doc


def _write_bundle(
    root: Path, proposal: dict | str | None, decisions: dict[str, dict | str]
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if proposal is not None:
        text = proposal if isinstance(proposal, str) else yaml.safe_dump(proposal)
        (root / "proposal.yaml").write_text(text, encoding="utf-8")
    ddir = root / "decisions"
    ddir.mkdir(exist_ok=True)
    for name, doc in decisions.items():
        text = doc if isinstance(doc, str) else yaml.safe_dump(doc)
        (ddir / name).write_text(text, encoding="utf-8")
    return root


def _clean_decisions() -> dict[str, dict]:
    return {
        "gd-001.yaml": _decision("GD-001", "qg5_business", 6),
        "gd-002.yaml": _decision(
            "GD-002",
            "qg5_committee",
            7,
            decided_by={"kind": "human", "id": "andrei", "role": "committee_chair"},
        ),
    }


def _errors(result) -> set[str]:
    return {f.rule_id for f in result.findings if f.severity == "error"}


def test_clean_bundle_admits(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path / "pp", _proposal(), _clean_decisions())
    result = check_intake(bundle)
    assert result.admitted
    assert result.proposal_id == "PP-101"
    assert result.proposal_version == 8
    assert not result.findings


def test_not_approved_rejects(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "pp", _proposal(status="business_approved"), _clean_decisions()
    )
    result = check_intake(bundle)
    assert not result.admitted
    assert "INTAKE-NOT-APPROVED" in _errors(result)


def test_missing_one_gate_decision_rejects(tmp_path: Path) -> None:
    decisions = _clean_decisions()
    del decisions["gd-002.yaml"]
    result = check_intake(_write_bundle(tmp_path / "pp", _proposal(), decisions))
    assert not result.admitted
    assert "INTAKE-DECISION-MISSING" in _errors(result)
    assert any("qg5_committee" in f.message for f in result.findings)


def test_superseded_approve_rejects(tmp_path: Path) -> None:
    """An approve overridden by a later record is not admission evidence."""
    decisions = _clean_decisions()
    decisions["gd-003.yaml"] = _decision(
        "GD-003",
        "qg5_committee",
        7,
        decision="hold",
        supersedes="gate-decision://GD-002",
        review_after="2026-09-01T00:00:00Z",
    )
    result = check_intake(_write_bundle(tmp_path / "pp", _proposal(), decisions))
    assert not result.admitted
    assert "INTAKE-DECISION-MISSING" in _errors(result)
    assert any("supersedes" in f.message for f in result.findings)


def test_replacement_approve_after_supersession_admits(tmp_path: Path) -> None:
    decisions = _clean_decisions()
    decisions["gd-003.yaml"] = _decision(
        "GD-003", "qg5_committee", 7, supersedes="gate-decision://GD-002"
    )
    result = check_intake(_write_bundle(tmp_path / "pp", _proposal(), decisions))
    assert result.admitted


def test_decision_about_other_proposal_does_not_count(tmp_path: Path) -> None:
    decisions = _clean_decisions()
    foreign = _decision("GD-002", "qg5_committee", 7)
    foreign["subject"] = {"kind": "product_proposal", "ref": "proposal://PP-999", "version": 7}
    decisions["gd-002.yaml"] = foreign
    result = check_intake(_write_bundle(tmp_path / "pp", _proposal(), decisions))
    assert not result.admitted
    assert "INTAKE-DECISION-MISSING" in _errors(result)
    assert any(f.rule_id == "INTAKE-FOREIGN-SUBJECT" for f in result.findings)


def test_decision_version_ahead_of_proposal_rejects(tmp_path: Path) -> None:
    decisions = _clean_decisions()
    decisions["gd-002.yaml"]["subject"]["version"] = 9  # proposal is v8
    result = check_intake(_write_bundle(tmp_path / "pp", _proposal(), decisions))
    assert not result.admitted
    assert "INTAKE-VERSION-AHEAD" in _errors(result)


def test_schema_invalid_decision_rejects(tmp_path: Path) -> None:
    """decided_by.kind is schema-pinned to human — an agent decision is invalid."""
    decisions = _clean_decisions()
    decisions["gd-002.yaml"]["decided_by"] = {"kind": "agent", "id": "bot"}
    result = check_intake(_write_bundle(tmp_path / "pp", _proposal(), decisions))
    assert not result.admitted
    assert "INTAKE-SCHEMA" in _errors(result)


def test_unparseable_decision_rejects(tmp_path: Path) -> None:
    decisions: dict[str, dict | str] = dict(_clean_decisions())
    decisions["broken.yaml"] = "{ not: [ yaml"
    result = check_intake(_write_bundle(tmp_path / "pp", _proposal(), decisions))
    assert not result.admitted
    assert "INTAKE-UNPARSEABLE" in _errors(result)


def test_missing_proposal_rejects(tmp_path: Path) -> None:
    result = check_intake(_write_bundle(tmp_path / "pp", None, _clean_decisions()))
    assert not result.admitted
    assert "INTAKE-PROPOSAL-MISSING" in _errors(result)


def test_missing_decisions_dir_rejects(tmp_path: Path) -> None:
    root = tmp_path / "pp"
    root.mkdir()
    (root / "proposal.yaml").write_text(yaml.safe_dump(_proposal()), encoding="utf-8")
    result = check_intake(root)
    assert not result.admitted
    assert "INTAKE-DECISION-MISSING" in _errors(result)


def test_duplicate_decision_id_rejects(tmp_path: Path) -> None:
    decisions = _clean_decisions()
    decisions["gd-002b.yaml"] = _decision("GD-002", "qg5_committee", 7)
    result = check_intake(_write_bundle(tmp_path / "pp", _proposal(), decisions))
    assert not result.admitted
    assert "INTAKE-DUPLICATE-DECISION-ID" in _errors(result)


def test_yaml_timestamps_stay_strings(tmp_path: Path) -> None:
    """Unquoted YAML timestamps must not become datetime (single-parser rule)."""
    proposal_text = yaml.safe_dump(_proposal()).replace(
        "'2026-08-12T02:08:53Z'", "2026-08-12T02:08:53Z"
    )
    bundle = _write_bundle(tmp_path / "pp", proposal_text, _clean_decisions())
    assert check_intake(bundle).admitted


def test_missing_bundle_dir_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(IntakeConfigError):
        check_intake(tmp_path / "nope")


def test_cli_admit_and_reject(tmp_path: Path) -> None:
    clean = _write_bundle(tmp_path / "clean", _proposal(), _clean_decisions())
    ok = runner.invoke(app, ["proposal-intake", str(clean)])
    assert ok.exit_code == 0, ok.output
    assert "admit: PP-101 v8" in ok.output

    bad = _write_bundle(
        tmp_path / "bad", _proposal(status="in_iteration", version=4), _clean_decisions()
    )
    rej = runner.invoke(app, ["proposal-intake", str(bad)])
    assert rej.exit_code == 1
    assert "reject" in rej.output

    cfg = runner.invoke(app, ["proposal-intake", str(tmp_path / "absent")])
    assert cfg.exit_code == 2
