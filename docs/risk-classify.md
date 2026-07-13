# Risk classification CLI

WS-006 M1 ships two commands: `steward risk-classify` computes a risk tier for a
change, and `steward waivers-check` validates gate waivers against the current commit.

## steward risk-classify

Classifies a change into a risk tier (`low` / `medium` / `high` / `critical`),
**ex-ante** over a declared scope (before the diff exists) or **ex-post** over an
actual diff. The tier is `max(profile floor, change_class, blast_radius,
trust_boundary)`. Output is byte-stable JSON on stdout (sorted keys, fixed
formatting, so two runs can be diffed directly) — the single source of truth for
tiers: consumers such as Maestro read it and never compute risk themselves.

### Input modes

Exactly one of the three modes must be given. Shared options: `--risk-model`
(default `profiles/risk-model.yaml`) and `--profile` (default `lite`).

1. `--declared scope.json` — ex-ante over a declared scope (`flags` is optional):

   ```json
   {"project": "steward", "sha": "<40-hex sha>", "scope": ["docs/**"], "flags": []}
   ```

2. `--no-fs facts.json` — ex-post with injected facts, no git or filesystem access
   (deterministic CI). Requires `project`, `sha`, `paths`; optional `declared_scope` and
   `flags`. A path outside `declared_scope` sets `scope_violation` and floors the tier at `high`:

   ```json
   {"project": "steward", "sha": "<40-hex sha>", "paths": ["src/steward/graph.py"], "declared_scope": ["src/**"]}
   ```

3. `--diff BASE..HEAD` — ex-post over live git:

   ```bash
   steward risk-classify --diff origin/master..HEAD --repo . --project steward
   ```

### Output

| Field | Meaning |
|---|---|
| `tier` | Resulting risk tier: `low` / `medium` / `high` / `critical`. |
| `phase` | `ex_ante` (declared scope) or `ex_post` (actual diff). |
| `inputs` | The three axis values fed to the combinator: `change_class`, `blast_radius`, `trust_boundary`. |
| `dominant_axis` | The axis that produced the max, or `floor` when a floor (profile or scope-violation) dominates. |
| `floor_profile` | The profile whose floor was applied (e.g. `lite`). |
| `mandatory_gates` | Gate ids mandatory at this tier, from the risk model's `tier_gates`. |
| `flags` | Raised flags, e.g. `scope_violation`. |
| `sha` | The commit SHA the classification is bound to. |
| `risk_model_version` | `sha256:…` hash of the risk-model file, for reproducibility. |

### Exit codes

- `0` — success; any tier (even `critical`) exits 0 — classification is a function, not a check.
- `2` — configuration error (bad or missing risk model, invalid input file, wrong option usage).

## steward waivers-check [DIR]

Validates the waiver files under `DIR` (default `spec/waivers`) against the current commit.
`--sha` overrides the default SHA, git HEAD of `--repo`. A missing directory is clean — exit 0.

### Waiver frontmatter

A waiver is a markdown file with YAML frontmatter; approval is the merged PR itself
(CODEOWNERS on the waivers directory). All five fields are required:

```yaml
---
gate_id: steward.gate_check # the mandatory gate being waived
sha: <40-hex commit sha>    # full commit SHA the waiver covers
tier: high                  # classification tier the waiver was issued against
waived_by: alice            # git handle of the approver
reason: "hotfix, review to follow in PR #42"  # free text (quote if it contains #)
---
```

### Semantics

- **SHA-bound.** A waiver is valid only while its `sha` equals the current HEAD; any
  new commit (including a rebase) invalidates all waivers (`waiver-stale-sha`).
- **Critical forbidden.** Waivers on the `critical` tier are rejected
  (`waiver-forbidden-tier`, driven by the risk model's `waiver_policy`).

### Exit codes

- `0` — all waivers parse and are valid for the current SHA (or none exist).
- `1` — findings: malformed file, non-40-hex SHA, unknown or forbidden tier, stale SHA.
- `2` — configuration error (bad risk model, invalid `--sha`, git failure).
