# Canonical Roles Migration (DEC-007 PR-2: D2+D4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The profile loader goes strict-canonical (singular `owner_role` slug validated against the roles catalog, new `reviewer_roles` / `allowed_approver_roles` fields, catalog passed explicitly), and ALL legacy `"@role[,@role]"` data migrates in this one PR: `profiles/{team,lite,team-exp}.yaml`, the repo's own `spec/*.md`, and the approved WS-005 bundle with its blob pins recomputed in topo order.

**Architecture:** `graph.py` gains role validation via an explicitly-passed `RolesCatalog` (no hidden filesystem lookups); the CLI loads the roles catalog BEFORE the profile (both are profile-dir siblings). Profile data and every frontmatter migrate to canonical form in the same PR, so no legacy path is added to the profile loader. WS-005 pins are recomputed from exact staged bytes; the stale-cascade gate on the final tree is the machine proof the pins are right.

**Tech Stack:** Python 3.12, uv, pytest, Typer, PyYAML, pyrefly, ruff (line length 100).

## Global Constraints

Owner's decisions, verbatim (DEC-007 2026-07-26 + design approval 2026-08-08). Every task implicitly includes this section.

- `owner_role` = exactly ONE accountable role, slug without `@`. Multiplicity ONLY via `reviewer_roles[]` / `allowed_approver_roles[]`. A multi-role legacy value is NEVER normalized silently — the accountable owner for each collision node was chosen BY THE OWNER: `requirements` → `owner_role: product`, `reviewer_roles: [architects]`; `behaviour-spec` → `owner_role: product`, `reviewer_roles: [qa]`.
- Profile loader: catalog passed explicitly — `load_profile(path, roles_catalog=...)` / `load_profile_data(data, roles_catalog=...)`. No function may silently search `profiles/roles.yaml` relative to CWD.
- `reviewer_roles` (profiles and frontmatter alike): absent → empty; if present, a non-empty list of unique resolving slugs.
- `allowed_approver_roles`: absent → effective `{owner_role}`; present → EXACT non-empty allowlist that REPLACES the default (does not extend it — otherwise separation of duties is inexpressible); empty list → error. Absent ≠ empty everywhere.
- `reviewer_roles` declares the required role; it is NOT machine-enforced until a separate PR-review evidence source exists — no code or comment may claim QA review is machine-checked.
- A role claimed inside injected approval facts is never authoritative (PR-3 concern; nothing here may start trusting `Approval.role` for authority decisions).
- D4 single migration PR must contain: all profile + frontmatter changes; recomputed blob pins; regenerated conformance evidence IF actually invalidated; a full gate-check on the final tree; an explicit migration note explaining the mass blob-hash change. Pins are computed from exact staged bytes, and verification recomputes them from the PR tree — never trust precalculated values.
- Emission contract gate-verdicts/v1 unchanged: canonical singular owner flows as an array of one element.
- Roles-catalog composition untouched (version 1, six slugs) — the composition-pin test enforces this.
- Project rules: uv only; `uv run pytest`; `uv run ruff format .` / `uv run ruff check .` (line 100); `uv run pyrefly check`; type hints; implementers commit locally only — push/PR is exclusively the controller's final step, never an implementer's.

---

### Task 1: `graph.py` — strict-canonical SpecNode + explicit catalog validation

**Files:**
- Modify: `src/steward/graph.py`
- Test: `tests/test_graph.py` (extend + update call sites)

**Interfaces:**
- Consumes: `RolesCatalog` from `steward.roles` (PR-1).
- Produces (Tasks 2–3 rely on these exact shapes):
  - `SpecNode.owner_role: str` — one canonical slug (validated: no `@`, no `,`, resolves in catalog).
  - `SpecNode.reviewer_roles: tuple[str, ...] = ()`.
  - `SpecNode.allowed_approver_roles: tuple[str, ...] | None = None` (None = absent → effective `{owner_role}` downstream; never stored as empty tuple).
  - `load_profile(path: str | Path, roles_catalog: RolesCatalog) -> SpecGraph`.
  - `load_profile_data(data: Any, roles_catalog: RolesCatalog) -> SpecGraph`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_graph.py` (adapt its existing helpers; it must define a small catalog once):

```python
from steward.roles import Role, RolesCatalog

