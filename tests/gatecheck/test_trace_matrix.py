"""Tests for the derived trace matrix (ADR Phase 1 slice item 5, FL-09)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from steward.gatecheck.checks import Artifact
from steward.gatecheck.cli import app
from steward.gatecheck.trace_matrix import (
    build_trace_matrix,
    render_matrix_json,
    render_matrix_text,
)
from steward.graph import load_profile_data
from steward.meta import parse_artifact

runner = CliRunner()

_PROFILE = {
    "profile": "team-exp-test",
    "solo_auto_approve": True,
    "artifacts": [
        {"id": "requirements", "owner_role": "@product", "upstream": []},
        {
            "id": "behaviour-spec",
            "owner_role": "@product,@qa",
            "upstream": ["requirements"],
        },
    ],
}

_REQUIREMENTS = """---
spec_stage: requirements
---
#### FR-01: Panel shows bundle state
**Priority**: 🔴 Must

#### FR-02: Verdicts file is the only source
**Priority**: 🔴 Must

#### FR-03: Freshness fields visible
**Priority**: 🟠 Should

#### NFR-01: Read path works offline
**Priority**: 🔴 Must
"""

_BEHAVIOUR = """---
spec_stage: behaviour-spec
structural_coverage:
  - fr: FR-02
    constraint: ARCH-C1
    obligation:
      detector: import
      expected_verdict: conformant
      owner_role: "@architects"
      release_gate: block
coverage_waivers:
  - {fr: FR-03, reason: freshness deferred to WS-C}
---
#### BEH-01: Clean bundle renders as pass `traces: [FR-01]`
- **checked_by**: `status: planned` `kind: e2e` `owner: @qa` `target: tests/ui.py::t1`

