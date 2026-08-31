"""Prospective gate-check over a candidate revision (steward#140).

Two properties carry the mode, and both are asserted against behaviour rather
than against the flag's existence:

1. it runs on content that no ref points at — including a directory outside
   any checkout — and the stale cascade there reads the *files*, so a pin
   break is caught before the commit exists (a live run reads ``HEAD:<path>``
   and cannot see it);
2. the ref-bound gates are absent from the output and *declared* absent —
   never silently green.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from steward.gatecheck.candidate import NOT_EVALUATED, CandidateGitFacts, blob_hash_of
from steward.gatecheck.cli import app
from steward.gatecheck.git_facts import FactsError

runner = CliRunner()

_PROFILE = """\
profile: test
solo_auto_approve: false
artifacts:
  - {id: requirements, owner_role: product, upstream: []}
  - {id: design, owner_role: architects, upstream: [requirements]}
"""

_REQ_BODY = "---\nspec_stage: requirements\nstatus: draft\nversion: 1\n---\n## REQ-001\n"


def _bundle(tmp_path: Path, design_status: str = "draft", pin: str | None = None) -> Path:
    """Profile + a two-artifact bundle in ``tmp_path`` (no git anywhere)."""
    (tmp_path / "test.yaml").write_text(_PROFILE)
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "req.md").write_text(_REQ_BODY)
    pin_line = f"upstream_hashes: {{requirements: {pin}}}\n" if pin else ""
    (spec / "des.md").write_text(
        f"---\nspec_stage: design\nstatus: {design_status}\nversion: 1\n"
        f"traces_to: [REQ-001]\n{pin_line}---\n"
    )
    return spec


def _behaviour_bundle(tmp_path: Path) -> Path:
    """Profile + bundle whose profile HAS a behaviour-spec node, so the derived
    trace matrix renders instead of failing as a config error."""
    (tmp_path / "test.yaml").write_text(
        "profile: beh\n"
        "solo_auto_approve: false\n"
        "artifacts:\n"
        "  - {id: requirements, owner_role: product, upstream: []}\n"
        "  - {id: behaviour-spec, owner_role: qa, upstream: [requirements]}\n"
    )
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "req.md").write_text(
        "---\nspec_stage: requirements\nstatus: draft\nversion: 1\n---\n"
        "#### FR-01\n\n**Priority**: Must\n\nтребование\n"
    )
    (spec / "beh.md").write_text(
        "---\nspec_stage: behaviour-spec\nstatus: draft\nversion: 1\n"
        "traces_to: [FR-01]\n---\n"
        "#### BEH-01\n\n`traces: [FR-01]`\n\n- **checked_by**: planned\n"
    )
    return spec


def _candidate(spec: Path, *extra: str) -> object:
    return runner.invoke(
        app, [str(spec), "--profile", str(spec.parent / "test.yaml"), "--candidate", *extra]
    )


# --- blob_hash_of: the content address, not an approximation of one ---


def test_blob_hash_matches_git_hash_object(tmp_path: Path) -> None:
    """The in-process hash must equal git's, or the pins it compares against
    (written by git-aware tooling) would never match anything."""
    payload = b"spec content\n\xd0\xbf\xd1\x80\xd0\xbe\xd0\xb1\xd0\xb0\n"
    target = tmp_path / "f.md"
    target.write_bytes(payload)
    expected = subprocess.run(
        ["git", "hash-object", str(target)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert blob_hash_of(payload) == expected


def test_blob_hash_of_empty_file_matches_git(tmp_path: Path) -> None:
    target = tmp_path / "empty.md"
    target.write_bytes(b"")
    expected = subprocess.run(
        ["git", "hash-object", str(target)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert blob_hash_of(b"") == expected


# --- CandidateGitFacts: content answered, history refused ---


def test_facts_hash_reads_the_file_on_disk(tmp_path: Path) -> None:
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "a.md").write_bytes(b"one\n")
    facts = CandidateGitFacts(spec)
    assert facts.blob_hash("a.md") == blob_hash_of(b"one\n")

    (spec / "a.md").write_bytes(b"two\n")
    assert facts.blob_hash("a.md") == blob_hash_of(b"two\n")


def test_facts_hash_of_absent_file_is_none(tmp_path: Path) -> None:
    """`None` keeps the protocol's "could not resolve" meaning, which the
    stale cascade renders as a warning — not as a proven mismatch."""
    assert CandidateGitFacts(tmp_path).blob_hash("nope.md") is None


def test_facts_approvals_are_unavailable_not_empty(tmp_path: Path) -> None:
    """`None`, never `()`: an empty tuple would claim an authoritative source
    confirmed there are no approvals, and that would make absence of a role
    mapping look like a proven violation."""
    assert CandidateGitFacts(tmp_path).approvals("a.md") is None


@pytest.mark.parametrize(
    ("method", "arg"),
    [
        ("on_default_branch", "a.md"),
        ("is_ancestor", "0" * 40),
        ("changed_paths_since", "0" * 40),
        ("merge_provenance", "a.md"),
    ],
)
def test_history_questions_raise_rather_than_fabricate(
    method: str, arg: str, tmp_path: Path
) -> None:
    """A candidate has no history, and "no" is not the answer — "not askable"
    is. Returning False from `on_default_branch` would be the expensive
    fabrication: every approved artifact would collect a GC-GIT-BRANCH finding
    about a question the run never asked."""
    with pytest.raises(FactsError) as err:
        getattr(CandidateGitFacts(tmp_path), method)(arg)
    assert "NOT_EVALUATED" in str(err.value)


# --- the mode: runs without a checkout, declares what it skipped ---


def test_candidate_runs_outside_any_git_repository(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """The whole point: content before the commit, in a directory that is not
    a checkout at all. The live path fails here with "not inside a git
    repository"."""
    spec = _bundle(tmp_path)
    assert not (tmp_path / ".git").exists()

    result = _candidate(spec)
    assert result.exit_code == 0, result.output
    assert "gate-check[candidate]" in result.output

    live = runner.invoke(app, [str(spec), "--profile", str(tmp_path / "test.yaml")])
    assert live.exit_code == 2
    assert "not inside a git repository" in live.output