CATALOG = RolesCatalog(
    version=1,
    slug_pattern="^[a-z][a-z0-9-]{1,31}$",
    roles=(Role("product", "Product"), Role("qa", "QA"), Role("architects", "Architecture")),
)


def _graph(data):
    return load_profile_data(data, roles_catalog=CATALOG)


def test_canonical_owner_role_loads():
    g = _graph(
        {
            "profile": "p",
            "artifacts": [{"id": "a", "owner_role": "product", "upstream": []}],
        }
    )
    assert g.nodes["a"].owner_role == "product"
    assert g.nodes["a"].reviewer_roles == ()
    assert g.nodes["a"].allowed_approver_roles is None


@pytest.mark.parametrize("bad", ["@product", "product,qa", "@product,@qa", "", 7, None])
def test_legacy_or_malformed_owner_role_rejected(bad):
    with pytest.raises(ProfileError, match="owner_role"):
        _graph(
            {
                "profile": "p",
                "artifacts": [{"id": "a", "owner_role": bad, "upstream": []}],
            }
        )


def test_unresolvable_owner_role_rejected():
    with pytest.raises(ProfileError, match="ghost"):
        _graph(
            {
                "profile": "p",
                "artifacts": [{"id": "a", "owner_role": "ghost", "upstream": []}],
            }
        )


def test_reviewer_roles_parse_and_resolve():
    g = _graph(
        {
            "profile": "p",
            "artifacts": [
                {"id": "a", "owner_role": "product", "reviewer_roles": ["qa"], "upstream": []}
            ],
        }
    )
    assert g.nodes["a"].reviewer_roles == ("qa",)


@pytest.mark.parametrize(
    "field", ["reviewer_roles", "allowed_approver_roles"]
)
@pytest.mark.parametrize("bad", [[], ["ghost"], ["qa", "qa"], ["@qa"], "qa", [7]])
def test_bad_role_arrays_rejected(field, bad):
    with pytest.raises(ProfileError, match=field):
        _graph(
            {
                "profile": "p",
                "artifacts": [
                    {"id": "a", "owner_role": "product", field: bad, "upstream": []}
                ],
            }
        )


def test_allowed_approver_roles_exact_allowlist_stored():
    # Owner's ruling: an explicit list REPLACES the {owner_role} default —
    # separation of duties must be expressible. The loader stores it verbatim;
    # it never unions in the owner.
    g = _graph(
        {
            "profile": "p",
            "artifacts": [
                {
                    "id": "a",
                    "owner_role": "product",
                    "allowed_approver_roles": ["qa"],
                    "upstream": [],
                }
            ],
        }
    )
    assert g.nodes["a"].allowed_approver_roles == ("qa",)
    assert "product" not in g.nodes["a"].allowed_approver_roles
```

Then update every existing test in `tests/test_graph.py`: call sites gain `roles_catalog=CATALOG`, and their profile data moves to canonical slugs (`"@product"` → `"product"` etc.). Multi-role fixtures (if any) split per the collision rulings.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_graph.py -v`
Expected: FAIL (signature/validation not implemented)

- [ ] **Step 3: Implement in `graph.py`**

1. Extend `SpecNode`:

```python
@dataclass(frozen=True)
class SpecNode:
    """One artifact (gate) in the governance DAG."""

    id: str
    owner_role: str
    upstream: tuple[str, ...] = ()
    required: bool = True
    template: str | None = None
    delegate: str | None = None
    per: str | None = None
    # DEC-007 canonical v2: exactly one accountable owner slug; multiplicity
    # lives in the separate arrays. reviewer_roles declares required reviewers
    # (NOT machine-enforced until a PR-review evidence source exists).
    reviewer_roles: tuple[str, ...] = ()
    # None = absent → effective allowlist is {owner_role}; an explicit list is
    # an EXACT allowlist that REPLACES that default (owner ruling 2026-08-08).
    allowed_approver_roles: tuple[str, ...] | None = None
```

