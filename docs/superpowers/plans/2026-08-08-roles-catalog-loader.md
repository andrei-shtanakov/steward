# Roles Catalog Loader (DEC-007 PR-1: D1+D3+D5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fail-closed loader for `profiles/roles.yaml` (the DEC-007 role-identity SSOT), a canonical-v2 frontmatter reader in `meta.py` that still accepts the legacy `"@role[,@role]"` form, frontmatter role-reference resolution wired into gate-check (unresolvable slug = config error, exit 2), and the deletion-guard / composition-pin tests.

**Architecture:** New module `src/steward/roles.py` owns all parsing/validation of `roles.yaml` (single reader); `gatecatalog.py` stops parsing that file inline and consumes the loader; `meta.py` gains canonical fields (`owner_role: str | None`, `reviewer_roles`, `allowed_approver_roles`) beside the preserved legacy `owner_roles` tuple; `gate-check` loads the roles catalog on **every** run (sibling of the profile YAML, same anchoring as gate-catalog.yaml) and validates every managed artifact's role references before running checks. Profile data (`profiles/*.yaml`) and spec frontmatter stay legacy in this PR — migration is PR-2.

**Tech Stack:** Python 3.12, uv, pytest, Typer CLI, PyYAML, pyrefly, ruff (line length 100).

## Global Constraints

Owner's decisions, verbatim (2026-07-26 DEC-007 + 2026-08-08 design approval). Every task's requirements implicitly include this section.

- `owner_role` = exactly ONE accountable role, slug without `@`; multiplicity only via separate `reviewer_roles[]` / `allowed_approver_roles[]`. A legacy tuple of length > 1 is NEVER normalized silently — no automatic choice of accountable owner by tooling.
- D1 closed form entirely: unknown top-level keys are an error; unknown keys inside a role entry are an error; slug matching uses `re.fullmatch`, not partial match; file-read and YAML errors also translate to `RolesError`; duplicate slugs are detected before building any mapping. `display` is validated as a non-empty string only — display uniqueness is NOT required.
- `gatecatalog.py` must convert `RolesError` into its own configuration error (`CatalogError`) without a traceback, and must NOT copy/duplicate the validation logic.
- No hidden filesystem dependency in loaders: catalogs are passed explicitly as arguments; no function may silently search `profiles/roles.yaml` relative to CWD.
- D3 legacy reader must distinguish three forms: `owner_role: "@product"` → single legacy slug; `owner_role: "@product,@qa"` → legacy tuple preserved without automatic owner choice; canonical `owner_role: product` + separate role arrays.
- Unresolvable role slug in frontmatter = configuration error, **exit 2**, and the message MUST contain the artifact path, the field name, and the offending slug. It is a defect of governance data, not a finding about the checked product; NO new gate_id.
- `allowed_approver_roles` semantics (recorded now, enforced from PR-2/3): field absent → effective `{owner_role}`; field present → EXACT non-empty allowlist that REPLACES the default (does not extend it); empty list → error.
- Approval facts: a role claimed inside injected approval facts is never authoritative; steward alone computes roles from identity (enforced in PR-3 — no code here may start trusting `Approval.role` for authority).
- Emission unchanged: canonical singular owner is still emitted as an array of one element in gate-verdicts/v1 — the vendored contract does not change in this PR.
- Composition guard (D5): the roles catalog's slug composition is pinned to its `version`; any composition change requires a version bump; version can never go below the pinned baseline.
- Project rules: uv only (never pip); `uv run pytest` for tests; `uv run ruff format .` / `uv run ruff check .` (line length 100); `uv run pyrefly check` after changes; type hints everywhere; PR-only workflow — but pushing/opening the PR is exclusively the final task, never an implementer's.

---

### Task 1: `src/steward/roles.py` — fail-closed roles catalog loader

**Files:**
- Create: `src/steward/roles.py`
- Test: `tests/roles/__init__.py`, `tests/roles/test_loader.py`

**Interfaces:**
- Produces: `RolesError(ValueError)`; `Role(slug: str, display: str)` frozen dataclass; `RolesCatalog(version: int, slug_pattern: str, roles: tuple[Role, ...])` frozen dataclass with `slugs() -> frozenset[str]` and `has(slug: str) -> bool`; `load_roles_catalog(path: Path) -> RolesCatalog`. Tasks 2, 4, 5 consume exactly these names.

