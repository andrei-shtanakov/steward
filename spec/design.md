# Design — docs: risk-classify and waivers-check usage guide

## Overview

This design specifies the single new artifact `docs/risk-classify.md`: a one-page English usage guide for the two WS-006 M1 CLI commands, `steward risk-classify` and `steward waivers-check`. The design is documentation-only — it defines the document's structure, the exact content each section must carry, and how each statement is grounded in the verified source of truth (`src/steward/riskclassify/{model,classify,cli,waivers}.py` and `workstreams/WS-006-risk-model/spec/design.md`). No code, config, or existing file is touched.

## Design Decisions

[DESIGN-001] **Single-file, additive change.** The only filesystem effect is creating `docs/risk-classify.md` (the `docs/` directory is created implicitly if absent — creating the parent directory for the new file is inherent to [REQ-001], not a separate artifact). No other file is created, modified, or deleted; in particular the guide does not add cross-links into `README.md` or other docs, since that would violate the one-file scope. Traces: [REQ-001].

[DESIGN-002] **Document skeleton.** The guide is valid GitHub-flavored markdown with this fixed outline, giving the required two-part structure:

```
# Risk classification CLI            (1–2 sentence intro: WS-006 M1, two commands)
## steward risk-classify             (part 1)
### Input modes                      (three modes, one example each)
### Output                           (JSON field table)
### Exit codes                       (0 / 2)
## steward waivers-check [DIR]       (part 2)
### Waiver frontmatter               (five fields)
### Semantics                        (SHA-bound, critical forbidden)
### Exit codes                       (0 / 1 / 2)
```

