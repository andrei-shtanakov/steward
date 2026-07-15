"""Contract/golden tests for compile-down (AC-006): emitters vs checked-in artifacts.

``project.yaml`` at the repo root is the hand-verified emitter contract
(``emitter-contract-check.md``: Maestro's loader + preflight accepted this exact
shape). The golden test regenerates it from the dogfood bundle and demands
byte equality — any drift in the emitter or in the decomposition block breaks
loudly here, not inside Maestro.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from steward.compile.cli import app
from steward.compile.decomposition import parse_decomposition
from steward.compile.delegation import emit_delegation
from steward.compile.project_yaml import emit_project_yaml

REPO = Path(__file__).resolve().parent.parent.parent
SPEC_DIR = REPO / "spec"
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

runner = CliRunner()


def _dogfood() -> tuple:
    text = (SPEC_DIR / "40-decomposition.md").read_text(encoding="utf-8")
    base = yaml.safe_load((SPEC_DIR / "maestro-base.yaml").read_text(encoding="utf-8"))
    return parse_decomposition(text), base


def test_project_yaml_golden_matches_contract_artifact() -> None:
    decomposition, base = _dogfood()
    assert emit_project_yaml(decomposition, base) == (REPO / "project.yaml").read_text(
        encoding="utf-8"
    )


def test_delegation_golden() -> None:
    decomposition, _ = _dogfood()
    assert emit_delegation(decomposition) == (GOLDEN_DIR / "delegation.yaml").read_text(
        encoding="utf-8"
    )


def test_emitted_project_yaml_shape_maestro_verified_invariants() -> None:
    # The invariants the live maestro run verified (emitter-contract-check.md):
    # 5 workstreams, every depends_on resolves, ids unique.
    decomposition, base = _dogfood()
    payload = yaml.safe_load(emit_project_yaml(decomposition, base))
    workstreams = payload["workstreams"]
    ids = [w["id"] for w in workstreams]
    assert len(workstreams) == 5
    assert len(set(ids)) == 5
    for w in workstreams:
        assert set(w["depends_on"]) <= set(ids)
        assert w["scope"], f"{w['id']} has empty scope"
    assert payload["project"] == "steward"
    assert payload["branch_prefix"] == "feature/steward-"


def test_cli_project_yaml_stdout_equals_contract_artifact() -> None:
    result = runner.invoke(
        app,
        ["project-yaml", str(SPEC_DIR), "--base", str(SPEC_DIR / "maestro-base.yaml")],
    )
    assert result.exit_code == 0, result.output
    assert result.output == (REPO / "project.yaml").read_text(encoding="utf-8")


def test_cli_config_errors_exit_two(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    no_decomposition = runner.invoke(app, ["project-yaml", str(empty)])
    assert no_decomposition.exit_code == 2

    missing_dir = runner.invoke(app, ["project-yaml", str(tmp_path / "absent")])
    assert missing_dir.exit_code == 2

    bad_base = runner.invoke(
        app, ["project-yaml", str(SPEC_DIR), "--base", str(tmp_path / "absent.yaml")]
    )
    assert bad_base.exit_code == 2

    two = tmp_path / "two"
    two.mkdir()
    for name in ("a.md", "b.md"):
        (two / name).write_text("---\nspec_stage: decomposition\n---\nbody\n")
    duplicated = runner.invoke(app, ["project-yaml", str(two)])
    assert duplicated.exit_code == 2

    no_block = tmp_path / "noblock"
    no_block.mkdir()
    (no_block / "d.md").write_text("---\nspec_stage: decomposition\n---\nprose only\n")
    blockless = runner.invoke(app, ["project-yaml", str(no_block)])
    assert blockless.exit_code == 2

    # A malformed frontmatter must surface itself, not a misleading
    # "no decomposition artifact" (Copilot review, PR #15).
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "d.md").write_text("---\nspec_stage: decomposition\n  bad: : :\n---\nbody\n")
    malformed = runner.invoke(app, ["project-yaml", str(broken)])
    assert malformed.exit_code == 2
    assert "malformed frontmatter" in malformed.output

    # An empty base config must fail fast, not silently drop the knobs.
    empty_base = tmp_path / "empty-base.yaml"
    empty_base.write_text("")
    dropped = runner.invoke(app, ["project-yaml", str(SPEC_DIR), "--base", str(empty_base)])
    assert dropped.exit_code == 2
    assert "non-empty YAML mapping" in dropped.output


def test_cli_delegation_and_out_file(tmp_path: Path) -> None:
    out = tmp_path / "delegation.yaml"
    result = runner.invoke(app, ["delegation", str(SPEC_DIR), "--out", str(out)])
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert [w["ws"] for w in payload["workstreams"]] == [
        "WS-001",
        "WS-002",
        "WS-003",
        "WS-004",
        "WS-005",
    ]
