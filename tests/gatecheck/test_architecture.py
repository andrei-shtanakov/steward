"""GC-ARCH-* gates: schema, evidence (Task 2) + conformance (Task 3)."""

from pathlib import Path

from steward.gatecheck.architecture import (
    collect_arch_bundle,
    check_arch_evidence,
    check_arch_schema,
)

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
