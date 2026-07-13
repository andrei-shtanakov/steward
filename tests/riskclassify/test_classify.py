"""Classifier tests (TASK-605): axes, combinator, two phases, scope violation."""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.riskclassify.classify import classify_declared, classify_diff
from steward.riskclassify.model import RiskModel, load_risk_model

CANONICAL = Path(__file__).parents[2] / "profiles" / "risk-model.yaml"


@pytest.fixture(scope="module")
def model():
    return load_risk_model(CANONICAL)


# --- change_class axis (golden cases on real ecosystem paths, REQ-602) ---


def test_docs_change_is_low(model) -> None:
    c = classify_diff(model, project="Maestro", paths=["README.md"], sha="a" * 40)
    assert c.tier == "low"
    assert c.inputs["change_class"] == "docs"


def test_contract_path_is_high(model) -> None:
    c = classify_diff(
        model,
        project="Maestro",
        paths=["contracts/work-correlation/schema.json"],
        sha="a" * 40,
    )
    assert c.inputs["change_class"] == "contract"
    assert c.tier == "high"  # contract=high, cross-repo=high (1 consumer), trust none


def test_state_machine_path_beats_generic_code(model) -> None:
    c = classify_diff(model, project="Maestro", paths=["maestro/models.py"], sha="a" * 40)
    assert c.inputs["change_class"] == "state-machine"


def test_per_repo_rule_beats_generic_md(model) -> None:
    # contracts/**/*.md must classify as contract (per-repo first), not docs (_generic).
    c = classify_diff(
        model,
        project="Maestro",
        paths=["contracts/observability/evidence-ref.md"],
        sha="a" * 40,
    )
    assert c.inputs["change_class"] == "contract"


def test_unmapped_path_fails_closed_to_medium(model) -> None:
    # Golden case #1 (REQ-603): a file in an unknown directory never lands below medium.
    c = classify_diff(model, project="Maestro", paths=["mystery/blob.bin"], sha="a" * 40)
    assert c.inputs["change_class"] == "unknown"
    assert c.tier in ("medium", "high", "critical")
    assert c.tier == "medium"


def test_unknown_project_uses_generic_then_default(model) -> None:
    c = classify_diff(model, project="new-repo", paths=["notes.md"], sha="a" * 40)
    assert c.inputs["change_class"] == "docs"
    c2 = classify_diff(model, project="new-repo", paths=["src/thing.py"], sha="a" * 40)
    assert c2.inputs["change_class"] == "unknown"


def test_ci_workflow_is_critical(model) -> None:
    c = classify_diff(model, project="steward", paths=[".github/workflows/ci.yml"], sha="a" * 40)
    assert c.inputs["change_class"] == "ci-deploy"
    assert c.tier == "critical"


# --- blast_radius axis ---


