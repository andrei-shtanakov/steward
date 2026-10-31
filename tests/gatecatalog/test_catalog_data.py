"""Реальный gate-catalog.yaml: состав v1 и сверка с соседними словарями."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from steward.gatecatalog import (
    CANONICAL_ID_PATTERN,
    PRODUCER_ID_PATTERN,
    RESERVED_OBLIGATION_TOKENS,
    load_catalog_files,
)

PROFILES = Path(__file__).resolve().parents[2] / "profiles"

EXPECTED_ACTIVE_QUALITY = {
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

EXPECTED_ACTIVE_APPROVAL = {
    "GC-APPROVAL-MISSING",
    # approval-facts v3 §7.2 (M0 Task 9): act mode and node status provider.
    "GC-ACT-MODE-CONFLICT",
    "GC-ACT-NOT-APPROVED",
    "GC-ACT-PROVIDER-MISSING",
}

EXPECTED_ACTIVE = EXPECTED_ACTIVE_QUALITY | EXPECTED_ACTIVE_APPROVAL


def _catalog():
    return load_catalog_files(PROFILES / "gate-catalog.yaml", PROFILES / "roles.yaml")


def test_v3_composition_23_active_19_quality_plus_4_approval():
    cat = _catalog()
    assert cat.active_ids() == frozenset(EXPECTED_ACTIVE)
    for gate_id in EXPECTED_ACTIVE_QUALITY:
        assert cat.entry(gate_id).obligation == "quality"
    approval = cat.entry("GC-APPROVAL-MISSING")
    assert approval is not None
    # AP-5 (2026-08-08): GC-APPROVAL-MISSING is active — the release-stage
    # merge-evidence policy (steward.gatecheck.approval.check_approval_evidence)
    # is implemented and wired into the CLI.
    assert approval.status == "active"
    assert approval.obligation == "approval"
    # решение владельца: applicable_roles ОПУЩЕН (через allowed_approver_roles артефакта)
    assert approval.applicable_roles is None
    # GC-APPROVAL-ROLE не резервировать до отдельного boundary-решения
    assert cat.entry("GC-APPROVAL-ROLE") is None


def test_act_gates_are_approval_obligations_on_both_stages():
    # approval-facts v3 §7.2.2: «не одобрен» — находка, а не подмена статуса;
    # три кода — записи каталога (obligation approval, authoring и release).
    # Новые записи меняют состав, поэтому версия каталога поднята до 3.
    cat = _catalog()
    assert cat.version == 3
    for gate_id in ("GC-ACT-MODE-CONFLICT", "GC-ACT-PROVIDER-MISSING", "GC-ACT-NOT-APPROVED"):
        entry = cat.entry(gate_id)
        assert entry is not None, gate_id
        assert (entry.obligation, entry.status) == ("approval", "active")
        assert set(entry.stages) == {"authoring", "release"}
        assert entry.applicable_roles is None


def test_obligation_vocabulary_is_exactly_quality_approval():
    assert set(_catalog().obligation_vocabulary) == {"quality", "approval"}


def test_namespace_mirror_is_published_for_vendoring_consumers():
    # Maestro вендорит файл, не загрузчик: паттерны обязаны быть в самом
    # каталоге и совпадать с правилом кода (steward#62 / maestro#160).
    raw = yaml.safe_load((PROFILES / "gate-catalog.yaml").read_text())
    assert raw["gate_id_namespaces"] == {
        "canonical_pattern": CANONICAL_ID_PATTERN,
        "producer_pattern": PRODUCER_ID_PATTERN,
    }
    assert set(raw["obligation_reserved_tokens"]) == RESERVED_OBLIGATION_TOKENS


def test_every_catalog_id_is_canonical_and_no_id_is_producer_shaped():
    cat = _catalog()
    for gate_id in cat.gates:
        assert re.fullmatch(CANONICAL_ID_PATTERN, gate_id), gate_id
        assert not re.fullmatch(PRODUCER_ID_PATTERN, gate_id), gate_id


@pytest.mark.parametrize(
    "gate_id",
    ["steward.risk_classify_tier", "human.owner_approval", "maestro.validate_strict"],
)
def test_maestro_producer_ids_are_conformant_and_unmapped(gate_id: str):
    # Ruling 3: три существующих id Maestro остаются как есть — форма уже
    # конформна producer-паттерну, канонического GC-* соответствия им не
    # выдаётся, и каталог о них ничего не знает.
    assert re.fullmatch(PRODUCER_ID_PATTERN, gate_id)
    assert not re.fullmatch(CANONICAL_ID_PATTERN, gate_id)
    assert _catalog().entry(gate_id) is None


def test_arch_policy_stage_keys_subset_of_stage_vocabulary():
    cat = _catalog()
    policy = yaml.safe_load((PROFILES / "arch-policy.yaml").read_text())
    assert set(policy["stages"]) <= set(cat.stage_vocabulary)
