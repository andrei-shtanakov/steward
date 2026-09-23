"""gate-check --upto <node>: an incomplete bundle judged up to a DAG level (steward#187).

Three owner rulings (2026-09-23) carry the flag, each asserted against
behaviour:

1. the boundary is the node's LEVEL, not its upstream closure — a same-level
   sibling stays required;
2. the profile is not truncated — only completeness is relaxed above the
   boundary, and an artifact above it that is present is still fully checked;
3. the boundary is declared (JSON ``upto``, a stderr line), and --upto with
   --stage release / --emit-verdicts is a config error.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from steward.gatecheck.cli import app

runner = CliRunner()

# a → b → {c1, c2} → d: c1 and c2 share level 2, d is level 3.
_PROFILE = """\
profile: waves
solo_auto_approve: false
artifacts:
  - {id: a, owner_role: product, upstream: []}
  - {id: b, owner_role: product, upstream: [a]}
  - {id: c1, owner_role: architects, upstream: [b]}
  - {id: c2, owner_role: qa, upstream: [b]}
  - {id: d, owner_role: product, upstream: [c1, c2]}
"""

_UPSTREAM = {"a": [], "b": ["a"], "c1": ["b"], "c2": ["b"], "d": ["c1", "c2"]}


def _bundle(tmp_path: Path, *nodes: str, d_traces: bool = True) -> Path:
    (tmp_path / "waves.yaml").write_text(_PROFILE)
    spec = tmp_path / "spec"
    spec.mkdir()
    for node in nodes:
        traces = f"traces_to: {_UPSTREAM[node]}\n" if _UPSTREAM[node] else ""
        if node == "d" and not d_traces:
            traces = ""
        (spec / f"{node}.md").write_text(
            f"---\nspec_stage: {node}\nstatus: draft\nversion: 1\n{traces}---\n"
        )
    return spec


def _run(spec: Path, *extra: str) -> object:
    return runner.invoke(
        app,
        [str(spec), "--profile", str(spec.parent / "waves.yaml"), "--candidate", *extra],
    )


def _rules(result: object) -> list[tuple[str, str]]:
    payload = json.loads(result.stdout)  # type: ignore[attr-defined]
    return [(f["rule_id"], f["artifact"]) for f in payload["findings"]]


def test_without_upto_the_incomplete_bundle_is_red(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """Baseline: the full profile requires d — this is what --upto exists for."""
    spec = _bundle(tmp_path, "a", "b", "c1", "c2")
    result = _run(spec, "--format", "json")
    assert result.exit_code == 1
    assert ("GC-COMPLETENESS", "d") in _rules(result)


def test_upto_does_not_require_nodes_above_the_boundary(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = _bundle(tmp_path, "a", "b", "c1", "c2")
    result = _run(spec, "--upto", "c1", "--format", "json")
    assert result.exit_code == 0, result.stdout
    assert not [r for r in _rules(result) if r[0] == "GC-COMPLETENESS"]


def test_boundary_is_the_level_so_a_same_level_sibling_stays_required(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """Ruling 1: by upstream closure --upto c1 would drop c2; by level it must not."""
    spec = _bundle(tmp_path, "a", "b", "c1")
    result = _run(spec, "--upto", "c1", "--format", "json")
    assert result.exit_code == 1
    assert ("GC-COMPLETENESS", "c2") in _rules(result)
    assert ("GC-COMPLETENESS", "d") not in _rules(result)


def test_artifact_above_the_boundary_is_still_fully_checked(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """Ruling 2: no truncation — d is a node of the graph, not an unknown stage."""
    spec = _bundle(tmp_path, "a", "b", "c1", "c2", "d", d_traces=False)
    result = _run(spec, "--upto", "b", "--format", "json")
    rules = _rules(result)
    assert ("GC-TRACE-EMPTY", "d.md") in rules
    assert not [r for r in rules if r[0] == "GC-STAGE"]


def test_json_declares_the_boundary(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = _bundle(tmp_path, "a", "b", "c1", "c2")
    payload = json.loads(_run(spec, "--upto", "c2", "--format", "json").stdout)
    assert payload["upto"] == {"node": "c2", "level": 2, "not_required": ["d"]}


def test_json_has_no_upto_key_without_the_flag(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = _bundle(tmp_path, "a", "b", "c1", "c2", "d")
    payload = json.loads(_run(spec, "--format", "json").stdout)
    assert "upto" not in payload


def test_text_run_declares_the_boundary_on_stderr(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = _bundle(tmp_path, "a", "b", "c1", "c2")
    result = _run(spec, "--upto", "c1")
    assert result.exit_code == 0
    assert "--upto c1" in result.stderr
    assert "d" in result.stderr.split("--upto c1", 1)[1]
    assert "--upto" not in result.stdout


def test_upto_unknown_node_is_config_error(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = _bundle(tmp_path, "a")
    result = _run(spec, "--upto", "nope")
    assert result.exit_code == 2
    assert "nope" in result.stderr


def test_upto_with_stage_release_is_config_error(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """Ruling 3: an incomplete bundle cannot be released. No --candidate here —
    the conflict must hold in the live mode too, not only via the candidate check."""
    spec = _bundle(tmp_path, "a")
    result = runner.invoke(
        app,
        [
            str(spec),
            "--profile",
            str(tmp_path / "waves.yaml"),
            "--upto",
            "a",
            "--stage",
            "release",
        ],
    )
    assert result.exit_code == 2
    assert "--upto judges an incomplete bundle" in result.stderr


def test_upto_with_emit_verdicts_is_config_error(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = _bundle(tmp_path, "a")
    result = runner.invoke(
        app,
        [str(spec), "--profile", str(tmp_path / "waves.yaml"), "--upto", "a", "--emit-verdicts"],
    )
    assert result.exit_code == 2
    assert "--upto judges an incomplete bundle" in result.stderr


def test_declared_scope_lists_only_relaxed_nodes_not_delegates(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """A delegate is never required, so --upto did not relax anything for it."""
    (tmp_path / "waves.yaml").write_text(
        _PROFILE + "  - {id: t, owner_role: product, upstream: [d], delegate: spec-runner}\n"
    )
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "a.md").write_text("---\nspec_stage: a\nstatus: draft\nversion: 1\n---\n")
    payload = json.loads(_run(spec, "--upto", "a", "--format", "json").stdout)
    assert payload["upto"]["not_required"] == ["b", "c1", "c2", "d"]


def test_upto_with_trace_matrix_is_config_error(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """The matrix payload cannot carry the boundary, and below the behaviour-spec
    level a missing matrix would read as a broken bundle — so the pair is refused."""
    spec = _bundle(tmp_path, "a")
    result = _run(spec, "--upto", "a", "--trace-matrix")
    assert result.exit_code == 2
    assert "--upto judges an incomplete bundle" in result.stderr


def test_upto_with_approval_facts_is_config_error(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """--upto forbids release, the only stage that reads --approval-facts: the
    override would be guaranteed inert, so it is refused rather than ignored."""
    spec = _bundle(tmp_path, "a")
    facts = tmp_path / "facts.jsonl"
    facts.write_text("")
    result = runner.invoke(
        app,
        [
            str(spec),
            "--profile",
            str(tmp_path / "waves.yaml"),
            "--upto",
            "a",
            "--approval-facts",
            str(facts),
        ],
    )
    assert result.exit_code == 2
    assert "--upto judges an incomplete bundle" in result.stderr
