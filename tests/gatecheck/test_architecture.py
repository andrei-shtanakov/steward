"""GC-ARCH-* gates: schema, evidence (Task 2) + conformance (Task 3)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from steward.gatecheck.architecture import (
    ArchPolicyError,
    check_arch_conformance,
    check_arch_evidence,
    check_arch_schema,
    collect_arch_bundle,
    load_arch_policy,
)
from steward.gatecheck.git_facts import InjectedGitFacts

VALID_MANIFEST = """\
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


def _bundle(tmp_path: Path, manifest: str) -> Path:
    (tmp_path / "intended-graph.yaml").write_text(manifest, encoding="utf-8")
    return tmp_path


def test_no_manifest_means_inactive(tmp_path: Path) -> None:
    assert collect_arch_bundle(tmp_path) is None


def test_valid_manifest_clean(tmp_path: Path) -> None:
    arch = collect_arch_bundle(_bundle(tmp_path, VALID_MANIFEST))
    assert arch is not None
    assert check_arch_schema(arch) == []
    assert check_arch_evidence(arch) == []


def test_schema_unknown_key_and_bad_enum(tmp_path: Path) -> None:
    bad = VALID_MANIFEST.replace("kind: service", "kind: banana") + "extra: boom\n"
    arch = collect_arch_bundle(_bundle(tmp_path, bad))
    assert arch is not None
    findings = check_arch_schema(arch)
    assert findings and all(f.rule_id == "GC-ARCH-SCHEMA" for f in findings)
    assert all(f.severity == "error" for f in findings)


def test_schema_unparseable_yaml(tmp_path: Path) -> None:
    arch = collect_arch_bundle(_bundle(tmp_path, "schema: [unclosed"))
    assert arch is not None
    findings = check_arch_schema(arch)
    assert len(findings) == 1 and "YAML" in findings[0].message


def test_evidence_missing_on_interface(tmp_path: Path) -> None:
    stripped = VALID_MANIFEST.replace("    evidence: [BEH-01]\n", "")
    arch = collect_arch_bundle(_bundle(tmp_path, stripped))
    assert arch is not None
    findings = check_arch_evidence(arch)
    assert [f.rule_id for f in findings] == ["GC-ARCH-EVIDENCE"]
    assert "I-01" in findings[0].message


def test_evidence_empty_list_is_a_finding(tmp_path: Path) -> None:
    bad = VALID_MANIFEST.replace("evidence: [FR-02]", "evidence: []")
    arch = collect_arch_bundle(_bundle(tmp_path, bad))
    assert arch is not None
    assert any("C-01" in f.message for f in check_arch_evidence(arch))


def test_manifest_found_in_nested_dir(tmp_path: Path) -> None:
    nested = tmp_path / "ws" / "spec"
    nested.mkdir(parents=True)
    (nested / "intended-graph.yaml").write_text(VALID_MANIFEST, encoding="utf-8")
    arch = collect_arch_bundle(tmp_path)
    assert arch is not None
    assert arch.manifest_rel == "ws/spec/intended-graph.yaml"


# --- Task 3: GC-ARCH-CONFORMANCE + stage policy -----------------------------

ARCH_POLICY_PATH = Path(__file__).resolve().parents[2] / "profiles" / "arch-policy.yaml"
MANIFEST_REPO_PATH = "workstreams/WS-005-gate-verdicts/spec/intended-graph.yaml"


class FakeGitFacts:
    """Minimal GitFacts fake covering the D9 surface used by check_arch_conformance."""

    def __init__(
        self,
        ancestors: set[str] | None = None,
        changed: dict[str, list[str]] | None = None,
    ) -> None:
        self._ancestors = ancestors or set()
        self._changed = changed or {}

    def on_default_branch(self, path: str) -> bool:
        return False

    def approvals(self, path: str) -> tuple:
        return ()

    def blob_hash(self, path: str) -> str | None:
        return None

    def is_ancestor(self, commit: str) -> bool:
        return commit in self._ancestors

    def changed_paths_since(self, commit: str) -> list[str]:
        return self._changed.get(commit, [])


def _bundle_with_report(tmp_path: Path, manifest: str, report: dict | None) -> Path:
    (tmp_path / "intended-graph.yaml").write_text(manifest, encoding="utf-8")
    if report is not None:
        (tmp_path / "conformance-report.json").write_text(json.dumps(report), encoding="utf-8")
    return tmp_path


