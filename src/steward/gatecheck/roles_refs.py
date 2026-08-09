"""Resolution of frontmatter role references against the roles catalog.

DEC-007 D3: an unresolvable role slug in a managed artifact's frontmatter is
a defect of governance DATA — a configuration error (exit 2), not a finding
about the checked product. Messages must name the artifact path, the field,
and the offending slug so the defect is fixable from the message alone.

DEC-009 extends the same rule to the nested behaviour-spec fields
``structural_coverage[].obligation.owner_role``: a separate schema
(per-obligation accountable role, NOT the artifact's governance owner), but
the role identity is shared — the value is a canonical slug without ``@``
that must resolve in the catalog. No normalization: a legacy ``"@architects"``
simply fails to resolve and is reported verbatim.
"""

from __future__ import annotations

from steward._vendor.spec_meta import split_frontmatter
from steward.gatecheck.checks import Artifact
from steward.roles import RolesCatalog

__all__ = ["unresolved_role_refs"]


def unresolved_role_refs(artifacts: list[Artifact], roles: RolesCatalog) -> list[str]:
    """One message per unresolvable role reference, empty when all resolve."""
    problems: list[str] = []
    for artifact in artifacts:
        meta = artifact.meta
        if meta.owner_role is not None:
            _check(problems, roles, artifact.path, "owner_role", meta.owner_role)
        else:
            for legacy in meta.owner_roles:
                # Transitional: "@product" and "product" must resolve the same
                # way until the legacy path dies; strip one leading '@' for
                # lookup, report the original spelling.
                if not roles.has(legacy.removeprefix("@")):
                    problems.append(_message(artifact.path, "owner_role", legacy))
        for slug in meta.reviewer_roles:
            _check(problems, roles, artifact.path, "reviewer_roles", slug)
        for slug in meta.allowed_approver_roles or ():
            _check(problems, roles, artifact.path, "allowed_approver_roles", slug)
        _check_structural_coverage(problems, roles, artifact)
    return problems


def _check_structural_coverage(
    problems: list[str], roles: RolesCatalog, artifact: Artifact
) -> None:
    """DEC-009: resolve nested obligation owner_role slugs against the catalog.

    Only well-shaped string values are resolved here — shape defects
    (missing/non-string owner_role, malformed entries) stay with the
    behaviour-spec checks, which already refuse to count such entries.
    """
    meta_dict, _ = split_frontmatter(artifact.text)
    if not isinstance(meta_dict, dict):
        return
    entries = meta_dict.get("structural_coverage")
    if not isinstance(entries, list):
        return
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        obligation = entry.get("obligation")
        if not isinstance(obligation, dict):
            continue
        value = obligation.get("owner_role")
        if isinstance(value, str) and value.strip() and not roles.has(value):
            problems.append(
                _message(artifact.path, f"structural_coverage[{i}].obligation.owner_role", value)
            )


def _check(problems: list[str], roles: RolesCatalog, path: str, field: str, slug: str) -> None:
    if not roles.has(slug):
        problems.append(_message(path, field, slug))


def _message(path: str, field: str, slug: str) -> str:
    # The catalog's actual location is the caller's knowledge (a sibling of the
    # selected profile, not necessarily profiles/) — the CLI appends it once.
    return f"{path}: {field}: role {slug!r} is not in the roles catalog"
