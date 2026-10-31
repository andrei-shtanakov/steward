"""Mandatory act mode, node status provider and ``GC-ACT-*`` (approval-facts v3 §7.2).

The combination matrix is parsed from the spec table itself (§7.2.4), so the
table and this test cannot drift apart: every row is a CLI run over the same
fixture — a two-node bundle A → B, both ``status: approved`` in frontmatter,
correct pins, no other defects — with the row's act mode, approvals tree,
provider answer and run kind. The negative doubles Д1–Д9 follow the matrix.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from steward.gatecatalog import load_catalog_files
from steward.gatecheck.approval import ApprovalPolicy, check_approval_evidence
from steward.gatecheck.candidate import CandidateGitFacts, blob_hash_of
from steward.gatecheck.checks import (
    ABSENT_FROM_PROVIDER,
    BUNDLE_ARTIFACT,
    DELEGATED_RULES,
    INVALID_ANSWER,
    NO_PROVIDER,
    PROVIDER_ERROR,
    act_conflict,
    act_statuses,
    collect_bundle,
    run_checks,
    snapshot_digest,
    snapshot_for,
)
from steward.gatecheck.cli import app
from steward.graph import load_profile_data
from steward.roles import Role, RolesCatalog
from steward.verdicts import emit_verdicts

ROOT = Path(__file__).resolve().parents[2]
SPEC_DOC = ROOT / "docs" / "superpowers" / "specs" / "2026-10-30-approval-facts-v3-design.md"

runner = CliRunner()

A = "req.md"
B = "des.md"
NOT_EVALUATED_LINE = "не проверено в prospective-режиме"
DELEGATION_MARK = "делегировано провайдеру статуса узла"

_PROFILE = """\
profile: test
solo_auto_approve: false
artifacts:
  - {id: requirements, owner_role: product, upstream: []}
  - {id: design, owner_role: architects, upstream: [requirements]}