- [ ] **Step 1: Write the failing tests**

```python
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
        load_roles_catalog(
            _write(tmp_path, 'version: 1\nslug_pattern: "^[a-z]+$"\nroles: []\n')
        )
    with pytest.raises(RolesError, match="roles"):
        load_roles_catalog(
            _write(tmp_path, 'version: 1\nslug_pattern: "^[a-z]+$"\nroles: {}\n')
        )


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/roles/test_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'steward.roles'`

- [ ] **Step 3: Implement `src/steward/roles.py`**

```python
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
            raise RolesError(
                f"roles file {path}: role {slug!r} needs a non-empty string 'display'"
            )
        roles.append(Role(slug=slug, display=display))

    return RolesCatalog(version=version, slug_pattern=slug_pattern, roles=tuple(roles))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/roles/ -v`
Expected: all PASS

- [ ] **Step 5: Format, lint, type-check, commit**

```bash
uv run ruff format src/steward/roles.py tests/roles/
uv run ruff check src/steward/roles.py tests/roles/
uv run pyrefly check
git add src/steward/roles.py tests/roles/
git commit -m "feat: fail-closed roles catalog loader (DEC-007 D1)"
```

---

### Task 2: `gatecatalog.py` consumes the loader — one file, one reader

**Files:**
- Modify: `src/steward/gatecatalog.py` (the `load_catalog` roles-parsing block, ~lines 195–225)
- Modify: `src/steward/gatecheck/cli.py:97-111` (`_load_catalog`)
- Modify: `tests/gatecatalog/test_loader.py`, `tests/gatecatalog/test_catalog_data.py`, `tests/gatecatalog/test_sync_with_code.py` (call-site signature updates)

**Interfaces:**
- Consumes: `load_roles_catalog`, `RolesCatalog`, `RolesError` from Task 1.
- Produces: NEW signature `load_catalog(catalog_path: Path, roles: RolesCatalog) -> GateCatalog` — the second parameter is a loaded catalog object, no longer a path. Task 4's CLI wiring relies on this exact signature.

- [ ] **Step 1: Write the failing test**

Add to `tests/gatecatalog/test_loader.py`:

```python
def test_duplicate_role_slug_now_fails_via_roles_loader(tmp_path: Path) -> None:
    # Before D1 the inline parser accepted duplicate slugs silently; the
    # roles loader must make this a CatalogError (converted, no traceback).
    roles = tmp_path / "roles.yaml"
    roles.write_text(
        'version: 1\nslug_pattern: "^[a-z]+$"\n'
        "roles:\n  - {slug: qa, display: A}\n  - {slug: qa, display: B}\n"
    )
    with pytest.raises(CatalogError, match="duplicate"):
        load_catalog(CATALOG_PATH, load_roles_catalog_or_convert(roles))
```

Note for the implementer: the conversion point decides this test's exact shape. The required behavior: `cli._load_catalog` (and any path that goes file→catalog) surfaces a roles-file defect as `CatalogError`/config error, never a raw `RolesError` traceback. Implement a tiny helper in `gatecatalog.py`:

```python
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
```

and write the test against `load_catalog_files(CATALOG_PATH, roles)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gatecatalog/test_loader.py -v`
Expected: FAIL — `load_catalog_files` not defined / signature mismatch

- [ ] **Step 3: Rework `load_catalog`**

In `src/steward/gatecatalog.py`:
1. `from steward.roles import RolesCatalog, RolesError, load_roles_catalog` (stdlib/import order per ruff I001).
2. Change signature to `def load_catalog(catalog_path: Path, roles: RolesCatalog) -> GateCatalog:` — delete the entire inline roles-file parsing block (open/`yaml.safe_load`/shape checks/slug collection) and replace with `available_roles = roles.slugs()`.
3. Add `load_catalog_files` exactly as in Step 1's note; export both in `__all__` if the module has one.
4. Update `cli.py::_load_catalog` to call `load_catalog_files(profile_path.parent / "gate-catalog.yaml", profile_path.parent / "roles.yaml")` — behavior identical (CatalogError → `_fail_config`). (Task 4 will rewire this to reuse the CLI-loaded `RolesCatalog`; keeping `load_catalog_files` here keeps Task 2 self-contained.)
5. Update every test call site: `load_catalog(catalog, roles_path)` → either `load_catalog_files(catalog, roles_path)` (tests exercising file-level errors) or `load_catalog(catalog, load_roles_catalog(roles_path))`. Tests that previously asserted CatalogError messages about roles-file *shape* (e.g. "every role entry needs a string 'slug'") now go through the loader — keep asserting `CatalogError`, relax message regexes only where the wording legitimately changed.

