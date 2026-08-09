# V1 live gated run — steps, exact commands, exit codes, classification

Run dates: 2026-08-08 → 2026-08-09 (executor half resumed once — see infra note).
Pins: `manifest.md`. Raw uncurated evidence (full out/err per step, incl. usage-error
attempts): dev workspace `_cowork_output/v1-live-run-2026-08-08/raw/` (dev-only, not
referenced by anything shipped). Project (bundle + deliverables + git history):
`_cowork_output/v1-live-run-2026-08-08/project/`, final HEAD = `gate_verdicts.jsonl`
header `source_commit`.

Conventions: `SR` = `uv run --project <spec-runner-v2.21.0-clone> spec-runner`
(run from the project dir); `GC` = `uv run gate-check` (run from the steward
checkout root, so `--profile lite` anchors profiles/ siblings).

| # | Command (verbatim form) | Exit | Classification |
|---|---|---|---|
| S0 | `SR doctor --json --yes` | 0 | PASS — backend probe ready (doctor.json) |
| S1 | `SR plan --gated --no-interactive "<task-description.txt>"` | 0 | PASS — requirements DRAFT, validation=pass |
| S2 | `SR spec approve requirements` | 0 | PASS — approved v2 |
| S3 | `GC <bundle> --profile lite` (requirements approved, NOT committed) | 1 | **expected policy rejection** — GC-GIT-BRANCH (+GC-COMPLETENESS for missing design), s3 out |
| S4 | commit; `GC <bundle> --profile lite` | 1 | PASS of the slice — GC-GIT-BRANCH cleared; GC-COMPLETENESS remains (design not yet authored) |
| S5 | `SR plan --gated --no-interactive --stage design "<desc>"`; approve; commit | 0 | PASS (needs the description arg — friction F4) |
| S6 | `SR plan --gated --no-interactive --stage tasks "<desc>"` | 0 | PASS — tasks DRAFT |
| S7 | `SR run --strict` (tasks still draft) | **0** | **expected policy rejection, WRONG exit code** — refusal message `⛔ … tasks.md is draft` on stdout but exit 0 (friction F1, spec-runner-side) |
| S8 | `SR spec approve tasks`; commit | 0 | PASS — approved v2 |
| S9 | `SR run --strict` → 1 task; `SR run --all --strict` ×2 (resumed after harness kill) | 0 | PASS — 12/12 tasks, each through branch→tests→review→merge; one graceful-shutdown resume, no state loss (infra note, not ERROR: cause was the driving harness's ~47-min background limit, not the stand) |
| S10 | `STEWARD_DIR=<steward> bash scripts/verify_break_glass.sh` | 0 | **PASS — the break-glass deliverable verified against live steward**: positive case (risk-classify ex-ante, declared scope, tier medium; SHA-bound waiver valid), stale-SHA waiver rejected (`error waiver-stale-sha:`), critical-tier waiver rejected (`error waiver-forbidden-tier:`), messages distinguishable |
| S11 | `GC <bundle> --profile lite --emit-verdicts` (clean tree) | 0 | PASS — 3 warn, all measured seams (below); verdicts emitted, header dirty=false |

## Correlation (DoD item 6)

Checked programmatically against the emitted `gate_verdicts.jsonl` (this dir):

- `source_commit` == project HEAD; `dirty: false`.
- Every finding `gate_id` ∈ active set of `profiles/gate-catalog.yaml` (v2); every
  finding carries `obligation: quality`.
- Every artifact `owner_roles` entry resolves in `profiles/roles.yaml` (canonical
  DEC-007 slugs, recovered via the profile node — the authored frontmatter carries
  no owner_role, as expected pre-SpecMeta-v2-revendor).
- Artifact identity: verdicts artifact paths == bundle files; findings reference
  only known artifacts.
- Waiver state: no waivers in the bundle run (none claimed); waiver validity and
  both fail-closed rejections exercised live in S10 via `steward waivers-check`.

## Measured seams (the run's purpose — recorded, NOT fixed pre-run)

1. **task/tasks (pre-registered hypothesis, confirmed):** spec-runner's stage
   `tasks` vs steward lite node `task` → `GC-STAGE` warn; in verdicts `tasks.md`
   has `node_id: null`, `owner_roles: []` — profile-side owner recovery is
   impossible for that artifact until the naming seam is ruled on.
2. spec-runner's gated approve writes no `traces_to` → `GC-TRACE-EMPTY` warn.
3. spec-runner's gated approve pins no `upstream_hashes` → `GC-STALE-UNPINNED`
   warn (stale-cascade cannot verify the requirements→design edge).

## Overall classification (DoD item 7)

**PASS.** The gate worked without bypass and without manual edits of intermediate
artifacts: both negative slices produced the expected rejections (S3, S7 — S7 with
a wrong exit code, recorded as a spec-runner defect, not worked around), the
positive path completed 12/12, the deliverable's own verification passed against
live steward, and the final gate run is 0 errors with three understood,
pre-registered-or-predictable warns. No infrastructure ERROR: the one interruption
was the driving harness's background time limit, resumed losslessly.

## Frictions (DoD item 9 — tracked as separate items, not buried here)

- F1 `run --strict` governance refusal exits 0 → spec-runner inbox issue.
- F2/F3 authoring seams (stage naming; no traces_to/upstream_hashes) → steward
  TODO item (cross-contract ruling needed).
- F4 stage generation requires a description argument (README shows bare call);
  `--no-menu` vs `--no-interactive` naming → folded into the spec-runner issue.
- F5 `SPEC_META_CONTRACT = 2` shipped in v2.21.0 with first-class `owner_role` —
  steward §2 re-vendor trigger has FIRED → §2 items unblocked.
- F6 single `run` executes one ready-wave; `run --all` needed for the rest
  (spec-runner#124 already tracks `--all` state-reconciliation) → noted in the
  spec-runner issue.
- F7 TASK-011 review stage logged `Execution error`, task then completed as
  "No-op" while its artifact landed from the pre-review commit — review-failure
  masking → spec-runner issue.