def test_declaration_is_on_stderr_so_stdout_stays_one_payload(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """stdout carries exactly one parseable payload in every output branch —
    findings JSON here, the matrix under --trace-matrix. The declaration would
    corrupt both if it shared the channel, and dropping it to keep stdout clean
    would be the fail-open the mode exists to avoid.
    """
    spec = _bundle(tmp_path)
    result = _candidate(spec, "--format", "json")
    json.loads(result.stdout)  # raises if the declaration leaked into stdout
    assert "не проверено" not in result.stdout
    assert "не проверено в prospective-режиме" in result.stderr


def test_declaration_survives_the_trace_matrix_branch(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """--trace-matrix replaces the findings render, so a declaration tied to
    that render would vanish precisely where the exit code still reports
    gates."""
    spec = _behaviour_bundle(tmp_path)
    result = _candidate(spec, "--trace-matrix")
    assert "| FR-01 |" in result.stdout  # stdout is the matrix, undisturbed
    assert "не проверено в prospective-режиме" in result.stderr


_VALID_MANIFEST = """\
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


_ARCH_POLICY = """\
self_project: alpha
stages:
  authoring:
    fail_on_findings: []
    fail_on_verdicts: [violation]
    unknown_policy: {allowed_reasons: [manual-evidence], allowed_elements: []}
    require_self_fresh: false
    max_snapshot_age_hours: null
  release:
    fail_on_findings: [missing-required-edge]
    fail_on_verdicts: [violation]
    unknown_policy: {allowed_reasons: [manual-evidence], allowed_elements: []}
    require_self_fresh: true
    max_snapshot_age_hours: 24
"""


def test_arch_conformance_still_fires_on_its_content_clauses(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """Only D9 self-freshness reads history inside GC-ARCH-CONFORMANCE; the
    rest of the gate is derived from bytes and must keep firing prospectively.

    A schema-valid manifest with no co-located conformance-report.json is the
    cheapest of those content clauses. Skipping the gate wholesale would hide
    this — and every sibling clause (unparseable JSON, schema violation, stale
    manifest hash, incomplete snapshot, blocking verdict) — behind a line that
    says "not evaluated", which is the fail-open this mode exists to avoid.
    """
    spec = _bundle(tmp_path)
    (spec / "intended-graph.yaml").write_text(_VALID_MANIFEST)
    (tmp_path / "arch-policy.yaml").write_text(_ARCH_POLICY)

    result = _candidate(spec, "--format", "json")
    assert result.exit_code == 1, result.stderr
    findings = json.loads(result.stdout)["findings"]
    conformance = [f for f in findings if f["rule_id"] == "GC-ARCH-CONFORMANCE"]
    assert conformance, findings
    assert "conformance-report.json" in conformance[0]["message"]


def test_conformance_is_declared_only_for_its_history_clause() -> None:
    """The declaration must name the *clause*, not the gate: a bare
    GC-ARCH-CONFORMANCE line would tell a reader the whole gate was skipped,
    which is exactly the claim the fix above disproves."""
    (entry,) = [e for e in NOT_EVALUATED if e.gate_id == "GC-ARCH-CONFORMANCE"]
    assert entry.scope == "D9 self-freshness"
    assert entry.label == "GC-ARCH-CONFORMANCE (D9 self-freshness)"


def test_ref_bound_gates_are_declared_not_dropped(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = _bundle(tmp_path)
    result = _candidate(spec)
    assert "не проверено в prospective-режиме" in result.output
    for entry in NOT_EVALUATED:
        assert entry.label in result.stderr


def test_no_ref_bound_finding_is_emitted(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """The same bundle that makes the ref-bound path emit GC-GIT-BRANCH (an
    approved artifact absent from the default branch) must produce none of the
    declared gates here — asserted against a live counter-run, so the fixture
    is proven capable of producing them rather than merely assumed to be."""
    spec = _bundle(tmp_path, design_status="approved")
    facts = tmp_path / "facts.json"
    facts.write_text("{}")

    injected = runner.invoke(
        app,
        [
            str(spec),
            "--profile",
            str(tmp_path / "test.yaml"),
            "--no-fs",
            str(facts),
            "--format",
            "json",
        ],
    )
    assert injected.exit_code == 1
    injected_gates = {f["rule_id"] for f in json.loads(injected.stdout)["findings"]}
    assert "GC-GIT-BRANCH" in injected_gates

    result = _candidate(spec, "--format", "json")
    payload = json.loads(result.stdout)
    emitted = {f["rule_id"] for f in payload["findings"]}
    # Only the WHOLLY skipped gates: GC-ARCH-CONFORMANCE is declared for one
    # clause (D9) and must still be able to fire on its content clauses —
    # see test_arch_conformance_still_fires_on_its_content_clauses.
    wholly_skipped = {e.gate_id for e in NOT_EVALUATED if not e.scope}
    assert emitted & wholly_skipped == set()


def test_stale_cascade_sees_the_candidate_content(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """The gate the mode exists for: a pin broken by an *uncommitted* edit.

    The pin is written against the file's current bytes, so a clean run is
    green; changing the file breaks it. A ref-bound run would compare against
    `HEAD:<path>` and stay green through the same edit — which is the whole
    reason a prospective mode is worth having.
    """
    spec = _bundle(tmp_path, design_status="approved", pin=blob_hash_of(_REQ_BODY.encode()))
    (spec / "req.md").write_text(_REQ_BODY)  # unchanged: pin holds

    clean = _candidate(spec, "--format", "json")
    stale = [f for f in json.loads(clean.stdout)["findings"] if f["rule_id"] == "GC-STALE"]
    assert stale == [], clean.output

    (spec / "req.md").write_text(_REQ_BODY + "## REQ-002\n")
    broken = _candidate(spec, "--format", "json")
    assert broken.exit_code == 1
    codes = [f["rule_id"] for f in json.loads(broken.stdout)["findings"]]
    assert "GC-STALE" in codes


# --- JSON shape ---


def test_json_declares_mode_and_not_evaluated(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = _bundle(tmp_path)
    payload = json.loads(_candidate(spec, "--format", "json").stdout)
    assert payload["mode"] == "candidate"
    assert payload["not_evaluated"] == [
        {"gate": e.gate_id, "scope": e.scope, "reason": e.reason} for e in NOT_EVALUATED
    ]


def test_ref_bound_runs_name_their_mode_without_claiming_completeness(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """`mode` is always present and always true; `not_evaluated` appears only
    where it describes something — an empty list on a ref-bound run would read
    as "everything else ran", which gate-check does not claim."""
    spec = _bundle(tmp_path)
    facts = tmp_path / "facts.json"
    facts.write_text("{}")
    payload = json.loads(
        runner.invoke(
            app,
            [
                str(spec),
                "--profile",
                str(tmp_path / "test.yaml"),
                "--no-fs",
                str(facts),
                "--format",
                "json",
            ],
        ).stdout
    )
    assert payload["mode"] == "injected"
    assert "not_evaluated" not in payload


# --- conflicting flags are config errors, not silent no-ops ---


@pytest.mark.parametrize(
    "extra",
    [
        ["--no-fs", "facts.json"],
        ["--emit-verdicts"],
        ["--approval-facts", "facts.jsonl"],
        ["--stage", "release"],
    ],
    ids=["no-fs", "emit-verdicts", "approval-facts", "stage-release"],
)
def test_ref_bound_flags_conflict_with_candidate(
    extra: list[str], tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """Each of these only means something about a committed revision. Ignoring
    one quietly would be worse than refusing: an ignored --approval-facts
    reads, from the outside, as evidence that was consulted and found fine."""
    spec = _bundle(tmp_path)
    (tmp_path / "facts.json").write_text("{}")
    (tmp_path / "facts.jsonl").write_text("")
    result = _candidate(spec, *extra)
    assert result.exit_code == 2, result.output
    assert "config error" in result.output


# --- the declaration cannot rot into dead strings ---


def test_declared_gate_ids_exist_in_the_catalog() -> None:
    """A renamed or retired gate must break this list loudly. Without the
    check, NOT_EVALUATED would keep printing an id nothing can emit — the
    "dead string that looks correct" failure this repo has already paid for
    once (`merge-broker[bot]` in approval-policy.yaml)."""
    catalog = yaml.safe_load(
        (Path(__file__).parents[2] / "profiles" / "gate-catalog.yaml").read_text(encoding="utf-8")
    )
    known = set(catalog["gates"])
    declared = {e.gate_id for e in NOT_EVALUATED}
    assert declared <= known, declared - known


def test_every_declared_gate_carries_a_reason() -> None:
    assert all(e.reason.strip() for e in NOT_EVALUATED)


def test_trace_matrix_works_prospectively(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """The third internal symbol devtools reaches for today (`build_trace_matrix`)
    is content-derived, so it needs no ref — and must be reachable through the
    CLI in this mode, or the migration off the internal API stays partial."""
    spec = _behaviour_bundle(tmp_path)
    profile = tmp_path / "test.yaml"

    result = runner.invoke(
        app, [str(spec), "--profile", str(profile), "--candidate", "--trace-matrix"]
    )
    assert "| FR-01 | Must | BEH-01 |" in result.stdout

    # The matrix replaces the findings *render*, never the exit code — that
    # contract is the same in every mode, so a prospective run still reports
    # what it found (here: the scenario's check is only planned).
    findings = runner.invoke(
        app, [str(spec), "--profile", str(profile), "--candidate", "--format", "json"]
    )
    assert {f["rule_id"] for f in json.loads(findings.stdout)["findings"]} == {"GC-CHECK-PLANNED"}
    assert result.exit_code == findings.exit_code == 1


def test_arch_policy_is_required_when_a_manifest_is_present(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """The policy is GC-ARCH-CONFORMANCE's input, and the gate runs here, so a
    missing arch-policy.yaml is a config error in this mode too — pinned
    because the doc once claimed the opposite while the code did this.
    """
    spec = _bundle(tmp_path)
    (spec / "intended-graph.yaml").write_text(_VALID_MANIFEST)
    assert not (tmp_path / "arch-policy.yaml").exists()

    result = _candidate(spec)
    assert result.exit_code == 2
    assert "arch-policy.yaml" in result.stderr


def test_arch_policy_is_only_needed_when_a_manifest_is_present(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """No manifest, no arch gates, no policy — the requirement is scoped to
    bundles that actually carry architecture evidence."""
    spec = _bundle(tmp_path)
    assert not (tmp_path / "arch-policy.yaml").exists()
    assert _candidate(spec).exit_code == 0