- [ ] **Step 4: Run the full gatecatalog + roles suites**

Run: `uv run pytest tests/gatecatalog/ tests/roles/ -v`
Expected: all PASS

- [ ] **Step 5: Format, lint, type-check, commit**

```bash
uv run ruff format . && uv run ruff check .
uv run pyrefly check
git add -A src tests
git commit -m "refactor: gatecatalog consumes roles loader, single reader of roles.yaml"
```

---

### Task 3: `meta.py` canonical-v2 reader (legacy preserved, no silent choice)

**Files:**
- Modify: `src/steward/meta.py`
- Test: `tests/test_meta.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces (Task 4 and PR-2/3 rely on these exact fields):
  - `ArtifactMeta.owner_role: str | None` — set ONLY for the canonical form (single slug, no `@`, no comma); `None` on legacy or absent.
  - `ArtifactMeta.owner_roles: tuple[str, ...]` — unchanged meaning, now also populated `(slug,)` for canonical form so existing consumers (`emitter._role_slugs`, `checks.check_status_git`) keep working unmodified.
  - `ArtifactMeta.reviewer_roles: tuple[str, ...]` — canonical-only list; absent → `()`.
  - `ArtifactMeta.allowed_approver_roles: tuple[str, ...] | None` — `None` = field absent (default `{owner_role}` applies downstream); present must be a non-empty list. Absent and empty are DIFFERENT states — never collapse them.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_meta.py`:

```python
def test_canonical_singular_owner_role() -> None:
    meta = parse_artifact(
        "---\nspec_stage: design\nstatus: draft\nversion: 1\nowner_role: product\n---\n"
    )
    assert meta is not None
    assert meta.owner_role == "product"
    assert meta.owner_roles == ("product",)


def test_legacy_single_role_is_not_canonical() -> None:
    meta = parse_artifact(
        '---\nspec_stage: design\nstatus: draft\nversion: 1\nowner_role: "@product"\n---\n'
    )
    assert meta is not None
    assert meta.owner_role is None  # legacy form: distinguished, not silently upgraded
    assert meta.owner_roles == ("@product",)


def test_legacy_tuple_preserved_without_owner_choice() -> None:
    meta = parse_artifact(
        '---\nspec_stage: design\nstatus: draft\nversion: 1\nowner_role: "@product,@qa"\n---\n'
    )
    assert meta is not None
    assert meta.owner_role is None
    assert meta.owner_roles == ("@product", "@qa")


def test_comma_without_at_is_still_legacy_multi() -> None:
    # A hybrid "product,qa" is multiplicity inside owner_role — never canonical.
    meta = parse_artifact(
        '---\nspec_stage: design\nstatus: draft\nversion: 1\nowner_role: "product,qa"\n---\n'
    )
    assert meta is not None
    assert meta.owner_role is None
    assert meta.owner_roles == ("product", "qa")


def test_reviewer_and_approver_arrays_parse() -> None:
    meta = parse_artifact(
        "---\nspec_stage: design\nstatus: draft\nversion: 1\n"
        "owner_role: product\nreviewer_roles: [qa]\nallowed_approver_roles: [qa, product]\n---\n"
    )
    assert meta is not None
    assert meta.reviewer_roles == ("qa",)
    assert meta.allowed_approver_roles == ("qa", "product")


def test_role_arrays_absent_defaults() -> None:
    meta = parse_artifact(
        "---\nspec_stage: design\nstatus: draft\nversion: 1\nowner_role: product\n---\n"
    )
    assert meta is not None
    assert meta.reviewer_roles == ()
    assert meta.allowed_approver_roles is None  # absent != empty: default {owner_role} applies


def test_explicit_empty_allowed_approver_roles_is_error() -> None:
    with pytest.raises(MetaError, match="allowed_approver_roles"):
        parse_artifact(
            "---\nspec_stage: design\nstatus: draft\nversion: 1\n"
            "owner_role: product\nallowed_approver_roles: []\n---\n"
        )


def test_explicit_empty_reviewer_roles_is_error() -> None:
    # Absent is the only spelling of "no required reviewers" — one state,
    # one representation.
    with pytest.raises(MetaError, match="reviewer_roles"):
        parse_artifact(
            "---\nspec_stage: design\nstatus: draft\nversion: 1\n"
            "owner_role: product\nreviewer_roles: []\n---\n"
        )


@pytest.mark.parametrize("field", ["reviewer_roles", "allowed_approver_roles"])
def test_at_sign_in_canonical_arrays_rejected(field: str) -> None:
    # The new arrays are canonical-only fields — the legacy "@" spelling
    # never leaks into them.
    with pytest.raises(MetaError, match=field):
        parse_artifact(
            "---\nspec_stage: design\nstatus: draft\nversion: 1\n"
            f'owner_role: product\n{field}: ["@qa"]\n---\n'
        )


@pytest.mark.parametrize("field", ["reviewer_roles", "allowed_approver_roles"])
def test_duplicate_slugs_in_role_arrays_rejected(field: str) -> None:
    with pytest.raises(MetaError, match=field):
        parse_artifact(
            "---\nspec_stage: design\nstatus: draft\nversion: 1\n"
            f"owner_role: product\n{field}: [qa, qa]\n---\n"
        )


@pytest.mark.parametrize("field", ["reviewer_roles", "allowed_approver_roles"])
def test_non_list_role_arrays_rejected(field: str) -> None:
    with pytest.raises(MetaError, match=field):
        parse_artifact(
            "---\nspec_stage: design\nstatus: draft\nversion: 1\n"
            f"owner_role: product\n{field}: qa\n---\n"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_meta.py -v`