#### BEH-02: Offline render `traces: [NFR-01, FR-01]`
- **checked_by**: `status: materialized` `kind: integration` `owner: @qa` `ref: tests/t.py::t2`
"""


def _artifacts() -> list[Artifact]:
    artifacts = []
    for node_id, path, text in (
        ("requirements", "10-requirements.md", _REQUIREMENTS),
        ("behaviour-spec", "15-behaviour.md", _BEHAVIOUR),
    ):
        meta = parse_artifact(text)
        assert meta is not None
        artifacts.append(Artifact(path=path, node_id=node_id, meta=meta, text=text))
    return artifacts


def _matrix() -> dict:
    matrix = build_trace_matrix(load_profile_data(_PROFILE), _artifacts())
    assert matrix is not None
    return matrix


def test_matrix_none_without_behaviour_node() -> None:
    data = {
        "profile": "lite-test",
        "artifacts": [{"id": "requirements", "owner_role": "@owner", "upstream": []}],
    }
    meta = parse_artifact(_REQUIREMENTS)
    assert meta is not None
    artifacts = [Artifact(path="r.md", node_id="requirements", meta=meta, text=_REQUIREMENTS)]
    assert build_trace_matrix(load_profile_data(data), artifacts) is None


def test_matrix_rows_are_id_sorted_and_complete() -> None:
    matrix = _matrix()
    ids = [row["id"] for row in matrix["requirements"]]
    assert ids == ["FR-01", "FR-02", "FR-03", "NFR-01"]  # FR block before NFR, numeric order


def test_matrix_derives_all_coverage_kinds() -> None:
    rows = {row["id"]: row for row in _matrix()["requirements"]}
    assert rows["FR-01"]["scenarios"] == ["BEH-01", "BEH-02"]
    assert rows["FR-02"]["structural"] == [{"constraint": "ARCH-C1", "detector": "import"}]
    assert rows["FR-03"]["waived"] == "freshness deferred to WS-C"
    assert rows["NFR-01"]["scenarios"] == ["BEH-02"]


def test_matrix_carries_check_bindings_verbatim() -> None:
    rows = {row["id"]: row for row in _matrix()["requirements"]}
    checks = {c["scenario"]: c for c in rows["FR-01"]["checks"]}
    assert checks["BEH-01"]["status"] == "planned"
    assert checks["BEH-01"]["target"] == "tests/ui.py::t1"
    assert checks["BEH-02"]["status"] == "materialized"
    assert checks["BEH-02"]["ref"] == "tests/t.py::t2"


def test_invalid_declarations_never_reach_the_matrix() -> None:
    behaviour = _BEHAVIOUR.replace("      release_gate: block\n", "")  # broken chain
    behaviour = behaviour.replace(", reason: freshness deferred to WS-C", "")  # broken waiver
    artifacts = _artifacts()
    meta = parse_artifact(behaviour)
    assert meta is not None
    artifacts[1] = Artifact(
        path="15-behaviour.md", node_id="behaviour-spec", meta=meta, text=behaviour
    )
    matrix = build_trace_matrix(load_profile_data(_PROFILE), artifacts)
    assert matrix is not None
    rows = {row["id"]: row for row in matrix["requirements"]}
    assert rows["FR-02"]["structural"] == []
    assert rows["FR-03"]["waived"] is None


def test_json_render_is_byte_stable() -> None:
    matrix = _matrix()
    assert render_matrix_json(matrix) == render_matrix_json(json.loads(render_matrix_json(matrix)))


def test_text_render_has_one_row_per_requirement() -> None:
    text = render_matrix_text(_matrix())
    assert text.count("\n| FR-") == 3 and text.count("\n| NFR-") == 1
    assert "ARCH-C1(import)" in text
    assert "BEH-02:materialized→tests/t.py::t2" in text


def _write_bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    profile = tmp_path / "team-exp-test.yaml"
    profile.write_text(
        "profile: team-exp-test\nsolo_auto_approve: true\nartifacts:\n"
        '  - {id: requirements, owner_role: "@product", upstream: []}\n'
        '  - {id: behaviour-spec, owner_role: "@qa", upstream: [requirements]}\n'
    )
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "10-requirements.md").write_text(_REQUIREMENTS)
    (spec / "15-behaviour.md").write_text(_BEHAVIOUR)
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"default_branch_files": [], "approvals": {}, "blob_hashes": {}}))
    return profile, spec, facts


def test_cli_trace_matrix_json(tmp_path: Path) -> None:
    profile, spec, facts = _write_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            str(spec),
            "--profile",
            str(profile),
            "--no-fs",
            str(facts),
            "--trace-matrix",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["profile"] == "team-exp-test"
    assert [row["id"] for row in payload["requirements"]] == ["FR-01", "FR-02", "FR-03", "NFR-01"]


def test_cli_trace_matrix_still_exits_1_on_findings(tmp_path: Path) -> None:
    profile, spec, facts = _write_bundle(tmp_path)
    # Break coverage: drop the FR-02 structural block entirely.
    behaviour = (spec / "15-behaviour.md").read_text()
    (spec / "15-behaviour.md").write_text(
        behaviour.replace(
            behaviour[behaviour.index("structural_coverage") : behaviour.index("coverage_waivers")],
            "",
        )
    )
    result = runner.invoke(
        app,
        [str(spec), "--profile", str(profile), "--no-fs", str(facts), "--trace-matrix"],
    )
    assert result.exit_code == 1  # matrix rendered, but the red bundle stays red
    assert "Trace matrix" in result.output


def test_cli_trace_matrix_without_behaviour_node_is_config_error(tmp_path: Path) -> None:
    profile, spec, facts = _write_bundle(tmp_path)
    profile.write_text(
        "profile: no-beh\nsolo_auto_approve: true\nartifacts:\n"
        '  - {id: requirements, owner_role: "@product", upstream: []}\n'
    )
    result = runner.invoke(
        app, [str(spec), "--profile", str(profile), "--no-fs", str(facts), "--trace-matrix"]
    )
    assert result.exit_code == 2
