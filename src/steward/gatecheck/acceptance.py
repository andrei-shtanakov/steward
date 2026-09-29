"""Acceptance completeness for charter schema 2: strict AC grammar + no orphans.

steward#190 PR-2 — the steward side of the devtools bundle-criteria oracle
(spec rev 10 §1.7; devtools ``governance/acceptance_guard.py`` carries the same
AC grammar): every BEH except Won't sits in the ``scenarios`` of at least one
non-Won't AC. An orphan is a BEH outside all AC, or one only Won't AC name.

Boundary (owner ruling 2026-09-29): the charter's ``schema`` decides, never the
presence of the grammar. Schema 2 is checked in full — an acceptance artifact
without a single well-formed AC is an error, not a reason to skip. Schema 1 is
not checked, and :func:`acceptance_skip_reason` names the skip so the CLI can
declare it: a silent zero would read as "no orphans". ``schema`` absent means 1;
``2`` and ``"2"`` both mean 2 (as devtools ``charter_guard`` reads it); anything
else is a GC-META error, never a quiet fallback to schema 1.

Normative AC format (devtools acceptance-node spec; prose follows the fields)::

    #### AC-01: Title · verification: test|manual|metric
    traces: [FR-01, NFR-02]
    scenarios: [BEH-01, BEH-02]
    <prose: the observable sign of the criterion>

An AC's fields are exactly the field lines *directly under its heading* (blank
lines between them allowed, as devtools accepts), each at most once. A ``traces:``/``scenarios:`` line anywhere else belongs to no AC and
is a finding — so a field line is never credited to a foreign AC, whatever prose
(a non-grammar AC entry, a reference list) stands between them.

``verification: test`` needs a non-empty ``scenarios``. A BEH's or AC's priority
is the maximum over the requirements it traces; it is Won't only when every
traced requirement is Won't. Findings reuse GC-BEH-COVERAGE (behaviour covered
by acceptance) — no new id in the vendored gate catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from steward._vendor.spec_meta import split_frontmatter
from steward.gatecheck.behaviour import BEHAVIOUR_NODE, parse_priorities, parse_scenarios
from steward.gatecheck.checks import Artifact, Finding
from steward.graph import SpecGraph

__all__ = ["AcCriterion", "acceptance_skip_reason", "check_acceptance", "parse_ac_criteria"]

ACCEPTANCE_NODE = "acceptance"
CHARTER_NODE = "charter"

_WONT = "Won't"
_AC_HEAD_RE = re.compile(
    r"(?m)^####\s+(AC-\d+[a-z]?):\s*(.+?)\s*·\s*verification:\s*(test|manual|metric)\s*$"
)
# Any AC-shaped *entry*: a heading, or a line opening with an AC id (optionally
# bulleted / bold, followed by a definition separator `:`/`·` — WS-005's prose
# form `**AC-001 · …**`). A reference (`- AC-01 — panel`, a mid-sentence mention)
# is not an entry. Entries the strict grammar rejects are findings.
_AC_NEAR_RE = re.compile(
    r"(?m)^(?:#{2,6}\s+(AC-[^\s:]*)"
    r"|[ \t]*(?:[-*+][ \t]+)?\**(AC-[^\s:*·]*)\**[ \t]*[:·])"
)
_FIELD_LINE_RE = re.compile(r"^(traces|scenarios):")
_FIELD_VALUE_RE = re.compile(r"^(?:traces|scenarios):\s*\[([^\]]*)\]\s*$")


@dataclass(frozen=True)
class AcCriterion:
    """One AC definition (``#### AC-NN: … · verification: …``)."""

    ac_id: str
    verification: str
    traces: tuple[str, ...] | None  # None: no `traces:` line at all
    scenarios: tuple[str, ...]


def check_acceptance(graph: SpecGraph, artifacts: list[Artifact]) -> list[Finding]:
    """Run the schema-2 acceptance checks; no findings outside schema 2."""
    present = _present(graph, artifacts)
    if present is None:
        return []
    charter, behaviour, acceptance = present
    schema, schema_finding = _charter_schema(charter)
    if schema_finding is not None:
        return [schema_finding]
    if schema != 2:
        return []

    priorities = parse_priorities(
        [a.text for a in artifacts if a.node_id in graph.nodes[ACCEPTANCE_NODE].upstream]
    )
    criteria, findings = parse_ac_criteria(acceptance)
    beh_ids = {s.beh_id for s in parse_scenarios(behaviour.text)}
    findings.extend(_reference_findings(acceptance, criteria, priorities, beh_ids))
    findings.extend(_orphan_findings(behaviour, criteria, priorities))
    return findings


def acceptance_skip_reason(graph: SpecGraph, artifacts: list[Artifact]) -> str | None:
    """Why the orphan check did not run on an applicable bundle, or None.

    None both when it ran (schema 2, or a schema error that is itself a finding)
    and when it does not apply at all — a profile without charter, behaviour-spec
    and acceptance nodes, or a bundle missing behaviour-spec, acceptance or an
    upstream of acceptance (completeness owns that). A profile *with* a charter node whose artifact is
    absent is declared: the schema, and so the boundary, is unknown.
    """
    present = _present(graph, artifacts)
    if present is None:
        return None
    charter, _, _ = present
    if charter is None:
        return "no charter artifact in the bundle — its schema, and so the boundary, is unknown"
    schema, schema_finding = _charter_schema(charter)
    if schema_finding is None and schema == 1:
        return (
            "charter schema 1 — the acceptance checks (strict AC grammar, and every "
            "non-Won't BEH in a non-Won't AC) apply from charter schema 2"
        )
    return None


def _present(
    graph: SpecGraph, artifacts: list[Artifact]
) -> tuple[Artifact | None, Artifact, Artifact] | None:
    if any(node not in graph.nodes for node in (CHARTER_NODE, BEHAVIOUR_NODE, ACCEPTANCE_NODE)):
        return None
    by_node = {a.node_id: a for a in artifacts if a.node_id is not None}
    behaviour = by_node.get(BEHAVIOUR_NODE)
    acceptance = by_node.get(ACCEPTANCE_NODE)
    if behaviour is None or acceptance is None:
        return None
    # Without every upstream of acceptance (requirements) each priority is
    # unknown: a Won't-only BEH would read as an orphan and every trace as
    # undefined. GC-COMPLETENESS already reds the bundle; say nothing false.
    if any(up not in by_node for up in graph.nodes[ACCEPTANCE_NODE].upstream):
        return None
    return by_node.get(CHARTER_NODE), behaviour, acceptance


def _charter_schema(charter: Artifact | None) -> tuple[int | None, Finding | None]:
    if charter is None:
        return None, None
    meta, _ = split_frontmatter(charter.text)
    raw = (meta or {}).get("schema", 1)
    if isinstance(raw, (int, str)) and not isinstance(raw, bool) and str(raw) in ("1", "2"):
        return int(raw), None
    return None, Finding(
        "error",
        "GC-META",
        charter.path,
        f"charter schema {raw!r} is outside the vocabulary 1|2 — the acceptance "
        "boundary cannot be decided",
    )


def parse_ac_criteria(acceptance: Artifact) -> tuple[list[AcCriterion], list[Finding]]:
    """Parse the AC definitions; findings for form defects (as devtools' guard)."""
    text = acceptance.text
    findings: list[Finding] = []
    strict = {m.start() for m in _AC_HEAD_RE.finditer(text)}
    for near in _AC_NEAR_RE.finditer(text):
        if near.start() not in strict:
            findings.append(
                _finding(
                    acceptance,
                    f"AC entry {near.group(1) or near.group(2)!r} is outside the AC grammar "
                    "(`#### AC-NN: <title> · verification: test|manual|metric`)",
                )
            )
    # Split on "\n" only: the heading index below counts "\n", and splitlines()
    # would also split on \f, \x85, U+2028 … and shift every index after them.
    lines = text.split("\n")
    head_lines = {text.count("\n", 0, m.start()): m for m in _AC_HEAD_RE.finditer(text)}
    if not head_lines:
        findings.append(_finding(acceptance, "charter schema 2 bundle has no AC definition"))
    claimed: set[int] = set()
    criteria: list[AcCriterion] = []
    for index, head in sorted(head_lines.items()):
        fields: dict[str, tuple[str, ...] | None] = {}
        cursor = index + 1
        while cursor < len(lines):
            if not lines[cursor].strip():  # blank lines stay inside (devtools parity)
                cursor += 1
                continue
            field = _FIELD_LINE_RE.match(lines[cursor])
            if field is None:
                break
            claimed.add(cursor)
            name = field.group(1)
            if name in fields:
                findings.append(
                    _finding(acceptance, f"{head.group(1)} has more than one `{name}:` line")
                )
            value = _FIELD_VALUE_RE.match(lines[cursor])
            if value is None:
                findings.append(
                    _finding(acceptance, f"{head.group(1)} `{name}:` is not a `[...]` list")
                )
            fields.setdefault(name, _split_list(value.group(1)) if value else None)
            cursor += 1
        criteria.append(
            AcCriterion(
                ac_id=head.group(1),
                verification=head.group(3),
                traces=fields.get("traces"),
                scenarios=fields.get("scenarios") or (),
            )
        )
    findings.extend(
        _finding(
            acceptance,
            f"`{line.split(':', 1)[0]}:` on line {number + 1} belongs to no AC — fields "
            "must stand directly under their `#### AC-NN` heading",
        )
        for number, line in enumerate(lines)
        if _FIELD_LINE_RE.match(line) and number not in claimed
    )
    for ac_id in sorted({c.ac_id for c in criteria}):
        count = sum(1 for c in criteria if c.ac_id == ac_id)
        if count > 1:
            findings.append(_finding(acceptance, f"{ac_id} is declared {count} times"))
    return criteria, findings


def _reference_findings(
    acceptance: Artifact,
    criteria: list[AcCriterion],
    priorities: dict[str, str],
    beh_ids: set[str],
) -> list[Finding]:
    findings: list[Finding] = []
    for c in criteria:
        if not c.traces:
            findings.append(_finding(acceptance, f"{c.ac_id} has no non-empty `traces:` line"))
        findings.extend(
            _finding(acceptance, f"{c.ac_id} traces {ref!r}, which no upstream artifact defines")
            for ref in c.traces or ()
            if ref not in priorities
        )
        if c.verification == "test" and not c.scenarios:
            findings.append(
                _finding(acceptance, f"{c.ac_id} is verification: test with empty `scenarios`")
            )
        findings.extend(
            _finding(acceptance, f"{c.ac_id} names scenario {beh!r}, absent from behaviour-spec")
            for beh in c.scenarios
            if beh not in beh_ids
        )
    return findings


def _orphan_findings(
    behaviour: Artifact, criteria: list[AcCriterion], priorities: dict[str, str]
) -> list[Finding]:
    covered = {
        beh for c in criteria if not _is_wont(c.traces or (), priorities) for beh in c.scenarios
    }
    return [
        _finding(
            behaviour,
            f"{s.beh_id} is an orphan: no non-Won't AC names it in `scenarios` "
            "(every BEH except Won't must be accepted by at least one AC)",
        )
        for s in parse_scenarios(behaviour.text)
        if not _is_wont(s.traces, priorities) and s.beh_id not in covered
    ]


def _is_wont(traces: tuple[str, ...], priorities: dict[str, str]) -> bool:
    """Won't only if every traced requirement is Won't — the max over them."""
    return bool(traces) and all(priorities.get(ref) == _WONT for ref in traces)


def _split_list(inner: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in inner.split(",") if part.strip())


def _finding(artifact: Artifact, message: str) -> Finding:
    return Finding("error", "GC-BEH-COVERAGE", artifact.path, message)
