# Tasks — docs: risk-classify and waivers-check usage guide

## Overview

Implementation plan for creating `docs/risk-classify.md`, a one-page English usage guide for `steward risk-classify` and `steward waivers-check` (WS-006 M1). Documentation-only; exactly one new file, grounded in the source modules and the WS-006 design doc.

## Tasks

### TASK-001: Verify source-of-truth anchors before writing
- **Priority:** P0
- **Estimate:** 20 min
- **Dependencies:** none
- **Traces:** [REQ-003], [REQ-014] / [DESIGN-003]
- **Checklist:**
  - [ ] Read `src/steward/riskclassify/cli.py` — confirm the three input modes (`--declared`, `--no-fs`, `--diff BASE..HEAD`), required JSON keys per mode, shared options (`--risk-model` default `profiles/risk-model.yaml`, `--profile` default `lite`), and the exit-code contract (0 classified / 2 config error; waivers-check 0/1/2).
  - [ ] Read `src/steward/riskclassify/classify.py` — confirm `Classification` field names, phase values `ex_ante`/`ex_post`, and `scope_violation` flag semantics.
  - [ ] Read `src/steward/riskclassify/model.py` — confirm tier ladder `low < medium < high < critical`, `RiskModelError` → exit 2, and `version_sha` (`sha256:…`) provenance.
  - [ ] Read `src/steward/riskclassify/waivers.py` — confirm the five frontmatter fields (`gate_id`, `sha`, `tier`, `waived_by`, `reason`), full-40-hex SHA requirement, `waiver-stale-sha` and `waiver-forbidden-tier` findings, and the default waivers directory (`spec/waivers`).
  - [ ] Skim `workstreams/WS-006-risk-model/spec/design.md` (DESIGN-604/605/608/609/610) for two-phase model, combinator, SHA invalidation, waiver-as-file/CODEOWNERS approval.
  - [ ] Note any divergence from the requirements text; where they differ, the source wins.

### TASK-002: Author part 1: `steward risk-classify` section
- **Priority:** P0
- **Estimate:** 40 min
- **Dependencies:** TASK-001
- **Traces:** [REQ-004], [REQ-005], [REQ-006], [REQ-007], [REQ-013], [REQ-014] / [DESIGN-002], [DESIGN-004], [DESIGN-005], [DESIGN-006], [DESIGN-007]
- **Checklist:**
  - [ ] Create `docs/risk-classify.md` with the fixed skeleton from DESIGN-002 (`# Risk classification CLI` intro + `## steward risk-classify` part).
  - [ ] Write the purpose paragraph: two-phase tier (`ex_ante` over declared scope / `ex_post` over a diff), tier = `max(profile floor, change_class, blast_radius, trust_boundary)`, deterministic JSON on stdout, consumers read the output rather than computing risk.
  - [ ] `### Input modes`: document all three modes with exactly one fenced example each — `--declared scope.json` (JSON with `project`, `sha`, `scope`, optional `flags`), `--no-fs facts.json` (JSON with `project`, `sha`, `paths`, optional `declared_scope`/`flags`; note `scope_violation` raises tier to ≥ `high`), `--diff BASE..HEAD` (bash invocation example).
  - [ ] Add the one-line note: exactly one mode must be given; mention `--risk-model` and `--profile` defaults.
  - [ ] `### Output`: markdown table covering all nine fields — `tier`, `phase`, `inputs`, `dominant_axis`, `floor_profile`, `mandatory_gates`, `flags`, `sha`, `risk_model_version` — with meanings per DESIGN-006, plus the byte-stable-output sentence.
  - [ ] `### Exit codes`: `0` classified (any tier, including `critical`), `2` config error. Do not document a nonexistent exit 1.

### TASK-003: Author part 2: `steward waivers-check [DIR]` section
- **Priority:** P0
- **Estimate:** 30 min
- **Dependencies:** TASK-001, TASK-002 (same file, sequential edit)
- **Traces:** [REQ-008], [REQ-009], [REQ-010], [REQ-011], [REQ-012], [REQ-013], [REQ-014] / [DESIGN-002], [DESIGN-008], [DESIGN-009], [DESIGN-010], [DESIGN-011]
- **Checklist:**
  - [ ] `## steward waivers-check [DIR]`: invocation form; `DIR` defaults to `spec/waivers`; one line on `--sha` (defaults to git HEAD of `--repo`); missing directory ⇒ clean exit 0.
  - [ ] `### Waiver frontmatter`: one fenced YAML example plus the five-field list (`gate_id`, `sha`, `tier`, `waived_by`, `reason`) with brief descriptions; note approval = merged PR via CODEOWNERS (DESIGN-609).
  - [ ] `### Semantics`: SHA-bound — waiver valid only while `sha` equals current HEAD; any new commit/rebase invalidates (`waiver-stale-sha`). Critical tier — waivers forbidden (`waiver-forbidden-tier`).
  - [ ] `### Exit codes`: `0` all waivers valid or none exist; `1` findings (malformed file, bad SHA, unknown tier, forbidden tier, stale SHA); `2` config error.

### TASK-004: Final verification and scope check
- **Priority:** P1
- **Estimate:** 15 min
- **Dependencies:** TASK-002, TASK-003
- **Traces:** [REQ-001], [REQ-002], [REQ-003], [REQ-013] / [DESIGN-001], [DESIGN-002], [DESIGN-012]
- **Checklist:**
  - [ ] The branch diff over its base, excluding `spec/`, `spec-runner.config.yaml`, `.gitignore`, and `tests/` (spec artifacts and repo-hygiene files already committed by earlier tasks/review fixes), contains exactly one new deliverable file, `docs/risk-classify.md`; no other file modified, created, or deleted. Use the branch diff, not `git status` — the executor auto-commits and leaves untracked runtime state under `spec/`.
  - [ ] `uv run pytest` passes (guards against spec/doc placement breaking repo tests, e.g. `tests/test_meta.py`).
  - [ ] Re-check every field name, JSON key, default path, flag name, and exit code in the guide against the four source modules (spot-check against TASK-001 notes).
  - [ ] Confirm document length ≈ 60–90 lines (one page), valid GFM: headings render, table renders, all commands/JSON/YAML in fenced blocks with language tags.
  - [ ] Confirm no speculative content: no flags, fields, or behaviors absent from WS-006 M1 code/design; no cross-links added to other docs.

## Task summary

| Task | Priority | Estimate | Depends on | Requirements |
|---|---|---|---|---|
| TASK-001 | P0 | 20 min | — | REQ-003, REQ-014 |
| TASK-002 | P0 | 40 min | TASK-001 | REQ-004…REQ-007, REQ-013, REQ-014 |
| TASK-003 | P0 | 30 min | TASK-001, TASK-002 | REQ-008…REQ-012, REQ-013, REQ-014 |
| TASK-004 | P1 | 15 min | TASK-002, TASK-003 | REQ-001, REQ-002, REQ-003, REQ-013 |

Total estimate: ~1 h 45 min. All REQ-001…REQ-014 are covered; execution order is strictly TASK-001 → TASK-002 → TASK-003 → TASK-004.
