# Design: docs/risk-classify.md — risk-classify and waivers-check usage guide

## Overview

This design specifies a single new Markdown file, `docs/risk-classify.md`, documenting the two WS-006 M1 CLI commands. It is a documentation-only change: no code, no tests, no other files. All content is derived from the docstrings in `src/steward/riskclassify/` (`cli.py`, `model.py`, `classify.py`, `waivers.py`) and `workstreams/WS-006-risk-model/spec/design.md` (DESIGN-601…DESIGN-610).

## Design decisions

[DESIGN-001] **Single-file deliverable.** The implementation creates exactly one file, `docs/risk-classify.md`, and touches nothing else. No index/README updates, no cross-links added elsewhere in the repo — the task scope is `docs/**` limited to this one file. *(Traces: [REQ-001])*

[DESIGN-002] **One-page document structure.** The document is organized as:
1. Title (`# risk-classify and waivers-check`) + a 1–2 sentence intro naming both commands and WS-006 M1.
2. `## steward risk-classify` — purpose, input modes, output fields, exit codes.
3. `## steward waivers-check [DIR]` — purpose, frontmatter fields, SHA-bound semantics, critical-tier rule, exit codes.

No design-rationale sections, no internals (glob-regex machinery, combinator implementation, model-loading validation details are excluded). Target length ≈ 80–110 lines of Markdown — roughly one printed page. *(Traces: [REQ-002])*

[DESIGN-003] **risk-classify purpose statement.** The section opens with: the command classifies a change into a risk tier (`low` / `medium` / `high` / `critical`) in one of two phases — **ex-ante** over a declared scope (the diff does not exist yet) or **ex-post** over an actual diff. Tier = the maximum of the profile floor and three axes (`change_class`, `blast_radius`, `trust_boundary`); the model can only raise, never lower. One sentence notes classification is a function, not a check — it never "fails" with findings. Sourced from `cli.py` and `classify.py` module docstrings and DESIGN-604/605. *(Traces: [REQ-003], [REQ-012])*

[DESIGN-004] **Input modes subsection.** Documents that exactly one of the three inputs is required, each under its own `###` heading or bold list item, in the order used by the CLI docstring:
- `--declared scope.json` — ex-ante over a declared scope; phase `ex_ante`.
- `--no-fs facts.json` — injected facts for deterministic CI (no git access); phase `ex_post`.
- `--diff BASE..HEAD` — live git: changed paths and head SHA from the repo in `--repo` (default: cwd); phase `ex_post`.

Also briefly mentions the supporting options exactly as defined in `cli.py`: `--risk-model` (default `profiles/risk-model.yaml`), `--profile` (floor profile, default `lite`), and for `--diff` only: `--repo`, `--project`. *(Traces: [REQ-004], [REQ-012])*

