"""CLI tests: exit codes and the deterministic --no-fs mode (REQ-207)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from steward.gatecheck.cli import app

runner = CliRunner()

_PROFILE = """\
profile: test
solo_auto_approve: false
artifacts:
  - {id: requirements, owner_role: "@product", upstream: []}
  - {id: design, owner_role: "@architects", upstream: [requirements]}
"""

_CYCLIC = """\
profile: broken
artifacts:
  - {id: a, owner_role: "@x", upstream: [b]}
  - {id: b, owner_role: "@x", upstream: [a]}
"""


def _bundle(tmp_path: Path, design_status: str = "draft") -> tuple[Path, Path]:
    profile = tmp_path / "test.yaml"
    profile.write_text(_PROFILE)
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "req.md").write_text(
        "---\nspec_stage: requirements\nstatus: draft\nversion: 1\n---\n## REQ-001\n"
    )
    (spec / "des.md").write_text(
        f"---\nspec_stage: design\nstatus: {design_status}\nversion: 1\ntraces_to: [REQ-001]\n---\n"
    )
    return profile, spec


def _facts(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(payload))
    return path


def test_clean_bundle_exit_zero(tmp_path: Path, write_roles: Path) -> None:
    profile, spec = _bundle(tmp_path)
    facts = _facts(tmp_path, {})
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--no-fs", str(facts)])
    assert result.exit_code == 0, result.output
    assert "0 error(s)" in result.output


def test_findings_exit_one(tmp_path: Path, write_roles: Path) -> None:
    # design approved while requirements is draft and git facts are empty
    profile, spec = _bundle(tmp_path, design_status="approved")
    facts = _facts(tmp_path, {})
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--no-fs", str(facts)])
    assert result.exit_code == 1
    assert "GC-UPSTREAM" in result.output
    assert "GC-GIT-BRANCH" in result.output


def test_config_errors_exit_two(tmp_path: Path, write_roles: Path) -> None:
    profile, spec = _bundle(tmp_path)
    missing_profile = runner.invoke(app, [str(spec), "--profile", "nope"])
    assert missing_profile.exit_code == 2

    cyclic = tmp_path / "cyclic.yaml"
    cyclic.write_text(_CYCLIC)
    cyclic_result = runner.invoke(app, [str(spec), "--profile", str(cyclic)])
    assert cyclic_result.exit_code == 2

    bad_facts = tmp_path / "bad.json"
    bad_facts.write_text("{not json")
    facts_result = runner.invoke(
        app, [str(spec), "--profile", str(profile), "--no-fs", str(bad_facts)]
    )
    assert facts_result.exit_code == 2

    missing_dir = runner.invoke(app, [str(tmp_path / "absent"), "--profile", str(profile)])
    assert missing_dir.exit_code == 2


def test_no_fs_is_deterministic(tmp_path: Path, write_roles: Path) -> None:
    profile, spec = _bundle(tmp_path, design_status="approved")
    facts = _facts(
        tmp_path,
        {
            "default_branch_files": ["des.md", "req.md"],
            "approvals": {"des.md": [{"handle": "@a", "role": "@architects"}]},
        },
    )
    args = [
        str(spec),
        "--profile",
        str(profile),
        "--no-fs",
        str(facts),
        "--format",
        "json",
    ]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)
    assert first.output == second.output
    payload = json.loads(first.output)
    # upstream gate still fires (req is draft); git checks are satisfied;
    # approved des.md carries no upstream pin, so stale-cascade warns (REQ-206)
    assert [f["rule_id"] for f in payload["findings"]] == ["GC-UPSTREAM", "GC-STALE-UNPINNED"]


def test_stale_pinned_hash_mismatch_exit_one(tmp_path: Path, write_roles: Path) -> None:
    # REQ-206 e2e: approved design pins the requirements blob; facts report a
    # different current blob -> GC-STALE error blocks the PR.
    profile, spec = _bundle(tmp_path)
    (spec / "req.md").write_text(
        "---\nspec_stage: requirements\nstatus: approved\nversion: 1\n---\n## REQ-001\n"
    )
    (spec / "des.md").write_text(
        "---\nspec_stage: design\nstatus: approved\nversion: 1\ntraces_to: [REQ-001]\n"
        "upstream_hashes: {requirements: old123}\n---\n"
    )
    facts = _facts(
        tmp_path,
        {
            "default_branch_files": ["des.md", "req.md"],
            "approvals": {
                "req.md": [{"handle": "@p", "role": "@product"}],
                "des.md": [{"handle": "@a", "role": "@architects"}],
            },
            "blob_hashes": {"req.md": "new456"},
        },
    )
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--no-fs", str(facts)])
    assert result.exit_code == 1
    assert "GC-STALE" in result.output


def test_json_format_shape(tmp_path: Path, write_roles: Path) -> None:
    profile, spec = _bundle(tmp_path)
    facts = _facts(tmp_path, {})
    result = runner.invoke(
        app,
        [str(spec), "--profile", str(profile), "--no-fs", str(facts), "--format", "json"],
    )
    payload = json.loads(result.output)
    assert payload["errors"] == 0
    assert set(payload) == {"findings", "errors", "warnings"}


# --- GC-ARCH-* wiring (Task 4) -----------------------------------------------
#
# profiles/arch-policy.yaml is loaded from a cwd-relative path by cli.py — these
# tests rely on pytest's rootdir being the repo root (per the project's own
# `uv run pytest` convention), same as the CLI's real invocation.

_ARCH_MANIFEST = """\
schema: intended-graph/v1
system: t-sys
components:
  - id: a.svc
    project: alpha
    kind: service
    owner: architects
    responsibility: "serves"
    evidence: [FR-01]