2. New signatures + validation:

```python
from steward.roles import RolesCatalog


def load_profile(path: str | Path, roles_catalog: RolesCatalog) -> SpecGraph:
    """Load and validate a profile YAML file into a :class:`SpecGraph`.

    The roles catalog is passed explicitly — this module never resolves
    profiles/roles.yaml from the filesystem itself (DEC-007 D2).
    """
    text = Path(path).read_text(encoding="utf-8")
    return load_profile_data(yaml.safe_load(text), roles_catalog)


def load_profile_data(data: Any, roles_catalog: RolesCatalog) -> SpecGraph:
    ...  # thread roles_catalog into _node_from_entry
```

3. In `_node_from_entry(entry, roles_catalog)`:

```python
    owner_role = entry.get("owner_role")
    if not isinstance(owner_role, str) or not owner_role:
        raise ProfileError(f"artifact {node_id!r} missing 'owner_role'")
    if "@" in owner_role or "," in owner_role:
        raise ProfileError(
            f"artifact {node_id!r}: owner_role {owner_role!r} is the legacy "
            "'@role[,@role]' form — profiles are canonical v2: exactly one "
            "slug without '@' (DEC-007; multiplicity goes to reviewer_roles/"
            "allowed_approver_roles)"
        )
    if not roles_catalog.has(owner_role):
        raise ProfileError(
            f"artifact {node_id!r}: owner_role {owner_role!r} is not in the roles catalog"
        )

    reviewer_roles = _role_array(entry, "reviewer_roles", node_id, roles_catalog)
    allowed_approver_roles = _role_array(
        entry, "allowed_approver_roles", node_id, roles_catalog
    )
```

with the shared helper (absent → None; present → exact non-empty unique resolving list):

```python
def _role_array(
    entry: dict, field: str, node_id: str, roles_catalog: RolesCatalog
) -> tuple[str, ...] | None:
    raw = entry.get(field)
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise ProfileError(
            f"artifact {node_id!r}: {field} must be a non-empty list of role slugs (or absent)"
        )
    slugs: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item or "@" in item:
            raise ProfileError(
                f"artifact {node_id!r}: {field} must be a non-empty list of "
                f"bare role slugs, got {item!r}"
            )
        if item in slugs:
            raise ProfileError(f"artifact {node_id!r}: {field} has duplicate slug {item!r}")
        if not roles_catalog.has(item):
            raise ProfileError(
                f"artifact {node_id!r}: {field} references {item!r}, not in the roles catalog"
            )
        slugs.append(item)
    return tuple(slugs)
```

and in the `SpecNode(...)` construction: `reviewer_roles=reviewer_roles or ()`, `allowed_approver_roles=allowed_approver_roles` (note: `reviewer_roles or ()` is safe because present-empty already raised; absent→None→`()`).

4. Update the module docstring (canonical v2, explicit catalog).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_graph.py -v`
Expected: PASS. (The FULL suite is expectedly red now — other tests still pass legacy profiles and the old signature; Tasks 2–3 repair them. Do NOT try to fix the full suite in this task.)

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format src/steward/graph.py tests/test_graph.py
uv run ruff check src/steward/graph.py tests/test_graph.py
git add src/steward/graph.py tests/test_graph.py
git commit -m "feat: strict canonical-v2 profile loader with explicit roles catalog (DEC-007 D2)"
```

