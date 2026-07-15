"""CLI tests: exit codes and the deterministic --no-fs mode (REQ-207)."""

from __future__ import annotations

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


def test_clean_bundle_exit_zero(tmp_path: Path) -> None:
    profile, spec = _bundle(tmp_path)
    facts = _facts(tmp_path, {})
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--no-fs", str(facts)])
    assert result.exit_code == 0, result.output
    assert "0 error(s)" in result.output


def test_findings_exit_one(tmp_path: Path) -> None:
    # design approved while requirements is draft and git facts are empty
    profile, spec = _bundle(tmp_path, design_status="approved")
    facts = _facts(tmp_path, {})
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--no-fs", str(facts)])
    assert result.exit_code == 1
    assert "GC-UPSTREAM" in result.output
    assert "GC-GIT-BRANCH" in result.output


def test_config_errors_exit_two(tmp_path: Path) -> None:
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


def test_no_fs_is_deterministic(tmp_path: Path) -> None:
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


def test_stale_pinned_hash_mismatch_exit_one(tmp_path: Path) -> None:
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


def test_json_format_shape(tmp_path: Path) -> None:
    profile, spec = _bundle(tmp_path)
    facts = _facts(tmp_path, {})
    result = runner.invoke(
        app,
        [str(spec), "--profile", str(profile), "--no-fs", str(facts), "--format", "json"],
    )
    payload = json.loads(result.output)
    assert payload["errors"] == 0
    assert set(payload) == {"findings", "errors", "warnings"}
