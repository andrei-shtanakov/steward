"""Acceptance completeness for charter schema 2: strict AC grammar + no orphans.

steward#190 PR-2, devtools bundle-criteria oracle spec rev 10 §1.7: every BEH
except Won't sits in the ``scenarios`` of at least one non-Won't AC. The boundary
is the charter's ``schema`` (owner ruling 2026-09-29): schema 2 is checked in
full and a missing/broken AC set is an error; schema 1 is skipped *and the skip
is declared* — never a silent zero. Grammar presence never decides activation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from steward.gatecheck.acceptance import acceptance_skip_reason, check_acceptance
from steward.gatecheck.checks import Artifact
from steward.gatecheck.cli import app
from steward.graph import load_profile_data
from steward.meta import parse_artifact
from steward.roles import Role, RolesCatalog

runner = CliRunner()

_PROFILE = {
    "profile": "team-exp-test",
    "solo_auto_approve": False,
    "artifacts": [
        {"id": "charter", "owner_role": "product", "upstream": []},
        {"id": "requirements", "owner_role": "product", "upstream": ["charter"]},
        {"id": "behaviour-spec", "owner_role": "product", "upstream": ["requirements"]},
        {
            "id": "acceptance",
            "owner_role": "qa",
            "upstream": ["requirements", "behaviour-spec"],
        },
    ],
}

_CATALOG = RolesCatalog(
    version=1,
    slug_pattern="^[a-z][a-z0-9-]{1,31}$",
    roles=(Role("product", "Product"), Role("qa", "QA")),
)

_CHARTER_V2 = "---\nspec_stage: charter\nschema: 2\ncode: ENC\n---\n# Charter\n"
_CHARTER_V1 = "---\nspec_stage: charter\n---\n# Charter\n"

_REQUIREMENTS = """---
spec_stage: requirements
---
#### FR-01: Panel
**Priority**: Must

#### FR-02: Trend
**Priority**: Could

#### FR-03: Export
**Priority**: Won't
"""

_BEHAVIOUR = """---
spec_stage: behaviour-spec
---
#### BEH-01: Panel renders `traces: [FR-01]`
- **checked_by**: `status: planned` `kind: unit` `owner: @qa` `target: t.py::a`

#### BEH-02: Trend view `traces: [FR-02]`

#### BEH-03: Export deferred `traces: [FR-03]`
"""

_ACCEPTANCE = """---
spec_stage: acceptance
---
# Acceptance

#### AC-01: Panel works · verification: test
traces: [FR-01]
scenarios: [BEH-01]

#### AC-02: Trend visible · verification: manual
traces: [FR-02]
scenarios: [BEH-02]