def test_non_contract_change_is_single_repo(model) -> None:
    c = classify_diff(model, project="Maestro", paths=["maestro/cli.py"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "single-repo"


def test_contract_with_one_consumer_is_cross_repo(model) -> None:
    c = classify_diff(
        model,
        project="Maestro",
        paths=["contracts/work-correlation/schema.json"],
        sha="a" * 40,
    )
    assert c.inputs["blast_radius"] == "cross-repo"


def test_contract_with_many_consumers_is_ecosystem(model) -> None:
    c = classify_diff(
        model,
        project="Maestro",
        paths=["contracts/observability/log-schema.json"],
        sha="a" * 40,
    )
    assert c.inputs["blast_radius"] == "ecosystem-contract"
    assert c.tier == "critical"


def test_unregistered_contract_dir_still_cross_repo(model) -> None:
    c = classify_diff(
        model, project="arbiter", paths=["contracts/new-thing/schema.json"], sha="a" * 40
    )
    assert c.inputs["blast_radius"] == "cross-repo"


def test_top_level_contracts_file_takes_project_worst_grade(model) -> None:
    # A file directly under contracts/ (2 segments) pins no dir key, so the
    # un-pinned lookup takes the project's worst registry entry by design.
    c = classify_diff(model, project="Maestro", paths=["contracts/README.md"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "ecosystem-contract"


def _registry_only_model(consumer_registry: dict[str, list[str]]) -> RiskModel:
    """Minimal hand-built model to pin registry-key matching semantics."""
    return RiskModel(
        version_sha="sha256:test",
        profile_floors={"lite": "low"},
        class_tiers={"unknown": "medium"},
        blast_tiers={"single-repo": "low", "cross-repo": "high", "ecosystem-contract": "critical"},
        trust_tiers={"none": "low"},
        tier_gates={t: [] for t in ("low", "medium", "high", "critical")},
        waiver_policy={},
        path_class={},
        generic_class=[],
        trust_rules=[],
        consumer_registry=consumer_registry,
    )


def test_overlapping_registry_keys_take_the_max_grade() -> None:
    # A 1-consumer exact-file key must not shadow the 3-consumer directory key
    # covering the same path — the model only escalates (DESIGN-601).
    m = _registry_only_model(
        {
            "Maestro/contracts/observability": ["spec-runner", "arbiter", "dispatcher"],
            "Maestro/contracts/observability/spec.md": ["dispatcher"],
        }
    )
    c = classify_diff(m, project="Maestro", paths=["contracts/observability/spec.md"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "ecosystem-contract"


def test_directory_registry_key_outside_contracts_grades_ex_post() -> None:
    # Ex-post must honor directory-shaped registry keys outside contracts/,
    # matching what classify_declared already grades ex-ante for such keys.
    m = _registry_only_model({"atp-platform/method/schemas": ["Maestro", "arbiter"]})
    c = classify_diff(m, project="atp-platform", paths=["method/schemas/eval.json"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "ecosystem-contract"


def test_sibling_directory_does_not_inherit_registry_key_grade() -> None:
    # Pins the "/" segment boundary: a bare startswith(k) would escalate the
    # unrelated sibling dir method/schemas-v2 to the method/schemas grade.
    m = _registry_only_model({"atp-platform/method/schemas": ["Maestro", "arbiter"]})
    c = classify_diff(
        m, project="atp-platform", paths=["method/schemas-v2/eval.json"], sha="a" * 40
    )
    assert c.inputs["blast_radius"] == "single-repo"


def test_lower_graded_registry_key_iterated_last_cannot_demote() -> None:
    # Outside contracts/ no pinned key is appended, so key order is pure
    # registry order: the 1-consumer exact key comes last and a last-wins
    # regression (plain assignment instead of tier_str_max) would demote.
    m = _registry_only_model(
        {
            "atp-platform/method/schemas": ["Maestro", "arbiter"],
            "atp-platform/method/schemas/eval.json": ["Maestro"],
        }
    )
    c = classify_diff(m, project="atp-platform", paths=["method/schemas/eval.json"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "ecosystem-contract"


def test_exact_file_key_escalates_over_unregistered_contract_dir() -> None:
    # The other shadowing direction: the pinned dir key (unregistered ->
    # cross-repo floor) is graded after the 2-consumer exact-file key and
    # must not shadow its ecosystem grade.
    m = _registry_only_model({"Maestro/contracts/foo/schema.json": ["arbiter", "dispatcher"]})
    c = classify_diff(m, project="Maestro", paths=["contracts/foo/schema.json"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "ecosystem-contract"


def test_ex_post_contracts_paths_keep_cross_repo_floor_with_empty_registry() -> None:
    # Fail-closed floor through _blast_of_paths with no registry entries at
    # all: both the pinned dir lookup and the un-pinned top-level lookup must
    # still grade cross-repo, never single-repo.
    m = _registry_only_model({})
    c = classify_diff(m, project="Maestro", paths=["contracts/foo/spec.md"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "cross-repo"
    c2 = classify_diff(m, project="Maestro", paths=["contracts/README.md"], sha="a" * 40)
    assert c2.inputs["blast_radius"] == "cross-repo"


def test_project_name_prefix_does_not_absorb_other_projects_entries() -> None:
    # Pins the project + "/" boundary in the un-pinned worst-entry lookup: a
    # bare startswith(project) would let project "atp" absorb atp-platform's
    # 2-consumer entry and grade its top-level contracts file ecosystem.
    m = _registry_only_model({"atp-platform/contracts/foo": ["Maestro", "arbiter"]})
    c = classify_diff(m, project="atp", paths=["contracts/README.md"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "cross-repo"


# --- trust_boundary axis ---


def test_env_file_touches_secrets_boundary(model) -> None:
    c = classify_diff(model, project="dispatcher", paths=[".env.local"], sha="a" * 40)
    assert c.inputs["trust_boundary"] == "secrets"
    assert c.tier == "critical"


def test_declared_flag_external_api(model) -> None:
    c = classify_diff(
        model,
        project="dispatcher",
        paths=["dispatcher/core/collect.py"],
        sha="a" * 40,
        flags=["external-api"],
    )
    assert c.inputs["trust_boundary"] == "external-api"
    assert c.tier == "high"


def test_unknown_declared_flag_rejected(model) -> None:
    with pytest.raises(ValueError, match="flag"):
        classify_diff(model, project="dispatcher", paths=["x.md"], sha="a" * 40, flags=["wat"])


# --- combinator (REQ-604) ---


def test_dominant_axis_reported(model) -> None:
    c = classify_diff(
        model,
        project="Maestro",
        paths=["contracts/observability/log-schema.json"],
        sha="a" * 40,
    )
    assert c.dominant_axis == "blast_radius"  # ecosystem-contract=critical beats contract=high


def test_floor_can_dominate(model) -> None:
    c = classify_diff(model, project="Maestro", paths=["README.md"], sha="a" * 40, profile="team")
    assert c.tier == "medium"
    assert c.dominant_axis == "floor"


def test_monotonicity_adding_files_never_lowers_tier(model) -> None:
    base = ["README.md"]
    grow = ["README.md", "maestro/models.py", ".github/workflows/ci.yml"]
    tiers = "low medium high critical".split()
    t1 = classify_diff(model, project="Maestro", paths=base, sha="a" * 40).tier
    t2 = classify_diff(model, project="Maestro", paths=grow, sha="a" * 40).tier
    assert tiers.index(t2) >= tiers.index(t1)


def test_mandatory_gates_follow_tier(model) -> None:
    c = classify_diff(model, project="Maestro", paths=["maestro/models.py"], sha="a" * 40)
    assert c.tier == "high"
    assert "human.owner_approval" in c.mandatory_gates
    low = classify_diff(model, project="Maestro", paths=["README.md"], sha="a" * 40)
    assert low.mandatory_gates == []


# --- two phases (REQ-605) ---


def test_ex_ante_from_declared_scope_globs(model) -> None:
    c = classify_declared(model, project="Maestro", scope=["contracts/**"], sha="a" * 40)
    assert c.phase == "ex_ante"
    assert c.inputs["change_class"] == "contract"


def test_ex_ante_broad_scope_includes_narrower_rules(model) -> None:
    # "maestro/**" may touch maestro/models.py -> state-machine must be included (fail-closed).
    c = classify_declared(model, project="Maestro", scope=["maestro/**"], sha="a" * 40)
    assert c.inputs["change_class"] == "state-machine"


def test_ex_post_scope_violation_escalates_and_flags(model) -> None:
    c = classify_diff(
        model,
        project="Maestro",
        paths=["README.md", "maestro/util.py"],
        sha="a" * 40,
        declared_scope=["README.md"],
    )
    assert "scope_violation" in c.flags
    assert c.tier == "high"


def test_ex_post_within_scope_no_flag(model) -> None:
    c = classify_diff(
        model,
        project="Maestro",
        paths=["maestro/util.py"],
        sha="a" * 40,
        declared_scope=["maestro/**"],
    )
    assert c.flags == []


# --- provenance ---


def test_classification_carries_sha_and_model_version(model) -> None:
    c = classify_diff(model, project="Maestro", paths=["README.md"], sha="f" * 40)
    assert c.sha == "f" * 40
    assert c.risk_model_version == model.version_sha


def test_ex_ante_repo_wide_scope_touches_contracts(model) -> None:
    # Regression (Copilot, PR #7): "**" has no fixed prefix and must still
    # conservatively count as touching contracts/** (worst registry entry).
    c = classify_declared(model, project="Maestro", scope=["**"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "ecosystem-contract"


def test_ex_ante_maestro_docs_scope_is_low(model) -> None:
    # Live governed-run finding: without a per-repo docs rule, an ex-ante
    # scope of docs/** classified unknown/medium (name-based generics are
    # excluded from ex-ante by design).
    c = classify_declared(model, project="Maestro", scope=["docs/**"], sha="a" * 40)
    assert c.inputs["change_class"] == "docs"
    assert c.tier == "low"


# --- atp-platform section (TASK-001) ---


def test_atp_agents_catalog_beats_method_policy(model) -> None:
    # Ordering-sensitive: the SSOT catalog rule must precede method/** —
    # reversed, first-match-wins would silently demote the catalog to policy.
    # Blast comes from the exact-file consumer_registry key (2 consumers ->
    # ecosystem-contract), so the tier is critical per REQ-003/DESIGN-003.
    c = classify_diff(
        model,
        project="atp-platform",
        paths=["method/agents-catalog.toml"],
        sha="a" * 40,
    )
    assert c.inputs["change_class"] == "contract"
    assert c.inputs["blast_radius"] == "ecosystem-contract"
    assert c.tier == "critical"


def test_atp_method_is_policy(model) -> None:
    # Nested path: ** crosses /, and a non-registry method/** file grades
    # policy/high without picking up the catalog's ecosystem blast.
    c = classify_diff(
        model,
        project="atp-platform",
        paths=["method/schemas/v2/eval-schema.json"],
        sha="a" * 40,
    )
    assert c.inputs["change_class"] == "policy"
    assert c.inputs["blast_radius"] == "single-repo"
    assert c.tier == "high"


def test_atp_method_readme_beats_generic_docs(model) -> None:
    # Section rules win over _generic: method/README.md is policy via
    # method/**, not docs via the _generic **/*.md rule.
    c = classify_diff(
        model,
        project="atp-platform",
        paths=["method/README.md"],
        sha="a" * 40,
    )
    assert c.inputs["change_class"] == "policy"
    assert c.tier == "high"


def test_atp_unmapped_path_falls_through_to_unknown(model) -> None:
    # DESIGN-001/REQ-603: no catch-all ** in the atp-platform section —
    # unmapped paths fall through _generic to the built-in unknown class.
    c = classify_diff(
        model,
        project="atp-platform",
        paths=["mystery/blob.bin"],
        sha="a" * 40,
    )
    assert c.inputs["change_class"] == "unknown"
    assert c.tier == "medium"


def test_atp_ci_templates_are_critical(model) -> None:
    c = classify_diff(
        model,
        project="atp-platform",
        paths=["ci-templates/python-ci.yml"],
        sha="a" * 40,
    )
    assert c.inputs["change_class"] == "ci-deploy"
    assert c.tier == "critical"


def test_atp_code_is_medium(model) -> None:
    c = classify_diff(model, project="atp-platform", paths=["atp/runner.py"], sha="a" * 40)
    assert c.inputs["change_class"] == "code"
    assert c.tier == "medium"


def test_ex_ante_atp_method_scope_is_critical(model) -> None:
    # Fail-closed ex-ante: a method/** scope may touch the registered catalog
    # (2 consumers), so blast grades ecosystem-contract -> critical.
    c = classify_declared(model, project="atp-platform", scope=["method/**"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "ecosystem-contract"
    assert c.tier == "critical"


def test_ex_ante_atp_code_scope_stays_single_repo(model) -> None:
    # A scope disjoint from the registered catalog must not pick up its blast.
    c = classify_declared(model, project="atp-platform", scope=["atp/**"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "single-repo"
    assert c.tier == "medium"


def test_atp_mixed_diff_is_order_independent(model) -> None:
    # Max across paths: the catalog's ecosystem blast must survive being
    # listed before or after lower-graded paths.
    paths = ["atp/runner.py", "method/agents-catalog.toml"]
    c = classify_diff(model, project="atp-platform", paths=paths, sha="a" * 40)
    r = classify_diff(model, project="atp-platform", paths=paths[::-1], sha="a" * 40)
    assert c.inputs["blast_radius"] == r.inputs["blast_radius"] == "ecosystem-contract"
    assert c.tier == r.tier == "critical"


def test_ex_ante_project_without_registry_entries(model) -> None:
    # dispatcher has no consumer_registry entries: the registry term of
    # touches_contracts is inert, and contracts/** still grades the
    # cross-repo fail-closed floor via the un-pinned lookup.
    c = classify_declared(model, project="dispatcher", scope=["contracts/**"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "cross-repo"
    c2 = classify_declared(model, project="dispatcher", scope=["dispatcher/**"], sha="a" * 40)
    assert c2.inputs["blast_radius"] == "single-repo"


# --- consumer_registry entry (TASK-002) ---


def test_atp_registry_entry_grades_broad_ex_ante_scope_ecosystem(model) -> None:
    # Discriminating check that the agents-catalog registry entry is loaded
    # and consumed: with 2 registered consumers the un-pinned lookup grades
    # atp-platform ecosystem-contract; without the entry it stays cross-repo.
    # Only blast_radius is asserted: the tier would be critical regardless,
    # via change_class (ci-templates/** -> ci-deploy intersects a "**" scope).
    c = classify_declared(model, project="atp-platform", scope=["**"], sha="a" * 40)
    assert c.inputs["blast_radius"] == "ecosystem-contract"