[DESIGN-005] **Exactly one example per input mode**, in fenced code blocks with language tags, matching the schemas enforced by `_read_json` in `cli.py`:
- `--declared` — a ` ```json ` block with required keys `project`, `sha`, `scope` (list of glob strings), plus a note that `flags` is optional:
  ```json
  {"project": "steward", "sha": "<full 40-hex sha>", "scope": ["src/steward/graph.py", "tests/**"]}
  ```
  (rendered pretty-printed in the doc; the actual example uses a valid-looking full SHA).
- `--no-fs` — a ` ```json ` block with required keys `project`, `sha`, `paths` (changed file paths), noting optional `declared_scope` and `flags`.
- `--diff` — a ` ```sh ` block showing the invocation, since its input is a git range, not a JSON file:
  `steward risk-classify --diff origin/master..HEAD --profile team`

Each example is minimal (only required keys + at most one optional key where it clarifies). *(Traces: [REQ-005], [REQ-012], [REQ-013])*

[DESIGN-006] **Output fields table.** A Markdown table (field / meaning) covering exactly the nine fields of the `Classification` dataclass, with one-line descriptions sourced from `classify.py` and DESIGN-604/607:

| Field | Description (summary of what the doc will say) |
|---|---|
| `tier` | resulting risk tier: `low`, `medium`, `high`, or `critical` |
| `phase` | `ex_ante` (declared scope) or `ex_post` (actual diff) |
| `inputs` | the three axis values: `change_class`, `blast_radius`, `trust_boundary` |
| `dominant_axis` | which axis produced the max tier (`floor` if the profile floor did) |
| `floor_profile` | the profile whose floor was applied (e.g. `lite`, `team`) |
| `mandatory_gates` | gate ids mandatory at this tier (from the model's `tier_gates`) |
| `flags` | raised flags, e.g. `scope_violation` when the diff escapes the declared scope |
| `sha` | commit SHA the classification is bound to |
| `risk_model_version` | `sha256:` hash of the risk-model.yaml used (reproducibility) |

A one-line note states output is deterministic JSON (sorted keys) so runs can be diffed. *(Traces: [REQ-006], [REQ-012])*

[DESIGN-007] **risk-classify exit codes.** A short table or two-item list: `0` — classified successfully (result on stdout); `2` — configuration error (bad risk model, bad/missing input file, wrong flag combination). Explicitly no exit code 1 for this command — classification has no findings. *(Traces: [REQ-007], [REQ-012])*

[DESIGN-008] **waivers-check section.** Opens with the synopsis `steward waivers-check [DIR]` (default `DIR`: `spec/waivers`; a missing directory is clean) and one sentence: it validates waiver files — Markdown files with YAML frontmatter, approved by PR-merge under CODEOWNERS. Then a table of the five required frontmatter fields with descriptions from the `waivers.py` docstring: `gate_id` (the waived gate), `sha` (full 40-hex head SHA the waiver covers), `tier` (classification tier the waiver was issued against), `waived_by` (git handle), `reason` (justification). Includes one minimal fenced example of a waiver file's frontmatter. Mentions the `--sha` / `--repo` / `--risk-model` options in one line. *(Traces: [REQ-008], [REQ-012], [REQ-013])*

[DESIGN-009] **SHA-bound semantics.** A short paragraph: every waiver is bound to a specific full commit SHA; if the waiver's `sha` differs from HEAD (or `--sha`), it is stale — any new commit or rebase invalidates all existing waivers, which must be removed or re-issued (rule `waiver-stale-sha`, DESIGN-608). *(Traces: [REQ-009], [REQ-012])*

[DESIGN-010] **Critical tier rule.** One sentence in the waivers-check section: waivers on the `critical` tier are forbidden by `waiver_policy` in the risk model; such a waiver is reported as an error (`waiver-forbidden-tier`) regardless of its SHA. *(Traces: [REQ-010], [REQ-012])*

[DESIGN-011] **waivers-check exit codes.** Table/list of three codes matching `cli.py`: `0` — all waivers valid (clean; includes the empty/missing-directory case); `1` — findings (stale SHA, forbidden tier, bad SHA format, bad tier, malformed waiver file); `2` — configuration error (bad risk model, invalid `--sha`, git failure). *(Traces: [REQ-011], [REQ-012])*

[DESIGN-012] **Accuracy discipline.** Every flag name, default value, field name, rule id, and exit code in the document is copied from the code read during implementation — nothing invented. Where the doc summarizes behavior (e.g., fail-closed `unknown` class, conservative ex-ante intersection), it does so in at most one sentence and only if it aids usage; deeper mechanics stay in the WS-006 design doc, which the guide links to in a final "Source" line (`workstreams/WS-006-risk-model/spec/design.md`). *(Traces: [REQ-012], [REQ-002])*

[DESIGN-013] **Markdown conventions.** ATX headings (`#`/`##`/`###`), fenced code blocks tagged `json` for JSON examples, `sh` for shell invocations, and `yaml`/`markdown` for the waiver frontmatter example; tables for field references and exit codes; backticks for all flags, fields, file paths, and tier names — consistent with existing repo docs. *(Traces: [REQ-013])*

## Out of scope

- Any change to source code, tests, `profiles/`, or other docs ([REQ-001]).
- Documenting internals: glob-to-regex rules, prefix-intersection algorithm, model-loading validation, `find_waiver` consumer API ([REQ-002]).
- Verdict records, gate obligations, and Maestro integration (DESIGN-606/607) — beyond the two commands' user-facing behavior.

## Requirements coverage

| Requirement | Covered by |
|---|---|
| REQ-001 | DESIGN-001 |
| REQ-002 | DESIGN-002, DESIGN-012 |
| REQ-003 | DESIGN-003 |
| REQ-004 | DESIGN-004 |
| REQ-005 | DESIGN-005 |
| REQ-006 | DESIGN-006 |
| REQ-007 | DESIGN-007 |
| REQ-008 | DESIGN-008 |
| REQ-009 | DESIGN-009 |
| REQ-010 | DESIGN-010 |
| REQ-011 | DESIGN-011 |
| REQ-012 | DESIGN-003…DESIGN-012 |
| REQ-013 | DESIGN-005, DESIGN-008, DESIGN-013 |
