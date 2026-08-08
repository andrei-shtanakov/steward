"""Artifact metadata: SpecMeta extended with steward governance fields (WS-001, REQ-002).

Reuses spec-runner's frontmatter parser and ``SpecMeta`` state shape (vendored,
DEC-003) and layers steward-only governance fields on top:

- ``owner_role`` → :attr:`ArtifactMeta.owner_role` and :attr:`ArtifactMeta.owner_roles`
  (DEC-007 canonical v2, 2026-08-08). Canonical form: single slug without '@' or ',' →
  ``owner_role="slug"`` and ``owner_roles=("slug",)``. Legacy form ("@a[,@b]" or comma
  multiplicity): ``owner_role=None``, ``owner_roles=tuple``. The reader never silently
  picks an accountable owner from a legacy tuple (REQ-004, DEC-007 D3).
- ``reviewer_roles`` → :attr:`ArtifactMeta.reviewer_roles` (canonical-only array; absent
  → ``()``, explicit empty → MetaError)
- ``allowed_approver_roles`` → :attr:`ArtifactMeta.allowed_approver_roles` (canonical-only;
  ``None`` = absent—downstream default applies; explicit empty → MetaError)
- ``traces_to`` → :attr:`ArtifactMeta.traces_to` (upstream artifact / REQ / DEC /
  AC ids used by the traceability gate, REQ-003)
- ``upstream_hashes`` → :attr:`ArtifactMeta.upstream_hashes` (git blob hash of
  each upstream artifact, stamped at approval time; the stale-cascade gate
  compares them against the current tree, REQ-206 / DESIGN-207)

A file with no frontmatter, or whose frontmatter carries no ``spec_stage``, is
*unmanaged* and parses to ``None`` — this is the passthrough gate-check relies on
so unrelated files never block a PR (REQ-208).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from steward._vendor.spec_meta import (
    SPEC_META_CONTRACT,
    SpecMeta,
    meta_from_dict,
    split_frontmatter,
)

__all__ = [
    "SPEC_META_CONTRACT",
    "ArtifactMeta",
    "MetaError",
    "load_artifact",
    "parse_artifact",
    "parse_owner_roles",
]


# Mirrors the vendored ``_FM_DELIM`` — a document opening with this is asserting
# it has frontmatter, so a parse failure past this point is an error, not "unmanaged".
_FRONTMATTER_START = "---\n"


class MetaError(ValueError):
    """Malformed artifact frontmatter (a governance field has the wrong type)."""


@dataclass(frozen=True)
class ArtifactMeta:
    """A managed artifact's frontmatter: base :class:`SpecMeta` plus governance fields.

    The base SpecMeta stays the single source of truth for the shared fields;
    the hot governance fields are exposed as read-only properties so callers need
    not reach through ``base``.
    """

    base: SpecMeta
    owner_roles: tuple[str, ...] = ()
    traces_to: tuple[str, ...] = ()
    upstream_hashes: tuple[tuple[str, str], ...] = ()  # (upstream node id, blob hash) pairs
    # DEC-007 canonical v2 (2026-08-08). owner_role is set ONLY when the
    # frontmatter carries the canonical singular form (one slug, no '@');
    # a legacy "@a[,@b]" string parses into owner_roles alone — the reader
    # never picks an accountable owner from a legacy tuple.
    owner_role: str | None = None
    reviewer_roles: tuple[str, ...] = ()
    # None = field absent (downstream default: {owner_role}); an explicit
    # empty list is a MetaError — absent and empty are different states.
    allowed_approver_roles: tuple[str, ...] | None = None

    @property
    def spec_stage(self) -> str:
        return self.base.spec_stage

    @property
    def status(self) -> str:
        return self.base.status

    @property
    def version(self) -> int:
        return self.base.version

    @property
    def approved_by(self) -> str | None:
        return self.base.approved_by

    @property
    def approved_at(self) -> str | None:
        return self.base.approved_at


def parse_owner_roles(raw: object) -> tuple[str, ...]:
    """Parse a CODEOWNERS ``owner_role`` string (``"@a,@b"``) into a role tuple."""
    if raw is None or raw == "":
        return ()
    if not isinstance(raw, str):
        raise MetaError("'owner_role' must be a string")
    return tuple(role.strip() for role in raw.split(",") if role.strip())


def _split_owner_role(raw: object) -> tuple[str | None, tuple[str, ...]]:
    """Return (canonical_owner_role, owner_roles) per DEC-007.

    Canonical: a single slug with no '@' and no ',' → ``(slug, (slug,))``.
    Legacy ``"@a[,@b]"`` (or any comma form): preserved as a tuple with NO
    automatic choice of accountable owner → ``(None, tuple)``.
    """
    roles = parse_owner_roles(raw)
    if len(roles) == 1 and "@" not in roles[0]:
        return roles[0], roles
    return None, roles


def _parse_role_array(raw: object, field: str) -> tuple[str, ...] | None:
    """Parse a canonical-only role array (``reviewer_roles`` etc.).

    Absent → None (caller decides the default). Present must be a non-empty
    list of unique slugs without '@' — the legacy spelling never leaks in.
    """
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise MetaError(f"'{field}' must be a non-empty list of role slugs (or absent)")
    slugs: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise MetaError(f"'{field}' must be a non-empty list of role slugs (or absent)")
        slug = item.strip()
        if "@" in slug:
            raise MetaError(f"'{field}' carries legacy '@' spelling: {slug!r} (use bare slugs)")
        if slug in slugs:
            raise MetaError(f"'{field}' has duplicate slug {slug!r}")
        slugs.append(slug)
    return tuple(slugs)


def parse_artifact(text: str) -> ArtifactMeta | None:
    """Parse artifact text into :class:`ArtifactMeta`, or ``None`` when unmanaged.

    A file that opens a frontmatter block but whose block cannot be parsed
    (malformed YAML, missing closing delimiter, non-mapping) raises
    :class:`MetaError` rather than parsing as unmanaged — otherwise a typo would
    let a managed artifact bypass governance.
    """
    meta_dict, _ = split_frontmatter(text)
    if meta_dict is None:
        if text.startswith(_FRONTMATTER_START):
            raise MetaError("frontmatter is present but could not be parsed")
        return None

    stage = meta_dict.get("spec_stage")
    if stage is None or (isinstance(stage, str) and not stage.strip()):
        return None  # no recognized spec_stage → unmanaged passthrough (REQ-208)
    if not isinstance(stage, str):
        raise MetaError("'spec_stage' must be a string")

    owner_role, owner_roles = _split_owner_role(meta_dict.get("owner_role"))
    reviewer_roles = _parse_role_array(meta_dict.get("reviewer_roles"), "reviewer_roles")
    return ArtifactMeta(
        base=meta_from_dict(meta_dict),
        owner_roles=owner_roles,
        traces_to=_parse_traces_to(meta_dict.get("traces_to")),
        upstream_hashes=_parse_upstream_hashes(meta_dict.get("upstream_hashes")),
        owner_role=owner_role,
        reviewer_roles=reviewer_roles if reviewer_roles is not None else (),
        allowed_approver_roles=_parse_role_array(
            meta_dict.get("allowed_approver_roles"), "allowed_approver_roles"
        ),
    )


def load_artifact(path: str | Path) -> ArtifactMeta | None:
    """Load and parse an artifact file, or ``None`` when unmanaged."""
    return parse_artifact(Path(path).read_text(encoding="utf-8"))


def _parse_upstream_hashes(raw: object) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise MetaError("'upstream_hashes' must map upstream id -> blob hash")
    pairs: list[tuple[str, str]] = []
    for node_id, blob in raw.items():
        if not isinstance(node_id, str) or not node_id.strip():
            raise MetaError("'upstream_hashes' must map upstream id -> blob hash")
        if not isinstance(blob, str) or not blob.strip():
            raise MetaError("'upstream_hashes' must map upstream id -> blob hash")
        pairs.append((node_id.strip(), blob.strip()))
    return tuple(pairs)


def _parse_traces_to(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise MetaError("'traces_to' must be a list of non-empty ids")
    ids: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise MetaError("'traces_to' must be a list of non-empty ids")
        ids.append(item.strip())
    return tuple(ids)