(pyrefly on the whole repo will still flag the not-yet-updated call sites — that is Task 2's job; run `uv run pyrefly check` for information but do not chase cross-file errors here.)

---

### Task 2: rewire every `load_profile` caller + migrate every test fixture to canonical

**Files:**
- Modify: `src/steward/gatecheck/cli.py` (`_resolve_profile` split; roles loaded before profile)
- Modify: `tests/verdicts/test_emitter.py`, `tests/gatecheck/test_behaviour.py`, `tests/gatecheck/test_checks.py`, `tests/gatecheck/test_trace_matrix.py`, `tests/gatecheck/test_stale_live.py`, `tests/gatecheck/test_cli.py`, `tests/gatecheck/test_stage_flag.py`, `tests/gatecheck/test_approval_check.py` (whichever of these carry legacy `_PROFILE` strings, `load_profile*` calls, or fs-facts `role:` strings — sweep, don't trust this list)

**Interfaces:**
- Consumes: Task 1's signatures; `_load_roles(profile_path)` from PR-1's cli.
- Produces: CLI flow `resolve profile PATH → _load_roles(path) → load_profile(path, roles) → collect → unresolved_role_refs → checks`; single roles load per run reused for profile validation, frontmatter resolution, and `--emit-verdicts`.

- [ ] **Step 1: Rewire the CLI**

In `cli.py`, `_resolve_profile` currently resolves the path AND loads. Split it:

```python
def _resolve_profile_path(profile: str) -> Path:
    """Resolve a profile name/path to the YAML path (no loading)."""
    candidate = Path(profile)
    if not candidate.suffix:
        candidate = _PROFILES_DIR / f"{profile}.yaml"
    if not candidate.is_file():
        _fail_config(f"profile {profile!r} not found (looked for {candidate})")
        raise AssertionError from None  # unreachable; keeps type-checkers calm
    return candidate
```

(match the existing name-resolution logic exactly — read the current `_resolve_profile` body and keep its behavior; only the load moves out). Then in `main`:

```python
    profile_path = _resolve_profile_path(profile)
    roles_catalog = _load_roles(profile_path)
    try:
        graph = load_profile(profile_path, roles_catalog)
    except ProfileError as err:
        _fail_config(str(err))
        raise AssertionError from None  # unreachable; keeps type-checkers calm
```

Delete the now-duplicate `_load_roles` call later in `main` (roles are loaded once, before the profile); keep `unresolved_role_refs` + the `roles catalog: <path>` trailing line and the `_load_catalog(profile_path, roles_catalog)` emit-verdicts wiring exactly as they are.

- [ ] **Step 2: Migrate every test fixture**

Sweep: `grep -rn "owner_role" tests/ | grep '@'` and `grep -rln "load_profile" tests/`. For each:
- profile YAML/dict fixtures → canonical slugs; multi-role entries split per the collision rulings (`requirements` → owner `product` + `reviewer_roles: [architects]`; `behaviour-spec` → owner `product` + `reviewer_roles: [qa]`; single legacy `"@x"`-style roles → a real catalog slug — reuse the slugs `DEFAULT_ROLES` provides).
- `load_profile(_data)` calls → pass a catalog (tests that hit the CLI need nothing: the CLI loads it; unit tests construct/reuse one like Task 1's `CATALOG` or load the fixture `write_roles` wrote).
- fs-facts JSON fixtures with `"role": "@qa"`-style approvals → bare slugs (`"qa"`), so GC-GIT-ROLE role-intersection tests keep testing the same semantics against canonical node roles.
- `tests/gatecheck/test_cli.py::_CYCLIC` keeps testing the cycle (owner_role values there must now be catalog slugs so the CYCLE, not role validation, stays the failing condition — verify the error message asserted is still the cycle one).

Ordering note: profile-level role validation (ProfileError, from `load_profile`) fires BEFORE frontmatter resolution; make sure no existing test relied on reaching frontmatter resolution with a legacy profile.

- [ ] **Step 3: Full suite green**

Run: `uv run pytest -q` and `uv run pyrefly check` and `uv run ruff format . && uv run ruff check .`
Expected: ALL PASS / clean. This is the task's exit gate.

- [ ] **Step 4: Commit**

```bash
git add -A src tests
git commit -m "refactor: CLI loads roles before profile; all test fixtures canonical (DEC-007 D2)"
```

---

### Task 3: migrate `profiles/{team,lite,team-exp}.yaml`

**Files:**
- Modify: `profiles/team.yaml`, `profiles/lite.yaml`, `profiles/team-exp.yaml`

**Interfaces:** consumed by the strict loader from Task 1; CI's gate-check jobs run `--profile team` and `--profile team-exp` against these files.

- [ ] **Step 1: Migrate the three profiles**

Exact values (owner rulings applied; everything else is a mechanical `@`-strip):

`profiles/team.yaml` artifacts:
```yaml
artifacts:
  - {id: charter,       template: charter.md,       owner_role: product,    upstream: []}
  - id: requirements
    template: requirements.md
    owner_role: product
    reviewer_roles: [architects]
    upstream: [charter]
  - {id: design,        template: design.md,        owner_role: architects, upstream: [requirements]}
  - {id: acceptance,    template: acceptance.md,    owner_role: qa,         upstream: [requirements]}
  - {id: decomposition, template: decomposition.md, owner_role: tech-lead,  upstream: [design, acceptance]}
  - {id: task,          owner_role: stream-owner,   upstream: [decomposition], delegate: spec-runner, per: workstream}
```

`profiles/lite.yaml`: the three `owner_role: "@owner"` → `owner_role: owner` (no reviewer arrays — solo).

`profiles/team-exp.yaml`: same as team, plus:
```yaml
  - id: behaviour-spec
    template: behaviour-spec.md
    owner_role: product
    reviewer_roles: [qa]
    upstream: [requirements]
```
(design/acceptance keep their behaviour-spec upstream edges exactly as they are today.)

Add one comment above the first `reviewer_roles:` in each file that has one:
```yaml
    # reviewer_roles declares the required reviewer role (DEC-007). It is NOT
    # machine-enforced yet: enforcement needs a PR-review evidence source
    # (review-facts), which does not exist — merge-actor evidence (mergedBy)
    # is a different fact and does not prove a QA review happened.
```
Preserve every existing comment in the three files (especially team-exp's solo_auto_approve block) byte-for-byte.

- [ ] **Step 2: Verify**

```bash
uv run pytest -q
uv run gate-check spec/ --profile team-exp; echo "exit=$?"
uv run gate-check spec/ --profile team; echo "exit=$?"
```
Expected: suite green; gate-check exits/findings unchanged vs master for the same commands (team-exp on spec/: exit 1, GC-COMPLETENESS behaviour-spec only; team on spec/: same result as master — capture master's output first from the main checkout, READ-ONLY).

- [ ] **Step 3: Commit**

```bash
git add profiles/
git commit -m "data: profiles migrated to canonical singular owner_role + reviewer_roles (DEC-007 D4)"
```

---

### Task 4: migrate frontmatter — own `spec/` (draft, no pins) and WS-005 (approved, pin cascade)

**Files:**
- Modify: `spec/00-charter.md`, `spec/10-requirements.md`, `spec/20-design.md`, `spec/30-acceptance.md`, `spec/40-decomposition.md` (frontmatter `owner_role` lines only)
- Modify: `workstreams/WS-005-gate-verdicts/spec/{00-charter,10-requirements,15-behaviour-spec,20-design,30-acceptance,40-decomposition}.md` (frontmatter: `owner_role` lines + recomputed `upstream_hashes`)
- Create: `docs/notes/2026-08-08-dec007-data-migration.md` (migration note)

**Interfaces:** none new — this is governance data. The stale-cascade gate (GC-STALE) on the final tree is the acceptance oracle for the pins.

- [ ] **Step 1: Migrate own `spec/` frontmatter**

All five files are `status: draft` and pin nothing, so this is a pure frontmatter edit:
- `spec/00-charter.md`: `owner_role: "@product"` → `owner_role: product`
- `spec/10-requirements.md`: `owner_role: "@product,@architects"` → `owner_role: product` + new line `reviewer_roles: [architects]` (owner's collision ruling)
- `spec/20-design.md`: `"@architects"` → `architects`
- `spec/30-acceptance.md`: `"@qa"` → `qa`
- `spec/40-decomposition.md`: `"@tech-lead"` → `tech-lead`
Touch nothing else in these files.

- [ ] **Step 2: Migrate WS-005 frontmatter in topo order, recomputing pins from exact staged bytes**

DAG: charter → requirements → behaviour-spec → {design, acceptance} → decomposition. For each file in that order: edit frontmatter, then `git hash-object <file>` to get the NEW blob for downstream pins. Role edits:
- `00-charter.md`: `"@product"` → `product`
- `10-requirements.md`: `"@product,@architects"` → `product` + `reviewer_roles: [architects]`
- `15-behaviour-spec.md`: `"@product,@qa"` → `product` + `reviewer_roles: [qa]`
- `20-design.md`: `"@architects"` → `architects`
- `30-acceptance.md`: `"@qa"` → `qa`
- `40-decomposition.md`: `"@tech-lead"` → `tech-lead`

Pin recompute (scripted, not hand-copied):
```bash
cd <worktree>
W=workstreams/WS-005-gate-verdicts/spec
# after editing 00-charter.md:
CH=$(git hash-object $W/00-charter.md)
# update 10-requirements.md: upstream_hashes.charter: $CH   … then:
RQ=$(git hash-object $W/10-requirements.md)
# update 15-behaviour-spec.md: upstream_hashes.requirements: $RQ   … then:
BS=$(git hash-object $W/15-behaviour-spec.md)
# update 20-design.md + 30-acceptance.md: requirements: $RQ, behaviour-spec: $BS  … then:
DS=$(git hash-object $W/20-design.md); AC=$(git hash-object $W/30-acceptance.md)
# update 40-decomposition.md: design: $DS, acceptance: $AC
```
Insert each value with an Edit on the exact existing hash string — do not regenerate whole frontmatter blocks. `status: approved`, `approved_by/at`, `version` fields stay UNTOUCHED (the migration is mechanical; approval provenance is not re-staged — the note explains this).

- [ ] **Step 3: Machine-verify the pins from the tree**

```bash
uv run gate-check workstreams/WS-005-gate-verdicts/spec/ --profile team-exp; echo "exit=$?"
```
Expected: exit 0, ZERO findings — in particular no GC-STALE / GC-STALE-KEY / GC-STALE-UNPINNED. This run recomputes every pin against the actual tree; if any hand-off between Step 2's hashes slipped, it fails here. Also run `uv run gate-check spec/ --profile team-exp` (expected: unchanged GC-COMPLETENESS finding only, exit 1) and the arch gates implicitly via the same run — `intended-graph.yaml` / `conformance-report.json` are untouched by frontmatter migration; if GC-ARCH-* unexpectedly fires, STOP and report (evidence regeneration would then be a real, separate decision — do not regenerate silently).

- [ ] **Step 4: Write the migration note**

`docs/notes/2026-08-08-dec007-data-migration.md`:

```markdown
# DEC-007 data migration: legacy "@role[,@role]" → canonical singular owner_role

Date: 2026-08-08 · Workstream: TODO §1 (DEC-007) PR-2 · Design: approved 2026-08-08

Every governance surface migrated in one PR: profiles/{team,lite,team-exp}.yaml,
spec/*.md, workstreams/WS-005-gate-verdicts/spec/*.md.

## Why every WS-005 blob hash changed

The WS-005 bundle is approved and pinned: each downstream artifact pins its
upstreams' git blob hashes in `upstream_hashes` (stale-cascade base). Editing
frontmatter changes a file's blob, so pins were recomputed in topo order
(charter → requirements → behaviour-spec → design/acceptance → decomposition)
from the exact staged bytes via `git hash-object`. The proof is machine-checked:
`gate-check workstreams/WS-005-gate-verdicts/spec/ --profile team-exp` on this
tree reports zero stale findings — the pins match the tree, not a hand-copied list.

## What deliberately did NOT change

- `status: approved`, `approved_by`, `approved_at`, `version` in the bundle:
  the migration is a mechanical form change decided by the owner (DEC-007,
  collision rulings: requirements → owner product + reviewer architects;
  behaviour-spec → owner product + reviewer qa). Approval provenance is not
  re-staged for it; the single migration PR (this one) is the review.
- `intended-graph.yaml` / `conformance-report.json`: architecture evidence does
  not reference frontmatter; verified by GC-ARCH-* staying green on this tree.
- The roles catalog itself (version 1, six slugs) — composition-pin test enforces.
- gate-verdicts/v1 contract: canonical singular owner still emits as a
  one-element `owner_roles` array.

## reviewer_roles semantics

`reviewer_roles` declares the required reviewer role. It is NOT machine-enforced:
enforcement needs a PR-review evidence source (review-facts) that does not exist
yet; merge-actor evidence (mergedBy) is a different fact. See TODO §1
(gc-git-role-authorization) for the enforcement path.
```

- [ ] **Step 5: Commit**

```bash
git add spec/ workstreams/WS-005-gate-verdicts/spec/ docs/notes/
git commit -m "data: migrate all spec frontmatter to canonical roles, recompute WS-005 pins (DEC-007 D4)"
```

---

### Task 5: truth-up docs + TODO + full verification battery

**Files:**
- Modify: `profiles/roles.yaml` (STATUS comment block only)
- Modify: `CLAUDE.md` (roles bullet: data migration done; GC-GIT-ROLE/mapping still PR-3)
- Modify: `TODO.md` §1 (flip `migrate-profiles-singular-roles`, `migrate-spec-frontmatter-roles`, `role-deletion-guard`, `meta-owner-roles-v2` — each with a one-line delivery note; leave `role-slug-github-handle-mapping`, `gc-git-role-authorization`, `dispatcher-roles-catalog-handoff` open)

**Interfaces:** none.

- [ ] **Step 1: `profiles/roles.yaml` STATUS block**

Replace the "Still legacy (PR-2)…" sentence: data migration landed (profiles + both spec bundles canonical); the legacy reader in `meta.py` remains transitionally for EXTERNAL data (spec-runner still writes legacy until its SpecMeta v2 — TODO §2); loader validation + composition pin unchanged. Keep data section byte-identical.

- [ ] **Step 2: `CLAUDE.md` roles bullet**

Update the `profiles/roles.yaml` bullet: strike "Data migration is still pending" — profiles and both spec bundles are canonical v2; the legacy `"@role[,@role]"` reader remains only for external/spec-runner-authored data (TODO §2); GC-GIT-ROLE still consults owner roles — `allowed_approver_roles` enforcement + slug→identity mapping are the remaining §1 items (PR-3).

- [ ] **Step 3: `TODO.md` §1 flips (exact wording judgment: keep each line's tags; append a delivery note)**

- `migrate-profiles-singular-roles` → `[x]` — "PR этой ветки: team/lite/team-exp canonical, collision rulings applied".
- `migrate-spec-frontmatter-roles` → `[x]` — "PR этой ветки: оба бандла; WS-005 пины пересчитаны в topo-порядке, GC-STALE 0 на итоговом дереве".
- `role-deletion-guard` → `[x]` — "composition-pin (PR-1) + разрешимость ссылок во всех загрузчиках (профили PR-2, frontmatter PR-1, gate-catalog PR-1): удаление используемой роли ломает загрузку громко; assignments-файл появится в PR-3 и валидируется так же".
- `meta-owner-roles-v2` → `[x]` — "reader canonical+legacy (PR-1), все данные steward canonical (PR-2); писателя frontmatter у steward нет — canonical закреплён данными и строгим profile-loader; legacy-путь останется до SpecMeta v2 (§2)".

- [ ] **Step 4: Full verification battery + dogfood parity**

```bash
uv run ruff format . && uv run ruff check .
uv run pyrefly check
uv run pytest -q
uv run gate-check spec/ --profile team-exp; echo "exit=$?"
uv run gate-check spec/ --profile team; echo "exit=$?"
uv run gate-check spec/ --profile lite; echo "exit=$?"
uv run gate-check workstreams/WS-005-gate-verdicts/spec/ --profile team-exp; echo "exit=$?"
```
Expected: battery clean; finding sets and exit codes identical to master for the same commands (capture master baselines READ-ONLY from the main checkout `/Users/Andrei_Shtanakov/labs/all_ai_orchestrators/steward` first). Any delta = STOP and report, not paper over.

- [ ] **Step 5: Commit**

```bash
git add profiles/roles.yaml CLAUDE.md TODO.md
git commit -m "docs: DEC-007 data migration landed; §1 data items closed"
```