Expected: new tests FAIL (`owner_role`/`reviewer_roles` attributes missing)

- [ ] **Step 3: Implement in `meta.py`**

1. Extend `ArtifactMeta`:

```python
    base: SpecMeta
    owner_roles: tuple[str, ...] = ()
    traces_to: tuple[str, ...] = ()
    upstream_hashes: tuple[tuple[str, str], ...] = ()
    # DEC-007 canonical v2 (2026-08-08). owner_role is set ONLY when the
    # frontmatter carries the canonical singular form (one slug, no '@');
    # a legacy "@a[,@b]" string parses into owner_roles alone — the reader
    # never picks an accountable owner from a legacy tuple.
    owner_role: str | None = None
    reviewer_roles: tuple[str, ...] = ()
    # None = field absent (downstream default: {owner_role}); an explicit
    # empty list is a MetaError — absent and empty are different states.
    allowed_approver_roles: tuple[str, ...] | None = None
```

2. Add the canonical/legacy split and array parser:

```python
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
```

3. In `parse_artifact`, replace the `owner_roles=` line:

```python
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
```

4. Update the module docstring (owner_role canonical v2 note) and `__all__` if new names should be public (keep `_split_owner_role`/`_parse_role_array` private).

- [ ] **Step 4: Run the meta suite + full suite**

Run: `uv run pytest tests/test_meta.py -v && uv run pytest -q`
Expected: all PASS (existing consumers read `owner_roles`, untouched)

- [ ] **Step 5: Format, lint, type-check, commit**

```bash
uv run ruff format . && uv run ruff check .
uv run pyrefly check
git add src/steward/meta.py tests/test_meta.py
git commit -m "feat: canonical v2 role fields in ArtifactMeta, legacy reader preserved (DEC-007 D3)"
```

---

### Task 4: gate-check resolves frontmatter role references (exit 2 on unresolvable)

**Files:**
- Create: `src/steward/gatecheck/roles_refs.py`
- Modify: `src/steward/gatecheck/cli.py` (load roles catalog every run; validate after `collect_bundle`; rewire `_load_catalog` to reuse the loaded catalog)
- Test: `tests/gatecheck/test_roles_refs.py` (new), `tests/gatecheck/conftest.py` (new — shared roles.yaml fixture), plus mechanical updates to every gatecheck/verdicts test that writes a profile into `tmp_path`