All command invocations and JSON snippets sit in fenced code blocks (` ```bash `, ` ```json `, ` ```yaml ` for the waiver frontmatter example). Target length ≈ 60–90 lines of markdown — one screenful, no exhaustive reference dump. Traces: [REQ-002], [REQ-013].

[DESIGN-003] **Grounding rule: source code wins.** Every documented fact is taken from the module docstrings and code, verified during authoring, not from the task description. Concretely verified anchors:
- `cli.py` module docstring — the three input modes, their JSON shapes, exit-code contract ("0 classified, 2 config error"), and the "classification never fails with findings — it is a function, not a check" framing.
- `classify.py` — `Classification` dataclass fields; phase values `ex_ante` / `ex_post`; `scope_violation` flag semantics.
- `model.py` — `RiskModelError` → exit 2 mapping; tier ladder `low < medium < high < critical`; `version_sha` provenance ("sha256:…" of the model file).
- `waivers.py` docstring + `validate_waivers` — the five required frontmatter fields, full-40-hex SHA requirement, stale-SHA and forbidden-tier findings.
- `workstreams/WS-006-risk-model/spec/design.md` (DESIGN-604/605/608/609/610) — two-phase model, combinator, SHA invalidation, waiver-as-file.

Where the project description and source could diverge, the source prevails (none found — description and source agree). Traces: [REQ-003], [REQ-014].

[DESIGN-004] **Part 1 intro — what risk-classify does.** One short paragraph: the command classifies a change into a risk tier (`low`/`medium`/`high`/`critical`) in one of two phases — **ex-ante** over a declared scope (before the diff exists) or **ex-post** over an actual diff — as `max(profile floor, change_class, blast_radius, trust_boundary)`; output is deterministic JSON on stdout, intended to be the single source of truth for tiers (consumers such as Maestro read it, never compute risk themselves). Traces: [REQ-004].

[DESIGN-005] **Input modes subsection.** Three short entries, each with exactly one fenced example, mirroring `cli.py`:

1. `--declared scope.json` — ex-ante over a declared scope. Example JSON with the required fields from `_read_json(required=("project","sha","scope"))`:
   ```json
   {"project": "steward", "sha": "<40-hex sha>", "scope": ["docs/**"], "flags": []}
   ```
   (`flags` noted as optional.)
2. `--no-fs facts.json` — ex-post with injected facts, no git/filesystem access (deterministic CI). Example with required `project`, `sha`, `paths` and optional `declared_scope`, `flags`; the guide notes that paths outside `declared_scope` set the `scope_violation` flag and raise the tier to at least `high`.
3. `--diff BASE..HEAD` — ex-post over live git; example is an invocation, not JSON: `steward risk-classify --diff origin/master..HEAD [--repo . --project steward]`.

A one-line note states that exactly one of the three must be given, and mentions the shared options `--risk-model` (default `profiles/risk-model.yaml`) and `--profile` (default `lite`) — both are M1 options visible in `cli.py`, so documenting them briefly does not breach [REQ-014]. Traces: [REQ-005], [REQ-014].

[DESIGN-006] **Output fields subsection.** A compact markdown table (field → meaning), one row per required field, meanings taken from `Classification` and the WS-006 design:
- `tier` — resulting risk tier: `low`/`medium`/`high`/`critical`.
- `phase` — `ex_ante` (declared scope) or `ex_post` (actual diff).
- `inputs` — the three axis values that fed the combinator: `change_class`, `blast_radius`, `trust_boundary`.
- `dominant_axis` — which axis produced the max (or `floor` when the profile floor dominates).
- `floor_profile` — the profile whose floor was applied (e.g. `lite`).
- `mandatory_gates` — gate ids mandatory at this tier, from the risk model's `tier_gates`.
- `flags` — raised flags, e.g. `scope_violation`.
- `sha` — the commit SHA the classification is bound to.
- `risk_model_version` — `sha256:…` hash of the risk-model file, for reproducibility.

One trailing sentence: output is byte-stable (sorted keys, fixed formatting) so two runs can be diffed directly. Traces: [REQ-006].

[DESIGN-007] **risk-classify exit codes.** Two-row list per `cli.py`: `0` — classified successfully (any tier, including `critical`, is exit 0 — classification is a function, not a check); `2` — config error (bad/missing risk model, invalid input file, wrong option usage). No exit 1 exists for this command and none is documented. Traces: [REQ-007], [REQ-014].

[DESIGN-008] **Part 2 — waivers-check invocation.** Documented form: `steward waivers-check [DIR]` where `DIR` defaults to `spec/waivers` (per the Typer argument in `cli.py`); mention `--sha` (defaults to `git HEAD` of `--repo`) in one line since SHA comparison is the command's core. A missing directory is clean (no waivers, exit 0). Traces: [REQ-008].

[DESIGN-009] **Waiver frontmatter subsection.** One fenced YAML frontmatter example plus a five-item field list, per `waivers.py`:
- `gate_id` — the mandatory gate being waived;
- `sha` — full 40-hex commit SHA the waiver covers;
- `tier` — the classification tier the waiver was issued against;
- `waived_by` — git handle of the approver;
- `reason` — human justification.

A one-line note that approval is the merged PR itself (CODEOWNERS on the waivers directory), per DESIGN-609. Traces: [REQ-009].

[DESIGN-010] **Semantics subsection.** Two short statements grounded in `validate_waivers` / DESIGN-608/609:
1. **SHA-bound:** a waiver is valid only while `sha` equals the current HEAD; any new commit (including a rebase) invalidates all waivers, which must be removed or re-issued (`waiver-stale-sha`).
2. **Critical forbidden:** waivers on the `critical` tier are rejected (`waiver-forbidden-tier`, driven by the risk model's `waiver_policy`).

Traces: [REQ-010], [REQ-011].

[DESIGN-011] **waivers-check exit codes.** Per `cli.py`: `0` — all waivers parse and are valid for the current SHA (or no waivers exist); `1` — findings: malformed waiver file, bad/short SHA, unknown tier, forbidden (critical) tier, or stale SHA; `2` — config error (bad risk model, invalid `--sha`, git failure). Traces: [REQ-012].

[DESIGN-012] **Verification.** Before completion: (a) `git status` shows exactly one new untracked file `docs/risk-classify.md`; (b) every field name, exit code, default path, and JSON key in the guide is re-checked against the four source modules; (c) the document renders as valid GFM (headings, table, fenced blocks) and stays within the one-page target. Traces: [REQ-001], [REQ-002], [REQ-003], [REQ-013].

## Traceability

| Requirement | Design |
|---|---|
| REQ-001 | DESIGN-001, DESIGN-012 |
| REQ-002 | DESIGN-002, DESIGN-012 |
| REQ-003 | DESIGN-003, DESIGN-012 |
| REQ-004 | DESIGN-004 |
| REQ-005 | DESIGN-005 |
| REQ-006 | DESIGN-006 |
| REQ-007 | DESIGN-007 |
| REQ-008 | DESIGN-008 |
| REQ-009 | DESIGN-009 |
| REQ-010 | DESIGN-010 |
| REQ-011 | DESIGN-010 |
| REQ-012 | DESIGN-011 |
| REQ-013 | DESIGN-002, DESIGN-012 |
| REQ-014 | DESIGN-003, DESIGN-005, DESIGN-007 |
