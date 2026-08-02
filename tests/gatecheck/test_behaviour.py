"""Tests for the behaviour-spec gates (ADR behaviour-architecture-lifecycle, Phase 1).

Rule ids: GC-BEH-TRACE, GC-BEH-COVERAGE, GC-CHECK-PLANNED. Formats mirror the
manual golden run (``../_cowork_output/golden-run-ws005/``).
"""

from __future__ import annotations

from pathlib import Path

from steward.gatecheck.behaviour import check_behaviour_spec
from steward.gatecheck.checks import Artifact
from steward.graph import load_profile, load_profile_data
from steward.meta import parse_artifact

PROFILES_DIR = Path(__file__).resolve().parent.parent.parent / "profiles"

_PROFILE = {
    "profile": "team-exp-test",
    "solo_auto_approve": False,
    "artifacts": [
        {"id": "requirements", "owner_role": "@product", "upstream": []},
        {
            "id": "behaviour-spec",
            "owner_role": "@product,@qa",
            "upstream": ["requirements"],
        },
    ],
}

_REQUIREMENTS = """---
spec_stage: requirements
---
# Requirements

#### FR-01: Panel shows bundle state
**Priority**: 🔴 Must
**Acceptance**: blocker visible on one screen.

#### FR-02: Verdicts file is the only source
**Priority**: 🔴 Must
**Acceptance**: collector never imports steward.

#### FR-03: Freshness fields visible
**Priority**: 🟠 Should
**Acceptance**: generated_at and source_commit shown.

#### FR-04: Trend view
**Priority**: 🟡 Could
**Acceptance**: out of the first slice.

#### NFR-01: Read path works offline
**Priority**: 🔴 Must
**Target**: renders with all watched processes stopped.
"""

_BEHAVIOUR_OK = """---
spec_stage: behaviour-spec
structural_coverage:
  - fr: FR-02
    constraint: ARCH-C1
    obligation:
      detector: import
      expected_verdict: conformant
      owner_role: "@architects"
      release_gate: block
---
# Behaviour Spec

#### BEH-01: Clean bundle renders as pass `traces: [FR-01, FR-03]`
- **Given**: a bundle with a fresh clean verdicts file
- **Then**: pass state with freshness fields
- **checked_by**: `status: planned` `kind: e2e` `owner: @qa` `target: tests/ui.py::t1`

#### BEH-02: Offline render `traces: [NFR-01]`
- **Then**: the bundle renders from disk only
- **checked_by**: `status: planned` `kind: integration` `owner: @qa` `target: tests/t.py::t2`
"""


def _graph(data: dict | None = None):
    return load_profile_data(data or _PROFILE)


def _artifacts(requirements: str, behaviour: str) -> list[Artifact]:
    artifacts = []
    for node_id, path, text in (
        ("requirements", "10-requirements.md", requirements),
        ("behaviour-spec", "15-behaviour.md", behaviour),
    ):
        meta = parse_artifact(text)
        assert meta is not None
        artifacts.append(Artifact(path=path, node_id=node_id, meta=meta, text=text))
    return artifacts


def _rule_ids(findings) -> list[str]:
    return [f.rule_id for f in findings]


def test_team_exp_profile_loads_with_behaviour_node() -> None:
    graph = load_profile(PROFILES_DIR / "team-exp.yaml")
    node = graph.nodes["behaviour-spec"]
    assert node.upstream == ("requirements",)
    order = graph.topo_order()
    assert order.index("requirements") < order.index("behaviour-spec")
    assert order.index("behaviour-spec") < order.index("design")
    assert order.index("behaviour-spec") < order.index("acceptance")


def test_clean_bundle_yields_no_findings() -> None:
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, _BEHAVIOUR_OK))
    assert findings == []


def test_profile_without_behaviour_node_is_skipped() -> None:
    data = {
        "profile": "lite-test",
        "artifacts": [{"id": "requirements", "owner_role": "@owner", "upstream": []}],
    }
    meta = parse_artifact(_REQUIREMENTS)
    assert meta is not None
    artifacts = [Artifact(path="r.md", node_id="requirements", meta=meta, text=_REQUIREMENTS)]
    assert check_behaviour_spec(_graph(data), artifacts) == []


def test_dangling_trace_errors() -> None:
    behaviour = _BEHAVIOUR_OK.replace("[NFR-01]", "[FR-99]")
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    assert "GC-BEH-TRACE" in _rule_ids(findings)
    assert any("FR-99" in f.message for f in findings)


def test_scenario_without_traces_errors() -> None:
    behaviour = _BEHAVIOUR_OK.replace(" `traces: [NFR-01]`", "")
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    assert "GC-BEH-TRACE" in _rule_ids(findings)
    assert any("BEH-02" in f.message for f in findings)


def test_uncovered_must_fr_errors() -> None:
    # Drop FR-02's structural coverage: a Must FR with no BEH/waiver must error.
    behaviour = "---\nspec_stage: behaviour-spec\n---\n" + _BEHAVIOUR_OK.split("---\n", 2)[2]
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    coverage = [f for f in findings if f.rule_id == "GC-BEH-COVERAGE"]
    assert len(coverage) == 1
    assert "FR-02" in coverage[0].message


def test_could_priority_needs_no_coverage() -> None:
    # FR-04 (Could) is uncovered in the clean fixture and produces nothing.
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, _BEHAVIOUR_OK))
    assert all("FR-04" not in f.message for f in findings)


def test_waiver_covers_a_should_fr() -> None:
    behaviour = _BEHAVIOUR_OK.replace("[FR-01, FR-03]", "[FR-01]").replace(
        "structural_coverage:",
        "coverage_waivers:\n  - {fr: FR-03, reason: freshness deferred}\nstructural_coverage:",
    )
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    assert all("FR-03" not in f.message for f in findings)


