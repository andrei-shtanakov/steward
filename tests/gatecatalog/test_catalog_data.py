"""Реальный gate-catalog.yaml: состав v1 и сверка с соседними словарями."""

from __future__ import annotations

from pathlib import Path

import yaml

from steward.gatecatalog import load_catalog

PROFILES = Path(__file__).resolve().parents[2] / "profiles"

EXPECTED_ACTIVE = {
    "GC-ARCH-CONFORMANCE",
    "GC-ARCH-EVIDENCE",
    "GC-ARCH-SCHEMA",
    "GC-BEH-COVERAGE",
    "GC-BEH-TRACE",
    "GC-CHECK-PLANNED",
    "GC-COMPILE",
    "GC-COMPLETENESS",
    "GC-DUP",
    "GC-GIT-BRANCH",
    "GC-GIT-ROLE",
    "GC-META",
    "GC-STAGE",
    "GC-STALE",
    "GC-STALE-KEY",
    "GC-STALE-UNPINNED",
    "GC-TRACE",
    "GC-TRACE-EMPTY",
    "GC-UPSTREAM",
}


def _catalog():
    return load_catalog(PROFILES / "gate-catalog.yaml", PROFILES / "roles.yaml")


def test_v1_composition_19_active_quality_plus_declared_approval():
    cat = _catalog()
    assert cat.active_ids() == frozenset(EXPECTED_ACTIVE)
    for gate_id in EXPECTED_ACTIVE:
        assert cat.entry(gate_id).obligation == "quality"
    approval = cat.entry("GC-APPROVAL-MISSING")
    assert approval is not None
    assert approval.status == "declared"
    assert approval.obligation == "approval"
    # решение владельца: applicable_roles ОПУЩЕН (через allowed_approver_roles артефакта)
    assert approval.applicable_roles is None
    # GC-APPROVAL-ROLE не резервировать до отдельного boundary-решения
    assert cat.entry("GC-APPROVAL-ROLE") is None


def test_obligation_vocabulary_is_exactly_quality_approval():
    assert set(_catalog().obligation_vocabulary) == {"quality", "approval"}


def test_arch_policy_stage_keys_subset_of_stage_vocabulary():
    cat = _catalog()
    policy = yaml.safe_load((PROFILES / "arch-policy.yaml").read_text())
    assert set(policy["stages"]) <= set(cat.stage_vocabulary)
