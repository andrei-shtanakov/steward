"""CLI tests (TASK-606): `steward risk-classify` — determinism, exit codes, shape."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from steward.riskclassify.cli import app

CANONICAL = Path(__file__).parents[2] / "profiles" / "risk-model.yaml"

runner = CliRunner()


def _facts(tmp_path: Path, **overrides: object) -> Path:
    data: dict = {
        "project": "Maestro",
        "sha": "a" * 40,
        "paths": ["maestro/models.py", "README.md"],
    }
    data.update(overrides)
    p = tmp_path / "facts.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_no_fs_json_output_shape(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["risk-classify", "--no-fs", str(_facts(tmp_path)), "--risk-model", str(CANONICAL)],
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["tier"] == "high"
    assert out["phase"] == "ex_post"
    assert out["inputs"]["change_class"] == "state-machine"
    assert out["dominant_axis"] == "change_class"
    assert out["floor_profile"] == "lite"
    assert "steward.gate_check" in out["mandatory_gates"]
    assert out["sha"] == "a" * 40
    assert out["risk_model_version"].startswith("sha256:")
    assert out["flags"] == []


def test_byte_identical_output_on_double_run(tmp_path: Path) -> None:
    args = ["risk-classify", "--no-fs", str(_facts(tmp_path)), "--risk-model", str(CANONICAL)]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)
    assert first.exit_code == second.exit_code == 0
    assert first.output == second.output


def test_declared_scope_input_is_ex_ante(tmp_path: Path) -> None:
    scope = tmp_path / "scope.json"
    scope.write_text(
        json.dumps({"project": "Maestro", "sha": "b" * 40, "scope": ["contracts/**"]}),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["risk-classify", "--declared", str(scope), "--risk-model", str(CANONICAL)],
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["phase"] == "ex_ante"
    assert out["inputs"]["change_class"] == "contract"


def test_scope_violation_via_facts(tmp_path: Path) -> None:
    facts = _facts(tmp_path, declared_scope=["README.md"])
    result = runner.invoke(
        app,
        ["risk-classify", "--no-fs", str(facts), "--risk-model", str(CANONICAL)],
    )
    out = json.loads(result.output)
    assert "scope_violation" in out["flags"]
    assert out["tier"] == "high"


def test_bad_model_is_exit_2(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["risk-classify", "--no-fs", str(_facts(tmp_path)), "--risk-model", str(bad)],
    )
    assert result.exit_code == 2


def test_bad_facts_is_exit_2(tmp_path: Path) -> None:
    p = tmp_path / "facts.json"
    p.write_text('{"project": "Maestro"}', encoding="utf-8")  # no sha, no paths
    result = runner.invoke(
        app,
        ["risk-classify", "--no-fs", str(p), "--risk-model", str(CANONICAL)],
    )
    assert result.exit_code == 2


def test_missing_input_source_is_exit_2() -> None:
    result = runner.invoke(app, ["risk-classify", "--risk-model", str(CANONICAL)])
    assert result.exit_code == 2


def test_wrong_type_paths_is_exit_2(tmp_path: Path) -> None:
    # Regression (Copilot, PR #7): non-list `paths` must be exit 2, not a TypeError.
    result = runner.invoke(
        app,
        [
            "risk-classify",
            "--no-fs",
            str(_facts(tmp_path, paths="maestro/models.py")),
            "--risk-model",
            str(CANONICAL),
        ],
    )
    assert result.exit_code == 2


def test_wrong_type_scope_is_exit_2(tmp_path: Path) -> None:
    scope = tmp_path / "scope.json"
    scope.write_text(
        json.dumps({"project": "Maestro", "sha": "b" * 40, "scope": {"not": "a list"}}),
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["risk-classify", "--declared", str(scope), "--risk-model", str(CANONICAL)]
    )
    assert result.exit_code == 2


def test_wrong_type_sha_is_exit_2(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "risk-classify",
            "--no-fs",
            str(_facts(tmp_path, sha=12345)),
            "--risk-model",
            str(CANONICAL),
        ],
    )
    assert result.exit_code == 2


# --- waivers-check (TASK-607) ---


def _waiver_dir(tmp_path: Path, sha: str, tier: str = "medium") -> Path:
    d = tmp_path / "waivers"
    d.mkdir(exist_ok=True)
    (d / "steward.gate_check-abc.md").write_text(
        f"---\ngate_id: steward.gate_check\nsha: {sha}\ntier: {tier}\n"
        f"waived_by: someone\nreason: tracked flake\n---\n",
        encoding="utf-8",
    )
    return d


def test_waivers_check_clean_is_exit_0(tmp_path: Path) -> None:
    d = _waiver_dir(tmp_path, "e" * 40)
    result = runner.invoke(
        app, ["waivers-check", str(d), "--sha", "e" * 40, "--risk-model", str(CANONICAL)]
    )
    assert result.exit_code == 0, result.output


def test_waivers_check_stale_is_exit_1(tmp_path: Path) -> None:
    d = _waiver_dir(tmp_path, "e" * 40)
    result = runner.invoke(
        app, ["waivers-check", str(d), "--sha", "f" * 40, "--risk-model", str(CANONICAL)]
    )
    assert result.exit_code == 1
    assert "waiver-stale-sha" in result.output


def test_waivers_check_critical_is_exit_1(tmp_path: Path) -> None:
    d = _waiver_dir(tmp_path, "e" * 40, tier="critical")
    result = runner.invoke(
        app, ["waivers-check", str(d), "--sha", "e" * 40, "--risk-model", str(CANONICAL)]
    )
    assert result.exit_code == 1
    assert "waiver-forbidden-tier" in result.output


def test_waivers_check_missing_dir_is_exit_0(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "waivers-check",
            str(tmp_path / "none"),
            "--sha",
            "e" * 40,
            "--risk-model",
            str(CANONICAL),
        ],
    )
    assert result.exit_code == 0


def test_waivers_check_malformed_file_is_exit_1(tmp_path: Path) -> None:
    d = tmp_path / "waivers"
    d.mkdir()
    (d / "broken.md").write_text("---\ngate_id: x\n---\n", encoding="utf-8")
    result = runner.invoke(
        app, ["waivers-check", str(d), "--sha", "e" * 40, "--risk-model", str(CANONICAL)]
    )
    assert result.exit_code == 1
    assert "broken.md" in result.output


def test_waivers_check_bad_model_is_exit_2(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- nope\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["waivers-check", str(tmp_path), "--sha", "e" * 40, "--risk-model", str(bad)],
    )
    assert result.exit_code == 2


def test_waivers_check_short_sha_option_is_exit_2(tmp_path: Path) -> None:
    d = _waiver_dir(tmp_path, "e" * 40)
    result = runner.invoke(
        app, ["waivers-check", str(d), "--sha", "abc123", "--risk-model", str(CANONICAL)]
    )
    assert result.exit_code == 2
