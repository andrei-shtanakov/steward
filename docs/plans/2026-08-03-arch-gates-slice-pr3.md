# Slice PR-3: `GC-ARCH-*` gates — design + implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `GC-ARCH-SCHEMA` / `GC-ARCH-EVIDENCE` / `GC-ARCH-CONFORMANCE` gates
as offline consumers of prograph's versioned-evidence conformance report, per the ADR
behaviour-architecture-lifecycle gate table and the owner rulings of 2026-08-03
(вариант 1: committed co-located report; вариант 2: no new DAG node in this slice).

**Status:** Design decisions below were settled with the owner in conversation
(2026-08-03); this document records them and decomposes the implementation.

## Design decisions (settled)

- **D1 — no new DAG node.** The `architecture` node / `design`→`architecture` rename
  stays an open ADR question (second signal). `GC-ARCH-*` checks activate on the
  presence of `intended-graph.yaml` in the bundle dir — file presence is the owner's
  opt-in, exactly as adding a `behaviour-spec` node is. No existing bundle carries the
  file, so `team`/`lite` behaviour is unchanged.
- **D2 — evidence is co-located and owner-committed.** The pair lives together:
  `workstreams/WS-005-gate-verdicts/spec/{intended-graph.yaml, conformance-report.json}`.
  The report is refreshed by the owner, explicitly, in the same PR that changes the
  manifest / modelled code surface. No bot commits evidence; a future scheduled job may
  recompute-and-compare and open drift issues, never rewrite the canon.
- **D3 — mandatory core vs freshness policy.** Always-on (any stage): report parses,
  validates against the pinned report schema, `manifest.sha256` matches the co-located
  manifest bytes, `complete: true`. Broader freshness (self-commit match, snapshot age)
  is **stage policy**, not hardcoded: `authoring` is permissive, `release` strict.
- **D4 — declarative stage policy** in `profiles/arch-policy.yaml` (governance data,
  changed via PR like other profiles):

  ```yaml
  self_project: steward
  stages:
    authoring:
      fail_on_findings: []
      fail_on_verdicts: [violation]
      require_self_fresh: false
      max_snapshot_age_hours: null
    release:
      fail_on_findings: [missing-required-edge]
      fail_on_verdicts: [violation, unknown]
      require_self_fresh: true
      max_snapshot_age_hours: 24
  ```

  Vocabulary is prograph's closed taxonomy verbatim — steward never re-interprets
  finding semantics, it only applies the declarative policy. Stage is selected by a new
  `gate-check --arch-stage authoring|release` option (default `authoring`).
  Age policy checks `snapshot.indexed_at` (the mandatory freshness dimension per the
  provenance spec), never `generated_at`.
- **D5 — vendored schemas, two guarantees.** Byte-pinned copies from
  `prograph@8deb730`:
  `contracts/prograph-intended-graph/v1/schema.json` and
  `contracts/prograph-conformance-report/v1/schema.json` (naming mirrors dispatcher's
  `contracts/steward-gate-verdicts/v1` consumer convention), each with a `PIN` file.
  Copy-integrity = offline contract test (sha256 of the vendored bytes against the PIN).
  Upstream-drift = scheduled observation, **out of PR CI** (workspace obligation;
  absent/expired ⇒ unknown, not clean) — same split as the gate-verdicts contract.
- **D6 — structural vs semantic split** (from the provenance spec D6):
  `GC-ARCH-SCHEMA` proves *structural* conformance of the manifest via the pinned
  schema and a stock `jsonschema` engine — no second parser, no re-derived integrity
  rules. Cross-object integrity is attested by the successfully produced report
  (prograph writes no report on exit 2).
- **D7 — the current WS-005 manifest fails GC-ARCH-EVIDENCE honestly.** Its four
  interfaces carry no `evidence` (components and constraints do). The slice includes a
  manifest amendment adding interface evidence — proposed ids below (Task 4), owner
  approves in the PR. The gate ships strict per the ADR row (error severity).