def _report(
    manifest_bytes: bytes,
    *,
    self_project: str | None = "steward",
    self_commit: str | None = "c0ffee",
    self_dirty: bool | None = False,
    indexed_at: str = "2026-08-01T00:00:00Z",
    complete: bool = True,
    elements: list[dict] | None = None,
    findings: list[dict] | None = None,
    manifest_sha256: str | None = None,
) -> dict:
    projects = {}
    if self_project is not None:
        projects[self_project] = {"commit": self_commit, "dirty": self_dirty}
    return {
        "schema": "conformance-report/v1",
        "system": "t-sys",
        "generated_at": "2099-01-01T00:00:00Z",  # never used for age (D3/D4)
        "manifest": {
            "project": "steward",
            "path": MANIFEST_REPO_PATH,
            "sha256": manifest_sha256 or hashlib.sha256(manifest_bytes).hexdigest(),
        },
        "snapshot": {
            "id": 1,
            "indexed_at": indexed_at,
            "content_hash": "prograph-snapshot/v1+sha256:" + "0" * 64,
            "complete": complete,
        },
        "tool": {"name": "prograph", "version": "1.0.0", "schema": "intended-graph/v1"},
        "projects": projects,
        "elements": elements if elements is not None else [],
        "findings": findings if findings is not None else [],
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


def _element(
    element_id: str,
    verdict: str,
    *,
    reason: str | None = None,
    waived_by: str | None = None,
    detector: str = "import",
    element_type: str = "interface",
) -> dict:
    return {
        "id": element_id,
        "type": element_type,
        "detector": detector,
        "verdict": verdict,
        "reason": reason,
        "waived_by": waived_by,
    }


def _finding(
    class_: str,
    *,
    element: str | None = "I-01",
    detail: str = "detail",
    suppressed_by: str | None = None,
) -> dict:
    return {"class": class_, "element": element, "detail": detail, "suppressed_by": suppressed_by}


def test_load_arch_policy_authoring_and_release_stage_present() -> None:
    authoring = load_arch_policy(ARCH_POLICY_PATH, "authoring")
    release = load_arch_policy(ARCH_POLICY_PATH, "release")
    assert authoring.require_self_fresh is False
    assert authoring.max_snapshot_age_hours is None
    assert release.require_self_fresh is True
    assert release.max_snapshot_age_hours == 24
    assert release.fail_on_findings == {"missing-required-edge"}


def test_load_arch_policy_unknown_stage_is_config_error() -> None:
    try:
        load_arch_policy(ARCH_POLICY_PATH, "nonsense")
        raise AssertionError("expected ArchPolicyError")
    except ArchPolicyError:
        pass


def test_load_arch_policy_unknown_vocabulary_is_config_error(tmp_path: Path) -> None:
    bad = tmp_path / "arch-policy.yaml"
    bad.write_text(
        "self_project: steward\n"
        "stages:\n"
        "  authoring:\n"
        "    fail_on_findings: [not-a-real-class]\n"
        "    fail_on_verdicts: [violation]\n"
        "    unknown_policy:\n"
        "      allowed_reasons: []\n"
        "      allowed_elements: []\n"
        "    require_self_fresh: false\n"
        "    max_snapshot_age_hours: null\n",
        encoding="utf-8",
    )
    try:
        load_arch_policy(bad, "authoring")
        raise AssertionError("expected ArchPolicyError")
    except ArchPolicyError:
        pass


def test_missing_report_is_mandatory_core_error(tmp_path: Path) -> None:
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, None)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "authoring")
    findings = check_arch_conformance(arch, policy, FakeGitFacts())
    assert len(findings) == 1
    assert findings[0].rule_id == "GC-ARCH-CONFORMANCE"
    assert "conformance-report.json" in findings[0].message


def test_bad_json_report_is_mandatory_core_error(tmp_path: Path) -> None:
    (tmp_path / "intended-graph.yaml").write_text(VALID_MANIFEST, encoding="utf-8")
    (tmp_path / "conformance-report.json").write_text("{not json", encoding="utf-8")
    arch = collect_arch_bundle(tmp_path)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "authoring")
    findings = check_arch_conformance(arch, policy, FakeGitFacts())
    assert len(findings) == 1
    assert "unparseable JSON" in findings[0].message


def test_non_utf8_report_is_mandatory_core_error_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "intended-graph.yaml").write_text(VALID_MANIFEST, encoding="utf-8")
    # invalid UTF-8 start byte -> json.loads(bytes) raises UnicodeDecodeError,
    # not JSONDecodeError; must become a finding, not an uncaught crash.
    (tmp_path / "conformance-report.json").write_bytes(b"\x80\x81\x82")
    arch = collect_arch_bundle(tmp_path)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "authoring")
    findings = check_arch_conformance(arch, policy, FakeGitFacts())
    assert len(findings) == 1
    assert "unparseable JSON" in findings[0].message


