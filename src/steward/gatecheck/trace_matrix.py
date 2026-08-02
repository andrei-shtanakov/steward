"""Derived trace matrix: requirement → scenarios → check bindings, as linter output.

Phase 1 slice item 5 of the behaviour-architecture-lifecycle ADR (golden-run
friction FL-09): a hand-maintained trace matrix drifts on every upstream edit,
so the matrix is *derived* — computed from the same parsed definitions the
behaviour gates check, never authored. ``gate-check --trace-matrix`` renders it
(text = markdown table, json = byte-stable payload); exit codes still come from
the findings, so a red bundle stays red even when only the matrix was requested.
"""

from __future__ import annotations

import json
from typing import Any

from steward.gatecheck.behaviour import (
    BEHAVIOUR_NODE,
    Scenario,
    parse_coverage_declarations,
    parse_priorities,
    parse_scenarios,
)
from steward.gatecheck.checks import Artifact
from steward.graph import SpecGraph

__all__ = ["build_trace_matrix", "render_matrix_json", "render_matrix_text"]


def build_trace_matrix(graph: SpecGraph, artifacts: list[Artifact]) -> dict[str, Any] | None:
    """Compute the matrix, or ``None`` when the profile/bundle has no behaviour data.

    One row per upstream FR/NFR definition, in stable id order; every row lists
    the scenarios tracing it, the structural obligation chains and waivers that
    cover it, and each scenario's check binding verbatim.
    """
    node = graph.nodes.get(BEHAVIOUR_NODE)
    if node is None:
        return None
    present = {a.node_id: a for a in artifacts if a.node_id is not None}
    behaviour = present.get(BEHAVIOUR_NODE)
    if behaviour is None:
        return None
    upstream_texts = [present[up].text for up in node.upstream if up in present]
    if not upstream_texts:
        return None

    scenarios = parse_scenarios(behaviour.text)
    priorities = parse_priorities(upstream_texts)
    structural, waivers = parse_coverage_declarations(behaviour.text)

    rows = [
        _requirement_row(req_id, priority, scenarios, structural, waivers)
        for req_id, priority in sorted(priorities.items(), key=_id_sort_key)
    ]
    return {"profile": graph.profile, "requirements": rows}


def _requirement_row(
    req_id: str,
    priority: str,
    scenarios: list[Scenario],
    structural: list[dict],
    waivers: list[dict],
) -> dict[str, Any]:
    tracing = [s for s in scenarios if req_id in s.traces]
    return {
        "id": req_id,
        "priority": priority,
        "scenarios": [s.beh_id for s in tracing],
        "structural": [
            {"constraint": e["constraint"], "detector": e["obligation"]["detector"]}
            for e in structural
            if e["fr"] == req_id
        ],
        "waived": next((e["reason"] for e in waivers if e["fr"] == req_id), None),
        "checks": [
            {"scenario": s.beh_id, **{k: s.checked_by[k] for k in sorted(s.checked_by)}}
            for s in tracing
            if s.has_checked_by
        ],
    }


def _id_sort_key(item: tuple[str, str]) -> tuple[str, int]:
    prefix, _, number = item[0].rpartition("-")
    return (prefix, int(number) if number.isdigit() else 0)


def render_matrix_json(matrix: dict[str, Any]) -> str:
    """Byte-stable JSON: sorted keys, fixed indent, no trailing spaces."""
    return json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=True)


def render_matrix_text(matrix: dict[str, Any]) -> str:
    """Markdown table, one row per requirement."""
    lines = [
        f"Trace matrix — profile {matrix['profile']}",
        "",
        "| Requirement | Priority | Scenarios | Structural | Waived | Checks |",
        "|---|---|---|---|---|---|",
    ]
    for row in matrix["requirements"]:
        checks = "; ".join(
            f"{c['scenario']}:{c.get('status', '?')}"
            + (f"→{c['target']}" if c.get("target") else "")
            + (f"→{c['ref']}" if c.get("ref") else "")
            for c in row["checks"]
        )
        structural = "; ".join(f"{e['constraint']}({e['detector']})" for e in row["structural"])
        lines.append(
            f"| {row['id']} | {row['priority'] or '—'} "
            f"| {', '.join(row['scenarios']) or '—'} "
            f"| {structural or '—'} "
            f"| {row['waived'] or '—'} "
            f"| {checks or '—'} |"
        )
    return "\n".join(lines)
