"""Emitter tests (WS-A): real git provenance, whole-file rewrite, fact-only records."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from steward.gatecatalog import load_catalog_files
from steward.gatecheck.checks import Finding, collect_bundle, run_checks
from steward.gatecheck.cli import app
from steward.gatecheck.git_facts import LiveGitFacts
from steward.graph import load_profile_data
from steward.roles import load_roles_catalog
from steward.verdicts import EmitError, ProvenanceError, emit_verdicts

runner = CliRunner()

_REPO_ROOT = Path(__file__).resolve().parents[2]

SCHEMA = json.loads((_REPO_ROOT / "contracts/gate-verdicts/v1/SCHEMA.json").read_text())

ROLES = load_roles_catalog(_REPO_ROOT / "profiles/roles.yaml")
CATALOG = load_catalog_files(
    _REPO_ROOT / "profiles/gate-catalog.yaml", _REPO_ROOT / "profiles/roles.yaml"
)

_PROFILE = {
    "profile": "team-exp-emit",
    "solo_auto_approve": True,
    "artifacts": [
        {
            "id": "requirements",
            "owner_role": "product",
            "reviewer_roles": ["architects"],
            "upstream": [],
        },
        {"id": "behaviour-spec", "owner_role": "qa", "upstream": ["requirements"]},
    ],
}

_REQUIREMENTS = """---
spec_stage: requirements
status: approved
---
#### FR-01: Something observable
**Priority**: 🔴 Must
"""

_BEHAVIOUR = """---
spec_stage: behaviour-spec
status: draft
---
#### BEH-01: Scenario `traces: [FR-01]`
- **checked_by**: `status: planned` `kind: e2e` `owner: @qa` `target: t.py::x`
"""


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    spec = repo / "spec"
    spec.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (spec / "10-requirements.md").write_text(_REQUIREMENTS)
    (spec / "15-behaviour.md").write_text(_BEHAVIOUR)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "bundle")
    return repo, spec


def _emit(repo: Path, spec: Path) -> list[dict]:
    graph = load_profile_data(_PROFILE, ROLES)
    artifacts, findings = collect_bundle(graph, spec)
    findings.extend(run_checks(graph, artifacts, LiveGitFacts(repo, spec)))
    out = emit_verdicts(graph, artifacts, findings, spec, CATALOG)
    assert out == repo / ".steward" / "gate_verdicts.jsonl"
    return [json.loads(line) for line in out.read_text().splitlines()]


def test_header_carries_real_provenance(tmp_path: Path) -> None:
    repo, spec = _repo(tmp_path)
    records = _emit(repo, spec)
    header = records[0]
    assert header["kind"] == "header"
    assert header["schema_version"] == "1"
    assert header["source_commit"] == _git(repo, "rev-parse", "HEAD")
    assert header["dirty"] is False
    assert header["profile"] == "team-exp-emit"
    assert header["bundle"] == "spec"


def test_dirty_tree_is_stamped(tmp_path: Path) -> None:
    repo, spec = _repo(tmp_path)
    (spec / "10-requirements.md").write_text(_REQUIREMENTS + "\nedited\n")
    records = _emit(repo, spec)
    assert records[0]["dirty"] is True  # the instrument's own provenance, RK-02


def test_records_validate_against_the_canon_schema(tmp_path: Path) -> None:
    repo, spec = _repo(tmp_path)
    for record in _emit(repo, spec):
        jsonschema.validate(record, SCHEMA)


def test_artifact_inventory_and_role_slugs(tmp_path: Path) -> None:
    repo, spec = _repo(tmp_path)
    artifacts = {r["path"]: r for r in _emit(repo, spec) if r["kind"] == "artifact"}
    req = artifacts["10-requirements.md"]
    assert req["node_id"] == "requirements"
    assert req["status"] == "approved"
    # DEC-007 canonical: exactly one accountable owner slug, no '@'; the former
    # architects co-owner now lives in reviewer_roles (not surfaced here).
    assert req["owner_roles"] == ["product"]
    assert artifacts["15-behaviour.md"]["owner_roles"] == ["qa"]


def test_findings_map_severity_to_verdict(tmp_path: Path) -> None:
    repo, spec = _repo(tmp_path)
    # behaviour-spec draft has no binding problem, but approved-upstream is fine;
    # force a finding: approve downstream while upstream loses approval.
    (spec / "10-requirements.md").write_text(_REQUIREMENTS.replace("approved", "draft"))
    (spec / "15-behaviour.md").write_text(_BEHAVIOUR.replace("status: draft", "status: approved"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "invert statuses")
    findings = [r for r in _emit(repo, spec) if r["kind"] == "finding"]
    assert any(f["gate_id"] == "GC-UPSTREAM" and f["verdict"] == "fail" for f in findings)
    for f in findings:
        jsonschema.validate(f, SCHEMA)


def test_active_finding_record_carries_obligation_from_catalog(tmp_path: Path) -> None:
    repo, spec = _repo(tmp_path)
    # force a finding the same way test_findings_map_severity_to_verdict does
    (spec / "10-requirements.md").write_text(_REQUIREMENTS.replace("approved", "draft"))
    (spec / "15-behaviour.md").write_text(_BEHAVIOUR.replace("status: draft", "status: approved"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "invert statuses")
    findings = [r for r in _emit(repo, spec) if r["kind"] == "finding"]
    assert findings
    for f in findings:
        entry = CATALOG.entry(f["gate_id"])
        assert entry is not None
        assert f["obligation"] == entry.obligation  # from the catalog, not hardcoded


def test_unknown_rule_id_raises_emit_error_and_writes_nothing(tmp_path: Path) -> None:
    repo, spec = _repo(tmp_path)
    graph = load_profile_data(_PROFILE, ROLES)
    artifacts, findings = collect_bundle(graph, spec)
    findings.append(Finding(severity="error", rule_id="GC-GHOST", artifact="x", message="m"))
    with pytest.raises(EmitError, match="GC-GHOST"):
        emit_verdicts(graph, artifacts, findings, spec, CATALOG)
    assert not (repo / ".steward" / "gate_verdicts.jsonl").exists()


def test_declared_rule_id_is_refused_like_unknown(tmp_path: Path) -> None:
    # AP-5 (2026-08-08) activated GC-APPROVAL-MISSING, so the real catalog no
    # longer has a live "declared" example — build a synthetic one-off
    # catalog with a declared gate (same tmp-YAML pattern as
    # tests/gatecatalog/test_loader.py) to keep exercising this refusal path.
    repo, spec = _repo(tmp_path)
    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()
    (catalog_dir / "roles.yaml").write_text(
        'version: 1\nslug_pattern: "^[a-z][a-z0-9-]{1,31}$"\nroles:\n  - {slug: qa, display: QA}\n'
    )
    (catalog_dir / "gate-catalog.yaml").write_text(
        "version: 1\n"
        "obligation_vocabulary: [quality, approval]\n"
        "stage_vocabulary: [authoring, release]\n"
        "gates:\n"
        "  GC-FUTURE:\n"
        "    obligation: approval\n"
        "    status: declared\n"
    )
    declared_catalog = load_catalog_files(
        catalog_dir / "gate-catalog.yaml", catalog_dir / "roles.yaml"
    )

    graph = load_profile_data(_PROFILE, ROLES)
    artifacts, findings = collect_bundle(graph, spec)
    findings.append(Finding(severity="error", rule_id="GC-FUTURE", artifact="x", message="m"))
    with pytest.raises(EmitError, match="declared") as exc_info:
        emit_verdicts(graph, artifacts, findings, spec, declared_catalog)
    assert "GC-FUTURE" in str(exc_info.value)
    assert not (repo / ".steward" / "gate_verdicts.jsonl").exists()


def test_file_is_rewritten_whole_each_run(tmp_path: Path) -> None:
    repo, spec = _repo(tmp_path)
    first = _emit(repo, spec)
    second = _emit(repo, spec)
    out = repo / ".steward" / "gate_verdicts.jsonl"
    assert len(out.read_text().splitlines()) == len(second)  # no append across runs
    assert len(first) == len(second)


def test_no_git_means_no_file(tmp_path: Path) -> None:
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "10-requirements.md").write_text(_REQUIREMENTS)
    graph = load_profile_data(_PROFILE, ROLES)
    artifacts, findings = collect_bundle(graph, spec)
    with pytest.raises(ProvenanceError):
        emit_verdicts(graph, artifacts, findings, spec, CATALOG)
    assert not (tmp_path / ".steward").exists()  # fail-closed: no provenance, no file


def test_unwritable_target_is_a_config_error_not_a_crash(tmp_path: Path) -> None:
    # Copilot review, PR #33: an OSError on write must surface as EmitError
    # (CLI exit 2), never as an uncaught crash.
    repo, spec = _repo(tmp_path)
    (repo / ".steward").write_text("a file where the directory must go")
    graph = load_profile_data(_PROFILE, ROLES)
    artifacts, findings = collect_bundle(graph, spec)
    with pytest.raises(EmitError, match="cannot write verdicts file"):
        emit_verdicts(graph, artifacts, findings, spec, CATALOG)


def test_cli_emit_writes_file_and_keeps_exit_semantics(tmp_path: Path) -> None:
    repo, spec = _repo(tmp_path)
    profile = repo / "p.yaml"
    profile.write_text(
        "profile: team-exp-emit\nsolo_auto_approve: true\nartifacts:\n"
        "  - {id: requirements, owner_role: product, upstream: []}\n"
        "  - {id: behaviour-spec, owner_role: qa, upstream: [requirements]}\n"
    )
    # gate-catalog.yaml/roles.yaml resolve relative to the profile actually
    # used (CWD-independent), so the tmp profile needs the real catalog as
    # its sibling — same pattern test_cli.py uses for arch-policy.yaml.
    (repo / "gate-catalog.yaml").write_bytes(
        (_REPO_ROOT / "profiles" / "gate-catalog.yaml").read_bytes()
    )
    (repo / "roles.yaml").write_bytes((_REPO_ROOT / "profiles" / "roles.yaml").read_bytes())
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--emit-verdicts"])
    assert result.exit_code == 0, result.output
    assert (repo / ".steward" / "gate_verdicts.jsonl").exists()


def test_cli_emit_with_missing_catalog_exits_two_not_traceback(tmp_path: Path) -> None:
    # No gate-catalog.yaml/roles.yaml sibling to the profile: _load_catalog
    # must fail closed (exit 2) instead of leaking a bare FileNotFoundError.
    repo, spec = _repo(tmp_path)
    profile = repo / "p.yaml"
    profile.write_text(
        "profile: team-exp-emit\nsolo_auto_approve: true\nartifacts:\n"
        "  - {id: requirements, owner_role: product, upstream: []}\n"
        "  - {id: behaviour-spec, owner_role: qa, upstream: [requirements]}\n"
    )
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--emit-verdicts"])
    assert result.exit_code == 2, result.output
    assert "config error" in result.output


def test_cli_emit_with_malformed_catalog_exits_two_not_traceback(tmp_path: Path) -> None:
    # A gate-catalog.yaml that fails to parse must also fail closed, not crash.
    repo, spec = _repo(tmp_path)
    profile = repo / "p.yaml"
    profile.write_text(
        "profile: team-exp-emit\nsolo_auto_approve: true\nartifacts:\n"
        "  - {id: requirements, owner_role: product, upstream: []}\n"
        "  - {id: behaviour-spec, owner_role: qa, upstream: [requirements]}\n"
    )
    (repo / "gate-catalog.yaml").write_text(":\n  not: [valid, yaml")
    (repo / "roles.yaml").write_bytes((_REPO_ROOT / "profiles" / "roles.yaml").read_bytes())
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--emit-verdicts"])
    assert result.exit_code == 2, result.output
    assert "config error" in result.output


def test_cli_emit_refuses_no_fs(tmp_path: Path) -> None:
    repo, spec = _repo(tmp_path)
    facts = tmp_path / "facts.json"
    facts.write_text('{"default_branch_files": [], "approvals": {}, "blob_hashes": {}}')
    profile = repo / "p.yaml"
    profile.write_text(
        "profile: t\nartifacts:\n  - {id: requirements, owner_role: product, upstream: []}\n"
    )
    result = runner.invoke(
        app,
        [str(spec), "--profile", str(profile), "--no-fs", str(facts), "--emit-verdicts"],
    )
    assert result.exit_code == 2  # provenance-free verdicts are forbidden, config error


def test_emitted_file_carries_an_intact_hash_chain(tmp_path: Path) -> None:
    # steward#105: every record after line 1 links to its predecessor, and a
    # mid-ledger edit is exposed by the successor's prev_hash.
    from steward.verdicts.chain import line_hash, verify_chain

    repo, spec = _repo(tmp_path)
    _emit(repo, spec)
    out = repo / ".steward" / "gate_verdicts.jsonl"
    lines = out.read_text().splitlines()
    assert "prev_hash" not in json.loads(lines[0])  # the header anchors the chain
    for i in range(1, len(lines)):
        assert json.loads(lines[i])["prev_hash"] == line_hash(lines[i - 1])
    assert verify_chain(out.read_text()).status == "chained"

    tampered = json.loads(lines[1])
    tampered["path"] = tampered["path"] + "-tampered"
    lines[1] = json.dumps(tampered, ensure_ascii=False)
    report = verify_chain("".join(line + "\n" for line in lines))
    assert report.status == "broken"
    assert report.broken_line == 3


def test_emitted_bytes_are_lf_only_and_verify_from_disk(tmp_path: Path) -> None:
    # Codex gate on PR #109, round 3: the producer must write exactly the
    # bytes it hashed — text-mode newline translation would break every
    # freshly emitted chain on Windows. Assert at the byte level; read_text()
    # would normalize the very defect this test exists to catch.
    from steward.verdicts.chain import verify_chain

    repo, spec = _repo(tmp_path)
    _emit(repo, spec)
    raw = (repo / ".steward" / "gate_verdicts.jsonl").read_bytes()
    assert b"\r" not in raw
    assert verify_chain(raw.decode("utf-8")).status == "chained"
