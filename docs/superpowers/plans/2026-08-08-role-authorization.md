# Role Authorization (DEC-007 PR-3: D6+D7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fail-closed loader for `profiles/role-assignments.yaml` (identity → roles, the CODEOWNERS-boundary mapping), the approval-facts model reworked so providers supply an *identity* and steward alone computes roles, and GC-GIT-ROLE checking authorization against `allowed_approver_roles` (absent → `{owner_role}`; explicit list = exact replacement) instead of ownership.

**Architecture:** New module `src/steward/roleassignments.py` owns the mapping file (validated against the roles catalog, passed explicitly). `git_facts.Approval` loses its provider-claimed `role` field and carries `identity` only — injected facts can no longer assert a role. `checks.check_status_git` computes each approver's roles through the mapping and intersects with the node's effective approver allowlist. The CLI loads `role-assignments.yaml` as a mandatory profile sibling ONLY for non-solo profiles (solo profiles never run GC-GIT-ROLE, and external consumers' solo smokes stay untouched).

**Tech Stack:** Python 3.12, uv, pytest, Typer, PyYAML, pyrefly, ruff (line length 100).

## Global Constraints

Owner's decisions, verbatim (DEC-007 + design approval 2026-08-08). Every task implicitly includes this section.

- A role claimed inside injected approval facts is NEVER authoritative — the provider gives an identity (e.g. `Approval(identity="github:andrei-shtanakov")`), and steward alone computes roles via the assignments mapping. No compatibility `role` field survives in any authority decision.
- Assignments file canonical direction: identity → roles. Identity has a strict grammar; roles are unique and resolve via roles.yaml; unknown identities get NO roles; no `@`-stripping or other normalization heuristics at this boundary — normalization is defined by the contract once.
- `allowed_approver_roles` effective semantics: field absent → `{owner_role}`; present → EXACT non-empty allowlist that REPLACES the default (separation of duties: the owner may be deliberately excluded); explicit empty/null already rejected by the loaders (PR-2).
- Honest semantics: the current facts source authorizes the recorded approver/merge actor. `reviewer_roles` is NOT enforced (needs a future review-facts source) — no message or doc may claim a separate PR reviewer was verified.
- GC-GIT-ROLE unavailable contour unchanged: `approvals() -> None` (always the case in live mode) ⇒ skip, no finding. Solo profiles (`solo_auto_approve: true`) skip as today.
- Node-level `allowed_approver_roles` (profile) governs in this PR. The frontmatter-level field parses (PR-2) but is deliberately NOT wired into the check — instance-vs-node precedence needs its own owner ruling; the code carries an explicit comment saying so (surfaced, not silent).
- Fail-closed: an unreadable/malformed assignments file = config error exit 2; a non-solo profile without a sibling `role-assignments.yaml` = config error exit 2. Absent ≠ unavailable ≠ unknown; one state, one representation (explicit `null` roles list = error, like PR-2).
- Emission contract gate-verdicts/v1 and the roles catalog data stay byte-untouched.
- Project rules: uv only; `uv run pytest`; `uv run ruff format .` / `uv run ruff check .` (line 100); `uv run pyrefly check`; implementers commit locally only — push/PR is exclusively the controller's final step.

---

### Task 1: `src/steward/roleassignments.py` + shipped `profiles/role-assignments.yaml`

**Files:**
- Create: `src/steward/roleassignments.py`
- Create: `profiles/role-assignments.yaml`
- Test: `tests/roles/test_assignments.py`

**Interfaces:**
- Consumes: `RolesCatalog` from `steward.roles`.
- Produces (Tasks 2–3 rely on these exact names):
  - `AssignmentsError(ValueError)`
  - `RoleAssignments(version: int, assignments: tuple[Assignment, ...])` frozen, with `roles_for(identity: str) -> frozenset[str]` (unknown identity → `frozenset()`).
  - `Assignment(identity: str, roles: tuple[str, ...])` frozen.
  - `load_role_assignments(path: Path, roles_catalog: RolesCatalog) -> RoleAssignments`.

- [ ] **Step 1: Write the failing tests**

`tests/roles/test_assignments.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/roles/test_assignments.py -v`
Expected: FAIL — no module `steward.roleassignments`

- [ ] **Step 3: Implement `src/steward/roleassignments.py`**

```python
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
```

- [ ] **Step 4: Create `profiles/role-assignments.yaml`**

```yaml
# Role assignments v1 — identity → roles at the CODEOWNERS boundary (DEC-007 D6,
# approved 2026-08-08). This file is the ONLY place identities acquire roles:
# a role claimed inside injected approval facts is never authoritative —
# steward computes roles from this mapping, alone. Unknown identities get no
# roles, so authorization fails closed.
#
# GOVERNANCE DATA — changes go through PR review like roles.yaml and the gate
# profiles. Identity grammar: "github:<login>" (machine accounts keep their
# literal "[bot]" suffix). Roles must resolve in profiles/roles.yaml.
#
# Solo reality: one human holds every role. Separation of duties becomes
# expressible the moment a second identity appears here.

version: 1
assignments:
  github:andrei-shtanakov:
    roles: [product, architects, qa, tech-lead, stream-owner, owner]
```

- [ ] **Step 5: Add a real-file test**

Append to `tests/roles/test_assignments.py`:

```python
PROFILES = Path(__file__).resolve().parents[2] / "profiles"


def test_shipped_assignments_load_against_shipped_catalog() -> None:
    from steward.roles import load_roles_catalog

    catalog = load_roles_catalog(PROFILES / "roles.yaml")
    a = load_role_assignments(PROFILES / "role-assignments.yaml", catalog)
    assert a.roles_for("github:andrei-shtanakov") == catalog.slugs()
```

- [ ] **Step 6: Run tests, format, type-check, commit**

```bash
uv run pytest tests/roles/ -v
uv run ruff format src/steward/roleassignments.py tests/roles/ && uv run ruff check .
uv run pyrefly check
git add src/steward/roleassignments.py profiles/role-assignments.yaml tests/roles/test_assignments.py
git commit -m "feat: role-assignments loader + shipped solo mapping (DEC-007 D6)"
```

---

### Task 2: `git_facts.Approval` carries identity, not a claimed role

**Files:**
- Modify: `src/steward/gatecheck/git_facts.py` (Approval dataclass ~line 100; FS parser ~lines 220–235; module docstring facts.json shape ~line 19)
- Test: `tests/gatecheck/test_git_facts.py` (parser tests updated)

**Interfaces:**
- Produces: `Approval(identity: str)` — the ONLY field. facts.json approvals shape becomes `[{"identity": "github:alice"}]`. Task 3 consumes `approval.identity`.

- [ ] **Step 1: Update the parser tests first**

In `tests/gatecheck/test_git_facts.py`, find every approvals-related test: the valid-parse cases become `{"identity": "github:alice"}` asserting `Approval(identity="github:alice")`; the malformed cases (missing key, non-string) assert the parser's fail-closed error mentions `identity`. Add one new case: an entry still carrying the OLD shape `{"handle": "@alice", "role": "product"}` must FAIL parsing (unknown/missing keys — a stale facts file must not half-work), with an error message naming `identity`.

Run: `uv run pytest tests/gatecheck/test_git_facts.py -v` → the updated tests FAIL against the old parser.

- [ ] **Step 2: Implement**

```python
@dataclass(frozen=True)
class Approval:
    """One recorded PR approval for an artifact.

    Carries the approver's forge identity ONLY (``github:<login>``). The
    provider cannot claim a role: roles are computed by steward from
    profiles/role-assignments.yaml (DEC-007 D6/D7) — an injected fact
    asserting a role would make the assignments file decorative.
    """

    identity: str
```

Parser block: each entry must be a mapping with exactly a non-empty string `identity` (unknown keys rejected — an old-shape `handle`/`role` entry fails loudly). Docstring facts.json example:

```
      "approvals": {"spec/10-requirements.md": [{"identity": "github:alice"}]},
```

- [ ] **Step 3: Run, sweep compile errors ONLY in git_facts tests, commit**

`uv run pytest tests/gatecheck/test_git_facts.py -v` → PASS. The full suite is red at `checks.py:323` (`.role` gone) — that is Task 3's first move; do NOT patch checks.py here.

```bash
uv run ruff format . && uv run ruff check src/steward/gatecheck/git_facts.py tests/gatecheck/test_git_facts.py
git add src/steward/gatecheck/git_facts.py tests/gatecheck/test_git_facts.py
git commit -m "feat: Approval carries identity only — providers cannot claim roles (DEC-007 D7)"
```

---

### Task 3: GC-GIT-ROLE authorizes via allowed_approver_roles × assignments

**Files:**
- Modify: `src/steward/gatecheck/checks.py` (`check_status_git`, `run_checks` signature)
- Modify: `src/steward/gatecheck/cli.py` (load assignments for non-solo profiles; thread into `run_checks`)
- Test: `tests/gatecheck/test_checks.py` (GC-GIT-ROLE block), `tests/gatecheck/test_cli.py` (facts payloads + non-solo sibling requirement), any other fixture carrying old approval shapes (sweep)

**Interfaces:**
- Consumes: `RoleAssignments.roles_for` (Task 1); `Approval.identity` (Task 2); `SpecNode.allowed_approver_roles` (PR-2).
- Produces: `run_checks(graph, artifacts, git, assignments: RoleAssignments | None = None)`; `check_status_git(graph, artifacts, git, assignments: RoleAssignments | None = None)`. CLI: non-solo profile ⇒ sibling `role-assignments.yaml` loaded (AssignmentsError / missing → `_fail_config`, exit 2); solo ⇒ not loaded, `None` passed.

- [ ] **Step 1: Write the failing check tests**

In `tests/gatecheck/test_checks.py`, rework the GC-GIT-ROLE tests (find them via `grep -n "GC-GIT-ROLE" tests/gatecheck/test_checks.py`) to the new model, using a module-level assignments fixture:

```python
from steward.roleassignments import Assignment, RoleAssignments

ASSIGNMENTS = RoleAssignments(
    version=1,
    assignments=(
        Assignment("github:alice", ("product",)),
        Assignment("github:quinn", ("qa",)),
    ),
)
```

Required scenarios (each is one test; adapt the file's existing helpers for building graph/artifacts/facts):
1. Default allowlist: node has NO `allowed_approver_roles`; approved artifact with an approval by `github:alice` where node `owner_role: product` → no finding (owner may approve by default).
2. Default allowlist miss: same node, approval only by `github:quinn` (qa) → GC-GIT-ROLE error; message names the needed roles (`product`) and states no approval from an allowed approver role was found.
3. Exact replacement: node `owner_role: product`, `allowed_approver_roles: ("qa",)`; approval by `github:alice` (product — the OWNER) → GC-GIT-ROLE error (separation of duties: owner explicitly excluded).
4. Exact replacement hit: same node, approval by `github:quinn` → no finding.
5. Unknown identity: approval by `github:mallory` (not in assignments) → error (no roles granted, fail closed).
6. Unavailable facts: `approvals()` returns None → no finding (skip, unchanged contour).
7. Solo profile: `solo_auto_approve: true` → no finding, and `check_status_git` never needs assignments (pass `assignments=None` and assert no crash).
8. Non-solo + approvals present + `assignments=None` → the check must NOT silently pass: assert it raises (a `FactsError` or dedicated error the CLI maps to exit 2) rather than skipping. (Fail-closed: a missing mapping must never read as "authorized" or "skip".)

Run to confirm they fail against current code.

- [ ] **Step 2: Implement `check_status_git`**

Replace the role-intersection block (current `checks.py:322-333`):

```python
        if artifact_approvals is None:
            # No authoritative facts (e.g. LiveGitFacts, no forge access):
            # absence of evidence is not a proven violation.
            continue
        if assignments is None:
            raise FactsError(
                "role assignments are required to authorize approvals for "
                f"non-solo profile {graph.profile!r} (profiles/role-assignments.yaml)"
            )
        node = graph.nodes[artifact.node_id]
        # DEC-007 D7 (owner ruling): absent allowed_approver_roles → the
        # accountable owner may approve; an explicit list REPLACES that
        # default (separation of duties — the owner may be excluded).
        # Node-level only for now: the frontmatter-level field parses but
        # instance-vs-node precedence awaits its own owner ruling.
        allowed = (
            frozenset(node.allowed_approver_roles)
            if node.allowed_approver_roles is not None
            else frozenset({node.owner_role})
        )
        approver_roles: set[str] = set()
        for approval in artifact_approvals:
            approver_roles |= assignments.roles_for(approval.identity)
        if not allowed & approver_roles:
            findings.append(
                Finding(
                    "error",
                    "GC-GIT-ROLE",
                    artifact.path,
                    "approved without an approval from an allowed approver role "
                    f"(need one of: {', '.join(sorted(allowed))}; approvers "
                    f"{', '.join(sorted(a.identity for a in artifact_approvals)) or '—'} "
                    f"map to: {', '.join(sorted(approver_roles)) or 'no roles'})",
                )
            )
```

Use `FactsError` from `git_facts` (exists at git_facts.py:95, ValueError subclass, already imported by cli.py). Via the CLI this raise is a defense-in-depth backstop (the CLI pre-loads assignments for every non-solo profile), but it must still land in exit 2, not a traceback: wrap the `run_checks` call at cli.py:246 in `try/except FactsError → _fail_config` in Step 3. Thread `assignments` through `run_checks(..., assignments: RoleAssignments | None = None)` → `check_status_git`. Remove the now-unused `parse_owner_roles` import from checks.py if nothing else uses it.

- [ ] **Step 3: Wire the CLI**

In `cli.py` after loading the profile:

```python
    assignments = None
    if not graph.solo_auto_approve:
        try:
            assignments = load_role_assignments(
                profile_path.parent / "role-assignments.yaml", roles_catalog
            )
        except AssignmentsError as err:
            _fail_config(str(err))
            raise AssertionError from None  # unreachable; keeps type-checkers calm
```

and pass `assignments=assignments` to `run_checks`. Add CLI tests: (a) non-solo profile (write one with `solo_auto_approve: false`) + missing sibling `role-assignments.yaml` → exit 2, message names the file; (b) solo profile without the file → runs fine; (c) end-to-end GC-GIT-ROLE hit through `--no-fs` facts with the new `identity` shape (write a sibling role-assignments.yaml in the fixture).

- [ ] **Step 4: Sweep remaining old-shape facts fixtures**

`grep -rn '"role"\|"handle"' tests/` — every facts payload moves to `{"identity": "github:..."}`; tests asserting GC-GIT-ROLE messages update to the new message shape (assert on the stable parts: gate id, "allowed approver role", the needed-roles list). Full suite green is this task's exit gate:

```bash
uv run pytest -q && uv run ruff format . && uv run ruff check . && uv run pyrefly check
```

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: GC-GIT-ROLE authorizes via allowed_approver_roles and role assignments (DEC-007 D7)"
```

---

### Task 4: docs + TODO truth-up, battery, dogfood parity

**Files:**
- Modify: `CLAUDE.md` (roles bullet + build-order row: GC-GIT-ROLE fixed; remaining §1 = dispatcher handoff + structural-coverage decision; mention role-assignments.yaml as governance data)
- Modify: `TODO.md` §1 (flip `role-slug-github-handle-mapping` and `gc-git-role-authorization` with delivery notes referencing "PR этой ветки"; leave `dispatcher-roles-catalog-handoff` and `structural-coverage-owner-role-form` open)
- Modify: `spec/20-design.md` — the GC-GIT-ROLE / DEC-007 section: one short paragraph that authorization now checks `allowed_approver_roles` × identity-mapping, node-level, frontmatter precedence deliberately unruled (mirror the code comment)

**Interfaces:** none.

- [ ] **Step 1: Make the three edits** (surgical; no claim that reviewer_roles is enforced; no claim that live mode authorizes — live approvals stay None/skip until a facts source exists).

- [ ] **Step 2: Full battery + dogfood parity vs master** (capture master baselines READ-ONLY from `/Users/Andrei_Shtanakov/labs/all_ai_orchestrators/steward`):

```bash
uv run ruff format . && uv run ruff check .
uv run pyrefly check
uv run pytest -q
uv run gate-check spec/ --profile team-exp; echo "exit=$?"
uv run gate-check spec/ --profile team; echo "exit=$?"
uv run gate-check spec/ --profile lite; echo "exit=$?"
uv run gate-check workstreams/WS-005-gate-verdicts/spec/ --profile team-exp; echo "exit=$?"
```
Expected: identical findings/exit codes both sides (live approvals are None ⇒ GC-GIT-ROLE never fires live; the `team` run now loads `profiles/role-assignments.yaml` — it exists, so no behavioral delta). Any delta = STOP and report.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md TODO.md spec/20-design.md
git commit -m "docs: DEC-007 authorization landed; §1 down to dispatcher handoff + structural-coverage ruling"
```
