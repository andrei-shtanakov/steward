"""Fail-closed loader for profiles/roles.yaml (DEC-007 D1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.roles import RolesCatalog, RolesError, load_roles_catalog

_VALID = """\
version: 1
slug_pattern: "^[a-z][a-z0-9-]{1,31}$"
roles:
  - {slug: product, display: "Product"}
  - {slug: qa,      display: "QA"}
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "roles.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_catalog_loads(tmp_path: Path) -> None:
    cat = load_roles_catalog(_write(tmp_path, _VALID))
    assert isinstance(cat, RolesCatalog)
    assert cat.version == 1
    assert cat.slugs() == frozenset({"product", "qa"})
    assert cat.has("qa") and not cat.has("ghost")
    assert cat.roles[0].display == "Product"


def test_missing_file_is_roles_error(tmp_path: Path) -> None:
    with pytest.raises(RolesError, match="roles"):
        load_roles_catalog(tmp_path / "absent.yaml")


def test_malformed_yaml_is_roles_error(tmp_path: Path) -> None:
    with pytest.raises(RolesError):
        load_roles_catalog(_write(tmp_path, "version: [unclosed"))


def test_non_mapping_top_level_rejected(tmp_path: Path) -> None:
    with pytest.raises(RolesError, match="mapping"):
        load_roles_catalog(_write(tmp_path, "- just\n- a list\n"))


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(RolesError, match="colour"):
        load_roles_catalog(_write(tmp_path, _VALID + "colour: red\n"))


def test_unknown_role_entry_key_rejected(tmp_path: Path) -> None:
    text = _VALID.replace(
        '{slug: qa,      display: "QA"}',
        '{slug: qa, display: "QA", team: core}',
    )
    with pytest.raises(RolesError, match="team"):
        load_roles_catalog(_write(tmp_path, text))


def test_missing_version_rejected(tmp_path: Path) -> None:
    text = _VALID.replace("version: 1\n", "")
    with pytest.raises(RolesError, match="version"):
        load_roles_catalog(_write(tmp_path, text))


@pytest.mark.parametrize("bad", ["version: true", "version: 0", "version: -1", "version: '1'"])
def test_bad_version_rejected(tmp_path: Path, bad: str) -> None:
    # bool is an int subclass in Python — `true` must NOT pass as version 1.
    text = _VALID.replace("version: 1", bad)
    with pytest.raises(RolesError, match="version"):
        load_roles_catalog(_write(tmp_path, text))


def test_missing_slug_pattern_rejected(tmp_path: Path) -> None:
    text = _VALID.replace('slug_pattern: "^[a-z][a-z0-9-]{1,31}$"\n', "")
    with pytest.raises(RolesError, match="slug_pattern"):
        load_roles_catalog(_write(tmp_path, text))


def test_invalid_regex_pattern_rejected(tmp_path: Path) -> None:
    text = _VALID.replace('"^[a-z][a-z0-9-]{1,31}$"', '"[unclosed"')
    with pytest.raises(RolesError, match="slug_pattern"):
        load_roles_catalog(_write(tmp_path, text))


def test_roles_must_be_non_empty_list(tmp_path: Path) -> None:
    with pytest.raises(RolesError, match="roles"):
        load_roles_catalog(_write(tmp_path, 'version: 1\nslug_pattern: "^[a-z]+$"\nroles: []\n'))
    with pytest.raises(RolesError, match="roles"):
        load_roles_catalog(_write(tmp_path, 'version: 1\nslug_pattern: "^[a-z]+$"\nroles: {}\n'))


def test_duplicate_slug_rejected(tmp_path: Path) -> None:
    text = _VALID.replace('{slug: qa,      display: "QA"}', '{slug: product, display: "Dup"}')
    with pytest.raises(RolesError, match="duplicate"):
        load_roles_catalog(_write(tmp_path, text))


def test_slug_must_fullmatch_pattern(tmp_path: Path) -> None:
    # Pattern without anchors: fullmatch must still be applied, so a slug with
    # a trailing illegal char fails even though a partial match would succeed.
    text = 'version: 1\nslug_pattern: "[a-z]+"\nroles:\n  - {slug: "qa!", display: "QA"}\n'
    with pytest.raises(RolesError, match="qa!"):
        load_roles_catalog(_write(tmp_path, text))


@pytest.mark.parametrize(
    "entry",
    [
        "- {display: 'No slug'}",
        "- {slug: 42, display: 'Num'}",
        "- {slug: qa}",
        "- {slug: qa, display: ''}",
        "- {slug: qa, display: 7}",
        "- plain-string",
    ],
)
def test_bad_role_entry_rejected(tmp_path: Path, entry: str) -> None:
    text = f'version: 1\nslug_pattern: "^[a-z]+$"\nroles:\n  {entry}\n'
    with pytest.raises(RolesError):
        load_roles_catalog(_write(tmp_path, text))


def test_display_uniqueness_not_required(tmp_path: Path) -> None:
    text = _VALID.replace('display: "QA"', 'display: "Product"')
    cat = load_roles_catalog(_write(tmp_path, text))
    assert cat.slugs() == frozenset({"product", "qa"})
