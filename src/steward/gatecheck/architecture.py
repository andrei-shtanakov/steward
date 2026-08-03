"""GC-ARCH-* gates: intended-architecture manifest + conformance evidence.

Active only when a bundle carries an ``intended-graph.yaml`` manifest
(:data:`ARCH_MANIFEST`) — team/team-exp profiles that opt into the
prograph-backed architecture workstream (WS-005 follow-on). ``lite`` bundles
without a manifest never produce these findings (:func:`collect_arch_bundle`
returns ``None``, gates inactive).

The manifest and its paired conformance report (:data:`ARCH_REPORT`, design
D2: co-located next to the manifest) are validated against the vendored
pinned schemas in ``contracts/prograph-intended-graph/v1`` and
``contracts/prograph-conformance-report/v1`` — steward is read-only toward
prograph; it consumes these as frozen contracts, never regenerates them.

This module is extended by Task 3 (GC-ARCH-CONFORMANCE + policy checks) and
wired into the CLI by Task 4.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import yaml

from steward.gatecheck.checks import Finding

__all__ = [
    "ARCH_MANIFEST",
    "ARCH_REPORT",
    "ArchBundle",
    "collect_arch_bundle",
    "check_arch_schema",
    "check_arch_evidence",
]

ARCH_MANIFEST = "intended-graph.yaml"
ARCH_REPORT = "conformance-report.json"

_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "contracts"
_MANIFEST_SCHEMA_PATH = _SCHEMA_DIR / "prograph-intended-graph" / "v1" / "schema.json"
_REPORT_SCHEMA_PATH = _SCHEMA_DIR / "prograph-conformance-report" / "v1" / "schema.json"

_EVIDENCE_COLLECTIONS = ("components", "interfaces", "constraints")


@dataclass(frozen=True)
class ArchBundle:
    """A collected intended-graph manifest, plus its paired conformance report."""

    manifest_rel: str  # bundle-relative POSIX path of the manifest
    manifest_bytes: bytes
    manifest_doc: object | None  # yaml.safe_load result; None when unparseable
    manifest_error: str | None
    report_rel: str | None  # None when the report file is absent
    report_bytes: bytes | None
    report_doc: object | None
    report_error: str | None


def _load_yaml(raw: bytes) -> tuple[object | None, str | None]:
    """Parse ``raw`` as YAML, capturing the error string on failure."""
    try:
        return yaml.safe_load(raw), None
    except yaml.YAMLError as err:
        return None, str(err)


def _load_json(raw: bytes) -> tuple[object | None, str | None]:
    """Parse ``raw`` as JSON, capturing the error string on failure."""
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as err:
        return None, str(err)


def collect_arch_bundle(spec_dir: Path) -> ArchBundle | None:
    """Find and load the intended-graph manifest (and its report) under ``spec_dir``.

    Returns ``None`` when no ``intended-graph.yaml`` exists anywhere under
    ``spec_dir`` — the GC-ARCH-* gates are inactive for this bundle. Nested
    bundle layouts are supported: the manifest is located via
    ``sorted(spec_dir.rglob(ARCH_MANIFEST))``, taking the first match when
    several exist. The conformance report must sit next to the manifest
    (co-located pair, design D2); it is optional (absent report is
    represented, not an error here).
    """
    matches = sorted(spec_dir.rglob(ARCH_MANIFEST))
    if not matches:
        return None
    manifest_path = matches[0]
    manifest_rel = manifest_path.relative_to(spec_dir).as_posix()
    manifest_bytes = manifest_path.read_bytes()
    manifest_doc, manifest_error = _load_yaml(manifest_bytes)

    report_path = manifest_path.parent / ARCH_REPORT
    report_rel: str | None = None
    report_bytes: bytes | None = None
    report_doc: object | None = None
    report_error: str | None = None
    if report_path.is_file():
        report_rel = report_path.relative_to(spec_dir).as_posix()
        report_bytes = report_path.read_bytes()
        report_doc, report_error = _load_json(report_bytes)

    return ArchBundle(
        manifest_rel=manifest_rel,
        manifest_bytes=manifest_bytes,
        manifest_doc=manifest_doc,
        manifest_error=manifest_error,
        report_rel=report_rel,
        report_bytes=report_bytes,
        report_doc=report_doc,
        report_error=report_error,
    )


def _load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_json_types(doc: object) -> object:
    return json.loads(json.dumps(doc, default=str))


def check_arch_schema(arch: ArchBundle) -> list[Finding]:
    """GC-ARCH-SCHEMA: the manifest must parse and match the intended-graph schema.

    Unparseable YAML is one finding. Otherwise the manifest is canonicalized
    to plain JSON types and validated against the vendored
    ``intended-graph/v1`` schema with ``jsonschema.Draft202012Validator``;
    every validation error becomes one finding, sorted by JSON path.
    """
    if arch.manifest_error is not None:
        return [
            Finding(
                "error",
                "GC-ARCH-SCHEMA",
                arch.manifest_rel,
                f"unparseable YAML: {arch.manifest_error}",
            )
        ]
    validator = jsonschema.Draft202012Validator(_load_schema(_MANIFEST_SCHEMA_PATH))
    errors = sorted(
        validator.iter_errors(_as_json_types(arch.manifest_doc)),
        key=lambda e: list(map(str, e.absolute_path)),
    )
    return [
        Finding(
            "error",
            "GC-ARCH-SCHEMA",
            arch.manifest_rel,
            f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}",
        )
        for e in errors
    ]


def check_arch_evidence(arch: ArchBundle) -> list[Finding]:
    """GC-ARCH-EVIDENCE: every component/interface/constraint must cite evidence.

    For each entry in ``components`` / ``interfaces`` / ``constraints``, the
    ``evidence`` key must be present, a list, and non-empty — else one
    finding naming the element's ``id`` (or its index when ``id`` is absent).
    Skips silently when the manifest doc is not a dict, or when it failed
    schema-level parsing (GC-ARCH-SCHEMA already reported it).
    """
    if arch.manifest_error is not None or not isinstance(arch.manifest_doc, dict):
        return []

    findings: list[Finding] = []
    for collection_name in _EVIDENCE_COLLECTIONS:
        entries = arch.manifest_doc.get(collection_name)
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            label = entry.get("id", f"{collection_name}[{index}]")
            evidence = entry.get("evidence")
            if isinstance(evidence, list) and evidence:
                continue
            findings.append(
                Finding(
                    "error",
                    "GC-ARCH-EVIDENCE",
                    arch.manifest_rel,
                    f"{label}: missing or empty evidence",
                )
            )
    return findings
