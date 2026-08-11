"""Загрузчик gate-catalog: fail-closed валидация каталога правил obligation.

Каждое правило валидации из дизайна (владелец, 2026-08-08) — отдельный тест;
особо: пустой applicable_roles — ОШИБКА, не синоним отсутствия (два
канонических представления одного состояния запрещены).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.gatecatalog import (
    CANONICAL_ID_PATTERN,
    PRODUCER_ID_PATTERN,
    CatalogError,
    load_catalog,
    load_catalog_files,
)
from steward.roles import load_roles_catalog

ROLES = (
    'version: 1\nslug_pattern: "^[a-z][a-z0-9-]{1,31}$"\n'
    "roles:\n  - {slug: qa, display: QA}\n  - {slug: owner, display: Owner}\n"
)

HEADER = (
    "version: 1\n"
    "obligation_vocabulary: [quality, approval]\n"
    "stage_vocabulary: [authoring, release]\n"
)


def _load(tmp_path: Path, gates_yaml: str, header: str = HEADER):
    catalog = tmp_path / "gate-catalog.yaml"
    catalog.write_text(header + "gates:\n" + gates_yaml)
    roles = tmp_path / "roles.yaml"
    roles.write_text(ROLES)
    return load_catalog(catalog, load_roles_catalog(roles))


def test_minimal_active_entry_loads(tmp_path):
    cat = _load(tmp_path, "  GC-TRACE:\n    obligation: quality\n    status: active\n")
    assert cat.version == 1
    assert cat.active_ids() == frozenset({"GC-TRACE"})
    entry = cat.entry("GC-TRACE")
    assert entry is not None
    assert entry.applicable_roles is None and entry.stages is None


def test_namespace_block_absent_falls_back_to_loader_rule(tmp_path):
    # HEADER не несёт gate_id_namespaces: блок опционален, правило — в коде.
    cat = _load(tmp_path, "  GC-TRACE:\n    obligation: quality\n    status: active\n")
    assert cat.canonical_id_pattern == CANONICAL_ID_PATTERN
    assert cat.producer_id_pattern == PRODUCER_ID_PATTERN


def test_producer_specific_id_rejected_in_catalog(tmp_path):
    # Ruling 2 (2026-08-12): такие id легальны на проводе, но принадлежат
    # producer'у — каталог именно то место, где их объявлять нельзя.
    with pytest.raises(CatalogError, match="producer-specific"):
        _load(
            tmp_path,
            "  maestro.validate_strict:\n    obligation: quality\n    status: active\n",
        )


def test_unknown_toplevel_key_rejected(tmp_path):
    # Механизм встречного обязательства: enforcement сюда не пролезет молча.
    header = HEADER + "enforcement: mandatory\n"
    with pytest.raises(CatalogError, match="enforcement"):
        _load(tmp_path, "  GC-TRACE:\n    obligation: quality\n    status: active\n", header)


def test_enforcement_token_in_obligation_vocabulary_rejected(tmp_path):
    header = (
        "version: 1\n"
        "obligation_vocabulary: [quality, approval, mandatory]\n"
        "stage_vocabulary: [authoring, release]\n"
    )
    with pytest.raises(CatalogError, match="enforcement-axis"):
        _load(tmp_path, "  GC-TRACE:\n    obligation: quality\n    status: active\n", header)


def test_namespace_mirror_divergence_rejected(tmp_path):
    # Блок — опубликованное зеркало правила, не ручка: расширенный паттерн
    # открыл бы зарезервированное пространство GC- чужому писателю.
    header = HEADER + (
        "gate_id_namespaces:\n"
        "  canonical_pattern: '^GC-.*$'\n"
        f"  producer_pattern: '{PRODUCER_ID_PATTERN}'\n"
    )
    with pytest.raises(CatalogError, match="published mirror"):
        _load(tmp_path, "  GC-TRACE:\n    obligation: quality\n    status: active\n", header)


def test_namespace_mirror_partial_keys_rejected(tmp_path):
    header = HEADER + f"gate_id_namespaces:\n  canonical_pattern: '{CANONICAL_ID_PATTERN}'\n"
    with pytest.raises(CatalogError, match="gate_id_namespaces keys"):
        _load(tmp_path, "  GC-TRACE:\n    obligation: quality\n    status: active\n", header)


def test_reserved_tokens_mirror_divergence_rejected(tmp_path):
    header = HEADER + "obligation_reserved_tokens: [mandatory]\n"
    with pytest.raises(CatalogError, match="obligation_reserved_tokens"):
        _load(tmp_path, "  GC-TRACE:\n    obligation: quality\n    status: active\n", header)


def test_declared_entry_is_not_active(tmp_path):
    cat = _load(
        tmp_path,
        "  GC-APPROVAL-MISSING:\n    obligation: approval\n    status: declared\n",
    )
    assert cat.active_ids() == frozenset()
    assert cat.entry("GC-APPROVAL-MISSING").status == "declared"


def test_empty_applicable_roles_is_error_not_agnostic(tmp_path):
    with pytest.raises(CatalogError, match="applicable_roles"):
        _load(
            tmp_path,
            "  GC-GIT-ROLE:\n    obligation: quality\n    status: active\n"
            "    applicable_roles: []\n",
        )


def test_applicable_roles_must_resolve_in_roles_catalog(tmp_path):
    with pytest.raises(CatalogError, match="ghost"):
        _load(
            tmp_path,
            "  GC-GIT-ROLE:\n    obligation: quality\n    status: active\n"
            "    applicable_roles: [ghost]\n",
        )


def test_obligation_outside_vocabulary_rejected(tmp_path):
    with pytest.raises(CatalogError, match="obligation"):
        _load(tmp_path, "  GC-X:\n    obligation: risk\n    status: active\n")


def test_stages_outside_vocabulary_rejected(tmp_path):
    with pytest.raises(CatalogError, match="stages"):
        _load(
            tmp_path,
            "  GC-X:\n    obligation: quality\n    status: active\n    stages: [shipping]\n",
        )


def test_bad_gate_id_grammar_rejected(tmp_path):
    with pytest.raises(CatalogError, match="gate_id"):
        _load(tmp_path, "  gc-lower:\n    obligation: quality\n    status: active\n")


def test_deprecated_requires_since_and_exactly_one_replacement(tmp_path):
    with pytest.raises(CatalogError, match="deprecated"):
        _load(tmp_path, "  GC-OLD:\n    obligation: quality\n    status: deprecated\n")
    cat = _load(
        tmp_path,
        "  GC-NEW:\n    obligation: quality\n    status: active\n"
        "  GC-OLD:\n    obligation: quality\n    status: deprecated\n"
        '    since: "2026-08-08"\n    replaced_by: GC-NEW\n',
    )
    assert cat.entry("GC-OLD").replaced_by == "GC-NEW"
    with pytest.raises(CatalogError, match="replaced_by"):
        _load(
            tmp_path,
            "  GC-OLD:\n    obligation: quality\n    status: deprecated\n"
            '    since: "2026-08-08"\n    replaced_by: GC-GHOST\n',
        )


def test_deprecated_with_empty_string_replaced_by_is_error(tmp_path):
    with pytest.raises(CatalogError, match="replaced_by"):
        _load(
            tmp_path,
            "  GC-OLD:\n    obligation: quality\n    status: deprecated\n"
            '    since: "2026-08-08"\n    replaced_by: ""\n',
        )


def test_deprecated_with_replacement_none_loads(tmp_path):
    cat = _load(
        tmp_path,
        "  GC-OLD:\n    obligation: quality\n    status: deprecated\n"
        '    since: "2026-08-08"\n    replacement: none\n',
    )
    entry = cat.entry("GC-OLD")
    assert entry.replacement_none is True
    assert entry.replaced_by is None


def test_replacement_non_none_value_rejected(tmp_path):
    with pytest.raises(CatalogError, match="replacement"):
        _load(
            tmp_path,
            "  GC-OLD:\n    obligation: quality\n    status: deprecated\n"
            '    since: "2026-08-08"\n    replacement: GC-NEW\n',
        )


def test_replacement_null_rejected(tmp_path):
    with pytest.raises(CatalogError, match="replacement"):
        _load(
            tmp_path,
            "  GC-OLD:\n    obligation: quality\n    status: deprecated\n"
            '    since: "2026-08-08"\n    replacement: null\n',
        )


def test_replacement_together_with_replaced_by_rejected(tmp_path):
    with pytest.raises(CatalogError, match="deprecated"):
        _load(
            tmp_path,
            "  GC-NEW:\n    obligation: quality\n    status: active\n"
            "  GC-OLD:\n    obligation: quality\n    status: deprecated\n"
            '    since: "2026-08-08"\n    replaced_by: GC-NEW\n    replacement: none\n',
        )


def test_replacement_none_key_rejected_spelling_forbidden(tmp_path):
    with pytest.raises(CatalogError, match="unknown key"):
        _load(
            tmp_path,
            "  GC-OLD:\n    obligation: quality\n    status: deprecated\n"
            '    since: "2026-08-08"\n    replacement_none: true\n',
        )


def test_unknown_entry_key_rejected(tmp_path):
    with pytest.raises(CatalogError, match="tier"):
        _load(
            tmp_path,
            "  GC-X:\n    obligation: quality\n    status: active\n    tier: high\n",
        )


def test_empty_catalog_file_is_catalog_error_not_traceback(tmp_path):
    catalog = tmp_path / "gate-catalog.yaml"
    catalog.write_text("")
    roles = tmp_path / "roles.yaml"
    roles.write_text(ROLES)
    with pytest.raises(CatalogError, match="mapping"):
        load_catalog_files(catalog, roles)


def test_list_toplevel_catalog_is_catalog_error(tmp_path):
    catalog = tmp_path / "gate-catalog.yaml"
    catalog.write_text("- just\n- a list\n")
    roles = tmp_path / "roles.yaml"
    roles.write_text(ROLES)
    with pytest.raises(CatalogError, match="mapping"):
        load_catalog_files(catalog, roles)


def test_role_entry_without_slug_is_catalog_error(tmp_path):
    # Now routed through the roles loader (Task 1): a shape defect in
    # roles.yaml surfaces as CatalogError, converted, no traceback.
    catalog = tmp_path / "gate-catalog.yaml"
    catalog.write_text(HEADER + "gates: {}\n")
    roles = tmp_path / "roles.yaml"
    roles.write_text('version: 1\nslug_pattern: "^[a-z]+$"\nroles:\n  - {display: NoSlug}\n')
    with pytest.raises(CatalogError, match="slug"):
        load_catalog_files(catalog, roles)


def test_duplicate_role_slug_now_fails_via_roles_loader(tmp_path: Path) -> None:
    # Before D1 the inline parser accepted duplicate slugs silently; the
    # roles loader must make this a CatalogError (converted, no traceback).
    catalog = tmp_path / "gate-catalog.yaml"
    catalog.write_text(HEADER + "gates: {}\n")
    roles = tmp_path / "roles.yaml"
    roles.write_text(
        'version: 1\nslug_pattern: "^[a-z]+$"\n'
        "roles:\n  - {slug: qa, display: A}\n  - {slug: qa, display: B}\n"
    )
    with pytest.raises(CatalogError, match="duplicate"):
        load_catalog_files(catalog, roles)