**Interfaces:**
- Consumes: `RolesCatalog` (Task 1); `ArtifactMeta` canonical fields (Task 3); `Artifact` from `steward.gatecheck.checks` (has `.path` and `.meta`).
- Produces: `unresolved_role_refs(artifacts: list[Artifact], roles: RolesCatalog) -> list[str]` — one message per defect, each containing artifact path, field name, and the offending slug. CLI: any non-empty result → `_fail_config` (exit 2).

**Resolution rules (from the approved design):**
- Canonical fields (`owner_role`, `reviewer_roles`, `allowed_approver_roles`): each slug must be in the catalog as-is.
- Legacy `owner_roles` entries: strip ONE leading `"@"` before lookup (transitional rule so `"@product"` and `product` resolve identically); the stripped slug must be in the catalog. This transitional strip lives ONLY here, documented, and dies with the legacy path in a later PR — it is not a boundary normalization contract.
- Unmanaged artifacts (`meta is None`) are never inspected; managed artifacts with no roles at all pass (completeness of role data is not this gate's job in PR-1).

- [ ] **Step 1: Write the failing unit tests**

`tests/gatecheck/test_roles_refs.py`:

```python
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
```

Note: `Artifact`'s constructor shape verified against current `checks.py:45` — fields `path`, `node_id`, `meta`, `text`; if it drifts, adapt the helper, not the assertions.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/gatecheck/test_roles_refs.py -v`
Expected: FAIL — no module `roles_refs`

- [ ] **Step 3: Implement `roles_refs.py`**

```python
"""Resolution of frontmatter role references against the roles catalog.

DEC-007 D3: an unresolvable role slug in a managed artifact's frontmatter is
a defect of governance DATA — a configuration error (exit 2), not a finding
about the checked product. Messages must name the artifact path, the field,
and the offending slug so the defect is fixable from the message alone.
"""

from __future__ import annotations

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
    return problems


def _check(
    problems: list[str], roles: RolesCatalog, path: str, field: str, slug: str
) -> None:
    if not roles.has(slug):
        problems.append(_message(path, field, slug))


def _message(path: str, field: str, slug: str) -> str:
    return f"{path}: {field}: role {slug!r} is not in the roles catalog (profiles/roles.yaml)"
```

- [ ] **Step 4: Run the unit tests**

Run: `uv run pytest tests/gatecheck/test_roles_refs.py -v`
Expected: all PASS

- [ ] **Step 5: Write the failing CLI tests**

Add to `tests/gatecheck/test_cli.py` (adapting to its existing `_bundle`/runner helpers):

```python
def test_unresolvable_frontmatter_role_is_config_error(tmp_path: Path) -> None:
    profile, spec = _bundle(tmp_path)
    (spec / "des.md").write_text(
        "---\nspec_stage: design\nstatus: draft\nversion: 1\n"
        "owner_role: ghost\ntraces_to: [REQ-001]\n---\n"
    )
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--no-fs", str(_facts(tmp_path, {}))])
    assert result.exit_code == 2
    err = result.output
    assert "des.md" in err and "owner_role" in err and "ghost" in err


def test_missing_sibling_roles_yaml_is_config_error(tmp_path: Path) -> None:
    profile, spec = _bundle(tmp_path, roles=False)
    result = runner.invoke(app, [str(spec), "--profile", str(profile), "--no-fs", str(_facts(tmp_path, {}))])
    assert result.exit_code == 2
    assert "roles" in result.output
```

(Exact invocation shape: mirror how neighboring tests in the file build `--no-fs` facts; do not invent a new style.)

- [ ] **Step 6: Wire the CLI**

In `cli.py`:

1. New helper next to `_load_catalog`:

```python
def _load_roles(profile_path: Path) -> RolesCatalog:
    """Load the roles catalog anchored to ``profile_path``'s directory.

    roles.yaml is a MANDATORY sibling of the profile on every run since
    DEC-007 D3 — role references in frontmatter are validated against it,
    so its absence is a configuration error, not a soft skip.
    """
    try:
        return load_roles_catalog(profile_path.parent / "roles.yaml")
    except RolesError as err:
        _fail_config(str(err))
        raise AssertionError from None  # unreachable; keeps type-checkers calm
```

2. In `main`, right after `artifacts, findings = collect_bundle(graph, spec_dir)`:

```python
    roles_catalog = _load_roles(profile_path)
    role_problems = unresolved_role_refs(artifacts, roles_catalog)
    if role_problems:
        _fail_config("\n".join(role_problems))
```

(Place the `_load_roles` call BEFORE `run_checks` so a data defect fails fast; order: collect → roles-resolution → checks.)

3. `_load_catalog` reuses the already-loaded catalog: change its signature to `_load_catalog(profile_path: Path, roles: RolesCatalog)` calling `load_catalog(profile_path.parent / "gate-catalog.yaml", roles)` (CatalogError → `_fail_config` unchanged), and pass `roles_catalog` at the `--emit-verdicts` call site. `load_catalog_files` from Task 2 remains for non-CLI callers.

- [ ] **Step 7: Add the shared test fixture and repair existing tests**

The new mandatory sibling breaks every test that writes a profile into `tmp_path` without `roles.yaml` (~8 sites across `tests/gatecheck/` and `tests/verdicts/`). Create `tests/gatecheck/conftest.py`:

```python
"""Shared gatecheck test fixtures (DEC-007: roles.yaml is a mandatory profile sibling)."""

from __future__ import annotations

from pathlib import Path

# Wide-open pattern + every slug the legacy test profiles mention (after '@'
# strip). Tests that need a NARROW catalog write their own file instead.
DEFAULT_ROLES = (
    "version: 1\n"
    'slug_pattern: "^[a-z][a-z0-9-]{1,31}$"\n'
    "roles:\n"
    "  - {slug: product, display: Product}\n"
    "  - {slug: architects, display: Architecture}\n"
    "  - {slug: qa, display: QA}\n"
    "  - {slug: tech-lead, display: Tech lead}\n"
    "  - {slug: stream-owner, display: Workstream owner}\n"
    "  - {slug: owner, display: Solo owner}\n"
    "  - {slug: x, display: X}\n"
)


def write_roles(profile_dir: Path) -> Path:
    """Drop a permissive roles.yaml next to a test profile."""
    path = profile_dir / "roles.yaml"
    path.write_text(DEFAULT_ROLES, encoding="utf-8")
    return path
```

Then: every helper that writes a profile (e.g. `test_cli.py::_bundle`) also calls `write_roles(tmp_path)`; `_bundle` gains a `roles: bool = True` parameter for the negative test in Step 5. Sweep with `grep -rn "write_text(_PROFILE" tests/` plus `grep -rln "profile" tests/gatecheck tests/verdicts` and fix every site. If a test's profile mentions a role slug not in `DEFAULT_ROLES` (check for `@x`-style roles), extend `DEFAULT_ROLES` rather than forking per-test catalogs.

`tests/verdicts/test_emitter.py`: if it invokes the CLI it needs the fixture too (import from `tests/gatecheck/conftest` will NOT work across packages — give `tests/verdicts/` its own conftest that re-exports, or move `write_roles` to the top-level `tests/conftest.py`; prefer the top-level conftest).

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS, including the two new CLI tests

- [ ] **Step 9: Format, lint, type-check, commit**

```bash
uv run ruff format . && uv run ruff check .
uv run pyrefly check
git add -A src tests
git commit -m "feat: gate-check resolves frontmatter role refs against roles catalog, exit 2 on unresolvable (DEC-007 D3)"
```

---

### Task 5: D5 guards — composition pin + version monotonicity + real-catalog load

**Files:**
- Test: `tests/roles/test_catalog_data.py` (new)

**Interfaces:**
- Consumes: `load_roles_catalog` (Task 1); the real `profiles/roles.yaml`.

- [ ] **Step 1: Write the tests**

```python
"""The real profiles/roles.yaml: composition pinned to its version (DEC-007 D5).

Deleting or adding a role without bumping `version` must fail here — the
same discipline as the gate-id catalog. Reference-resolution (profiles,
frontmatter, gate-catalog applicable_roles, role assignments) makes deleting
a USED role fail loudly in the respective loaders; this file guards the
version contract itself.
"""

from __future__ import annotations

from pathlib import Path

from steward.roles import load_roles_catalog

PROFILES = Path(__file__).resolve().parents[2] / "profiles"

# Baseline: the version this pin was recorded against. Composition changes
# require version > baseline AND updating EXPECTED_SLUGS in the same commit.
BASELINE_VERSION = 1
EXPECTED_SLUGS = frozenset(
    {"product", "architects", "qa", "tech-lead", "stream-owner", "owner"}
)


def _catalog():
    return load_roles_catalog(PROFILES / "roles.yaml")


def test_real_catalog_loads_fail_closed() -> None:
    cat = _catalog()
    assert cat.version >= 1


def test_composition_change_requires_version_bump() -> None:
    cat = _catalog()
    if cat.slugs() == EXPECTED_SLUGS:
        assert cat.version == BASELINE_VERSION, (
            "composition unchanged but version moved — revert the bump or "
            "change the composition it announces"
        )
    else:
        assert cat.version > BASELINE_VERSION, (
            "roles composition changed without a version bump — bump "
            "`version` in profiles/roles.yaml AND update EXPECTED_SLUGS/"
            "BASELINE_VERSION here in the same commit"
        )


def test_version_never_below_baseline() -> None:
    assert _catalog().version >= BASELINE_VERSION
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/roles/test_catalog_data.py -v`
Expected: all PASS against the real catalog (version 1, six slugs)

- [ ] **Step 3: Commit**

```bash
uv run ruff format tests/roles/ && uv run ruff check tests/roles/
git add tests/roles/test_catalog_data.py
git commit -m "test: pin roles catalog composition to version, guard baseline (DEC-007 D5)"
```

---

### Task 6: Docs truth-up, full verification, dogfood run

**Files:**
- Modify: `profiles/roles.yaml` (STATUS comment block only — no data change)
- Modify: `CLAUDE.md` (the `profiles/roles.yaml` bullet in Architecture)

**Interfaces:** none (documentation + verification).

- [ ] **Step 1: Update the STATUS comment in `profiles/roles.yaml`**

Replace the stale paragraph (`STATUS 2026-07-26: catalog declared; the profile loader and gate-check do not yet validate against it…`) with:

```
# STATUS 2026-08-08 (PR-1 of DEC-007 §1): steward loads and validates this file
# fail-closed (src/steward/roles.py) — slug uniqueness, slug_pattern fullmatch,
# closed key set — and gate-check resolves every frontmatter role reference
# against it (unresolvable slug = config error, exit 2). Composition is pinned
# to `version` by tests/roles/test_catalog_data.py: any composition change
# requires a version bump. Still legacy (PR-2): profiles/*.yaml and the repo's
# own spec/*.md frontmatter carry "@role[,@role]"; the reader accepts that form
# transitionally, the accountable-owner choice is never made by tooling.
```

Keep the rest of the header (identity/ownership prose) intact. Do NOT touch `version`, `slug_pattern`, or `roles` — data migration is PR-2, and the composition-pin test enforces that.

- [ ] **Step 2: Update `CLAUDE.md`**

In the `profiles/roles.yaml` architecture bullet, replace "**The code has not migrated yet**" wording: the loader + gate-check resolution now exist (name `src/steward/roles.py`); data migration (`profiles/{lite,team,team-exp}.yaml`, own `spec/*.md`) still pending in `TODO.md` §1. Also note that `roles.yaml` is now a mandatory sibling of the profile for every `gate-check` run — external consumers running the pinned binary need it next to their profile (dispatcher's live smoke already ships one; its minimal catalog will need the real slugs at the next re-vendor — that lands with the D8 handoff).

- [ ] **Step 3: Full verification battery**

```bash
uv run ruff format . && uv run ruff check .
uv run pyrefly check
uv run pytest -q
```

Expected: format/lint clean, pyrefly clean, full suite PASS.

- [ ] **Step 4: Dogfood on the real bundles**

```bash
uv run gate-check spec/ --profile team-exp
uv run gate-check workstreams/WS-005-gate-verdicts/spec/ --profile team-exp
```

Expected: same findings as on master (legacy roles all resolve after `@`-strip: product, architects, qa, tech-lead, stream-owner are catalog slugs) — NO new config errors, exit codes unchanged vs master. If a legacy role fails to resolve, that is a real data defect to surface, not to paper over.

- [ ] **Step 5: Commit**

```bash
git add profiles/roles.yaml CLAUDE.md
git commit -m "docs: roles catalog is now loaded fail-closed; note mandatory sibling contract"
```
