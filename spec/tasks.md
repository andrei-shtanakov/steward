# Tasks: docs/risk-classify.md — risk-classify and waivers-check usage guide

## Overview

Documentation-only change: create exactly one new file, `docs/risk-classify.md`, sourced from `src/steward/riskclassify/` docstrings and `workstreams/WS-006-risk-model/spec/design.md`. No code, no tests, no other file touched.

---

### TASK-001: Extract facts from source of truth

- **Priority:** P0
- **Estimate:** 0.5h
- **Dependencies:** none
- **Traceability:** [REQ-012], [DESIGN-003], [DESIGN-004], [DESIGN-005], [DESIGN-006], [DESIGN-007], [DESIGN-008], [DESIGN-011], [DESIGN-012]

Read the source files and collect every fact the document will state, verbatim from code — nothing from memory or invention.

**Checklist:**
- [x] Read `src/steward/riskclassify/cli.py`: exact flag names and defaults (`--declared`, `--no-fs`, `--diff`, `--risk-model` default `profiles/risk-model.yaml`, `--profile` default `lite`, `--repo`, `--project`, `--sha`), exit codes for both commands, `waivers-check [DIR]` default directory (`spec/waivers`), missing-directory behavior.
- [x] Read `src/steward/riskclassify/cli.py` `_read_json` (and related helpers): required/optional keys for the `--declared` schema (`project`, `sha`, `scope`, optional `flags`) and the `--no-fs` schema (`project`, `sha`, `paths`, optional `declared_scope`, `flags`).
- [x] Read `src/steward/riskclassify/classify.py` and `model.py`: the nine `Classification` output fields and their meanings; tier values (`low`/`medium`/`high`/`critical`); phases (`ex_ante`/`ex_post`); the three axes; max-of-floor-and-axes rule; deterministic sorted-key JSON output.
- [x] Read `src/steward/riskclassify/waivers.py`: the five frontmatter fields (`gate_id`, `sha`, `tier`, `waived_by`, `reason`), SHA-bound/stale semantics, rule ids (`waiver-stale-sha`, `waiver-forbidden-tier`), critical-tier prohibition, finding categories behind exit code 1.
- [x] Skim `workstreams/WS-006-risk-model/spec/design.md` (DESIGN-601…610) to confirm terminology and cross-check the above.
- [x] Record collected facts (flag names, defaults, field names, rule ids, exit codes) as notes for TASK-002; flag any discrepancy between design doc and code (code wins per [DESIGN-012]).

**Acceptance criteria:**
- Every fact planned for the doc has a confirmed source line in code or the WS-006 design doc.

---

### TASK-002: Write docs/risk-classify.md

- **Priority:** P0
- **Estimate:** 1h
- **Dependencies:** TASK-001
- **Traceability:** [REQ-001]–[REQ-011], [REQ-013], [DESIGN-001]–[DESIGN-011], [DESIGN-013]

Create the single new file `docs/risk-classify.md` following the structure fixed in [DESIGN-002].

