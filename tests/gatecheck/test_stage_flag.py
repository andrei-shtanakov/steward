"""CLI `--stage` generalization: `--arch-stage` becomes a deprecated alias (D4а).

Same fixture style as ``tests/gatecheck/test_cli.py``'s arch-bundle tests —
duplicated here rather than imported, matching this repo's no-cross-test-import
convention.
"""

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


def _bundle(tmp_path: Path, design_status: str = "draft") -> tuple[Path, Path]:
    profile = tmp_path / "test.yaml"
    profile.write_text(_PROFILE)
    # --stage release loads approval-policy.yaml as a sibling of the profile
    # actually used (AP-5, same anchoring as arch-policy.yaml below) — every
    # test in this module that reaches release stage needs the real policy
    # vendored in, even the ones with no approved artifacts.
    repo_approval_policy = Path(__file__).resolve().parents[2] / "profiles" / "approval-policy.yaml"
    (profile.parent / "approval-policy.yaml").write_bytes(repo_approval_policy.read_bytes())
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
    """A gate-check-shaped bundle (no governance artifacts) plus an arch manifest.

    With this report, ``authoring`` exits clean while ``release`` fails on
    self-freshness + stale snapshot age — a real, pre-existing behavior split
    between the two stages, used here to prove ``--stage`` picks the same
    policy branch ``--arch-stage`` used to.
    """
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


def test_stage_flag_selects_release(tmp_path: Path) -> None:
    # --stage release must pick the exact same policy branch --arch-stage
    # release used to (self-freshness + max-snapshot-age errors on this bundle).
    profile, spec = _arch_bundle(tmp_path)
    facts = _facts(tmp_path, {})
    base_args = [str(spec), "--profile", str(profile), "--no-fs", str(facts)]
    new_flag = runner.invoke(app, [*base_args, "--stage", "release"])
    old_flag = runner.invoke(app, [*base_args, "--arch-stage", "release"])
    assert new_flag.exit_code == old_flag.exit_code == 1, (new_flag.output, old_flag.output)
    assert new_flag.output == old_flag.output
    assert "GC-ARCH-CONFORMANCE" in new_flag.output


def test_arch_stage_alias_still_works(tmp_path: Path) -> None:
    profile, spec = _arch_bundle(tmp_path)
    facts = _facts(tmp_path, {})
    result = runner.invoke(
        app,
        [str(spec), "--profile", str(profile), "--no-fs", str(facts), "--arch-stage", "release"],
    )
    assert result.exit_code == 1, result.output
    assert "GC-ARCH-CONFORMANCE" in result.output

    help_result = runner.invoke(app, ["--help"])
    assert "[deprecated alias of --stage]" in help_result.output


def test_conflicting_stage_flags_exit_2(tmp_path: Path) -> None:
    profile, spec = _bundle(tmp_path)
    facts = _facts(tmp_path, {})
    result = runner.invoke(
        app,
        [
            str(spec),
            "--profile",
            str(profile),
            "--no-fs",
            str(facts),
            "--stage",
            "authoring",
            "--arch-stage",
            "release",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "conflict" in result.output


def test_equal_duplicate_flags_allowed(tmp_path: Path) -> None:
    profile, spec = _bundle(tmp_path)
    facts = _facts(tmp_path, {})
    result = runner.invoke(
        app,
        [
            str(spec),
            "--profile",
            str(profile),
            "--no-fs",
            str(facts),
            "--stage",
            "release",
            "--arch-stage",
            "release",
        ],
    )
    assert result.exit_code == 0, result.output


def test_invalid_stage_value_exit_2(tmp_path: Path) -> None:
    profile, spec = _bundle(tmp_path)
    facts = _facts(tmp_path, {})
    result = runner.invoke(
        app,
        [str(spec), "--profile", str(profile), "--no-fs", str(facts), "--stage", "shipping"],
    )
    assert result.exit_code == 2, result.output


def test_release_no_fs_missing_merge_provenance_section_exit_2(tmp_path: Path) -> None:
    # Pre-existing facts.json files (written before AP-3) never declare a
    # 'merge_provenance' section — the same "section absent" shape as
    # 'ancestors'/'changed_paths_since' (D9). InjectedGitFacts.merge_provenance
    # raises FactsError lazily, from inside GC-APPROVAL-MISSING's per-artifact
    # loop, not at facts-load time — so it must be caught where it's raised,
    # not just around InjectedGitFacts.from_file. An approved artifact on the
    # default branch is required to reach that call at all.
    profile, spec = _bundle(tmp_path, design_status="approved")
    facts = _facts(
        tmp_path,
        {
            "default_branch_files": ["des.md", "req.md"],
            "approvals": {"des.md": [{"handle": "@a", "role": "@architects"}]},
        },
    )
    result = runner.invoke(
        app,
        [str(spec), "--profile", str(profile), "--no-fs", str(facts), "--stage", "release"],
    )
    assert result.exit_code == 2, result.output
    assert "config error" in result.output
    assert "merge_provenance" in result.output
    assert "Traceback" not in result.output
