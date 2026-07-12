# Requirements: docs/risk-classify.md — risk-classify and waivers-check usage guide

## Scope

Create a single new documentation file, `docs/risk-classify.md`, describing the two steward CLI commands shipped in WS-006 M1 (`steward risk-classify` and `steward waivers-check`). No other files may be created or modified.

## Requirements

[REQ-001] The deliverable MUST be exactly one new file, `docs/risk-classify.md`; no other file in the repository may be created, modified, or deleted.

[REQ-002] The document MUST be written in English as a concise, one-page usage guide (approximately one printed page; no exhaustive internals or design rationale).

[REQ-003] The document MUST contain a section for `steward risk-classify` that explains what the command does: computing a two-phase risk tier over either a diff or a declared scope.

[REQ-004] The `risk-classify` section MUST document all three input modes: `--declared scope.json`, `--no-fs facts.json`, and `--diff BASE..HEAD`.

[REQ-005] Each of the three input modes MUST be accompanied by exactly one example JSON input (or, for `--diff`, an example invocation with its diff range argument), kept minimal and accurate to the actual accepted schema.

[REQ-006] The `risk-classify` section MUST document the output JSON fields: `tier`, `phase`, `inputs`, `dominant_axis`, `floor_profile`, `mandatory_gates`, `flags`, `sha`, and `risk_model_version` — with a short description of each.

[REQ-007] The `risk-classify` section MUST document the exit codes: `0` (successfully classified) and `2` (configuration error).

[REQ-008] The document MUST contain a section for `steward waivers-check [DIR]` that documents the waiver file frontmatter fields: `gate_id`, `sha`, `tier`, `waived_by`, and `reason`.

[REQ-009] The `waivers-check` section MUST explain the SHA-bound semantics of waivers: a waiver is bound to a specific commit SHA, and any new commit invalidates existing waivers.

[REQ-010] The `waivers-check` section MUST state that waivers on the critical tier are forbidden.

[REQ-011] The `waivers-check` section MUST document its exit codes `0`, `1`, and `2`, with the meaning of each.

[REQ-012] All documented behavior (flags, field names, semantics, exit codes) MUST be sourced from and consistent with the docstrings in `src/steward/riskclassify/` (`model.py`, `classify.py`, `cli.py`, `waivers.py`) and `workstreams/WS-006-risk-model/spec/design.md`; the document must not invent behavior absent from those sources.

[REQ-013] The document SHOULD follow standard Markdown conventions consistent with existing repository documentation (headings, fenced code blocks with language tags for JSON and shell examples).