**Checklist:**
- [ ] Title `# risk-classify and waivers-check` + 1–2 sentence intro naming both commands and WS-006 M1. *(REQ-002, DESIGN-002)*
- [ ] `## steward risk-classify` — purpose paragraph: two-phase risk tier (ex-ante over declared scope / ex-post over a diff), tier = max of profile floor and the three axes (`change_class`, `blast_radius`, `trust_boundary`), classification never "fails" with findings. *(REQ-003, DESIGN-003)*
- [ ] Input-modes subsection covering all three modes in CLI-docstring order, noting exactly one is required, plus one-line mention of `--risk-model`, `--profile`, and (diff-only) `--repo`/`--project`. *(REQ-004, DESIGN-004)*
- [ ] Exactly one example per mode: ```json blocks for `--declared` and `--no-fs` (minimal, schema-accurate, full 40-hex SHA), ```sh invocation for `--diff` (e.g. `steward risk-classify --diff origin/master..HEAD --profile team`). *(REQ-005, DESIGN-005)*
- [ ] Output-fields table with all nine fields — `tier`, `phase`, `inputs`, `dominant_axis`, `floor_profile`, `mandatory_gates`, `flags`, `sha`, `risk_model_version` — one-line description each, plus a note that output is deterministic JSON (sorted keys). *(REQ-006, DESIGN-006)*
- [ ] risk-classify exit codes: `0` classified, `2` config error; note there is no exit 1. *(REQ-007, DESIGN-007)*
- [ ] `## steward waivers-check [DIR]` — synopsis (default `spec/waivers`, missing dir is clean), purpose sentence, table of the five frontmatter fields, one minimal fenced frontmatter example, one-line mention of `--sha`/`--repo`/`--risk-model`. *(REQ-008, DESIGN-008)*
- [ ] SHA-bound semantics paragraph: waiver bound to a full commit SHA; any new commit/rebase invalidates existing waivers (`waiver-stale-sha`). *(REQ-009, DESIGN-009)*
- [ ] Critical-tier rule sentence: waivers on `critical` are forbidden (`waiver-forbidden-tier`), regardless of SHA. *(REQ-010, DESIGN-010)*
- [ ] waivers-check exit codes: `0` clean, `1` findings, `2` config error — with meanings. *(REQ-011, DESIGN-011)*
- [ ] Final "Source" line linking to `workstreams/WS-006-risk-model/spec/design.md`. *(DESIGN-012)*
- [ ] Markdown conventions: ATX headings, fenced blocks tagged `json`/`sh`/`yaml`, tables for fields and exit codes, backticks for flags/fields/paths/tiers. *(REQ-013, DESIGN-013)*
- [ ] Length ≈ 80–110 lines — trim internals (glob-regex, intersection algorithm, model validation, `find_waiver` API) per the out-of-scope list. *(REQ-002, DESIGN-002, DESIGN-012)*

**Acceptance criteria:**
- `docs/risk-classify.md` exists and covers every checklist item above.
- No behavior stated that is absent from the sources read in TASK-001.

---

### TASK-003: Verify scope and accuracy

- **Priority:** P1
- **Estimate:** 0.5h
- **Dependencies:** TASK-002
- **Traceability:** [REQ-001], [REQ-002], [REQ-012], [DESIGN-001], [DESIGN-012]

Final check before handoff: single-file scope and fact-for-fact accuracy.

**Checklist:**
- [ ] `git status` shows exactly one new file, `docs/risk-classify.md`; no other file created, modified, or deleted. *(REQ-001, DESIGN-001)*
- [ ] Re-diff every flag name, default value, field name, tier name, rule id, and exit code in the doc against the code read in TASK-001. *(REQ-012, DESIGN-012)*
- [ ] Validate the two JSON examples parse (e.g. `python -c "import json; json.load(...)"`) and contain only the documented keys. *(REQ-005)*
- [ ] Confirm one-page length and that no design-rationale/internals sections crept in. *(REQ-002)*
- [ ] Walk the requirements table: REQ-001…REQ-013 each satisfied by a concrete part of the document. *(all REQs)*

**Acceptance criteria:**
- Working tree contains only the one intended file; all documented behavior traces to source; all 13 requirements verified.

---

## Requirements traceability

| Requirement | Tasks |
|---|---|
| REQ-001 | TASK-002, TASK-003 |
| REQ-002 | TASK-002, TASK-003 |
| REQ-003 | TASK-001, TASK-002 |
| REQ-004 | TASK-001, TASK-002 |
| REQ-005 | TASK-001, TASK-002, TASK-003 |
| REQ-006 | TASK-001, TASK-002 |
| REQ-007 | TASK-001, TASK-002 |
| REQ-008 | TASK-001, TASK-002 |
| REQ-009 | TASK-001, TASK-002 |
| REQ-010 | TASK-001, TASK-002 |
| REQ-011 | TASK-001, TASK-002 |
| REQ-012 | TASK-001, TASK-002, TASK-003 |
| REQ-013 | TASK-002 |