- **D8 — pure checks, I/O at the edge.** Following the module convention
  (checks are pure functions), `architecture.py` exposes
  `collect_arch_bundle(spec_dir)` (the only I/O: reads the two files) and a pure
  `check_architecture(arch, policy, stage, git)`; the CLI wires them.

## Global Constraints

- uv only. New **runtime** dep: `jsonschema` (`uv add jsonschema`). PyYAML already
  present.
- Ruff line length 100 (`pyproject.toml`); `uv run pyrefly check` per repo convention;
  `uv run ruff format .` / `uv run ruff check . --fix`; `uv run pytest` — local gate
  before every commit (ci.yml also runs these).
- Git: branch `feat/arch-gates`, PR at the end, no direct master commits, no self-merge.
- prograph vocabulary is closed and normative: finding classes
  `missing-required-edge | forbidden-edge | undeclared-edge | orphan-component |
  expired-waiver | manual-obligation`; verdicts `conformant | violation | unknown`.
  Policy validation rejects unknown entries (config error, exit 2).
- Gate rule ids and severities per the ADR table: `GC-ARCH-EVIDENCE` error,
  `GC-ARCH-SCHEMA` error, `GC-ARCH-CONFORMANCE` error/warn (stage policy decides which
  findings block).
- Vendored files are byte-exact from `prograph@8deb730`; fetched via
  `git -C ../prograph show 8deb730:contracts/<name>/v1/schema.json` (prograph is
  READ-ONLY — never modify it).

## File Structure

- Create: `contracts/prograph-intended-graph/v1/{schema.json,PIN}`,
  `contracts/prograph-conformance-report/v1/{schema.json,PIN}`
- Create: `src/steward/gatecheck/architecture.py`, `profiles/arch-policy.yaml`
- Modify: `src/steward/gatecheck/checks.py` (run_checks dispatch),
  `src/steward/gatecheck/cli.py` (`--arch-stage`), `pyproject.toml` (+jsonschema)
- Create: `tests/contract/test_prograph_schemas_pinned.py`,
  `tests/gatecheck/test_architecture.py`
- Modify: `workstreams/WS-005-gate-verdicts/spec/intended-graph.yaml` (interface
  evidence, D7), `TODO.md`, `CLAUDE.md`

---

### Task 1: Vendor the two schemas + copy-integrity contract test

**Files:**
- Create: `contracts/prograph-intended-graph/v1/schema.json`,
  `contracts/prograph-intended-graph/v1/PIN`,
  `contracts/prograph-conformance-report/v1/schema.json`,
  `contracts/prograph-conformance-report/v1/PIN`
- Modify: `pyproject.toml` (`uv add jsonschema`)
- Test: `tests/contract/test_prograph_schemas_pinned.py`

**Interfaces:**
- Produces (Tasks 2–3 consume): the two vendored schema paths; helper-free — checks
  load them by path.

- [ ] **Step 1: Vendor byte-exact + write PINs**

```sh
uv add jsonschema
mkdir -p contracts/prograph-intended-graph/v1 contracts/prograph-conformance-report/v1
git -C ../prograph show 8deb730:contracts/intended-graph/v1/schema.json \
  > contracts/prograph-intended-graph/v1/schema.json
git -C ../prograph show 8deb730:contracts/conformance-report/v1/schema.json \
  > contracts/prograph-conformance-report/v1/schema.json
shasum -a 256 contracts/prograph-intended-graph/v1/schema.json \
  contracts/prograph-conformance-report/v1/schema.json
```

`contracts/prograph-intended-graph/v1/PIN` (fill the real hash from shasum):

```
source: prograph@8deb730 contracts/intended-graph/v1/schema.json
sha256: <hex from shasum>
vendored: 2026-08-03
purpose: GC-ARCH-SCHEMA structural validation (steward never re-derives integrity rules)
```

