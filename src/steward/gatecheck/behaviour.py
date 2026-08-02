"""Behaviour-spec checks: GC-BEH-TRACE / GC-BEH-COVERAGE / GC-CHECK-PLANNED.

Phase 1 slice of the behaviour-architecture-lifecycle ADR (2026-08-02); the body
and frontmatter formats below were rehearsed by its manual golden run on WS-005.
Both live in the dev-only coordination workspace — pointers in ``TODO.md`` §6b;
runtime code neither reads nor resolves them. Active only when the profile carries
a ``behaviour-spec`` node (``team-exp``); ``team``/``lite`` bundles never produce
these findings.

Normative body format (mirrors the golden run):

- Requirement definitions in the upstream artifact: ``#### FR-01: Title`` headings
  (also matches ``NFR-NN``); the block until the next definition may carry a
  ``**Priority**: 🔴 Must`` line (Must | Should | Could | Won't).
- Scenario definitions: ``#### BEH-01: Title`` headings with an inline
  `` `traces: [FR-01, NFR-02]` `` code span on the heading line or inside the block.
- Check binding, one line inside the scenario block, all fields as code spans::

      - **checked_by**: `status: planned` `kind: e2e` `owner: @qa` `target: tests/x.py::t`

  ``status: planned`` needs ``kind``/``owner``/``target``; ``materialized`` needs
  ``ref``; ``waived`` needs ``reason``. Two-stage gate (ADR D2): ``planned`` is
  enough before compile-down; GC-CHECK-READY (workstream/release stage) is out of
  this slice.
- Frontmatter, structural coverage (FR → ARCH-constraint → verification obligation
  chain, FL-03 as refined at golden-run acceptance) and waivers::

      structural_coverage:
        - fr: FR-02
          constraint: ARCH-C1
          obligation: {detector: import, expected_verdict: conformant,
                       owner_role: "@architects", release_gate: block}
      coverage_waivers:
        - {fr: FR-07, reason: why this Must/Should FR is intentionally uncovered}

  A constraint alone is NOT coverage: the obligation must name a detector plus its
  expected conformance verdict — or, for the permanently-unknown ``manual-evidence``
  detector, an ``evidence_target`` — plus ``owner_role`` and ``release_gate``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from steward._vendor.spec_meta import split_frontmatter
from steward.gatecheck.checks import Artifact, Finding
from steward.graph import SpecGraph

__all__ = [
    "Scenario",
    "check_behaviour_spec",
    "parse_coverage_declarations",
    "parse_priorities",
    "parse_scenarios",
]

BEHAVIOUR_NODE = "behaviour-spec"

_BLOCKING_PRIORITY = "Must"
_COVERED_PRIORITIES = ("Must", "Should")
_CHECK_KINDS = frozenset({"atp", "contract", "integration", "e2e", "manual"})
_CHECK_STATUSES = frozenset({"planned", "materialized", "waived"})
# Fields that make a checked_by binding complete, per status (ADR D2 two-stage gate).
_REQUIRED_CHECK_FIELDS = {
    "planned": ("kind", "owner", "target"),
    "materialized": ("kind", "owner", "ref"),
    "waived": ("reason",),
}
_MANUAL_EVIDENCE_DETECTOR = "manual-evidence"

_DEF_RE = re.compile(r"(?m)^####\s+((?:N?FR|BEH)-\d+)\b")
_PRIORITY_RE = re.compile(r"\*\*Priority\*\*:[^\n]*?\b(Must|Should|Could|Won't)\b")
_TRACES_RE = re.compile(r"`traces:\s*\[([^\]`]*)\]`")
_CHECKED_BY_RE = re.compile(r"(?m)^-\s+\*\*checked_by\*\*:([^\n]*)")
_CHECK_FIELD_RE = re.compile(r"`([a-z_]+):\s*([^`]+)`")


@dataclass(frozen=True)
class Scenario:
    """One parsed BEH entry."""

    beh_id: str
    traces: tuple[str, ...]
    checked_by: dict[str, str] = field(default_factory=dict)
    has_checked_by: bool = False


def check_behaviour_spec(graph: SpecGraph, artifacts: list[Artifact]) -> list[Finding]:
    """Run the three behaviour gates over the bundle's behaviour-spec artifact.

    Skips silently when the profile has no behaviour-spec node, or when the
    behaviour-spec / upstream requirements artifact is absent — completeness
    (GC-COMPLETENESS) already errors on missing required nodes.
    """
    node = graph.nodes.get(BEHAVIOUR_NODE)
    if node is None:
        return []
    present = {a.node_id: a for a in artifacts if a.node_id is not None}
    behaviour = present.get(BEHAVIOUR_NODE)
    if behaviour is None:
        return []
    upstream_texts = [present[up].text for up in node.upstream if up in present]
    if not upstream_texts:
        return []

    scenarios = parse_scenarios(behaviour.text)
    priorities = parse_priorities(upstream_texts)
    findings: list[Finding] = []
    findings.extend(_check_trace(behaviour, scenarios, priorities))
    findings.extend(_check_coverage(behaviour, scenarios, priorities))
    findings.extend(_check_planned(behaviour, scenarios, priorities))
    return findings


def _check_trace(
    behaviour: Artifact, scenarios: list[Scenario], priorities: dict[str, str]
) -> list[Finding]:
    """GC-BEH-TRACE: every scenario traces ≥1 FR/NFR *defined* upstream.

    A definition is a ``#### FR-NN``/``#### NFR-NN`` heading; an incidental
    mention of the id in upstream prose does not satisfy the trace.
    """
    findings = []
    for scenario in scenarios:
        if not scenario.traces:
            findings.append(
                Finding(
                    "error",
                    "GC-BEH-TRACE",
                    behaviour.path,
                    f"{scenario.beh_id} carries no `traces: [...]` link to an FR/NFR",
                )
            )
            continue
        for ref in scenario.traces:
            if ref not in priorities:
                findings.append(
                    Finding(
                        "error",
                        "GC-BEH-TRACE",
                        behaviour.path,
                        f"{scenario.beh_id} traces {ref!r}, which no upstream artifact "
                        "defines (definitions are `#### FR-NN`/`#### NFR-NN` headings)",
                    )
                )
    return findings


def _check_coverage(
    behaviour: Artifact, scenarios: list[Scenario], priorities: dict[str, str]
) -> list[Finding]:
    """GC-BEH-COVERAGE: every Must/Should FR is covered by BEH, obligation chain, or waiver."""
    findings: list[Finding] = []
    traced = {ref for scenario in scenarios for ref in scenario.traces}
    structural, waived, front_findings = _parse_frontmatter_coverage(behaviour)
    findings.extend(front_findings)
    for fr_id, priority in priorities.items():
        if priority not in _COVERED_PRIORITIES or not fr_id.startswith("FR-"):
            continue
        if fr_id in traced or fr_id in structural or fr_id in waived:
            continue
        findings.append(
            Finding(
                "error",
                "GC-BEH-COVERAGE",
                behaviour.path,
                f"{priority} requirement {fr_id} is covered by no BEH scenario, "
                "no structural_coverage obligation chain, and no coverage_waiver",
            )
        )
    return findings


def _check_planned(
    behaviour: Artifact, scenarios: list[Scenario], priorities: dict[str, str]
) -> list[Finding]:
    """GC-CHECK-PLANNED: blocking scenarios carry a complete checked_by binding.

    Blocking = the scenario traces a Must-priority requirement — FR *or* NFR:
    a Must NFR's scenario losing its check binding is exactly the fail-closed
    hole this gate exists to keep shut (golden run BEH-08/NFR-02 is one).
    """
    findings = []
    for scenario in scenarios:
        if not any(priorities.get(ref) == _BLOCKING_PRIORITY for ref in scenario.traces):
            continue
        if not scenario.has_checked_by:
            findings.append(
                Finding(
                    "error",
                    "GC-CHECK-PLANNED",
                    behaviour.path,
                    f"blocking scenario {scenario.beh_id} (traces a Must-priority "
                    "requirement) has no **checked_by** binding",
                )
            )
            continue
        findings.extend(_binding_findings(behaviour, scenario))
    return findings


def _binding_findings(behaviour: Artifact, scenario: Scenario) -> list[Finding]:
    binding = scenario.checked_by
    status = binding.get("status")
    if status not in _CHECK_STATUSES:
        return [
            Finding(
                "error",
                "GC-CHECK-PLANNED",
                behaviour.path,
                f"{scenario.beh_id} checked_by status must be one of "
                f"{', '.join(sorted(_CHECK_STATUSES))}; got {status!r}",
            )
        ]
    findings = []
    missing = [key for key in _REQUIRED_CHECK_FIELDS[status] if not binding.get(key)]
    if missing:
        findings.append(
            Finding(
                "error",
                "GC-CHECK-PLANNED",
                behaviour.path,
                f"{scenario.beh_id} checked_by ({status}) is missing: {', '.join(missing)}",
            )
        )
    kind = binding.get("kind")
    if kind is not None and kind not in _CHECK_KINDS:
        findings.append(
            Finding(
                "error",
                "GC-CHECK-PLANNED",
                behaviour.path,
                f"{scenario.beh_id} checked_by kind must be one of "
                f"{', '.join(sorted(_CHECK_KINDS))}; got {kind!r}",
            )
        )
    return findings


def parse_scenarios(text: str) -> list[Scenario]:
    scenarios = []
    for beh_id, block in _definition_blocks(text):
        if not beh_id.startswith("BEH-"):
            continue
        traces_match = _TRACES_RE.search(block)
        traces = ()
        if traces_match:
            traces = tuple(ref.strip() for ref in traces_match.group(1).split(",") if ref.strip())
        checked_match = _CHECKED_BY_RE.search(block)
        checked_by: dict[str, str] = {}
        if checked_match:
            checked_by = {
                key: value.strip() for key, value in _CHECK_FIELD_RE.findall(checked_match.group(1))
            }
        scenarios.append(
            Scenario(
                beh_id=beh_id,
                traces=traces,
                checked_by=checked_by,
                has_checked_by=checked_match is not None,
            )
        )
    return scenarios


def parse_priorities(upstream_texts: list[str]) -> dict[str, str]:
    """Map every upstream FR/NFR definition to its priority ('' when none stated)."""
    priorities: dict[str, str] = {}
    for text in upstream_texts:
        for def_id, block in _definition_blocks(text):
            if def_id.startswith("BEH-"):
                continue
            match = _PRIORITY_RE.search(block)
            priorities[def_id] = match.group(1) if match else ""
    return priorities


def _definition_blocks(text: str) -> list[tuple[str, str]]:
    """Split text into (definition id, block-until-next-definition) pairs."""
    matches = list(_DEF_RE.finditer(text))
    blocks = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append((match.group(1), text[match.start() : end]))
    return blocks


def parse_coverage_declarations(text: str) -> tuple[list[dict], list[dict]]:
    """Return the *valid* structural_coverage and coverage_waivers frontmatter entries.

    Invalid entries (a broken obligation chain, a waiver without a reason) are
    dropped here; :func:`check_behaviour_spec` is where they become findings.
    Used by the derived trace matrix, which only reports what actually counts.
    """
    meta_dict, _ = split_frontmatter(text)
    if meta_dict is None:
        return [], []
    structural = [
        entry
        for entry in _entry_list(meta_dict, "structural_coverage")
        if _obligation_problem(entry) is None
    ]
    waivers = [
        entry
        for entry in _entry_list(meta_dict, "coverage_waivers")
        if isinstance(entry.get("fr"), str) and isinstance(entry.get("reason"), str)
    ]
    return structural, waivers


def _parse_frontmatter_coverage(
    behaviour: Artifact,
) -> tuple[set[str], set[str], list[Finding]]:
    """Parse structural_coverage / coverage_waivers; invalid entries never count."""
    meta_dict, _ = split_frontmatter(behaviour.text)
    if meta_dict is None:
        return set(), set(), []
    findings: list[Finding] = []

    structural: set[str] = set()
    for entry in _entry_list(meta_dict, "structural_coverage"):
        problem = _obligation_problem(entry)
        if problem is None:
            structural.add(str(entry["fr"]))
        else:
            findings.append(
                Finding(
                    "error",
                    "GC-BEH-COVERAGE",
                    behaviour.path,
                    f"structural_coverage entry {entry.get('fr', '?')!r} does not count "
                    f"as coverage: {problem} (a constraint without a verification "
                    "obligation is not coverage)",
                )
            )

    waived: set[str] = set()
    for entry in _entry_list(meta_dict, "coverage_waivers"):
        if isinstance(entry.get("fr"), str) and isinstance(entry.get("reason"), str):
            waived.add(entry["fr"])
        else:
            findings.append(
                Finding(
                    "error",
                    "GC-BEH-COVERAGE",
                    behaviour.path,
                    f"coverage_waivers entry {entry.get('fr', '?')!r} needs both "
                    "'fr' and a non-empty 'reason'",
                )
            )
    return structural, waived, findings


def _entry_list(meta_dict: dict, key: str) -> list[dict]:
    raw = meta_dict.get(key)
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def _obligation_problem(entry: dict) -> str | None:
    """Validate the FR → constraint → obligation chain; return the defect or None."""
    if not isinstance(entry.get("fr"), str):
        return "missing 'fr'"
    if not isinstance(entry.get("constraint"), str):
        return "missing 'constraint'"
    obligation = entry.get("obligation")
    if not isinstance(obligation, dict):
        return "missing 'obligation'"
    for required in ("detector", "owner_role", "release_gate"):
        if not isinstance(obligation.get(required), str):
            return f"obligation is missing {required!r}"
    detector = obligation["detector"]
    if detector == _MANUAL_EVIDENCE_DETECTOR:
        if not isinstance(obligation.get("evidence_target"), str):
            return (
                "a manual-evidence detector reports 'unknown' forever, so the "
                "obligation must name an 'evidence_target'"
            )
    elif not isinstance(obligation.get("expected_verdict"), str):
        return "obligation is missing 'expected_verdict' for its detector"
    return None