def test_schema_invalid_report_is_mandatory_core_error(tmp_path: Path) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(manifest_bytes)
    report["snapshot"]["complete"] = "yes"  # wrong type -> schema violation
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "authoring")
    findings = check_arch_conformance(arch, policy, FakeGitFacts())
    assert findings and all(f.rule_id == "GC-ARCH-CONFORMANCE" for f in findings)


def test_sha_mismatch_is_mandatory_core_error(tmp_path: Path) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(manifest_bytes, manifest_sha256="0" * 64)
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "authoring")
    findings = check_arch_conformance(arch, policy, FakeGitFacts())
    assert len(findings) == 1
    assert "stale evidence" in findings[0].message


def test_complete_false_is_mandatory_core_error(tmp_path: Path) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(manifest_bytes, complete=False)
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "authoring")
    findings = check_arch_conformance(arch, policy, FakeGitFacts())
    assert len(findings) == 1
    assert "snapshot.complete" in findings[0].message


def test_authoring_stage_passes_with_any_reason_unknowns_but_fails_on_violation(
    tmp_path: Path,
) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    clean_report = _report(
        manifest_bytes,
        elements=[
            _element("I-01", "unknown", reason="outside-workspace"),
            _element("I-02", "unknown", reason=None),
        ],
    )
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, clean_report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "authoring")
    assert check_arch_conformance(arch, policy, FakeGitFacts()) == []

    violation_report = _report(manifest_bytes, elements=[_element("I-03", "violation")])
    bundle2 = _bundle_with_report(tmp_path, VALID_MANIFEST, violation_report)
    arch2 = collect_arch_bundle(bundle2)
    assert arch2 is not None
    findings = check_arch_conformance(arch2, policy, FakeGitFacts())
    assert len(findings) == 1 and "I-03" in findings[0].message


def test_release_stage_fails_on_missing_required_edge_finding(tmp_path: Path) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(
        manifest_bytes,
        self_commit="deadbeef",
        findings=[_finding("missing-required-edge", element="I-01")],
    )
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "release")
    git = FakeGitFacts(ancestors={"deadbeef"}, changed={"deadbeef": []})
    findings = check_arch_conformance(
        arch, policy, git, now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    )
    assert any("missing-required-edge" in f.message and "I-01" in f.message for f in findings)


def test_release_stage_unknown_reasons_disallowed_vs_permanent_allowed(
    tmp_path: Path,
) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(
        manifest_bytes,
        self_commit="deadbeef",
        elements=[
            _element("I-outside", "unknown", reason="outside-workspace"),
            _element("I-null", "unknown", reason=None),
            _element("I-manual", "unknown", reason="manual-evidence"),
            _element("I-unsupported", "unknown", reason="unsupported-resolution"),
        ],
    )
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "release")
    git = FakeGitFacts(ancestors={"deadbeef"}, changed={"deadbeef": []})
    findings = check_arch_conformance(
        arch, policy, git, now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    )
    messages = " ".join(f.message for f in findings)
    assert "I-outside" in messages
    assert "I-null" in messages
    assert "I-manual" not in messages
    assert "I-unsupported" not in messages


def test_allowed_elements_exemption_works(tmp_path: Path) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(
        manifest_bytes,
        self_commit="deadbeef",
        elements=[_element("I-weird", "unknown", reason="outside-workspace")],
    )
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    release_cfg = load_arch_policy(ARCH_POLICY_PATH, "release")
    from dataclasses import replace

    policy = replace(release_cfg, unknown_allowed_elements=frozenset({"I-weird"}))
    git = FakeGitFacts(ancestors={"deadbeef"}, changed={"deadbeef": []})
    findings = check_arch_conformance(
        arch, policy, git, now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    )
    assert findings == []


def test_stale_snapshot_age_fails_release(tmp_path: Path) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(manifest_bytes, self_commit="deadbeef", indexed_at="2026-07-01T00:00:00Z")
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "release")
    git = FakeGitFacts(ancestors={"deadbeef"}, changed={"deadbeef": []})
    findings = check_arch_conformance(
        arch, policy, git, now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    )
    assert any("snapshot age" in f.message for f in findings)


def test_snapshot_age_never_uses_generated_at(tmp_path: Path) -> None:
    # generated_at in _report is 2099 (far future); if age used generated_at
    # the check would pass trivially. It must use snapshot.indexed_at instead.
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(manifest_bytes, self_commit="deadbeef", indexed_at="2020-01-01T00:00:00Z")
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "release")
    git = FakeGitFacts(ancestors={"deadbeef"}, changed={"deadbeef": []})
    findings = check_arch_conformance(
        arch, policy, git, now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    )
    assert any("snapshot age" in f.message for f in findings)