`contracts/prograph-conformance-report/v1/PIN` — same shape, source
`prograph@8deb730 contracts/conformance-report/v1/schema.json`, purpose
`GC-ARCH-CONFORMANCE step 1: the committed report must be a conformance-report/v1`.

- [ ] **Step 2: Write the failing contract test**

`tests/contract/test_prograph_schemas_pinned.py`:

```python
"""Copy-integrity guarantee for the vendored prograph schemas (offline PR-gate).

Upstream-drift is the OTHER guarantee — scheduled observation outside PR CI
(two-guarantees rule); this test must never call out to the sibling repo.
"""

import hashlib
import json
from pathlib import Path

import pytest

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
VENDORED = [
    CONTRACTS / "prograph-intended-graph" / "v1",
    CONTRACTS / "prograph-conformance-report" / "v1",
]


def _pinned_sha(pin_text: str) -> str:
    for line in pin_text.splitlines():
        if line.startswith("sha256:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("PIN file has no sha256: line")


@pytest.mark.parametrize("vdir", VENDORED, ids=lambda p: p.parent.name)
def test_vendored_schema_matches_pin(vdir: Path) -> None:
    schema_bytes = (vdir / "schema.json").read_bytes()
    assert hashlib.sha256(schema_bytes).hexdigest() == _pinned_sha(
        (vdir / "PIN").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("vdir", VENDORED, ids=lambda p: p.parent.name)
def test_vendored_schema_is_valid_json_schema(vdir: Path) -> None:
    import jsonschema

    schema = json.loads((vdir / "schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
```

- [ ] **Step 3: Run to verify failure, then fill PINs, then pass**

Run `uv run pytest tests/contract/test_prograph_schemas_pinned.py -v` — first with a
placeholder PIN sha to see the mismatch assert fire, then with the real hashes: PASS.

- [ ] **Step 4: Format, lint, typecheck, commit**

```sh
uv run ruff format . && uv run ruff check .
uv run pyrefly check
uv run pytest -q
git add contracts pyproject.toml uv.lock tests/contract/test_prograph_schemas_pinned.py
git commit -m "feat(contracts): vendor prograph intended-graph/v1 + conformance-report/v1 (pinned)"
```

---

### Task 2: `architecture.py` — bundle collection, GC-ARCH-SCHEMA, GC-ARCH-EVIDENCE

**Files:**
- Create: `src/steward/gatecheck/architecture.py`
- Test: `tests/gatecheck/test_architecture.py`

**Interfaces:**
- Consumes: `Finding` from `steward.gatecheck.checks`; the vendored schema paths.
- Produces (Task 3 extends, Task 4 wires):

```python
ARCH_MANIFEST = "intended-graph.yaml"
ARCH_REPORT = "conformance-report.json"

@dataclass(frozen=True)
class ArchBundle:
    manifest_rel: str            # bundle-relative POSIX path of the manifest
    manifest_bytes: bytes
    manifest_doc: object | None  # yaml.safe_load result; None when unparseable
    manifest_error: str | None
    report_rel: str | None       # None when the report file is absent
    report_bytes: bytes | None
    report_doc: object | None
    report_error: str | None

def collect_arch_bundle(spec_dir: Path) -> ArchBundle | None
    # None when no intended-graph.yaml anywhere under spec_dir (gates inactive).
    # Finds the manifest via rglob (nested bundle layouts allowed); the report must
    # sit NEXT TO the manifest (co-located pair, design D2).

def check_arch_schema(arch: ArchBundle) -> list[Finding]
def check_arch_evidence(arch: ArchBundle) -> list[Finding]
```

Semantics:

- `check_arch_schema` (rule `GC-ARCH-SCHEMA`, severity error): manifest unparseable
  YAML ⇒ error; else canonicalize to JSON types
  (`json.loads(json.dumps(doc, default=str))`) and validate against the vendored
  intended-graph schema with `jsonschema.Draft202012Validator`; every validation error
  becomes one finding (path-prefixed message, sorted by JSON path, no truncation).