"""

_PROFILE_DATA = {
    "profile": "test",
    "solo_auto_approve": False,
    "artifacts": [
        {"id": "requirements", "owner_role": "product", "upstream": []},
        {"id": "design", "owner_role": "architects", "upstream": ["requirements"]},
    ],
}

_CATALOG = RolesCatalog(
    version=1,
    slug_pattern="^[a-z][a-z0-9-]{1,31}$",
    roles=(Role("product", "Product"), Role("architects", "Architecture")),
)

ALL_APPROVED = {"requirements": "approved", "design": "approved"}

_ANSWERS = {
    "approved": "approved",
    "not_approved": "not_approved:review_withdrawn",
    "unavailable": "unavailable:forge_down",
}


def _req_body(status: str = "approved", tail: str = "") -> str:
    return f"---\nspec_stage: requirements\nstatus: {status}\nversion: 1\n---\n## REQ-001\n{tail}"


def _des_body(pin: str, status: str = "approved") -> str:
    return (
        f"---\nspec_stage: design\nstatus: {status}\nversion: 1\n"
        f"traces_to: [REQ-001]\nupstream_hashes: {{requirements: {pin}}}\n---\n"
    )


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _commit_all(repo: Path) -> None:
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "steward-test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "bundle")


def write_review_tree(approvals: Path) -> None:
    """An envelope and a request manifest that claim the ``review/v1`` act."""
    (approvals / "requests").mkdir(parents=True, exist_ok=True)
    (approvals / "requirements.yaml").write_text("act: review/v1\nnode: requirements\n")
    (approvals / "requests" / "r-1.yaml").write_text("act: review/v1\nnodes: [requirements]\n")


def make_bundle(
    tmp_path: Path,
    *,
    review_tree: bool = False,
    a_status: str = "approved",
    b_pin: str | None = None,
    git: bool = True,
) -> Path:
    """Profile in ``tmp_path``; bundle at ``tmp_path/repo/spec``, approvals next to it."""
    (tmp_path / "test.yaml").write_text(_PROFILE)
    repo = tmp_path / "repo"
    spec = repo / "spec"
    spec.mkdir(parents=True)
    req = _req_body(a_status)
    (spec / A).write_text(req)
    (spec / B).write_text(_des_body(b_pin or blob_hash_of(req.encode())))
    if review_tree:
        write_review_tree(repo / "approvals")
    if git:
        _commit_all(repo)
    return spec


def write_provider(path: Path, digest: str, nodes: dict[str, object]) -> Path:
    path.write_text(json.dumps({"snapshot_digest": digest, "nodes": nodes}))
    return path


def run_gate(spec: Path, args: list[str], *, candidate: bool = False):
    profile = spec.parent.parent / "test.yaml"
    extra = ["--candidate"] if candidate else []
    return runner.invoke(
        app, [str(spec), "--profile", str(profile), "--format", "json", *extra, *args]
    )


def error_codes(result) -> set[tuple[str, str]]:
    findings = json.loads(result.stdout)["findings"]
    return {(f["rule_id"], f["artifact"]) for f in findings if f["severity"] == "error"}


def messages(result, rule_id: str) -> list[str]:
    return [f["message"] for f in json.loads(result.stdout)["findings"] if f["rule_id"] == rule_id]


def delegation_line(path: str) -> str:
    return f"{DELEGATION_MARK}: {path}: {', '.join(DELEGATED_RULES)}"


# --- the §7.2.4 table, parsed from the spec -----------------------------------


@dataclass(frozen=True)
class Row:
    n: int
    mode: str
    tree: bool
    provider: str
    run: str
    findings: frozenset[tuple[str, str]]
    reason: str | None
    exit: int
    not_evaluated: bool
    delegated: bool
    call_error: str | None  # the exit-2 reason the row's cell names


_LETTER = {"A": A, "B": B}
_CODE = re.compile(r"`(GC-[A-Z-]+)`(?:\s*\(([^)]*)\))?")


def _parse_findings(cell: str) -> tuple[frozenset[tuple[str, str]], str | None]:
    if cell.startswith("—"):
        return frozenset(), None
    found: set[tuple[str, str]] = set()
    reason = None
    for code, scope in _CODE.findall(cell):
        if not scope:
            found.add((code, BUNDLE_ARTIFACT))
            continue
        letters, _, note = scope.partition(";")
        found |= {(code, _LETTER[x.strip()]) for x in letters.split(",")}
        hint = re.search(r"`(\w+):", note)
        if hint:
            reason = f"{hint.group(1)}:"
    return frozenset(found), reason


ACT_MODE_REQUIRED = "--act-mode is required: pass the admission result (legacy|review)"
NODE_STATUS_NEEDS_REVIEW = "--node-status requires --act-mode review"


def _call_error(cell: str) -> str | None:
    if "`--node-status` без `--act-mode review`" in cell:
        return NODE_STATUS_NEEDS_REVIEW
    if "нет `--act-mode`" in cell:
        return ACT_MODE_REQUIRED
    return None


def load_matrix_from_spec() -> list[Row]:
    text = SPEC_DOC.read_text(encoding="utf-8")
    section = text.split("#### 7.2.4.", 1)[1].split("Отрицательные двойники", 1)[0]
    rows = []
    for line in section.splitlines():
        if not re.match(r"^\| \d+ \|", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        n, mode, tree, provider, run, findings, exit_code, declared = cells
        parsed, reason = _parse_findings(findings)
        rows.append(
            Row(
                n=int(n),
                mode=mode.strip("`"),
                tree=tree == "да",
                provider=provider,
                run=run,
                findings=parsed,
                reason=reason,
                exit=int(exit_code),
                not_evaluated="NOT_EVALUATED" in declared,
                delegated="delegated" in declared,
                call_error=_call_error(findings),
            )
        )
    return rows


MATRIX = load_matrix_from_spec()


def test_matrix_is_the_whole_table() -> None:
    assert [r.n for r in MATRIX] == list(range(1, 49))


def mode_args(mode: str) -> list[str]:
    return [] if mode == "absent" else ["--act-mode", mode]


def provider_args(provider: str, spec: Path) -> list[str]:
    if provider == "нет":
        return []
    answer = _ANSWERS[provider]
    path = write_provider(
        spec.parent.parent / "provider.json",
        snapshot_digest(spec),
        {"requirements": answer, "design": answer},
    )
    return ["--node-status", str(path)]


@pytest.mark.parametrize("row", MATRIX, ids=lambda r: f"#{r.n}")
def test_combination(
    row: Row, tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = make_bundle(tmp_path, review_tree=row.tree)
    args = mode_args(row.mode) + provider_args(row.provider, spec)
    res = run_gate(spec, args, candidate=row.run == "candidate")
    assert res.exit_code == row.exit, res.output
    if row.exit == 2:
        # Any config error exits 2; the row names which one it must be.
        assert row.call_error is not None and row.call_error in res.output, res.output
        return
    assert row.call_error is None
    assert error_codes(res) == row.findings
    reasons = messages(res, "GC-ACT-NOT-APPROVED")
    if row.reason is not None:
        assert reasons and all(row.reason in m for m in reasons), reasons
    elif row.mode == "review" and row.provider == "нет":
        assert reasons and all(NO_PROVIDER in m for m in reasons), reasons
    assert (NOT_EVALUATED_LINE in res.stderr) == row.not_evaluated
    if row.delegated:
        for path in (A, B):
            assert delegation_line(path) in res.stderr, res.stderr
    else:
        assert DELEGATION_MARK not in res.stderr


# --- negative doubles Д1–Д9 ---------------------------------------------------


def test_d1_deleted_envelope_and_manifest_review(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """Blocker r1: the envelope and manifest are deleted, frontmatter stays approved."""
    spec = make_bundle(tmp_path, review_tree=True, git=False)
    shutil.rmtree(spec.parent / "approvals")
    provider = write_provider(
        tmp_path / "p.json",
        snapshot_digest(spec),
        {"requirements": "not_approved:no_envelope", "design": "approved"},
    )
    res = run_gate(spec, ["--act-mode", "review", "--node-status", str(provider)], candidate=True)
    assert res.exit_code == 1, res.output
    assert error_codes(res) == {("GC-ACT-NOT-APPROVED", A), ("GC-UPSTREAM", B)}
    assert "not_approved:no_envelope" in messages(res, "GC-ACT-NOT-APPROVED")[0]


def test_d2_missing_act_mode_exit_2(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = make_bundle(tmp_path, git=False)
    res = run_gate(spec, [], candidate=True)
    assert res.exit_code == 2
    assert "--act-mode is required" in res.output


def test_d3_no_approvals_ever_but_mode_review(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """Major r2: the product is in review before its first manifest."""
    spec = make_bundle(tmp_path, git=False)
    assert not (spec.parent / "approvals").exists()
    provider = write_provider(
        tmp_path / "p.json",
        snapshot_digest(spec),
        {"requirements": "not_approved:no_manifest", "design": "not_approved:no_manifest"},
    )
    res = run_gate(spec, ["--act-mode", "review", "--node-status", str(provider)], candidate=True)
    assert res.exit_code == 1
    assert error_codes(res) == {
        ("GC-ACT-NOT-APPROVED", A),
        ("GC-ACT-NOT-APPROVED", B),
        ("GC-UPSTREAM", B),
    }
    assert all("no_manifest" in m for m in messages(res, "GC-ACT-NOT-APPROVED"))


def test_d4_candidate_legacy_changed_upstream_bytes(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = make_bundle(tmp_path, git=False)
    (spec / A).write_text(_req_body(tail="## REQ-002\n"))
    res = run_gate(spec, ["--act-mode", "legacy"], candidate=True)
    assert res.exit_code == 1
    assert error_codes(res) == {("GC-STALE", B)}


def test_d5_provider_file_digest_mismatch_exit_2(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = make_bundle(tmp_path, git=False)
    printed = runner.invoke(app, ["--print-snapshot-digest", "--candidate", str(spec)])
    assert printed.exit_code == 0, printed.output
    digest = printed.stdout.strip()
    (spec / A).write_text(_req_body(tail="## REQ-002\n"))
    provider = write_provider(tmp_path / "p.json", digest, ALL_APPROVED)
    res = run_gate(spec, ["--act-mode", "review", "--node-status", str(provider)], candidate=True)
    assert res.exit_code == 2
    assert "snapshot_digest" in res.output


def test_d6_cli_builds_candidate_snapshot_not_base(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """The real CLI: base (HEAD) with bytes X, a separate candidate dir with Y ≠ X."""
    base = make_bundle(tmp_path)
    repo = base.parent
    cand = repo / "cand"
    shutil.copytree(base, cand)
    req = _req_body(tail="## REQ-002\n")
    (cand / A).write_text(req)
    (cand / B).write_text(_des_body(blob_hash_of(req.encode())))
    assert snapshot_digest(cand) != snapshot_digest(base)

    good = write_provider(tmp_path / "good.json", snapshot_digest(cand), ALL_APPROVED)
    res = run_gate(cand, ["--act-mode", "review", "--node-status", str(good)], candidate=True)
    assert res.exit_code == 0, res.output
    snap = json.loads(res.stdout)["snapshot"]
    assert (snap["kind"], Path(snap["root"]).resolve(), snap["digest"]) == (
        "candidate",
        cand.resolve(),
        snapshot_digest(cand),
    )

    bad = write_provider(tmp_path / "bad.json", snapshot_digest(base), ALL_APPROVED)
    res = run_gate(cand, ["--act-mode", "review", "--node-status", str(bad)], candidate=True)
    assert res.exit_code == 2


def test_d7_unestablished_node_keeps_stale_cascade(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = make_bundle(tmp_path, b_pin="f" * 40, git=False)
    provider = write_provider(
        tmp_path / "p.json",
        snapshot_digest(spec),
        {"requirements": "approved", "design": "not_approved:x"},
    )
    res = run_gate(spec, ["--act-mode", "review", "--node-status", str(provider)], candidate=True)
    assert res.exit_code == 1
    assert error_codes(res) == {("GC-ACT-NOT-APPROVED", B), ("GC-STALE", B)}


def test_d8_upstream_judged_by_established(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = make_bundle(tmp_path, a_status="draft", git=False)
    provider = write_provider(tmp_path / "p.json", snapshot_digest(spec), ALL_APPROVED)
    res = run_gate(spec, ["--act-mode", "review", "--node-status", str(provider)], candidate=True)
    assert res.exit_code == 0, res.output
    assert error_codes(res) == set()


def test_d9_api_requires_act_mode(tmp_path: Path) -> None:
    spec = make_bundle(tmp_path, git=False)
    graph = load_profile_data(_PROFILE_DATA, _CATALOG)
    arts, _ = collect_bundle(graph, spec, act_mode="legacy")
    snap = snapshot_for("candidate", spec, None)

    def provider(node: str, snapshot: object) -> str:
        return "approved"

    with pytest.raises(TypeError):
        collect_bundle(graph, spec)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        act_statuses(arts, node_status=None, snapshot=None)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        act_statuses(arts, act_mode="legacy", node_status=provider, snapshot=snap)
    with pytest.raises(ValueError):
        act_statuses(arts, act_mode="review", node_status=provider, snapshot=None)
    with pytest.raises(ValueError):
        act_statuses(arts, act_mode="maybe", node_status=None, snapshot=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        run_checks(graph, arts, CandidateGitFacts(spec))  # type: ignore[call-arg]


# --- the provider: answers, failures, the file form -----------------------------


def _review(spec: Path, provider) -> tuple[list, object]:
    graph = load_profile_data(_PROFILE_DATA, _CATALOG)
    arts, findings = collect_bundle(graph, spec, act_mode="review")
    snap = snapshot_for("candidate", spec, None)
    statuses = act_statuses(arts, act_mode="review", node_status=provider, snapshot=snap)
    findings += run_checks(
        graph, arts, CandidateGitFacts(spec), statuses=statuses, prospective=True
    )
    return findings, statuses


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("yes", INVALID_ANSWER),
        ("not_approved:", INVALID_ANSWER),
        ("unavailable:", INVALID_ANSWER),
        ("", INVALID_ANSWER),
        (5, INVALID_ANSWER),
        (None, INVALID_ANSWER),
        ("not_approved:review_withdrawn", "not_approved:review_withdrawn"),
        ("unavailable:forge_down", "unavailable:forge_down"),
    ],
)
def test_provider_invalid_answer_and_exception(
    answer: object, expected: str, tmp_path: Path
) -> None:
    spec = make_bundle(tmp_path, git=False)
    findings, statuses = _review(spec, lambda node, snap: answer)
    assert statuses.answers == {"requirements": expected, "design": expected}
    act = [f for f in findings if f.rule_id == "GC-ACT-NOT-APPROVED"]
    assert {f.artifact for f in act} == {A, B}
    assert all(expected in f.message for f in act)


def test_provider_exception_is_provider_error_not_a_traceback(tmp_path: Path) -> None:
    spec = make_bundle(tmp_path, git=False)

    def broken(node: str, snap: object) -> str:
        raise RuntimeError("forge exploded")

    findings, statuses = _review(spec, broken)
    assert set(statuses.answers.values()) == {PROVIDER_ERROR}
    assert {f.artifact for f in findings if f.rule_id == "GC-ACT-NOT-APPROVED"} == {A, B}


def test_provider_receives_the_snapshot(tmp_path: Path) -> None:
    spec = make_bundle(tmp_path, git=False)
    seen: list[tuple[str, object]] = []

    def provider(node: str, snap: object) -> str:
        seen.append((node, snap))
        return "approved"

    _review(spec, provider)
    assert sorted(n for n, _ in seen) == ["design", "requirements"]
    assert all(s == snapshot_for("candidate", spec, None) for _, s in seen)


@pytest.mark.parametrize(
    "raw",
    [
        '{"snapshot_digest": "D", "nodes": {}, "nodes": {}}',
        '{"snapshot_digest": "D", "nodes": {"design": "approved", "design": "approved"}}',
        '{"snapshot_digest": "D", "nodes": {}, "extra": 1}',
        '{"snapshot_digest": "D", "nodes": {"design": 1}}',
        '{"snapshot_digest": "D"}',
        '{"nodes": {}}',
        '{"snapshot_digest": 7, "nodes": {}}',
        '["D"]',
        "not json",
    ],
    ids=[
        "dup-top",
        "dup-node",
        "extra-key",
        "non-string",
        "no-nodes",
        "no-digest",
        "digest-not-str",
        "not-object",
        "not-json",
    ],
)
def test_provider_file_duplicate_keys_exit_2(
    raw: str, tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = make_bundle(tmp_path, git=False)
    path = tmp_path / "p.json"
    path.write_text(raw.replace('"D"', json.dumps(snapshot_digest(spec))))
    res = run_gate(spec, ["--act-mode", "review", "--node-status", str(path)], candidate=True)
    assert res.exit_code == 2, res.output
    assert "config error" in res.output


def test_node_absent_from_provider_file(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = make_bundle(tmp_path, git=False)
    provider = write_provider(
        tmp_path / "p.json", snapshot_digest(spec), {"requirements": "approved"}
    )
    res = run_gate(spec, ["--act-mode", "review", "--node-status", str(provider)], candidate=True)
    assert res.exit_code == 1
    assert error_codes(res) == {("GC-ACT-NOT-APPROVED", B)}
    assert ABSENT_FROM_PROVIDER in messages(res, "GC-ACT-NOT-APPROVED")[0]


def test_status_is_never_rewritten(tmp_path: Path) -> None:
    spec = make_bundle(tmp_path, git=False)
    graph = load_profile_data(_PROFILE_DATA, _CATALOG)
    arts, _ = collect_bundle(graph, spec, act_mode="review")
    snap = snapshot_for("candidate", spec, None)
    statuses = act_statuses(
        arts, act_mode="review", node_status=lambda n, s: "not_approved:x", snapshot=snap
    )
    assert {a.meta.status for a in arts} == {"approved"}
    assert all(statuses.claimed(a) and not statuses.established(a) for a in arts)
    assert all(statuses.in_scope(a) for a in arts)


def test_legacy_statuses_are_frontmatter(tmp_path: Path) -> None:
    spec = make_bundle(tmp_path, a_status="draft", git=False)
    graph = load_profile_data(_PROFILE_DATA, _CATALOG)
    arts, _ = collect_bundle(graph, spec, act_mode="legacy")
    statuses = act_statuses(arts, act_mode="legacy", node_status=None, snapshot=None)
    by_path = {a.path: a for a in arts}
    assert not statuses.established(by_path[A]) and not statuses.in_scope(by_path[A])
    assert statuses.established(by_path[B])
    assert statuses.answers == {}


# --- the GC-ACT-MODE-CONFLICT detector ----------------------------------------


def _legacy_conflicts(spec: Path) -> list:
    graph = load_profile_data(_PROFILE_DATA, _CATALOG)
    _, findings = collect_bundle(graph, spec, act_mode="legacy")
    return [f for f in findings if f.rule_id == "GC-ACT-MODE-CONFLICT"]


def test_unreadable_approvals_dir_is_conflict_in_legacy(tmp_path: Path) -> None:
    spec = make_bundle(tmp_path, git=False)
    approvals = spec.parent / "approvals"
    approvals.mkdir()
    approvals.chmod(0)
    try:
        conflicts = _legacy_conflicts(spec)
    finally:
        approvals.chmod(0o755)
    assert [(f.severity, f.artifact) for f in conflicts] == [("error", BUNDLE_ARTIFACT)]


def test_unparseable_or_unreadable_yaml_is_conflict_in_legacy(tmp_path: Path) -> None:
    spec = make_bundle(tmp_path, git=False)
    approvals = spec.parent / "approvals"
    (approvals / "requests").mkdir(parents=True)
    (approvals / "requests" / "r-1.yaml").write_text("act: [unclosed\n")
    assert len(_legacy_conflicts(spec)) == 1

    (approvals / "requests" / "r-1.yaml").write_text("act: merge\n")
    locked = approvals / "design.yaml"
    locked.write_text("act: merge\n")
    locked.chmod(0)
    try:
        assert len(_legacy_conflicts(spec)) == 1
    finally:
        locked.chmod(0o644)


def test_detector_reads_envelope_and_manifest_only_for_review_act(tmp_path: Path) -> None:
    spec = make_bundle(tmp_path, git=False)
    approvals = spec.parent / "approvals"
    (approvals / "requests").mkdir(parents=True)
    (approvals / "requirements.yaml").write_text("act: merge\n")
    (approvals / "requests" / "r-1.yaml").write_text("act: merge\n")
    assert _legacy_conflicts(spec) == []

    (approvals / "requests" / "r-1.yaml").write_text("act: review/v1\n")
    assert len(_legacy_conflicts(spec)) == 1


def test_detector_is_silent_in_review_mode(tmp_path: Path) -> None:
    spec = make_bundle(tmp_path, review_tree=True, git=False)
    graph = load_profile_data(_PROFILE_DATA, _CATALOG)
    _, findings = collect_bundle(graph, spec, act_mode="review")
    assert findings == []


# --- consumers of the statuses: emitter and release evidence ---------------------


def test_emitter_status_in_review_mode(tmp_path: Path) -> None:
    spec = make_bundle(tmp_path, a_status="draft")
    graph = load_profile_data(_PROFILE_DATA, _CATALOG)
    arts, findings = collect_bundle(graph, spec, act_mode="review")
    snap = snapshot_for("candidate", spec, None)
    statuses = act_statuses(
        arts,
        act_mode="review",
        node_status=lambda n, s: "approved" if n == "requirements" else "not_approved:x",
        snapshot=snap,
    )
    findings += run_checks(
        graph, arts, CandidateGitFacts(spec), statuses=statuses, prospective=True
    )
    catalog = load_catalog_files(
        ROOT / "profiles" / "gate-catalog.yaml", ROOT / "profiles" / "roles.yaml"
    )
    out = emit_verdicts(graph, arts, findings, spec, catalog, statuses=statuses)
    records = [json.loads(line) for line in out.read_text().splitlines()]
    status = {r["path"]: r["status"] for r in records if r["kind"] == "artifact"}
    # A: frontmatter draft, established → approved; B: claimed, not established → unknown.
    assert status == {A: "approved", B: "unknown"}


class _ReleaseGit:
    def on_default_branch(self, path: str) -> bool:
        return True

    def merge_provenance(self, path: str) -> None:
        return None


def test_release_evidence_runs_for_claimed_unestablished(tmp_path: Path) -> None:
    spec = make_bundle(tmp_path, git=False)
    graph = load_profile_data(_PROFILE_DATA, _CATALOG)
    arts, _ = collect_bundle(graph, spec, act_mode="review")
    snap = snapshot_for("candidate", spec, None)
    statuses = act_statuses(
        arts, act_mode="review", node_status=lambda n, s: "not_approved:x", snapshot=snap
    )
    policy = ApprovalPolicy(version=1, human_identities=frozenset(), agent_identities=frozenset())
    findings = check_approval_evidence(
        arts,
        _ReleaseGit(),
        policy,
        None,
        stage="release",
        statuses=statuses,  # type: ignore[arg-type]
    )
    assert {(f.rule_id, f.artifact) for f in findings} == {
        ("GC-APPROVAL-MISSING", A),
        ("GC-APPROVAL-MISSING", B),
    }


# --- JSON payload and caller migration ------------------------------------------


def test_json_reports_act_mode_delegated_and_snapshot(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = make_bundle(tmp_path, git=False)
    provider = write_provider(tmp_path / "p.json", snapshot_digest(spec), ALL_APPROVED)
    review = json.loads(
        run_gate(
            spec, ["--act-mode", "review", "--node-status", str(provider)], candidate=True
        ).stdout
    )
    assert review["act_mode"] == "review"
    assert review["delegated"] == list(DELEGATED_RULES)
    assert review["snapshot"]["digest"] == snapshot_digest(spec)

    legacy = json.loads(run_gate(spec, ["--act-mode", "legacy"], candidate=True).stdout)
    assert (legacy["act_mode"], legacy["delegated"]) == ("legacy", [])


def test_unknown_act_mode_value_exit_2(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = make_bundle(tmp_path, git=False)
    res = run_gate(spec, ["--act-mode", "merge"], candidate=True)
    assert res.exit_code == 2
    assert "config error" in res.output


def test_ci_workflow_passes_act_mode() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    runs = [line for line in ci.splitlines() if "gate-check --" in line]
    assert runs
    assert all("--act-mode legacy" in line for line in runs)


# --- fix round 1: review-mode checks that must not switch off / must skip ----------


def test_status_git_still_runs_for_claimed_unestablished_node(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """§7.2.2: a provider's refusal switches no check off — GC-GIT-BRANCH still fires."""
    spec = make_bundle(tmp_path, git=False)
    facts = tmp_path / "facts.json"
    facts.write_text(
        json.dumps(
            {
                "default_branch_files": [A],
                "approvals": {
                    A: [{"identity": "github:alice"}],
                    B: [{"identity": "github:bob"}],
                },
            }
        )
    )
    provider = write_provider(
        tmp_path / "p.json",
        snapshot_digest(spec),
        {"requirements": "approved", "design": "not_approved:x"},
    )
    res = run_gate(
        spec, ["--act-mode", "review", "--node-status", str(provider), "--no-fs", str(facts)]
    )
    assert res.exit_code == 1, res.output
    assert error_codes(res) == {("GC-ACT-NOT-APPROVED", B), ("GC-GIT-BRANCH", B)}