## Notes
"""


def _artifacts(charter: str, acceptance: str = _ACCEPTANCE, behaviour: str = _BEHAVIOUR):
    artifacts = []
    for node_id, path, text in (
        ("charter", "00-charter.md", charter),
        ("requirements", "10-requirements.md", _REQUIREMENTS),
        ("behaviour-spec", "15-behaviour-spec.md", behaviour),
        ("acceptance", "25-acceptance.md", acceptance),
    ):
        meta = parse_artifact(text)
        assert meta is not None
        artifacts.append(Artifact(path=path, node_id=node_id, meta=meta, text=text))
    return artifacts


def _check(charter: str = _CHARTER_V2, **kwargs):
    return check_acceptance(load_profile_data(_PROFILE, _CATALOG), _artifacts(charter, **kwargs))


def _messages(findings) -> str:
    return "\n".join(f"{f.rule_id} {f.artifact} {f.message}" for f in findings)


def test_clean_schema_2_bundle_has_no_findings() -> None:
    assert _check() == []


def test_orphan_beh_is_an_error() -> None:
    acceptance = _ACCEPTANCE.replace("scenarios: [BEH-02]", "scenarios: [BEH-01]")
    findings = _check(acceptance=acceptance)
    assert [f.rule_id for f in findings] == ["GC-BEH-COVERAGE"]
    assert "BEH-02" in findings[0].message and "orphan" in findings[0].message
    assert findings[0].artifact == "15-behaviour-spec.md"


def test_won_t_beh_needs_no_ac() -> None:
    # BEH-03 traces only a Won't FR — it is not an orphan (the clean fixture).
    assert "BEH-03" not in _messages(_check())


def test_beh_covered_only_by_won_t_ac_is_an_orphan() -> None:
    acceptance = _ACCEPTANCE.replace(
        "traces: [FR-02]\nscenarios: [BEH-02]", "traces: [FR-03]\nscenarios: [BEH-02]"
    )
    findings = _check(acceptance=acceptance)
    assert any("BEH-02" in f.message and "orphan" in f.message for f in findings)


def test_schema_2_without_any_ac_is_an_error_not_a_skip() -> None:
    findings = _check(acceptance="---\nspec_stage: acceptance\n---\n# Acceptance\nprose\n")
    assert any("no AC" in f.message for f in findings)
    assert all(f.rule_id == "GC-BEH-COVERAGE" for f in findings)


def test_prose_ac_is_a_grammar_finding_under_schema_2() -> None:
    # WS-005's prose style: not a definition, so it must not pass silently.
    acceptance = _ACCEPTANCE.replace(
        "#### AC-02: Trend visible · verification: manual", "#### AC-02 · Trend visible"
    )
    assert "'AC-02'" in _messages(_check(acceptance=acceptance))


def test_ac_form_findings() -> None:
    acceptance = (
        _ACCEPTANCE.replace("traces: [FR-01]\n", "")
        .replace("scenarios: [BEH-02]", "scenarios: [BEH-02, BEH-77]")
        .replace("traces: [FR-02]", "traces: [FR-02, FR-99]")
    )
    text = _messages(_check(acceptance=acceptance))
    assert "AC-01" in text and "traces" in text
    assert "BEH-77" in text
    assert "FR-99" in text


def test_test_verification_needs_scenarios() -> None:
    acceptance = _ACCEPTANCE.replace("scenarios: [BEH-01]", "scenarios: []")
    text = _messages(_check(acceptance=acceptance))
    assert "AC-01" in text and "verification: test" in text


def test_duplicate_ac_id_is_an_error() -> None:
    acceptance = _ACCEPTANCE.replace("#### AC-02: Trend", "#### AC-01: Trend")
    assert "declared 2 times" in _messages(_check(acceptance=acceptance))


def test_section_heading_ends_the_ac_block() -> None:
    # `scenarios:` below a `##` section is not AC-02's — BEH-02 is then an orphan.
    acceptance = _ACCEPTANCE.replace(
        "traces: [FR-02]\nscenarios: [BEH-02]\n\n## Notes\n",
        "traces: [FR-02]\n\n## Notes\nscenarios: [BEH-02]\n",
    )
    assert "BEH-02" in _messages(_check(acceptance=acceptance))


def test_schema_1_is_skipped_with_a_declared_reason() -> None:
    graph = load_profile_data(_PROFILE, _CATALOG)
    artifacts = _artifacts(_CHARTER_V1, acceptance="---\nspec_stage: acceptance\n---\nprose\n")
    assert check_acceptance(graph, artifacts) == []
    reason = acceptance_skip_reason(graph, artifacts)
    assert reason is not None and "schema 1" in reason


def test_schema_2_is_not_declared_skipped() -> None:
    graph = load_profile_data(_PROFILE, _CATALOG)
    assert acceptance_skip_reason(graph, _artifacts(_CHARTER_V2)) is None


def test_string_schema_2_counts_as_schema_2() -> None:
    charter = _CHARTER_V2.replace("schema: 2", 'schema: "2"')
    acceptance = _ACCEPTANCE.replace("scenarios: [BEH-02]", "scenarios: [BEH-01]")
    assert any("orphan" in f.message for f in _check(charter, acceptance=acceptance))


def test_unknown_schema_is_a_meta_error_not_a_skip() -> None:
    charter = _CHARTER_V2.replace("schema: 2", "schema: 3")
    findings = _check(charter)
    assert [f.rule_id for f in findings] == ["GC-META"]
    assert findings[0].artifact == "00-charter.md" and "1|2" in findings[0].message


def test_profile_without_acceptance_node_is_inapplicable() -> None:
    data = {**_PROFILE, "artifacts": _PROFILE["artifacts"][:3]}
    graph = load_profile_data(data, _CATALOG)
    artifacts = _artifacts(_CHARTER_V2)[:3]
    assert check_acceptance(graph, artifacts) == []
    assert acceptance_skip_reason(graph, artifacts) is None


# --- CLI: the schema-1 skip is declared on stderr and in JSON ----------------


def _write_bundle(tmp_path: Path, charter: str) -> Path:
    (tmp_path / "test.yaml").write_text(
        "profile: accept\n"
        "solo_auto_approve: false\n"
        "artifacts:\n"
        "  - {id: charter, owner_role: product, upstream: []}\n"
        "  - {id: requirements, owner_role: product, upstream: [charter]}\n"
        "  - {id: behaviour-spec, owner_role: product, upstream: [requirements]}\n"
        "  - {id: acceptance, owner_role: qa, upstream: [requirements, behaviour-spec]}\n"
    )
    spec = tmp_path / "spec"
    spec.mkdir()
    head = "status: draft\nversion: 1\n"
    (spec / "00-charter.md").write_text(charter.replace("---\n", f"---\n{head}", 1))
    for name, text, traces in (
        ("10-requirements.md", _REQUIREMENTS, "[charter]"),
        ("15-behaviour-spec.md", _BEHAVIOUR, "[requirements]"),
        ("25-acceptance.md", _ACCEPTANCE, "[requirements, behaviour-spec]"),
    ):
        (spec / name).write_text(text.replace("---\n", f"---\n{head}traces_to: {traces}\n", 1))
    return spec


def _run(spec: Path, *extra: str):
    return runner.invoke(
        app, [str(spec), "--profile", str(spec.parent / "test.yaml"), "--candidate", *extra]
    )


@pytest.mark.usefixtures("write_roles", "write_role_assignments")
def test_cli_declares_schema_1_skip(tmp_path: Path) -> None:
    result = _run(_write_bundle(tmp_path, _CHARTER_V1), "--format", "json")
    assert result.exit_code == 0, result.output
    assert "schema 1" in result.stderr
    payload = json.loads(result.stdout)
    assert payload["skipped"][0]["gate"] == "GC-BEH-COVERAGE"
    assert "schema 1" in payload["skipped"][0]["reason"]


@pytest.mark.usefixtures("write_roles", "write_role_assignments")
def test_cli_schema_2_runs_without_a_skip_declaration(tmp_path: Path) -> None:
    result = _run(_write_bundle(tmp_path, _CHARTER_V2), "--format", "json")
    assert result.exit_code == 0, result.output
    assert "skipped" not in json.loads(result.stdout)


# --- local review, PR-2: a field line must belong to its own AC -------------

_ABSORBING = """---
spec_stage: acceptance
---
## Criteria