- `check_arch_evidence` (rule `GC-ARCH-EVIDENCE`, severity error): for every entry in
  `components` / `interfaces` / `constraints`: `evidence` key present, is a list, and
  non-empty — else one finding naming the element id (or its index when `id` is
  absent). Skips silently when the manifest doc is not a dict or failed schema-level
  parse (GC-ARCH-SCHEMA already reported it).

- [ ] **Step 1: Write the failing tests**

`tests/gatecheck/test_architecture.py` (start; Task 3 appends):

```python
"""GC-ARCH-* gates: schema, evidence (Task 2) + conformance (Task 3)."""

from pathlib import Path

from steward.gatecheck.architecture import (
    collect_arch_bundle,
    check_arch_evidence,
    check_arch_schema,
)

VALID_MANIFEST = """\
schema: intended-graph/v1
system: t-sys
components:
  - id: a.svc
    project: alpha
    kind: service
    owner: architects
    responsibility: "serves"
    evidence: [FR-01]
interfaces:
  - id: I-01
    producer: a.svc
    consumer: "file:beta/data.txt"
    detector: declared
    evidence: [BEH-01]
constraints:
  - id: C-01
    rule: "forbidden: alpha -> beta"
    detector: import
    evidence: [FR-02]
"""


def _bundle(tmp_path: Path, manifest: str) -> Path:
    (tmp_path / "intended-graph.yaml").write_text(manifest, encoding="utf-8")
    return tmp_path


def test_no_manifest_means_inactive(tmp_path: Path) -> None:
    assert collect_arch_bundle(tmp_path) is None


def test_valid_manifest_clean(tmp_path: Path) -> None:
    arch = collect_arch_bundle(_bundle(tmp_path, VALID_MANIFEST))
    assert arch is not None
    assert check_arch_schema(arch) == []
    assert check_arch_evidence(arch) == []


def test_schema_unknown_key_and_bad_enum(tmp_path: Path) -> None:
    bad = VALID_MANIFEST.replace("kind: service", "kind: banana") + "extra: boom\n"
    arch = collect_arch_bundle(_bundle(tmp_path, bad))
    assert arch is not None
    findings = check_arch_schema(arch)
    assert findings and all(f.rule_id == "GC-ARCH-SCHEMA" for f in findings)
    assert all(f.severity == "error" for f in findings)


def test_schema_unparseable_yaml(tmp_path: Path) -> None:
    arch = collect_arch_bundle(_bundle(tmp_path, "schema: [unclosed"))
    assert arch is not None
    findings = check_arch_schema(arch)
    assert len(findings) == 1 and "YAML" in findings[0].message


def test_evidence_missing_on_interface(tmp_path: Path) -> None:
    stripped = VALID_MANIFEST.replace("    evidence: [BEH-01]\n", "")
    arch = collect_arch_bundle(_bundle(tmp_path, stripped))
    assert arch is not None
    findings = check_arch_evidence(arch)
    assert [f.rule_id for f in findings] == ["GC-ARCH-EVIDENCE"]
    assert "I-01" in findings[0].message


def test_evidence_empty_list_is_a_finding(tmp_path: Path) -> None:
    bad = VALID_MANIFEST.replace("evidence: [FR-02]", "evidence: []")
    arch = collect_arch_bundle(_bundle(tmp_path, bad))
    assert arch is not None
    assert any("C-01" in f.message for f in check_arch_evidence(arch))


def test_manifest_found_in_nested_dir(tmp_path: Path) -> None:
    nested = tmp_path / "ws" / "spec"
    nested.mkdir(parents=True)
    (nested / "intended-graph.yaml").write_text(VALID_MANIFEST, encoding="utf-8")
    arch = collect_arch_bundle(tmp_path)
    assert arch is not None
    assert arch.manifest_rel == "ws/spec/intended-graph.yaml"
```

- [ ] **Step 2: Run to verify failure** — module absent.