def test_established_node_skips_the_stale_cascade(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """§7.2.3, the skip side of Д7: an established node's broken frontmatter pin is
    the provider's concern — no GC-STALE*, and the delegation is declared."""
    spec = make_bundle(tmp_path, b_pin="f" * 40, git=False)
    provider = write_provider(tmp_path / "p.json", snapshot_digest(spec), ALL_APPROVED)
    res = run_gate(spec, ["--act-mode", "review", "--node-status", str(provider)], candidate=True)
    assert res.exit_code == 0, res.output
    rules = {f["rule_id"] for f in json.loads(res.stdout)["findings"]}
    assert not rules & set(DELEGATED_RULES), rules
    assert delegation_line(B) in res.stderr


# --- fix round 1: the conflict detector through the CLI -------------------------


def test_cli_unparseable_approvals_yaml_is_conflict(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    spec = make_bundle(tmp_path, git=False)
    (spec.parent / "approvals" / "requests").mkdir(parents=True)
    (spec.parent / "approvals" / "requests" / "r-1.yaml").write_text("act: [unclosed\n")
    res = run_gate(spec, ["--act-mode", "legacy"], candidate=True)
    assert res.exit_code == 1, res.output
    assert error_codes(res) == {("GC-ACT-MODE-CONFLICT", BUNDLE_ARTIFACT)}


def test_cli_unreadable_approvals_dir_is_exit_2(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    """Two fail-closed rules cover this input; the stricter one wins at the CLI:
    the snapshot cannot hash approvals/ (exit 2) before the detector runs."""
    spec = make_bundle(tmp_path, git=False)
    approvals = spec.parent / "approvals"
    approvals.mkdir()
    approvals.chmod(0)
    try:
        res = run_gate(spec, ["--act-mode", "legacy"], candidate=True)
    finally:
        approvals.chmod(0o755)
    assert res.exit_code == 2, res.output
    assert "config error: snapshot" in res.output


def test_detector_ignores_other_subdirectories(tmp_path: Path) -> None:
    """§7.2.1 names only envelopes <node>.yaml and requests/*.yaml."""
    spec = make_bundle(tmp_path, git=False)
    other = spec.parent / "approvals" / "other"
    other.mkdir(parents=True)
    (other / "x.yaml").write_text("act: review/v1\n")
    assert _legacy_conflicts(spec) == []


# --- fix round 1: relative bundle paths (I1) --------------------------------------


def _relative_layout(tmp_path: Path) -> tuple[Path, Path]:
    """``w/sub/bundle`` with a review tree in ``w/sub/approvals``; profile in tmp_path."""
    (tmp_path / "test.yaml").write_text(_PROFILE)
    base = tmp_path / "w"
    spec = base / "sub" / "bundle"
    (spec / "extra").mkdir(parents=True)
    req = _req_body()
    (spec / A).write_text(req)
    (spec / B).write_text(_des_body(blob_hash_of(req.encode())))
    return base, spec


RELATIVE_CASES = [
    ("sub/bundle", "."),
    (".", "./sub/bundle"),
    ("sub/bundle", "../bundle"),
    ("sub/approvals", "../bundle"),
    (".", "./sub/bundle/extra/.."),
]


@pytest.mark.parametrize(("cwd", "arg"), RELATIVE_CASES, ids=[a for _, a in RELATIVE_CASES])
def test_relative_bundle_path_sees_the_real_approvals(
    cwd: str,
    arg: str,
    tmp_path: Path,
    write_roles: Path,
    write_role_assignments: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, spec = _relative_layout(tmp_path)
    without_tree = snapshot_digest(spec)
    write_review_tree(spec.parent / "approvals")
    absolute = snapshot_digest(spec)
    assert absolute != without_tree  # the envelopes are in the digest

    monkeypatch.chdir(base / cwd)
    rel = Path(arg)
    assert snapshot_digest(rel) == absolute
    assert snapshot_for("candidate", rel, None).root == spec.resolve()
    assert [f.rule_id for f in act_conflict(rel)] == ["GC-ACT-MODE-CONFLICT"]

    printed = runner.invoke(app, ["--print-snapshot-digest", "--candidate", arg])
    assert printed.exit_code == 0, printed.output
    assert printed.stdout.strip() == absolute

    res = runner.invoke(
        app,
        [arg, "--profile", str(tmp_path / "test.yaml"), "--format", "json"]
        + ["--candidate", "--act-mode", "legacy"],
    )
    assert res.exit_code == 1, res.output
    assert error_codes(res) == {("GC-ACT-MODE-CONFLICT", BUNDLE_ARTIFACT)}
    assert json.loads(res.stdout)["snapshot"]["digest"] == absolute


def test_print_snapshot_digest_rejects_node_status(tmp_path: Path) -> None:
    spec = make_bundle(tmp_path, git=False)
    res = runner.invoke(
        app, ["--print-snapshot-digest", "--candidate", str(spec), "--node-status", "p.json"]
    )
    assert res.exit_code == 2
    assert "config error" in res.output