#### AC-01: Panel works · verification: manual
traces: [FR-01]
scenarios: [BEH-01]

**AC-02 · Trend visible**
traces: [FR-02]
scenarios: [BEH-02]
"""


def test_prose_ac_entry_after_a_strict_ac_is_not_absorbed() -> None:
    text = _messages(_check(acceptance=_ABSORBING))
    assert "'AC-02'" in text  # the prose entry is a grammar finding
    assert "BEH-02" in text and "orphan" in text  # and does not cover BEH-02


def test_repeated_field_line_in_one_block_is_ambiguous() -> None:
    acceptance = _ACCEPTANCE.replace(
        "scenarios: [BEH-01]\n", "scenarios: [BEH-01]\nscenarios: [BEH-02]\n"
    )
    assert "more than one `scenarios:`" in _messages(_check(acceptance=acceptance))


def test_any_heading_ends_the_ac_block() -> None:
    acceptance = _ACCEPTANCE.replace(
        "traces: [FR-02]\nscenarios: [BEH-02]\n",
        "traces: [FR-02]\n\n#### Rationale\nscenarios: [BEH-02]\n",
    )
    assert "BEH-02" in _messages(_check(acceptance=acceptance))


def test_ac_mentioned_mid_sentence_is_not_an_entry() -> None:
    acceptance = _ACCEPTANCE.replace("## Notes\n", "## Notes\nSee AC-01 and AC-02 above.\n")
    assert _check(acceptance=acceptance) == []


@pytest.mark.usefixtures("write_roles", "write_role_assignments")
def test_cli_declares_missing_charter_skip(tmp_path: Path) -> None:
    spec = _write_bundle(tmp_path, _CHARTER_V1)
    (spec / "00-charter.md").unlink()
    result = _run(spec, "--format", "json")
    assert "no charter" in json.loads(result.stdout)["skipped"][0]["reason"]


def test_line_start_reference_without_separator_is_not_an_entry() -> None:
    acceptance = _ACCEPTANCE.replace(
        "# Acceptance\n", "# Acceptance\n\n- AC-01 — panel\n- AC-02 — trend\n"
    )
    assert _check(acceptance=acceptance) == []


def test_prose_entry_finding_does_not_claim_a_heading() -> None:
    text = _messages(_check(acceptance=_ABSORBING))
    assert "AC entry 'AC-02'" in text and "heading 'AC-02'" not in text


def test_profile_without_charter_node_is_inapplicable() -> None:
    data = {**_PROFILE, "artifacts": [dict(a) for a in _PROFILE["artifacts"][1:]]}
    data["artifacts"][0]["upstream"] = []
    graph = load_profile_data(data, _CATALOG)
    artifacts = _artifacts(_CHARTER_V2)[1:]
    assert acceptance_skip_reason(graph, artifacts) is None
    assert check_acceptance(graph, artifacts) == []


def test_field_line_after_an_unrecognised_entry_belongs_to_no_ac() -> None:
    # Local review round 3: `AC-02 — …` is no entry, yet its `scenarios:` must
    # not be credited to AC-01 (a manual AC without scenarios of its own).
    acceptance = """---
spec_stage: acceptance
---
# Acceptance

#### AC-01: Panel works · verification: manual
traces: [FR-01]

AC-02 — Trend visible
scenarios: [BEH-01, BEH-02]
"""
    text = _messages(_check(acceptance=acceptance))
    assert "belongs to no AC" in text
    assert "BEH-01 is an orphan" in text and "BEH-02 is an orphan" in text


def test_field_value_must_be_a_list() -> None:
    acceptance = _ACCEPTANCE.replace("scenarios: [BEH-01]", "scenarios: BEH-01")
    assert "not a `[...]` list" in _messages(_check(acceptance=acceptance))


@pytest.mark.usefixtures("write_roles", "write_role_assignments")
def test_cli_skip_names_its_scope_not_the_whole_gate(tmp_path: Path) -> None:
    result = _run(_write_bundle(tmp_path, _CHARTER_V1), "--format", "json")
    assert json.loads(result.stdout)["skipped"][0]["scope"] == "acceptance"
    assert "GC-BEH-COVERAGE [acceptance]" in result.stderr
    assert "AC grammar" in result.stderr and "non-Won't BEH" in result.stderr
