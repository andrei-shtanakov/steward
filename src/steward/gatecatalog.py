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

Namespace ruling (owner, 2026-08-12, steward#62 / maestro#160):
  - ``GC-`` is a reserved, closed namespace minted only by this catalog.
  - Producer-specific ids are allowed outside it as ``<namespace>.<name>``
    (PRODUCER_ID_PATTERN), owned by the emitting producer; steward defines
    neither their semantics nor an alias into GC-*. Lowercase-initial by
    construction, so the two namespaces are disjoint on case alone.
  - ``obligation`` carries INTENT and is catalog-owned; ENFORCEMENT
    (blocking vs advisory) is a per-run policy of the consumer. Standing
    cross-repo commitment, enforced by RESERVED_OBLIGATION_TOKENS and the
    closed top-level key set: this catalog never defines an ``enforcement``
    key and never admits ``mandatory`` / ``advisory`` as obligations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from steward.roles import RolesCatalog, RolesError, load_roles_catalog


CANONICAL_ID_PATTERN = r"^GC-[A-Z0-9]+(-[A-Z0-9]+)*$"
"""Reserved namespace: only this catalog mints ids matching it."""

PRODUCER_ID_PATTERN = r"^[a-z][a-z0-9-]*\.[a-z0-9_]+(\.[a-z0-9_]+)*$"
"""Producer-specific ids, owned by their emitting producer, never by steward."""

RESERVED_OBLIGATION_TOKENS = frozenset({"mandatory", "advisory"})
"""Enforcement-axis tokens permanently barred from obligation_vocabulary."""

TOPLEVEL_KEYS = frozenset(
    {
        "version",
        "gate_id_namespaces",
        "obligation_vocabulary",
        "obligation_reserved_tokens",
        "stage_vocabulary",
        "gates",
    }
)
"""Closed top-level key set — this is what bars a future ``enforcement:`` key."""


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
    canonical_id_pattern: str = CANONICAL_ID_PATTERN
    producer_id_pattern: str = PRODUCER_ID_PATTERN

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


def _check_toplevel_keys(data: dict) -> None:
    """Fail on unknown top-level keys (closed form, mirrors per-entry rule).

    This is the mechanism behind the standing cross-repo commitment: an
    ``enforcement:`` key cannot appear here without an explicit code change,
    so a consumer owning that axis can never collide with the catalog.
    """
    unknown = set(data.keys()) - set(TOPLEVEL_KEYS)
    if unknown:
        # key=repr: a YAML mapping may mix string and non-string keys, and a
        # bare sorted() would then raise TypeError — a defect must surface as
        # a configuration error, never as a traceback.
        raise CatalogError(f"unknown top-level key(s) {sorted(unknown, key=repr)}")


def _check_namespaces(data: dict) -> tuple[str, str]:
    """Validate the optional ``gate_id_namespaces`` published mirror.

    The block is a mirror of the loader's rule for consumers vendoring a
    pinned copy, not a knob: any divergence from the module constants is an
    error, because a locally widened pattern would open the reserved
    ``GC-`` namespace to a producer.
    """
    block = data.get("gate_id_namespaces")
    if block is None:
        return CANONICAL_ID_PATTERN, PRODUCER_ID_PATTERN
    if not isinstance(block, dict):
        raise CatalogError("gate_id_namespaces must be a mapping")
    expected = {
        "canonical_pattern": CANONICAL_ID_PATTERN,
        "producer_pattern": PRODUCER_ID_PATTERN,
    }
    if set(block.keys()) != set(expected):
        raise CatalogError(
            f"gate_id_namespaces keys must be exactly {sorted(expected)}, "
            f"got {sorted(block.keys())}"
        )
    for key, canonical in expected.items():
        if block[key] != canonical:
            raise CatalogError(
                f"gate_id_namespaces.{key} diverges from the loader rule "
                f"{canonical!r} — the block is a published mirror, not a knob"
            )
    return expected["canonical_pattern"], expected["producer_pattern"]


def _check_vocabulary(vocab: list, name: str) -> tuple[str, ...]:
    """Validate vocabulary list is non-empty and return as tuple."""
    if not vocab or not isinstance(vocab, list):
        raise CatalogError(f"{name} must be non-empty list")
    return tuple(vocab)


def _check_obligation_vocabulary(data: dict) -> tuple[str, ...]:
    """Validate the obligation vocabulary and its reserved-token commitment."""
    vocab = _check_vocabulary(data.get("obligation_vocabulary"), "obligation_vocabulary")
    reserved = sorted(RESERVED_OBLIGATION_TOKENS & set(vocab))
    if reserved:
        raise CatalogError(
            f"obligation_vocabulary contains enforcement-axis token(s) {reserved} — "
            "obligation carries intent; enforcement belongs to the consumer"
        )
    declared = data.get("obligation_reserved_tokens")
    if declared is not None and not _mirrors_reserved_tokens(declared):
        raise CatalogError(
            "obligation_reserved_tokens must be a list of unique strings equal to "
            f"{sorted(RESERVED_OBLIGATION_TOKENS)} — it is a published mirror "
            "of the loader rule, not a knob"
        )
    return vocab


def _mirrors_reserved_tokens(declared: object) -> bool:
    """Whether ``declared`` is a faithful list mirror of the reserved tokens.

    Shape is checked before the comparison: a bare ``set(declared)`` would
    turn a string into its characters and a non-iterable into a TypeError,
    trading a configuration error for a traceback. Duplicates are rejected —
    a mirror with two entries for one token no longer mirrors anything.
    """
    if not isinstance(declared, list) or not all(isinstance(t, str) for t in declared):
        return False
    return len(set(declared)) == len(declared) and set(declared) == RESERVED_OBLIGATION_TOKENS


def _check_gate_id(gate_id: str, canonical_pattern: str, producer_pattern: str) -> None:
    """Validate gate_id lives in the reserved ``GC-`` namespace.

    A producer-shaped id is rejected with its own message: such ids are
    legal on the wire but are owned by their producer, so the catalog is
    exactly the wrong place to declare one.
    """
    if re.fullmatch(canonical_pattern, gate_id):
        return
    if re.fullmatch(producer_pattern, gate_id):
        raise CatalogError(
            f"gate_id '{gate_id}' is producer-specific — such ids are owned by "
            "their producer and are never declared in this catalog"
        )
    raise CatalogError(f"gate_id '{gate_id}' does not match pattern {canonical_pattern}")


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
    _check_toplevel_keys(data)
    version = _check_version(data)
    canonical_pattern, producer_pattern = _check_namespaces(data)
    obligation_vocab = _check_obligation_vocabulary(data)
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
        _check_gate_id(gate_id, canonical_pattern, producer_pattern)

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
        canonical_id_pattern=canonical_pattern,
        producer_id_pattern=producer_pattern,
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
