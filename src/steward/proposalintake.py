"""Admission check for an approved ProductProposal (steward#64, `proposal-intake`).

The product-governance contour (impresario) hands an engineering initiative to
steward as a bundle: ``proposal.yaml`` + ``decisions/*.yaml``. Admission is an
evidence check, never trust in a status field:

- the proposal must be schema-valid ``product-proposal/v1`` with
  ``status: approved``;
- for each required QG-5 gate (``qg5_business``, ``qg5_committee``) there must
  be an ACTIVE ``decision: approve`` GateDecision — one not overridden by any
  other record's ``supersedes`` chain — whose subject is exactly this proposal
  at a version that already existed (``subject.version <= proposal.version``).

Both schemas are vendored pinned copies (``contracts/impresario-*/v1``,
two-guarantees rule: copy-integrity is this repo's PR gate, upstream-drift is
a scheduled watch). Finding codes use the ``INTAKE-`` prefix deliberately:
``GC-*`` is a closed namespace minted only by the gate catalog (steward#62),
and admission findings are not gate-check verdicts.

YAML is parsed with timestamps kept as strings — the contract schemas type
timestamps as strings, and a second parser producing ``datetime`` is a known
cross-repo trap (impresario friction #10).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import yaml

_CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
_PROPOSAL_SCHEMA_PATH = _CONTRACTS / "impresario-product-proposal" / "v1" / "schema.json"
_DECISION_SCHEMA_PATH = _CONTRACTS / "impresario-gate-decision" / "v1" / "schema.json"

REQUIRED_GATES = ("qg5_business", "qg5_committee")

PROPOSAL_FILE = "proposal.yaml"
DECISIONS_DIR = "decisions"


class IntakeConfigError(Exception):
    """The instrument itself cannot run (bad bundle path, unreadable schema)."""


@dataclass(frozen=True)
class IntakeFinding:
    severity: str  # "error" | "warn"
    rule_id: str
    path: str
    message: str


@dataclass(frozen=True)
class IntakeResult:
    admitted: bool
    proposal_id: str | None
    proposal_version: int | None
    findings: tuple[IntakeFinding, ...]


# Subclasses SafeLoader: arbitrary-object tags stay rejected; the only change
# is that YAML timestamps are constructed as strings, matching the contracts.
class _PlainLoader(yaml.SafeLoader):
    """SafeLoader that keeps timestamps as strings (single-parser rule)."""


_PlainLoader.add_constructor(
    "tag:yaml.org,2002:timestamp",
    lambda loader, node: loader.construct_yaml_str(node),
)


def _load_schema(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntakeConfigError(f"vendored schema unreadable: {path}: {exc}") from exc


def _parse_yaml(path: Path) -> tuple[object | None, str | None]:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=_PlainLoader), None
    except (OSError, yaml.YAMLError) as exc:
        return None, str(exc)


def _schema_findings(doc: object, schema: dict, path: str, kind: str) -> list[IntakeFinding]:
    validator = jsonschema.Draft202012Validator(schema)
    return [
        IntakeFinding(
            "error",
            "INTAKE-SCHEMA",
            path,
            f"{kind} does not match {schema.get('$id', 'schema')}: {err.json_path}: {err.message}",
        )
        for err in sorted(validator.iter_errors(doc), key=lambda e: e.json_path)
    ]


def check_intake(bundle_dir: Path) -> IntakeResult:
    """Judge one proposal bundle. Findings with severity ``error`` reject it.

    Raises :class:`IntakeConfigError` when the check itself cannot run —
    distinct from a judged-and-rejected bundle.
    """
    if not bundle_dir.is_dir():
        raise IntakeConfigError(f"bundle directory not found: {bundle_dir}")
    proposal_schema = _load_schema(_PROPOSAL_SCHEMA_PATH)
    decision_schema = _load_schema(_DECISION_SCHEMA_PATH)

    findings: list[IntakeFinding] = []

    proposal_path = bundle_dir / PROPOSAL_FILE
    proposal, proposal_id, proposal_version = _judge_proposal(
        proposal_path, proposal_schema, findings
    )

    decisions = _load_decisions(bundle_dir / DECISIONS_DIR, decision_schema, findings)
    if proposal is not None and proposal_id is not None and proposal_version is not None:
        _judge_evidence(proposal_id, proposal_version, decisions, findings)

    admitted = not any(f.severity == "error" for f in findings)
    return IntakeResult(
        admitted=admitted,
        proposal_id=proposal_id,
        proposal_version=proposal_version,
        findings=tuple(findings),
    )


def _judge_proposal(
    proposal_path: Path, schema: dict, findings: list[IntakeFinding]
) -> tuple[dict | None, str | None, int | None]:
    rel = proposal_path.name
    if not proposal_path.is_file():
        findings.append(
            IntakeFinding(
                "error",
                "INTAKE-PROPOSAL-MISSING",
                rel,
                "bundle has no proposal.yaml — nothing to admit",
            )
        )
        return None, None, None
    doc, parse_error = _parse_yaml(proposal_path)
    if parse_error is not None:
        findings.append(IntakeFinding("error", "INTAKE-UNPARSEABLE", rel, parse_error))
        return None, None, None
    schema_findings = _schema_findings(doc, schema, rel, "proposal")
    findings.extend(schema_findings)
    if schema_findings or not isinstance(doc, dict):
        return None, None, None
    if doc["status"] != "approved":
        findings.append(
            IntakeFinding(
                "error",
                "INTAKE-NOT-APPROVED",
                rel,
                f"proposal status is '{doc['status']}', admission requires 'approved'",
            )
        )
    return doc, doc["proposal_id"], doc["version"]


def _load_decisions(
    decisions_dir: Path, schema: dict, findings: list[IntakeFinding]
) -> list[tuple[str, dict]]:
    """Parse and schema-validate every decision file; return the valid ones."""
    if not decisions_dir.is_dir():
        findings.append(
            IntakeFinding(
                "error",
                "INTAKE-DECISION-MISSING",
                DECISIONS_DIR,
                "bundle has no decisions/ directory — approval evidence is absent",
            )
        )
        return []
    valid: list[tuple[str, dict]] = []
    for path in sorted(decisions_dir.glob("*.yaml")):
        rel = f"{DECISIONS_DIR}/{path.name}"
        doc, parse_error = _parse_yaml(path)
        if parse_error is not None:
            findings.append(IntakeFinding("error", "INTAKE-UNPARSEABLE", rel, parse_error))
            continue
        schema_findings = _schema_findings(doc, schema, rel, "decision")
        findings.extend(schema_findings)
        if not schema_findings and isinstance(doc, dict):
            valid.append((rel, doc))
    ids = [doc["decision_id"] for _, doc in valid]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        # Ambiguous supersedes graph — fail closed rather than pick a record.
        findings.append(
            IntakeFinding(
                "error",
                "INTAKE-DUPLICATE-DECISION-ID",
                DECISIONS_DIR,
                f"decision_id {dup} occurs more than once; supersedes chain is ambiguous",
            )
        )
    return valid


def _judge_evidence(
    proposal_id: str,
    proposal_version: int,
    decisions: list[tuple[str, dict]],
    findings: list[IntakeFinding],
) -> None:
    subject_ref = f"proposal://{proposal_id}"
    superseded = {
        doc["supersedes"].removeprefix("gate-decision://")
        for _, doc in decisions
        if "supersedes" in doc
    }

    for rel, doc in decisions:
        if doc["subject"]["ref"] != subject_ref:
            findings.append(
                IntakeFinding(
                    "warn",
                    "INTAKE-FOREIGN-SUBJECT",
                    rel,
                    f"decision {doc['decision_id']} is about {doc['subject']['ref']}, "
                    f"not {subject_ref}; it is not admission evidence",
                )
            )

    for gate in REQUIRED_GATES:
        candidates = [
            (rel, doc)
            for rel, doc in decisions
            if doc["gate_id"] == gate
            and doc["decision"] == "approve"
            and doc["subject"]["ref"] == subject_ref
            and doc["decision_id"] not in superseded
        ]
        if not candidates:
            had_superseded = any(
                doc["gate_id"] == gate
                and doc["decision"] == "approve"
                and doc["subject"]["ref"] == subject_ref
                for _, doc in decisions
            )
            detail = (
                "an approve exists but is overridden by a supersedes chain"
                if had_superseded
                else "no approve decision references this proposal"
            )
            findings.append(
                IntakeFinding(
                    "error",
                    "INTAKE-DECISION-MISSING",
                    DECISIONS_DIR,
                    f"no active approve for {gate}: {detail}",
                )
            )
            continue
        for rel, doc in candidates:
            if doc["subject"]["version"] > proposal_version:
                findings.append(
                    IntakeFinding(
                        "error",
                        "INTAKE-VERSION-AHEAD",
                        rel,
                        f"decision {doc['decision_id']} approves {subject_ref} "
                        f"v{doc['subject']['version']}, but the proposal is only "
                        f"v{proposal_version} — evidence cannot postdate its subject",
                    )
                )
