"""Frontmatter role-reference resolution against the roles catalog (DEC-007 D3)."""

from __future__ import annotations

from steward.gatecheck.checks import Artifact
from steward.gatecheck.roles_refs import unresolved_role_refs
from steward.meta import parse_artifact
from steward.roles import Role, RolesCatalog

_CATALOG = RolesCatalog(
    version=1,
    slug_pattern="^[a-z][a-z0-9-]{1,31}$",
    roles=(Role("product", "Product"), Role("qa", "QA")),
)


def _artifact(path: str, frontmatter: str) -> Artifact:
    text = f"---\nspec_stage: design\nstatus: draft\nversion: 1\n{frontmatter}---\n"
    meta = parse_artifact(text)
    assert meta is not None
    return Artifact(path=path, node_id="design", meta=meta, text=text)


def test_canonical_resolvable_roles_pass() -> None:
    art = _artifact("spec/a.md", "owner_role: product\nreviewer_roles: [qa]\n")
    assert unresolved_role_refs([art], _CATALOG) == []


def test_canonical_unresolvable_owner_named_in_message() -> None:
    art = _artifact("spec/a.md", "owner_role: ghost\n")
    (msg,) = unresolved_role_refs([art], _CATALOG)
    assert "spec/a.md" in msg and "owner_role" in msg and "ghost" in msg


def test_unresolvable_array_slug_named_with_field() -> None:
    art = _artifact("spec/a.md", "owner_role: product\nallowed_approver_roles: [ghost]\n")
    (msg,) = unresolved_role_refs([art], _CATALOG)
    assert "spec/a.md" in msg and "allowed_approver_roles" in msg and "ghost" in msg


def test_legacy_roles_resolve_after_at_strip() -> None:
    art = _artifact("spec/a.md", 'owner_role: "@product,@qa"\n')
    assert unresolved_role_refs([art], _CATALOG) == []


def test_legacy_unresolvable_reports_original_spelling() -> None:
    art = _artifact("spec/a.md", 'owner_role: "@ghost"\n')
    (msg,) = unresolved_role_refs([art], _CATALOG)
    assert "spec/a.md" in msg and "owner_role" in msg and "@ghost" in msg


def test_artifact_without_roles_passes() -> None:
    art = _artifact("spec/a.md", "")
    assert unresolved_role_refs([art], _CATALOG) == []


def test_one_message_per_defect() -> None:
    art = _artifact("spec/a.md", "owner_role: ghost\nreviewer_roles: [phantom]\n")
    msgs = unresolved_role_refs([art], _CATALOG)
    assert len(msgs) == 2
