# Requirements — docs: risk-classify and waivers-check usage guide

## Introduction

This work item adds `docs/risk-classify.md`, a concise one-page usage guide (in English) for the two steward CLI commands shipped in WS-006 M1: `steward risk-classify` and `steward waivers-check`. The guide is derived from the docstrings in `src/steward/riskclassify/` (`model.py`, `classify.py`, `cli.py`, `waivers.py`) and from `workstreams/WS-006-risk-model/spec/design.md`. Scope is documentation only (`docs/**`); no code changes.

## Requirements

[REQ-001] **File creation and scope.** The change SHALL create exactly one new file, `docs/risk-classify.md`, and SHALL NOT modify, delete, or create any other file in the repository.

[REQ-002] **Language and length.** The document SHALL be written in English and SHALL be a concise one-page usage guide (roughly what fits on a single printed page / one screenful of markdown; no exhaustive reference dump).

[REQ-003] **Accuracy against source of truth.** All documented behavior (semantics, field names, exit codes, frontmatter fields) SHALL be consistent with the docstrings in `src/steward/riskclassify/model.py`, `classify.py`, `cli.py`, `waivers.py` and with `workstreams/WS-006-risk-model/spec/design.md`. Where the description below and the source disagree, the source code/design SHALL prevail.

[REQ-004] **risk-classify — purpose.** The guide SHALL contain a section for `steward risk-classify` that explains what the command does: computing a two-phase risk tier over either a diff or a declared scope.

[REQ-005] **risk-classify — input modes.** The guide SHALL document all three input modes:
- `--declared scope.json`
- `--no-fs facts.json`
- `--diff BASE..HEAD`

Each mode SHALL be accompanied by exactly one example JSON snippet (or, for `--diff`, an example invocation with its range argument) illustrating its input.

[REQ-006] **risk-classify — output fields.** The guide SHALL document the output JSON fields: `tier`, `phase`, `inputs`, `dominant_axis`, `floor_profile`, `mandatory_gates`, `flags`, `sha`, `risk_model_version` — each with a brief description of its meaning consistent with the source docstrings.

[REQ-007] **risk-classify — exit codes.** The guide SHALL document the exit codes for `risk-classify`: `0` = classified successfully, `2` = configuration error.

[REQ-008] **waivers-check — invocation.** The guide SHALL contain a section for `steward waivers-check [DIR]` describing its invocation form, including the optional directory argument.

[REQ-009] **waivers-check — waiver frontmatter.** The guide SHALL document the waiver file frontmatter fields: `gate_id`, `sha`, `tier`, `waived_by`, `reason`, each with a brief description.

[REQ-010] **waivers-check — SHA-bound semantics.** The guide SHALL explain the SHA-bound semantics of waivers: a waiver is bound to a specific commit SHA, and a new commit invalidates existing waivers.

[REQ-011] **waivers-check — critical-tier restriction.** The guide SHALL state that waivers on the critical tier are forbidden.

[REQ-012] **waivers-check — exit codes.** The guide SHALL document the exit codes for `waivers-check`: `0`, `1`, and `2`, with the meaning of each as defined by the implementation (success / waiver violation / configuration error, verified against `waivers.py` and `cli.py`).

[REQ-013] **Document structure.** The document SHALL be valid GitHub-flavored markdown with a clear two-part structure (part 1: `steward risk-classify`; part 2: `steward waivers-check`), using fenced code blocks for all command examples and JSON snippets.

[REQ-014] **No speculative content.** The guide SHALL NOT document flags, fields, behaviors, or future plans that are not present in the WS-006 M1 implementation or its design document.