interfaces:
  - id: I-01
    producer: a.svc
    consumer: "file:beta/data.txt"
    detector: declared
    evidence: [BEH-01]
constraints:
  - id: C-01
    rule: "forbidden: alpha -> beta"
    detector: import
    evidence: [FR-02]
"""


def _arch_report(manifest_bytes: bytes) -> dict:
    return {
        "schema": "conformance-report/v1",
        "system": "t-sys",
        "generated_at": "2026-08-01T00:00:00Z",
        "manifest": {
            "project": "steward",
            "path": "spec/intended-graph.yaml",
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        },
        "snapshot": {
            "id": 1,
            "indexed_at": "2026-08-01T00:00:00Z",
            "content_hash": "prograph-snapshot/v1+sha256:" + "0" * 64,
            "complete": True,
        },
        "tool": {"name": "prograph", "version": "1.0.0", "schema": "intended-graph/v1"},
        "projects": {},
        "elements": [],
        "findings": [],
        "exceptions": [],
        "summary": {
            "verdicts": {"conformant": 0, "violation": 0, "unknown": 0},
            "findings": {
                "missing-required-edge": 0,
                "forbidden-edge": 0,
                "undeclared-edge": 0,
                "orphan-component": 0,
                "expired-waiver": 0,
                "manual-obligation": 0,
            },
        },
    }


def _arch_bundle(tmp_path: Path, *, with_report: bool = True) -> tuple[Path, Path]:
    """A gate-check-shaped bundle (no governance artifacts) plus an arch manifest."""
    profile, spec = _bundle(tmp_path)
    # arch-policy.yaml resolves relative to the profile actually used (CWD-independent),
    # so the tmp profile needs the real policy as its sibling.
    repo_policy = Path(__file__).resolve().parents[2] / "profiles" / "arch-policy.yaml"
    (profile.parent / "arch-policy.yaml").write_bytes(repo_policy.read_bytes())
    manifest_bytes = _ARCH_MANIFEST.encode("utf-8")
    (spec / "intended-graph.yaml").write_bytes(manifest_bytes)
    if with_report:
        (spec / "conformance-report.json").write_text(
            json.dumps(_arch_report(manifest_bytes)), encoding="utf-8"
        )
    return profile, spec


def test_arch_bundle_with_matching_report_exits_zero_at_authoring(
    tmp_path: Path, write_roles: Path
) -> None:
    profile, spec = _arch_bundle(tmp_path)
    facts = _facts(tmp_path, {})
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--no-fs", str(facts)])
    assert result.exit_code == 0, result.output
    assert "GC-ARCH" not in result.output


def test_arch_bundle_missing_report_exits_one_with_conformance_finding(
    tmp_path: Path, write_roles: Path
) -> None:
    profile, spec = _arch_bundle(tmp_path, with_report=False)
    facts = _facts(tmp_path, {})
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--no-fs", str(facts)])
    assert result.exit_code == 1, result.output
    assert "GC-ARCH-CONFORMANCE" in result.output


def test_arch_stage_nonsense_exits_two(tmp_path: Path, write_roles: Path) -> None:
    profile, spec = _arch_bundle(tmp_path)
    facts = _facts(tmp_path, {})
    result = runner.invoke(
        app,
        [str(spec), "--profile", str(profile), "--no-fs", str(facts), "--arch-stage", "nonsense"],
    )
    assert result.exit_code == 2, result.output


def test_bundle_without_manifest_has_no_arch_findings(tmp_path: Path, write_roles: Path) -> None:
    profile, spec = _bundle(tmp_path)
    facts = _facts(tmp_path, {})
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--no-fs", str(facts)])
    assert result.exit_code == 0, result.output
    assert "GC-ARCH" not in result.output


# --- roles catalog resolution (Task 4, DEC-007 D3) --------------------------


def test_unresolvable_frontmatter_role_is_config_error(tmp_path: Path, write_roles: Path) -> None:
    profile, spec = _bundle(tmp_path)
    (spec / "des.md").write_text(
        "---\nspec_stage: design\nstatus: draft\nversion: 1\n"
        "owner_role: ghost\ntraces_to: [REQ-001]\n---\n"
    )
    result = runner.invoke(
        app, [str(spec), "--profile", str(profile), "--no-fs", str(_facts(tmp_path, {}))]
    )
    assert result.exit_code == 2
    err = result.output
    assert "des.md" in err and "owner_role" in err and "ghost" in err
    # The message names the catalog actually consulted (a sibling of the
    # selected profile), not a hardcoded profiles/roles.yaml hint.
    assert str(write_roles) in err


def test_missing_sibling_roles_yaml_is_config_error(tmp_path: Path) -> None:
    # Deliberately does NOT request the write_roles fixture: this is the
    # negative case proving the sibling is mandatory, not a soft skip.
    profile, spec = _bundle(tmp_path)
    result = runner.invoke(
        app, [str(spec), "--profile", str(profile), "--no-fs", str(_facts(tmp_path, {}))]
    )
    assert result.exit_code == 2
    assert "roles" in result.output