- [ ] **Step 3: Implement** `src/steward/gatecheck/architecture.py` per the interface
block above. Skeleton for the schema check body:

```python
_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "contracts"
_MANIFEST_SCHEMA_PATH = _SCHEMA_DIR / "prograph-intended-graph" / "v1" / "schema.json"
_REPORT_SCHEMA_PATH = _SCHEMA_DIR / "prograph-conformance-report" / "v1" / "schema.json"


def _load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_json_types(doc: object) -> object:
    return json.loads(json.dumps(doc, default=str))


def check_arch_schema(arch: ArchBundle) -> list[Finding]:
    if arch.manifest_error is not None:
        return [
            Finding(
                "error", "GC-ARCH-SCHEMA", arch.manifest_rel,
                f"unparseable YAML: {arch.manifest_error}",
            )
        ]
    validator = jsonschema.Draft202012Validator(_load_schema(_MANIFEST_SCHEMA_PATH))
    errors = sorted(
        validator.iter_errors(_as_json_types(arch.manifest_doc)),
        key=lambda e: list(map(str, e.absolute_path)),
    )
    return [
        Finding(
            "error", "GC-ARCH-SCHEMA", arch.manifest_rel,
            f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}",
        )
        for e in errors
    ]
```

(`collect_arch_bundle` reads bytes, `yaml.safe_load` in try/except capturing the error
string; the report file is loaded the same way when present next to the manifest.
`check_arch_evidence` iterates the three collections with `isinstance` guards.)

- [ ] **Step 4: Run tests, format, typecheck, commit**

```sh
uv run pytest tests/gatecheck/test_architecture.py -v && uv run pytest -q
uv run ruff format . && uv run ruff check .
uv run pyrefly check
git add src/steward/gatecheck/architecture.py tests/gatecheck/test_architecture.py
git commit -m "feat(gatecheck): GC-ARCH-SCHEMA + GC-ARCH-EVIDENCE over the intended manifest"
```

---

### Task 3: GC-ARCH-CONFORMANCE + stage policy

**Files:**
- Create: `profiles/arch-policy.yaml` (content = design D4 block verbatim)
- Modify: `src/steward/gatecheck/architecture.py`
- Test: `tests/gatecheck/test_architecture.py` (append)

**Interfaces:**
- Produces (Task 4 wires):

```python
@dataclass(frozen=True)
class ArchPolicy:
    self_project: str | None
    fail_on_findings: frozenset[str]
    fail_on_verdicts: frozenset[str]
    require_self_fresh: bool
    max_snapshot_age_hours: int | None

class ArchPolicyError(Exception): ...

def load_arch_policy(path: Path, stage: str) -> ArchPolicy
    # Raises ArchPolicyError on: unreadable/malformed YAML, unknown stage, unknown
    # finding classes / verdicts (validated against the closed prograph vocabulary),
    # negative age. The CLI maps ArchPolicyError to exit 2 (config error).

def check_arch_conformance(
    arch: ArchBundle,
    policy: ArchPolicy,
    git: GitFacts,
    *,
    now: dt.datetime | None = None,
) -> list[Finding]
```

`check_arch_conformance` semantics (rule `GC-ARCH-CONFORMANCE`):

