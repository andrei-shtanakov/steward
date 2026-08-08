"""Gate catalog loader with fail-closed validation.

Stability policy:
  - gate_id never changes AND never reused (after deprecated, the id is forever
    occupied by the tombstone entry).
  - Removing a gate uses deprecated status with tombstone: exactly one of
    replaced_by: GC-NEW (points to active successor) or replacement: none
    (the literal string "none" — no other value is accepted).
  - ANY change in catalog composition (entries added/removed/status changed)
    requires version bump. Vocabulary changes (obligation/stage items
    added/removed) also require version bump.

Rule: applicable_roles field is canonical. If absent, gate is role-agnostic
(None). Empty list [] is an error — never conflate absence with empty.
Non-empty lists must resolve to existing role slugs from roles.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from steward.roles import RolesCatalog, RolesError, load_roles_catalog


class CatalogError(ValueError):
    """Catalog validation error, always names gate_id and field."""

    pass


@dataclass(frozen=True)
class GateEntry:
    """Single gate entry in the catalog."""

    gate_id: str
    obligation: str
    status: Literal["active", "declared", "deprecated"]
    title: str | None = None
    stages: tuple[str, ...] | None = None
    applicable_roles: tuple[str, ...] | None = None
    since: str | None = None
    replaced_by: str | None = None
    replacement_none: bool = False


@dataclass(frozen=True)
class GateCatalog:
    """Immutable gate catalog with lookups."""

    version: int
    obligation_vocabulary: tuple[str, ...]
    stage_vocabulary: tuple[str, ...]
    gates: dict[str, GateEntry]

    def active_ids(self) -> frozenset[str]:
        """Return frozenset of gate_ids with status='active'."""
        return frozenset(
            gate_id for gate_id, entry in self.gates.items() if entry.status == "active"
        )

    def entry(self, gate_id: str) -> GateEntry | None:
        """Return entry by gate_id or None."""
        return self.gates.get(gate_id)


def _check_version(data: dict) -> int:
    """Validate and return version field."""
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise CatalogError("version must be int >= 1")
    return version


def _check_vocabulary(vocab: list, name: str) -> tuple[str, ...]:
    """Validate vocabulary list is non-empty and return as tuple."""
    if not vocab or not isinstance(vocab, list):
        raise CatalogError(f"{name} must be non-empty list")
    return tuple(vocab)


def _check_gate_id(gate_id: str) -> None:
    """Validate gate_id matches pattern ^GC-[A-Z0-9]+(-[A-Z0-9]+)*$."""
    pattern = r"^GC-[A-Z0-9]+(-[A-Z0-9]+)*$"
    if not re.match(pattern, gate_id):
        raise CatalogError(f"gate_id '{gate_id}' does not match pattern {pattern}")


def _check_obligation(obligation: str, vocab: tuple[str, ...], gate_id: str) -> None:
    """Validate obligation is in vocabulary."""
    if obligation not in vocab:
        raise CatalogError(f"gate_id '{gate_id}': obligation '{obligation}' not in vocabulary")


def _check_status(status: str, gate_id: str) -> None:
    """Validate status is one of the allowed values."""
    if status not in ("active", "declared", "deprecated"):
        raise CatalogError(
            f"gate_id '{gate_id}': status '{status}' must be 'active', 'declared', or 'deprecated'"
        )


def _check_stages(
    stages: list | None, vocab: tuple[str, ...], gate_id: str
) -> tuple[str, ...] | None:
    """Validate stages if present: non-empty and all in vocabulary."""
    if stages is None:
        return None
    if not isinstance(stages, list) or not stages:
        raise CatalogError(f"gate_id '{gate_id}': stages must be non-empty list")
    for stage in stages:
        if stage not in vocab:
            raise CatalogError(f"gate_id '{gate_id}': stages: '{stage}' not in vocabulary")
    return tuple(stages)


def _check_applicable_roles(
    roles_field: list | None, available_roles: frozenset[str], gate_id: str
) -> tuple[str, ...] | None:
    """Validate applicable_roles: absent=None, empty list=error, else resolve."""
    if roles_field is None:
        return None
    if isinstance(roles_field, list):
        if not roles_field:
            raise CatalogError(
                f"gate_id '{gate_id}': applicable_roles empty list forbidden "
                "— omit field for role-agnostic"
            )
        for role in roles_field:
            if role not in available_roles:
                raise CatalogError(
                    f"gate_id '{gate_id}': applicable_roles contains '{role}' not in roles.yaml"
                )
        return tuple(roles_field)
    raise CatalogError(f"gate_id '{gate_id}': applicable_roles must be list or absent")


def _check_deprecated_fields(
    entry_dict: dict, status: str, gate_id: str, all_gate_ids: frozenset[str]
) -> tuple[str | None, str | None, bool]:
    """Validate deprecated/non-deprecated field combinations.

    The YAML tombstone form for "no successor" is ``replacement: none`` — the
    literal string "none", not the YAML null/``replacement_none``. Any other
    ``replacement`` value (including YAML null) is an error.

    Returns (since, replaced_by, replacement_none).
    """
    since = entry_dict.get("since")
    replaced_by = entry_dict.get("replaced_by")
    has_replacement_key = "replacement" in entry_dict
    replacement_value = entry_dict.get("replacement")

    if has_replacement_key and replacement_value != "none":
        raise CatalogError(
            f"gate_id '{gate_id}': replacement '{replacement_value!r}' must be "
            "the literal string 'none' (no successor)"
        )
    replacement_none = has_replacement_key

    if status == "deprecated":
        if not since:
            raise CatalogError(f"gate_id '{gate_id}': deprecated status requires 'since' field")
        # Exactly one of replaced_by or replacement: none
        has_replaced = replaced_by is not None
        if not (has_replaced ^ replacement_none):  # XOR: exactly one
            raise CatalogError(
                f"gate_id '{gate_id}': deprecated must have exactly one of "
                "'replaced_by' or 'replacement: none'"
            )
        if has_replaced:
            if not replaced_by or replaced_by not in all_gate_ids:
                raise CatalogError(
                    f"gate_id '{gate_id}': replaced_by '{replaced_by}' not found in gates"
                )
    else:
        # Non-deprecated: these fields forbidden
        if since or replaced_by or replacement_none:
            raise CatalogError(
                f"gate_id '{gate_id}': status={status} forbids since/replaced_by/replacement fields"
            )

    return since, replaced_by, replacement_none


def _check_unknown_keys(entry_dict: dict, allowed_keys: set[str], gate_id: str) -> None:
    """Fail on unknown keys (closed form validation)."""
    unknown = set(entry_dict.keys()) - allowed_keys
    if unknown:
        raise CatalogError(f"gate_id '{gate_id}': unknown key(s) {sorted(unknown)}")


def load_catalog(catalog_path: Path, roles: RolesCatalog) -> GateCatalog:
    """Load and validate gate catalog from YAML, resolving roles against ``roles``.

    Args:
        catalog_path: Path to gate-catalog.yaml
        roles: Already-loaded roles catalog (see ``steward.roles``), used to
            validate ``applicable_roles`` entries against known slugs.

    Returns:
        GateCatalog with all entries validated.

    Raises:
        CatalogError: On any validation failure.
    """
    available_roles = roles.slugs()

    # Load catalog
    with open(catalog_path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise CatalogError(f"catalog file {catalog_path} must be a mapping")

    # Validate top-level structure
    version = _check_version(data)
    obligation_vocab = _check_vocabulary(data.get("obligation_vocabulary"), "obligation_vocabulary")
    stage_vocab = _check_vocabulary(data.get("stage_vocabulary"), "stage_vocabulary")

    gates_dict = data.get("gates", {})
    if not isinstance(gates_dict, dict):
        raise CatalogError("gates must be a dict")

    # First pass: collect all gate_ids for cross-references
    all_gate_ids = frozenset(gates_dict.keys())

    # Validate each gate
    entries: dict[str, GateEntry] = {}
    allowed_keys = {
        "obligation",
        "status",
        "title",
        "stages",
        "applicable_roles",
        "since",
        "replaced_by",
        "replacement",
    }

    for gate_id, entry_dict in gates_dict.items():
        _check_gate_id(gate_id)

        if not isinstance(entry_dict, dict):
            raise CatalogError(f"gate_id '{gate_id}': entry must be a dict")

        _check_unknown_keys(entry_dict, allowed_keys, gate_id)

        obligation = entry_dict.get("obligation")
        if not obligation:
            raise CatalogError(f"gate_id '{gate_id}': obligation field required")
        _check_obligation(obligation, obligation_vocab, gate_id)

        status = entry_dict.get("status")
        if not status:
            raise CatalogError(f"gate_id '{gate_id}': status field required")
        _check_status(status, gate_id)

        title = entry_dict.get("title")
        stages = _check_stages(entry_dict.get("stages"), stage_vocab, gate_id)
        applicable_roles = _check_applicable_roles(
            entry_dict.get("applicable_roles"), available_roles, gate_id
        )

        since, replaced_by, replacement_none = _check_deprecated_fields(
            entry_dict, status, gate_id, all_gate_ids
        )

        entries[gate_id] = GateEntry(
            gate_id=gate_id,
            obligation=obligation,
            status=status,
            title=title,
            stages=stages,
            applicable_roles=applicable_roles,
            since=since,
            replaced_by=replaced_by,
            replacement_none=replacement_none,
        )

    return GateCatalog(
        version=version,
        obligation_vocabulary=obligation_vocab,
        stage_vocabulary=stage_vocab,
        gates=entries,
    )


def load_catalog_files(catalog_path: Path, roles_path: Path) -> GateCatalog:
    """File-level convenience: load roles.yaml, then the catalog.

    Converts RolesError to CatalogError so callers keep a single
    configuration-error type (no copied validation, no traceback).
    """
    try:
        roles = load_roles_catalog(roles_path)
    except RolesError as err:
        raise CatalogError(str(err)) from err
    return load_catalog(catalog_path, roles)
