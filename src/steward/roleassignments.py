"""Role assignments loader — identity → roles at the CODEOWNERS boundary (DEC-007 D6).

Maps a forge identity (strict grammar ``github:<login>``, with an optional
``[bot]`` suffix) to the catalog roles that identity may act as. This file is
the ONLY place identities acquire roles: a role claimed inside injected
approval facts is never authoritative — steward computes roles here, alone.
Unknown identities get no roles (authorization then fails closed downstream).

Fail-closed like the roles catalog: unknown keys, bad identity grammar, an
unresolvable or duplicated role, unreadable file, malformed YAML — all raise
:class:`AssignmentsError`, which the CLI maps to exit 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from steward.roles import RolesCatalog

__all__ = ["Assignment", "AssignmentsError", "RoleAssignments", "load_role_assignments"]

_TOP_LEVEL_KEYS = frozenset({"version", "assignments"})
_ASSIGNMENT_KEYS = frozenset({"roles"})
# GitHub login: alphanumerics and single hyphens, no leading/trailing hyphen;
# machine accounts carry a literal "[bot]" suffix. Defined ONCE, here — no
# other module may re-derive or normalize identities.
_IDENTITY_RE = re.compile(r"^github:[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9]))*(\[bot\])?$")


class AssignmentsError(ValueError):
    """Invalid role-assignments file: bad shape, identity grammar, or role ref."""


@dataclass(frozen=True)
class Assignment:
    """One identity's role grant."""

    identity: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class RoleAssignments:
    """A loaded, validated identity → roles mapping."""

    version: int
    assignments: tuple[Assignment, ...]

    def roles_for(self, identity: str) -> frozenset[str]:
        """Roles granted to ``identity``; unknown identities get none."""
        for assignment in self.assignments:
            if assignment.identity == identity:
                return frozenset(assignment.roles)
        return frozenset()


def load_role_assignments(path: Path, roles_catalog: RolesCatalog) -> RoleAssignments:
    """Load and validate the assignments file at ``path``.

    Raises:
        AssignmentsError: on any read, parse, or validation failure.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise AssignmentsError(f"role-assignments file {path}: cannot read ({err})") from err
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise AssignmentsError(f"role-assignments file {path}: malformed YAML ({err})") from err

    if not isinstance(data, dict):
        raise AssignmentsError(f"role-assignments file {path}: must be a mapping")
    unknown = set(data) - _TOP_LEVEL_KEYS
    if unknown:
        raise AssignmentsError(
            f"role-assignments file {path}: unknown top-level keys: "
            f"{', '.join(sorted(map(str, unknown)))}"
        )

    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise AssignmentsError(f"role-assignments file {path}: 'version' must be an integer >= 1")

    raw = data.get("assignments")
    if not isinstance(raw, dict):
        raise AssignmentsError(f"role-assignments file {path}: 'assignments' must be a mapping")

    assignments: list[Assignment] = []
    for identity, body in raw.items():
        if not isinstance(identity, str) or _IDENTITY_RE.fullmatch(identity) is None:
            raise AssignmentsError(
                f"role-assignments file {path}: identity {identity!r} does not match "
                "the required grammar 'github:<login>[bot-suffix optional]'"
            )
        if not isinstance(body, dict):
            raise AssignmentsError(
                f"role-assignments file {path}: assignment for {identity!r} must be a mapping"
            )
        unknown = set(body) - _ASSIGNMENT_KEYS
        if unknown:
            raise AssignmentsError(
                f"role-assignments file {path}: assignment for {identity!r} has unknown "
                f"keys: {', '.join(sorted(map(str, unknown)))}"
            )
        roles_raw = body.get("roles")
        if not isinstance(roles_raw, list) or not roles_raw:
            raise AssignmentsError(
                f"role-assignments file {path}: {identity!r}: 'roles' must be a "
                "non-empty list of role slugs"
            )
        slugs: list[str] = []
        for item in roles_raw:
            if not isinstance(item, str) or not item or "@" in item:
                raise AssignmentsError(
                    f"role-assignments file {path}: {identity!r}: 'roles' must be bare "
                    f"role slugs, got {item!r}"
                )
            if item in slugs:
                raise AssignmentsError(
                    f"role-assignments file {path}: {identity!r}: 'roles' has duplicate {item!r}"
                )
            if not roles_catalog.has(item):
                raise AssignmentsError(
                    f"role-assignments file {path}: {identity!r}: 'roles' references "
                    f"{item!r}, not in the roles catalog"
                )
            slugs.append(item)
        assignments.append(Assignment(identity=identity, roles=tuple(slugs)))

    return RoleAssignments(version=version, assignments=tuple(assignments))
