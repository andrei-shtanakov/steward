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

GC-ARCH-CONFORMANCE (Task 3) consumes the co-located conformance report as
frozen evidence too: a mandatory core (report present, parseable, schema-
valid, matches the manifest by sha256, snapshot marked complete) gates a
declarative stage policy (:data:`profiles/arch-policy.yaml`, D4) that decides
which report findings/verdicts/unknowns block, plus self-freshness (D9,
ancestor + path-scoped diff via the :class:`~steward.gatecheck.git_facts.GitFacts`
extension) and snapshot age. It is wired into the CLI by Task 4.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import yaml

from steward.gatecheck.checks import Finding
from steward.gatecheck.git_facts import FactsError, GitFacts

__all__ = [
    "ARCH_MANIFEST",
    "ARCH_REPORT",
    "FINDING_CLASSES",
    "VERDICTS",
    "UNKNOWN_REASONS",
    "ArchBundle",
    "ArchPolicy",
    "ArchPolicyError",
    "collect_arch_bundle",
    "check_arch_schema",
    "check_arch_evidence",
    "check_arch_conformance",
    "load_arch_policy",
]

ARCH_MANIFEST = "intended-graph.yaml"
ARCH_REPORT = "conformance-report.json"

_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "contracts"
_MANIFEST_SCHEMA_PATH = _SCHEMA_DIR / "prograph-intended-graph" / "v1" / "schema.json"
_REPORT_SCHEMA_PATH = _SCHEMA_DIR / "prograph-conformance-report" / "v1" / "schema.json"

_EVIDENCE_COLLECTIONS = ("components", "interfaces", "constraints")

# prograph's closed vocabulary (verbatim from contracts/prograph-conformance-report/v1
# and design D4) — policy validation rejects anything outside these sets.
FINDING_CLASSES: frozenset[str] = frozenset(
    {
        "missing-required-edge",
        "forbidden-edge",
        "undeclared-edge",
        "orphan-component",
        "expired-waiver",
        "manual-obligation",
    }
)
VERDICTS: frozenset[str] = frozenset({"conformant", "violation", "unknown"})
UNKNOWN_REASONS: frozenset[str | None] = frozenset(
    {
        "manual-evidence",
        "unsupported-resolution",
        "outside-workspace",
        "orphan-component",
        None,
    }
)


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
    """Parse ``raw`` as JSON, capturing the error string on failure.

    ``json.loads`` decodes ``bytes`` itself and raises ``UnicodeDecodeError``
    (not ``JSONDecodeError``) on non-UTF-8 input — a non-UTF-8 conformance
    report must become a mandatory-core error finding, not an uncaught crash.
    """
    try:
        return json.loads(raw), None
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
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


@dataclass(frozen=True)
class ArchPolicy:
    """Declarative GC-ARCH-CONFORMANCE stage policy (design D4).

    Loaded from ``profiles/arch-policy.yaml`` for a given stage
    (``authoring`` / ``release``, selected via ``--arch-stage``). All
    vocabulary fields are validated at load time against prograph's closed
    vocabulary (:data:`FINDING_CLASSES`, :data:`VERDICTS`,
    :data:`UNKNOWN_REASONS`) — unknown entries are a config error, not a
    silently-ignored typo.
    """

    self_project: str | None
    fail_on_findings: frozenset[str]
    fail_on_verdicts: frozenset[str]
    unknown_allowed_reasons: frozenset[str | None]
    unknown_allowed_elements: frozenset[str]
    require_self_fresh: bool
    max_snapshot_age_hours: int | None


class ArchPolicyError(Exception):
    """Malformed or invalid arch-policy YAML (config-level error, exit 2)."""


def _validate_vocabulary(
    raw: object, vocabulary: frozenset[str | None], path: Path, field: str
) -> list[object]:
    """Check ``raw`` is a list whose entries all belong to ``vocabulary``."""
    if not isinstance(raw, list):
        raise ArchPolicyError(f"arch policy {path}: {field!r} must be a list")
    for entry in raw:
        if entry not in vocabulary:
            allowed = sorted((v for v in vocabulary if v is not None), key=str)
            suffix = " (or null)" if None in vocabulary else ""
            raise ArchPolicyError(
                f"arch policy {path}: {field} entry {entry!r} not in closed "
                f"vocabulary {allowed}{suffix}"
            )
    return raw