def test_whitespace_reason_waiver_does_not_count() -> None:
    # Copilot review, PR #26: an empty/whitespace-only reason is not a reason.
    behaviour = _BEHAVIOUR_OK.replace("[FR-01, FR-03]", "[FR-01]").replace(
        "structural_coverage:",
        'coverage_waivers:\n  - {fr: FR-03, reason: "   "}\nstructural_coverage:',
    )
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    messages = [f.message for f in findings if f.rule_id == "GC-BEH-COVERAGE"]
    assert any("reason" in m for m in messages)
    assert any("FR-03" in m and "covered by no" in m for m in messages)


def test_waiver_without_reason_does_not_count() -> None:
    behaviour = _BEHAVIOUR_OK.replace("[FR-01, FR-03]", "[FR-01]").replace(
        "structural_coverage:",
        "coverage_waivers:\n  - {fr: FR-03}\nstructural_coverage:",
    )
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    messages = [f.message for f in findings if f.rule_id == "GC-BEH-COVERAGE"]
    assert any("reason" in m for m in messages)
    assert any("FR-03" in m for m in messages)


def test_constraint_without_obligation_is_not_coverage() -> None:
    behaviour = _BEHAVIOUR_OK.replace(
        """    obligation:
      detector: import
      expected_verdict: conformant
      owner_role: "@architects"
      release_gate: block
""",
        "",
    )
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    messages = [f.message for f in findings if f.rule_id == "GC-BEH-COVERAGE"]
    assert any("obligation" in m for m in messages)  # invalid entry named
    assert any("FR-02" in m and "covered by no" in m for m in messages)  # and FR uncovered


def test_manual_evidence_detector_requires_evidence_target() -> None:
    behaviour = _BEHAVIOUR_OK.replace("detector: import", "detector: manual-evidence")
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    assert any("evidence_target" in f.message for f in findings if f.rule_id == "GC-BEH-COVERAGE")
    # ... and naming one makes the chain count again.
    behaviour_ok = behaviour.replace(
        "detector: manual-evidence",
        "detector: manual-evidence\n      evidence_target: review checklist item R1",
    )
    assert check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour_ok)) == []


def test_blocking_scenario_without_checked_by_errors() -> None:
    behaviour = _BEHAVIOUR_OK.replace(
        "- **checked_by**: `status: planned` `kind: e2e` `owner: @qa` `target: tests/ui.py::t1`\n",
        "",
    )
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    planned = [f for f in findings if f.rule_id == "GC-CHECK-PLANNED"]
    assert len(planned) == 1
    assert "BEH-01" in planned[0].message


def test_planned_binding_missing_target_errors() -> None:
    behaviour = _BEHAVIOUR_OK.replace(" `target: tests/ui.py::t1`", "")
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    planned = [f for f in findings if f.rule_id == "GC-CHECK-PLANNED"]
    assert len(planned) == 1
    assert "target" in planned[0].message


def test_unknown_kind_errors() -> None:
    behaviour = _BEHAVIOUR_OK.replace("`kind: e2e`", "`kind: cucumber`")
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    assert any("cucumber" in f.message for f in findings if f.rule_id == "GC-CHECK-PLANNED")


def test_materialized_binding_needs_ref() -> None:
    behaviour = _BEHAVIOUR_OK.replace(
        "`status: planned` `kind: e2e` `owner: @qa` `target: tests/ui.py::t1`",
        "`status: materialized` `kind: e2e` `owner: @qa` `ref: tests/ui.py::t1`",
    )
    assert check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour)) == []
    broken = behaviour.replace(" `ref: tests/ui.py::t1`", "")
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, broken))
    assert any("ref" in f.message for f in findings if f.rule_id == "GC-CHECK-PLANNED")


def test_non_blocking_scenario_needs_no_binding() -> None:
    # BEH-02 traces NFR-01 (Must) — rewrite it to trace only FR-03 (Should).
    behaviour = _BEHAVIOUR_OK.replace("[NFR-01]", "[FR-03]").replace(
        "- **checked_by**: `status: planned` `kind: integration` `owner: @qa` `target: tests/t.py::t2`\n",
        "",
    )
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    assert all("BEH-02" not in f.message for f in findings if f.rule_id == "GC-CHECK-PLANNED")


def test_must_nfr_scenario_is_blocking() -> None:
    # Copilot review, PR #25: blocking deliberately includes Must-priority NFRs —
    # BEH-02 traces NFR-01 (Must), so dropping its binding must error.
    behaviour = _BEHAVIOUR_OK.replace(
        "- **checked_by**: `status: planned` `kind: integration` `owner: @qa` `target: tests/t.py::t2`\n",
        "",
    )
    findings = check_behaviour_spec(_graph(), _artifacts(_REQUIREMENTS, behaviour))
    planned = [f for f in findings if f.rule_id == "GC-CHECK-PLANNED"]
    assert len(planned) == 1
    assert "BEH-02" in planned[0].message


def test_prose_mention_is_not_a_definition() -> None:
    # Copilot review, PR #25: an id mentioned in upstream prose (no heading)
    # must not satisfy GC-BEH-TRACE.
    requirements = _REQUIREMENTS + "\nProse aside: FR-77 might exist one day.\n"
    behaviour = _BEHAVIOUR_OK.replace("[FR-01, FR-03]", "[FR-01, FR-77]")
    findings = check_behaviour_spec(_graph(), _artifacts(requirements, behaviour))
    trace = [f for f in findings if f.rule_id == "GC-BEH-TRACE"]
    assert len(trace) == 1
    assert "FR-77" in trace[0].message and "headings" in trace[0].message
