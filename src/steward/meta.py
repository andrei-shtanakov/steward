"""Artifact metadata: SpecMeta extended with steward governance fields (WS-001, REQ-002).

Reuses spec-runner's frontmatter parser and ``SpecMeta`` state shape (vendored,
DEC-003) and layers steward-only governance fields on top:

- ``owner_role`` → :attr:`ArtifactMeta.owner_roles` (CODEOWNERS roles, REQ-004)
- ``traces_to`` → :attr:`ArtifactMeta.traces_to` (upstream artifact / REQ / DEC /
  AC ids used by the traceability gate, REQ-003)

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


def parse_artifact(text: str) -> ArtifactMeta | None:
    """Parse artifact text into :class:`ArtifactMeta`, or ``None`` when unmanaged."""
    meta_dict, _ = split_frontmatter(text)
    if meta_dict is None or not meta_dict.get("spec_stage"):
        return None
    return ArtifactMeta(
        base=meta_from_dict(meta_dict),
        owner_roles=parse_owner_roles(meta_dict.get("owner_role")),
        traces_to=_parse_traces_to(meta_dict.get("traces_to")),
    )


def load_artifact(path: str | Path) -> ArtifactMeta | None:
    """Load and parse an artifact file, or ``None`` when unmanaged."""
    return parse_artifact(Path(path).read_text(encoding="utf-8"))


def _parse_traces_to(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise MetaError("'traces_to' must be a list of non-empty ids")
    return tuple(raw)