def test_self_freshness_non_ancestor_commit_fails(tmp_path: Path) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(manifest_bytes, self_commit="notanancestor")
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "release")
    git = FakeGitFacts(ancestors=set())  # commit is not an ancestor
    findings = check_arch_conformance(
        arch, policy, git, now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    )
    assert any("not an ancestor" in f.message for f in findings)


def test_self_freshness_diff_touching_manifest_fails(tmp_path: Path) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(manifest_bytes, self_commit="deadbeef")
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "release")
    git = FakeGitFacts(ancestors={"deadbeef"}, changed={"deadbeef": [MANIFEST_REPO_PATH]})
    findings = check_arch_conformance(
        arch, policy, git, now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    )
    assert any("not provable" in f.message for f in findings)


def test_self_freshness_diff_touching_component_scope_fails(tmp_path: Path) -> None:
    # a.svc's project is "alpha" in VALID_MANIFEST; scope-matching is keyed by
    # policy.self_project, so point the policy at "alpha" for this test.
    manifest_with_scope = VALID_MANIFEST.replace(
        "    evidence: [FR-01]\n", "    evidence: [FR-01]\n    scope: src/alpha\n"
    )
    manifest_bytes = manifest_with_scope.encode("utf-8")
    report = _report(manifest_bytes, self_project="alpha", self_commit="deadbeef")
    bundle = _bundle_with_report(tmp_path, manifest_with_scope, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    from dataclasses import replace

    release_cfg = load_arch_policy(ARCH_POLICY_PATH, "release")
    policy = replace(release_cfg, self_project="alpha")
    git = FakeGitFacts(ancestors={"deadbeef"}, changed={"deadbeef": ["src/alpha/service.py"]})
    findings = check_arch_conformance(
        arch, policy, git, now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    )
    assert any("not provable" in f.message for f in findings)


def test_self_freshness_diff_touching_only_report_or_unmodelled_passes(
    tmp_path: Path,
) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(manifest_bytes, self_commit="deadbeef")
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "release")
    git = FakeGitFacts(
        ancestors={"deadbeef"},
        changed={
            "deadbeef": [
                "workstreams/WS-005-gate-verdicts/spec/conformance-report.json",
                "docs/some-unrelated-doc.md",
            ]
        },
    )
    findings = check_arch_conformance(
        arch, policy, git, now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    )
    assert findings == []


def test_self_freshness_dirty_true_fails(tmp_path: Path) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(manifest_bytes, self_commit="deadbeef", self_dirty=True)
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "release")
    git = FakeGitFacts(ancestors={"deadbeef"}, changed={"deadbeef": []})
    findings = check_arch_conformance(
        arch, policy, git, now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    )
    assert any("dirty" in f.message for f in findings)


def test_suppressed_findings_and_waived_elements_do_not_fire(tmp_path: Path) -> None:
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(
        manifest_bytes,
        self_commit="deadbeef",
        elements=[_element("I-01", "violation", waived_by="WAIVER-1")],
        findings=[_finding("missing-required-edge", element="I-02", suppressed_by="WAIVER-2")],
    )
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "release")
    git = FakeGitFacts(ancestors={"deadbeef"}, changed={"deadbeef": []})
    findings = check_arch_conformance(
        arch, policy, git, now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    )
    assert findings == []


def test_missing_injected_ancestor_facts_under_require_self_fresh_is_fail_closed_finding(
    tmp_path: Path,
) -> None:
    """D9 fail-closed route (owner-directed deviation from a raised exception):

    InjectedGitFacts.is_ancestor raises FactsError when the facts file never
    declared 'ancestors' at all. check_arch_conformance catches that and
    converts it into a blocking GC-ARCH-CONFORMANCE finding rather than
    letting the exception propagate/crash — freshness must be provable, not
    assumed, but an unprovable instrument is a finding, not a crash.
    """
    manifest_bytes = VALID_MANIFEST.encode("utf-8")
    report = _report(manifest_bytes, self_commit="deadbeef")
    bundle = _bundle_with_report(tmp_path, VALID_MANIFEST, report)
    arch = collect_arch_bundle(bundle)
    assert arch is not None
    policy = load_arch_policy(ARCH_POLICY_PATH, "release")
    git = InjectedGitFacts(frozenset(), {}, {})  # no ancestors/changed_paths_since key
    findings = check_arch_conformance(
        arch, policy, git, now=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    )
    assert len(findings) == 1
    assert findings[0].rule_id == "GC-ARCH-CONFORMANCE"
    assert "ancestors/changed_paths_since" in findings[0].message