def load_arch_policy(path: Path, stage: str) -> ArchPolicy:
    """Load and validate the stage policy at ``path`` for ``stage``.

    Raises :class:`ArchPolicyError` on unreadable/malformed YAML, an unknown
    stage, unknown finding classes / verdicts / unknown-reasons (validated
    against the closed prograph vocabulary — ``None``/YAML ``null`` is a
    legitimate entry in ``unknown_policy.allowed_reasons`` only), or a
    negative ``max_snapshot_age_hours``. The CLI maps this to exit 2.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise ArchPolicyError(f"cannot read arch policy file {path}: {err}") from err
    try:
        doc = yaml.safe_load(raw_text)
    except yaml.YAMLError as err:
        raise ArchPolicyError(f"malformed arch policy YAML in {path}: {err}") from err
    if not isinstance(doc, dict):
        raise ArchPolicyError(f"arch policy {path}: top level must be a mapping")

    self_project = doc.get("self_project")
    if self_project is not None and not isinstance(self_project, str):
        raise ArchPolicyError(f"arch policy {path}: 'self_project' must be a string or null")

    stages = doc.get("stages")
    if not isinstance(stages, dict):
        raise ArchPolicyError(f"arch policy {path}: 'stages' must be a mapping")
    if stage not in stages:
        raise ArchPolicyError(
            f"arch policy {path}: unknown stage {stage!r} (known: {sorted(stages)})"
        )
    cfg = stages[stage]
    if not isinstance(cfg, dict):
        raise ArchPolicyError(f"arch policy {path}: stage {stage!r} must be a mapping")

    fail_on_findings = frozenset(
        _validate_vocabulary(
            cfg.get("fail_on_findings", []), FINDING_CLASSES, path, "fail_on_findings"
        )
    )
    fail_on_verdicts = frozenset(
        _validate_vocabulary(cfg.get("fail_on_verdicts", []), VERDICTS, path, "fail_on_verdicts")
    )

    unknown_policy = cfg.get("unknown_policy", {})
    if not isinstance(unknown_policy, dict):
        raise ArchPolicyError(
            f"arch policy {path}: stage {stage!r} 'unknown_policy' must be a mapping"
        )
    unknown_allowed_reasons = frozenset(
        _validate_vocabulary(
            unknown_policy.get("allowed_reasons", []),
            UNKNOWN_REASONS,
            path,
            "unknown_policy.allowed_reasons",
        )
    )
    allowed_elements_raw = unknown_policy.get("allowed_elements", [])
    if not isinstance(allowed_elements_raw, list) or not all(
        isinstance(e, str) for e in allowed_elements_raw
    ):
        raise ArchPolicyError(
            f"arch policy {path}: stage {stage!r} "
            "'unknown_policy.allowed_elements' must be a list of strings"
        )

    require_self_fresh = cfg.get("require_self_fresh", False)
    if not isinstance(require_self_fresh, bool):
        raise ArchPolicyError(
            f"arch policy {path}: stage {stage!r} 'require_self_fresh' must be a bool"
        )

    max_snapshot_age_hours = cfg.get("max_snapshot_age_hours")
    if max_snapshot_age_hours is not None:
        if not isinstance(max_snapshot_age_hours, int) or isinstance(max_snapshot_age_hours, bool):
            raise ArchPolicyError(
                f"arch policy {path}: stage {stage!r} 'max_snapshot_age_hours' "
                "must be an int or null"
            )
        if max_snapshot_age_hours < 0:
            raise ArchPolicyError(
                f"arch policy {path}: stage {stage!r} 'max_snapshot_age_hours' must not be negative"
            )

    return ArchPolicy(
        self_project=self_project,
        fail_on_findings=fail_on_findings,
        fail_on_verdicts=fail_on_verdicts,
        unknown_allowed_reasons=unknown_allowed_reasons,
        unknown_allowed_elements=frozenset(allowed_elements_raw),
        require_self_fresh=require_self_fresh,
        max_snapshot_age_hours=max_snapshot_age_hours,
    )


def _check_report_schema(report_doc: object, report_rel: str) -> list[Finding]:
    """Validate ``report_doc`` against the vendored conformance-report/v1 schema."""
    validator = jsonschema.Draft202012Validator(_load_schema(_REPORT_SCHEMA_PATH))
    errors = sorted(
        validator.iter_errors(_as_json_types(report_doc)),
        key=lambda e: list(map(str, e.absolute_path)),
    )
    return [
        Finding(
            "error",
            "GC-ARCH-CONFORMANCE",
            report_rel,
            f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}",
        )
        for e in errors
    ]


def _check_findings_policy(report: dict, policy: ArchPolicy, report_rel: str) -> list[Finding]:
    findings: list[Finding] = []
    for entry in report.get("findings", []):
        if entry["class"] in policy.fail_on_findings and entry["suppressed_by"] is None:
            element = entry.get("element") or "<system>"
            findings.append(
                Finding(
                    "error",
                    "GC-ARCH-CONFORMANCE",
                    report_rel,
                    f"{element}: finding class {entry['class']!r} blocked by stage "
                    f"policy — {entry['detail']}",
                )
            )
    return findings


def _check_verdicts_policy(report: dict, policy: ArchPolicy, report_rel: str) -> list[Finding]:
    findings: list[Finding] = []
    for element in report.get("elements", []):
        if element["verdict"] in policy.fail_on_verdicts and element["waived_by"] is None:
            findings.append(
                Finding(
                    "error",
                    "GC-ARCH-CONFORMANCE",
                    report_rel,
                    f"{element['id']}: verdict {element['verdict']!r} blocked by stage policy",
                )
            )
    return findings


def _check_unknown_policy(report: dict, policy: ArchPolicy, report_rel: str) -> list[Finding]:
    findings: list[Finding] = []
    for element in report.get("elements", []):
        if element["verdict"] != "unknown" or element["waived_by"] is not None:
            continue
        reason = element["reason"]
        if (
            reason in policy.unknown_allowed_reasons
            or element["id"] in policy.unknown_allowed_elements
        ):
            continue
        findings.append(
            Finding(
                "error",
                "GC-ARCH-CONFORMANCE",
                report_rel,
                f"{element['id']}: unknown verdict with reason {reason!r} not "
                "permitted by stage policy (add to unknown_policy.allowed_reasons "
                "or allowed_elements in profiles/arch-policy.yaml)",
            )
        )
    return findings


def _under_scope(path: str, scope: str) -> bool:
    scope = scope.rstrip("/")
    return path == scope or path.startswith(f"{scope}/")


def _modelled_scopes(manifest_doc: object, self_project: str) -> frozenset[str]:
    if not isinstance(manifest_doc, dict):
        return frozenset()
    components = manifest_doc.get("components")
    if not isinstance(components, list):
        return frozenset()
    return frozenset(
        component["scope"]
        for component in components
        if isinstance(component, dict)
        and component.get("project") == self_project
        and isinstance(component.get("scope"), str)
    )


def _check_self_fresh(
    arch: ArchBundle, report: dict, policy: ArchPolicy, git: GitFacts, report_rel: str
) -> list[Finding]:
    """D9: self-freshness = ancestor commit + a diff that spares the modelled surface.

    A committed report can never satisfy ``commit == HEAD`` (committing the
    report itself moves HEAD past the provenance commit). Instead the
    provenance commit must be an ancestor of HEAD, ``dirty`` must be false,
    and nothing in ``commit..HEAD`` may touch the manifest file
    (``report["manifest"]["path"]``, the repo-relative path the producer
    recorded) or any ``scope`` of a manifest component belonging to
    ``self_project`` (the manifest's own scopes define the modelled surface —
    no second registry).
    """
    self_project = policy.self_project
    assert self_project is not None  # guarded by the caller
    label = f"self-freshness ({self_project})"

    def _unprovable(detail: str) -> list[Finding]:
        return [
            Finding(
                "error",
                "GC-ARCH-CONFORMANCE",
                report_rel,
                f"{label}: self freshness not provable ⇒ unknown, not clean — {detail}",
            )
        ]

    projects = report.get("projects")
    proj = projects.get(self_project) if isinstance(projects, dict) else None
    if not isinstance(proj, dict) or proj.get("commit") is None or proj.get("dirty") is not False:
        return _unprovable(
            f"projects[{self_project!r}] missing, has a null commit, or dirty is not false"
        )
    commit = proj["commit"]

    try:
        is_ancestor = git.is_ancestor(commit)
        changed_paths = git.changed_paths_since(commit) if is_ancestor else []
    except FactsError as err:
        return [
            Finding(
                "error",
                "GC-ARCH-CONFORMANCE",
                report_rel,
                "self freshness not provable under --no-fs: facts file lacks "
                f"ancestors/changed_paths_since ({err})",
            )
        ]

    if not is_ancestor:
        return _unprovable(f"provenance commit {commit!r} is not an ancestor of HEAD")

    manifest_path = report.get("manifest", {}).get("path")
    scopes = _modelled_scopes(arch.manifest_doc, self_project)
    for changed_path in changed_paths:
        if changed_path == manifest_path or any(
            _under_scope(changed_path, scope) for scope in scopes
        ):
            return _unprovable(f"{changed_path} changed since provenance commit {commit!r}")
    return []


def _check_snapshot_age(
    report: dict, policy: ArchPolicy, report_rel: str, now: dt.datetime | None
) -> list[Finding]:
    if policy.max_snapshot_age_hours is None:
        return []
    indexed_at_raw = report.get("snapshot", {}).get("indexed_at")
    try:
        indexed_at = dt.datetime.strptime(indexed_at_raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except (TypeError, ValueError) as err:
        return [
            Finding(
                "error",
                "GC-ARCH-CONFORMANCE",
                report_rel,
                f"snapshot.indexed_at unparseable: {err}",
            )
        ]
    current = now if now is not None else dt.datetime.now(dt.timezone.utc)
    age_hours = (current - indexed_at).total_seconds() / 3600
    if age_hours > policy.max_snapshot_age_hours:
        return [
            Finding(
                "error",
                "GC-ARCH-CONFORMANCE",
                report_rel,
                f"snapshot age {age_hours:.1f}h exceeds max_snapshot_age_hours="
                f"{policy.max_snapshot_age_hours} (indexed_at={indexed_at_raw})",
            )
        ]
    return []


def check_arch_conformance(
    arch: ArchBundle,
    policy: ArchPolicy,
    git: GitFacts,
    *,
    now: dt.datetime | None = None,
) -> list[Finding]:
    """GC-ARCH-CONFORMANCE: consume the co-located conformance report.

    Mandatory core (always on, any stage, per D3) runs first: report
    present, parseable JSON, schema-valid, ``manifest.sha256`` matches
    ``arch.manifest_bytes``, and ``snapshot.complete is True``. Any failure
    there is an error finding and the stage-policy checks below are skipped
    (garbage in, no point). The stage policy (D4) then applies
    ``fail_on_findings`` / ``fail_on_verdicts`` / the unknown-verdict
    allowlists, D9 self-freshness (when ``require_self_fresh`` and
    ``self_project`` are set), and ``max_snapshot_age_hours``.
    """
    report_rel = arch.report_rel
    if report_rel is None:
        return [
            Finding(
                "error",
                "GC-ARCH-CONFORMANCE",
                arch.manifest_rel,
                "manifest present but no co-located conformance-report.json — commit "
                "the evidence in the same PR (see "
                "docs/plans/2026-08-03-arch-gates-slice-pr3.md)",
            )
        ]
    if arch.report_error is not None:
        return [
            Finding(
                "error",
                "GC-ARCH-CONFORMANCE",
                report_rel,
                f"unparseable JSON: {arch.report_error}",
            )
        ]

    schema_findings = _check_report_schema(arch.report_doc, report_rel)
    if schema_findings:
        return schema_findings

    report = arch.report_doc
    assert isinstance(report, dict)  # schema validation above guarantees this

    manifest_sha256 = hashlib.sha256(arch.manifest_bytes).hexdigest()
    report_sha256 = report["manifest"]["sha256"]
    if report_sha256 != manifest_sha256:
        return [
            Finding(
                "error",
                "GC-ARCH-CONFORMANCE",
                report_rel,
                "stale evidence: report.manifest.sha256 "
                f"({report_sha256}) != manifest sha256 ({manifest_sha256})",
            )
        ]

    if report["snapshot"]["complete"] is not True:
        return [
            Finding(
                "error",
                "GC-ARCH-CONFORMANCE",
                report_rel,
                "snapshot.complete is not true — incomplete index, evidence not trustworthy",
            )
        ]

    findings: list[Finding] = []
    findings.extend(_check_findings_policy(report, policy, report_rel))
    findings.extend(_check_verdicts_policy(report, policy, report_rel))
    findings.extend(_check_unknown_policy(report, policy, report_rel))
    if policy.require_self_fresh and policy.self_project is not None:
        findings.extend(_check_self_fresh(arch, report, policy, git, report_rel))
    findings.extend(_check_snapshot_age(report, policy, report_rel, now))
    return findings
