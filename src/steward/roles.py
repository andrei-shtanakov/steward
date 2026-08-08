"""Roles catalog loader — the DEC-007 role-identity SSOT (profiles/roles.yaml).

Single reader of the catalog file. Fail-closed like ``gatecatalog``: any
shape defect — unknown keys, duplicate slugs, a slug that does not fullmatch
``slug_pattern``, unreadable file, malformed YAML — raises :class:`RolesError`,
which the CLI maps to exit 2 (configuration error). Nothing else in the code
base may parse roles.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

__all__ = ["Role", "RolesCatalog", "RolesError", "load_roles_catalog"]

_TOP_LEVEL_KEYS = frozenset({"version", "slug_pattern", "roles"})
_ROLE_KEYS = frozenset({"slug", "display"})


class RolesError(ValueError):
    """Invalid roles catalog: bad shape, duplicate slug, or pattern mismatch."""


@dataclass(frozen=True)
class Role:
    """One role identity: stable machine slug + human display name."""

    slug: str
    display: str


@dataclass(frozen=True)
class RolesCatalog:
    """A loaded, validated role catalog (DEC-007)."""

    version: int
    slug_pattern: str
    roles: tuple[Role, ...]

    def slugs(self) -> frozenset[str]:
        """All role slugs in the catalog."""
        return frozenset(role.slug for role in self.roles)

    def has(self, slug: str) -> bool:
        """Whether ``slug`` names a catalog role."""
        return any(role.slug == slug for role in self.roles)


def load_roles_catalog(path: Path) -> RolesCatalog:
    """Load and validate the roles catalog at ``path``.

    Raises:
        RolesError: on any read, parse, or validation failure.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise RolesError(f"roles file {path}: cannot read ({err})") from err
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise RolesError(f"roles file {path}: malformed YAML ({err})") from err

    if not isinstance(data, dict):
        raise RolesError(f"roles file {path}: must be a mapping")
    unknown = set(data) - _TOP_LEVEL_KEYS
    if unknown:
        raise RolesError(
            f"roles file {path}: unknown top-level keys: {', '.join(sorted(map(str, unknown)))}"
        )

    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise RolesError(f"roles file {path}: 'version' must be an integer >= 1")

    slug_pattern = data.get("slug_pattern")
    if not isinstance(slug_pattern, str) or not slug_pattern:
        raise RolesError(f"roles file {path}: 'slug_pattern' must be a non-empty string")
    try:
        pattern = re.compile(slug_pattern)
    except re.error as err:
        raise RolesError(f"roles file {path}: 'slug_pattern' is not a valid regex ({err})") from err

    raw_roles = data.get("roles")
    if not isinstance(raw_roles, list) or not raw_roles:
        raise RolesError(f"roles file {path}: 'roles' must be a non-empty list")

    seen: set[str] = set()
    roles: list[Role] = []
    for entry in raw_roles:
        if not isinstance(entry, dict):
            raise RolesError(f"roles file {path}: every role entry must be a mapping")
        unknown = set(entry) - _ROLE_KEYS
        if unknown:
            raise RolesError(
                f"roles file {path}: role entry has unknown keys: "
                f"{', '.join(sorted(map(str, unknown)))}"
            )
        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug:
            raise RolesError(f"roles file {path}: every role entry needs a string 'slug'")
        if slug in seen:
            raise RolesError(f"roles file {path}: duplicate slug {slug!r}")
        seen.add(slug)
        if pattern.fullmatch(slug) is None:
            raise RolesError(
                f"roles file {path}: slug {slug!r} does not match slug_pattern {slug_pattern!r}"
            )
        display = entry.get("display")
        if not isinstance(display, str) or not display:
            raise RolesError(f"roles file {path}: role {slug!r} needs a non-empty string 'display'")
        roles.append(Role(slug=slug, display=display))

    return RolesCatalog(version=version, slug_pattern=slug_pattern, roles=tuple(roles))
