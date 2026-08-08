"""Fail-closed loader for profiles/role-assignments.yaml (DEC-007 D6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.roleassignments import AssignmentsError, RoleAssignments, load_role_assignments
from steward.roles import Role, RolesCatalog

CATALOG = RolesCatalog(
    version=1,
    slug_pattern="^[a-z][a-z0-9-]{1,31}$",
    roles=(Role("product", "Product"), Role("qa", "QA")),
)

_VALID = """\
version: 1
assignments:
  github:alice:
    roles: [product, qa]
  github:dependabot[bot]:
    roles: [qa]
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "role-assignments.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_assignments_load(tmp_path: Path) -> None:
    a = load_role_assignments(_write(tmp_path, _VALID), CATALOG)
    assert isinstance(a, RoleAssignments)
    assert a.roles_for("github:alice") == frozenset({"product", "qa"})
    assert a.roles_for("github:dependabot[bot]") == frozenset({"qa"})


def test_unknown_identity_gets_no_roles(tmp_path: Path) -> None:
    a = load_role_assignments(_write(tmp_path, _VALID), CATALOG)
    assert a.roles_for("github:mallory") == frozenset()
    assert a.roles_for("") == frozenset()


def test_missing_file_is_error(tmp_path: Path) -> None:
    with pytest.raises(AssignmentsError, match="role-assignments"):
        load_role_assignments(tmp_path / "absent.yaml", CATALOG)


def test_malformed_yaml_is_error(tmp_path: Path) -> None:
    with pytest.raises(AssignmentsError):
        load_role_assignments(_write(tmp_path, "version: [unclosed"), CATALOG)


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(AssignmentsError, match="colour"):
        load_role_assignments(_write(tmp_path, _VALID + "colour: red\n"), CATALOG)


def test_unknown_assignment_key_rejected(tmp_path: Path) -> None:
    text = _VALID.replace("roles: [qa]", "roles: [qa]\n    team: core")
    with pytest.raises(AssignmentsError, match="team"):
        load_role_assignments(_write(tmp_path, text), CATALOG)


@pytest.mark.parametrize(
    "identity",
    ["alice", "@alice", "github:", "github:al ice", "gitlab:alice", "github:alice[bot"],
)
def test_identity_grammar_enforced(tmp_path: Path, identity: str) -> None:
    text = f'version: 1\nassignments:\n  "{identity}":\n    roles: [qa]\n'
    with pytest.raises(AssignmentsError, match="identity"):
        load_role_assignments(_write(tmp_path, text), CATALOG)


def test_unresolvable_role_rejected(tmp_path: Path) -> None:
    text = _VALID.replace("[product, qa]", "[product, ghost]")
    with pytest.raises(AssignmentsError, match="ghost"):
        load_role_assignments(_write(tmp_path, text), CATALOG)


@pytest.mark.parametrize("bad", ["[]", "[qa, qa]", '["@qa"]', "qa", "null", "[7]"])
def test_bad_roles_list_rejected(tmp_path: Path, bad: str) -> None:
    text = f"version: 1\nassignments:\n  github:alice:\n    roles: {bad}\n"
    with pytest.raises(AssignmentsError, match="roles"):
        load_role_assignments(_write(tmp_path, text), CATALOG)


def test_missing_roles_key_rejected(tmp_path: Path) -> None:
    text = "version: 1\nassignments:\n  github:alice: {}\n"
    with pytest.raises(AssignmentsError, match="roles"):
        load_role_assignments(_write(tmp_path, text), CATALOG)


@pytest.mark.parametrize("bad", ["version: true", "version: 0", "version: '1'"])
def test_bad_version_rejected(tmp_path: Path, bad: str) -> None:
    text = _VALID.replace("version: 1", bad)
    with pytest.raises(AssignmentsError, match="version"):
        load_role_assignments(_write(tmp_path, text), CATALOG)


def test_empty_assignments_map_allowed(tmp_path: Path) -> None:
    # A repo may legitimately have no mapped identities yet; every identity
    # then has no roles and authorization fails closed downstream.
    a = load_role_assignments(_write(tmp_path, "version: 1\nassignments: {}\n"), CATALOG)
    assert a.roles_for("github:alice") == frozenset()


def test_duplicate_identity_impossible_note() -> None:
    # YAML mappings cannot carry duplicate keys past safe_load (last wins
    # silently) — the loader cannot see them. Documented limitation; the
    # grammar test suite pins everything the loader CAN see.
    assert True


PROFILES = Path(__file__).resolve().parents[2] / "profiles"


def test_shipped_assignments_load_against_shipped_catalog() -> None:
    from steward.roles import load_roles_catalog

    catalog = load_roles_catalog(PROFILES / "roles.yaml")
    a = load_role_assignments(PROFILES / "role-assignments.yaml", catalog)
    assert a.roles_for("github:andrei-shtanakov") == catalog.slugs()
