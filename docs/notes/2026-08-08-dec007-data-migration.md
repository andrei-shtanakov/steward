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