1. **Mandatory core — severity error regardless of stage (design D3):**
   - report file absent ⇒ `"manifest present but no co-located conformance-report.json
     — commit the evidence in the same PR (see docs/plans/2026-08-03-arch-gates-slice-pr3.md)"`;
   - report unparseable JSON ⇒ error;
   - report fails the vendored conformance-report schema ⇒ one error per validation
     error (same rendering as GC-ARCH-SCHEMA);
   - `report["manifest"]["sha256"] != sha256(arch.manifest_bytes).hexdigest()` ⇒ error
     (stale evidence — the report judged a different manifest);
   - `report["snapshot"]["complete"] is not True` ⇒ error (fail-closed on the
     producer's assertion).
   When any of these fire, the stage checks below are skipped (garbage in, no point).
2. **Stage policy (declarative, D4):**
   - any report finding with `class` ∈ `policy.fail_on_findings` and
     `suppressed_by is None` ⇒ error naming the class and element;
   - any report element with `verdict` ∈ `policy.fail_on_verdicts` and
     `waived_by is None` ⇒ error naming the element id and verdict;
   - `policy.require_self_fresh` and `policy.self_project` set: the report's
     `projects[self_project]` must exist, have `dirty == False`, and its `commit` must
     equal the repo HEAD from `git` (reuse the same HEAD source `emit_verdicts` /
     `check_status_git` already use — adapt to the actual `GitFacts` surface); any
     mismatch/null ⇒ error ("self freshness not provable ⇒ unknown, not clean");
   - `policy.max_snapshot_age_hours` set: parse `report["snapshot"]["indexed_at"]`
     (`YYYY-MM-DDTHH:MM:SSZ`); if `now - indexed_at` exceeds the bound ⇒ error. `now`
     is injectable for tests; defaults to `datetime.now(UTC)`. NEVER uses
     `generated_at` (report age ≠ snapshot age — the owner-mandated split).

- [ ] **Step 1: Write the failing tests** (append; build a minimal valid report dict in
a helper and serialize with the real sha256 of the manifest bytes; cover: missing
report / bad json / sha mismatch / complete false / schema-invalid report /
authoring-stage passes with unknown verdicts but fails on violation / release-stage
fails on missing-required-edge + unknown verdict + stale snapshot age + self-commit
mismatch / suppressed findings and waived elements do NOT fire / unknown policy stage
and unknown vocabulary raise ArchPolicyError). Use a fake GitFacts per existing
gatecheck test patterns.

- [ ] **Step 2: Run to verify failure; implement; run to pass** (as specced above; keep
`check_arch_conformance` pure — all I/O already happened in `collect_arch_bundle`).

- [ ] **Step 3: Full local gate, commit**

```sh
uv run pytest -q && uv run ruff format . && uv run ruff check . && uv run pyrefly check
git add src/steward/gatecheck/architecture.py profiles/arch-policy.yaml \
  tests/gatecheck/test_architecture.py
git commit -m "feat(gatecheck): GC-ARCH-CONFORMANCE — offline evidence consumer + stage policy"
```

---

### Task 4: CLI wiring, WS-005 manifest evidence, docs, TODO

**Files:**
- Modify: `src/steward/gatecheck/checks.py` (dispatch), `src/steward/gatecheck/cli.py`
  (`--arch-stage`), `workstreams/WS-005-gate-verdicts/spec/intended-graph.yaml`,
  `TODO.md`, `CLAUDE.md`
- Test: `tests/gatecheck/test_cli.py` (append) or `test_architecture.py` integration
  section

**Steps:**

- [ ] **Step 1: Wire dispatch.** In `run_checks` — note the architecture checks need
`spec_dir` (file-driven, not artifact-driven) which `run_checks` does not receive:
follow the existing seam instead — call the arch checks from `cli.py` right after
`run_checks`, mirroring how `collect_bundle` + `run_checks` compose:

```python
    from steward.gatecheck.architecture import (
        ArchPolicyError,
        check_arch_conformance,
        check_arch_evidence,
        check_arch_schema,
        collect_arch_bundle,
        load_arch_policy,
    )

    arch = collect_arch_bundle(spec_dir)
    if arch is not None:
        try:
            policy = load_arch_policy(Path("profiles/arch-policy.yaml"), arch_stage)
        except ArchPolicyError as err:
            _fail_config(str(err))
        findings.extend(check_arch_schema(arch))
        findings.extend(check_arch_evidence(arch))
        findings.extend(check_arch_conformance(arch, policy, git))
```

with the new CLI option:

```python
    arch_stage: str = typer.Option(
        "authoring",
        "--arch-stage",
        help="GC-ARCH-CONFORMANCE stage policy: authoring | release "
        "(profiles/arch-policy.yaml).",
    ),
```

- [ ] **Step 2: Integration test** — tmp bundle with the WS-005-shaped manifest + a
matching minimal report ⇒ `gate-check` (CliRunner or direct function calls per existing
test_cli.py patterns) exits 0 at authoring; with the report deleted ⇒ exit 1 with a
GC-ARCH-CONFORMANCE error; `--arch-stage nonsense` ⇒ exit 2.

- [ ] **Step 3: WS-005 manifest evidence (D7).** Add `evidence` to the four interfaces
in `workstreams/WS-005-gate-verdicts/spec/intended-graph.yaml` — proposed mapping from
the design artifact's conformance table (owner approves in the PR):

```yaml
  # I-01 (emitter -> file):        evidence: [BEH-01, BEH-07]
  # I-02 (file -> collector):      evidence: [BEH-02, BEH-03]
  # I-03 (contract vendoring):     evidence: [CON-02]
  # I-04 (collector -> panel):     evidence: [BEH-09, FR-01]
```

Note: the co-located `conformance-report.json` for WS-005 is NOT committed in this
slice — generating it is the operational cross-repo step (run
`prograph conformance --project steward --format json` in the umbrella workspace after
a fresh index) and lands in its own PR together with this manifest change, because the
manifest edit invalidates any previously generated report (`manifest.sha256`). Until
then the WS-005 bundle is simply not linted with the arch gates in CI (nothing points
gate-check at `workstreams/` today).

- [ ] **Step 4: Docs + TODO.**
  - `CLAUDE.md`: architecture section gains `gatecheck/architecture.py` (GC-ARCH-*,
    offline consumer of prograph's conformance report; policy in
    `profiles/arch-policy.yaml`); ecosystem-rules section notes the two vendored
    prograph contracts and the two-guarantees split; commands block notes
    `--arch-stage`.
  - `TODO.md`: flip `@id:behaviour-arch-gates` to `[x]` recording what shipped; add a
    follow-up item for the operational WS-005 evidence run + a `@trigger`-style note
    for the scheduled upstream-drift/freshness workspace check.

- [ ] **Step 5: Full gate, commit, PR**

```sh
uv run pytest -q && uv run ruff format . && uv run ruff check . && uv run pyrefly check
git add -A && git commit -m "feat(gatecheck): wire GC-ARCH-* into gate-check; WS-005 interface evidence"
git push -u origin feat/arch-gates
gh pr create --title "feat: GC-ARCH-* gates — offline consumers of prograph conformance evidence" \
  --body "Slice PR-3 per docs/plans/2026-08-03-arch-gates-slice-pr3.md ..."
```

Then action the Copilot review; do NOT merge (owner merges).

---

## Self-Review (performed while writing)

- **Coverage vs the settled decisions:** D1 (file-presence activation, no node — Tasks
  2/4), D2 (co-located pair, owner-committed — Task 3 mandatory core message, Task 4
  step 3 keeps the report out of this slice deliberately), D3 (mandatory core vs
  policy — Task 3 ordering), D4 (declarative policy + closed-vocabulary validation +
  snapshot-age-not-report-age — Task 3), D5 (two pinned vendored schemas +
  copy-integrity test, drift out of CI — Task 1), D6 (stock jsonschema engine, no
  second parser — Task 2), D7 (WS-005 interface evidence amendment with proposed ids —
  Task 4), D8 (I/O only in collect_arch_bundle — Tasks 2–4).
- **ADR gate table:** GC-ARCH-EVIDENCE error ✓, GC-ARCH-SCHEMA error ✓,
  GC-ARCH-CONFORMANCE error/warn-by-policy ✓ (authoring permissive, release strict).
- **Known adaptation points for implementers** (named, not hidden): the exact
  `GitFacts` HEAD/dirty surface (Task 3 reuses whatever `emit_verdicts` /
  `check_status_git` use); existing test-helper patterns for fake git facts and CLI
  invocation.
