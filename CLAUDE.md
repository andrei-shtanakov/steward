# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**steward** — spec governance layer: gated multi-artifact authoring above spec-runner/Maestro. It shepherds a spec through a DAG of approved artifacts (gates), enforces order and traceability via git-PR/CODEOWNERS/CI, and compiles down by delegation (decomposition → Maestro, task specs → spec-runner).

Implemented so far: profiles + `graph.py` (WS-001), `meta.py` + vendored SpecMeta, the gate-check linter with CI dogfood (WS-002), the compile-down emitters (WS-004), the risk-model classifier + waivers (WS-006), and `profiles/authority.yaml` as the authority-policy SSOT (RD-006 M2, vendored by arbiter). Sources of truth, in order:

- `TODO.md` — the open-items surface: what is actually left, tagged on checkbox lines with typed `@owner:<principal>` / `@blocked_by:<reference>` / `@trigger:"…"` / `@id:<node-id>`. Owner principals are `github:<login>`, `github-team:<org>/<team>`, `repo:<manifest-key>`, or `TBD`; an absent owner is `missing`, not `TBD`. Read this first; it is also what Robin's ecosystem digest reads.
- `NEXT-STEPS.md` — the phase roadmap and its rationale (Phase 0–3, items D1/V1/C1–C5); explains *why* the order is what it is.
- `BOOTSTRAP.md` — the bootstrap blueprint: target structure, file skeletons, dependency decision. Apply structure from it rather than inventing.
- `spec/` — steward's own dogfood spec (`00-charter` … `40-decomposition`), written in its own format. `spec/20-design.md` holds the frontmatter schema and key decisions DEC-001…DEC-006.
- `workstreams/WS-002-gate-check/spec/` — implemented leaf spec for the gate-check linter (requirements/design/tasks in spec-runner format); keep it as provenance and contract context.

`project.yaml` at the repo root is a **contract-check artifact**, not runtime config: it's the `decomposition → Maestro` output of `steward-compile project-yaml`, kept byte-equal to the emitter by a golden test in `tests/contract/` (shape verified against Maestro's loader/preflight — see `emitter-contract-check.md`). steward never writes this file at runtime; regenerate it explicitly when the decomposition block or emitter changes.

## Commands

Package management is **uv only** (never pip):

- `uv sync` — install dependencies
- `uv add <package>` — add a dependency
- `uv run pytest` — run tests (`uv run pytest tests/gatecheck/test_x.py::test_name` for a single test)
- `uv run ruff format .` / `uv run ruff check . --fix` — format and lint (line length 100 per the blueprint's pyproject)
- `uv run gate-check --profile team spec/` — run the gate-check CLI (`--stage authoring|release` selects the GC-ARCH-CONFORMANCE stage policy, `profiles/arch-policy.yaml`; default `authoring`; `--arch-stage` is deprecated alias)
- `uv run steward risk-classify …` / `uv run steward waivers-check …` — risk tier + waiver validation (WS-006)
- `uv run steward-compile project-yaml …` / `uv run steward-compile delegation …` — compile-down emitters

Python >= 3.12.

CI runs two workflows: `ci.yml` (ruff format/check, `pyrefly check`, pytest — needs `fetch-depth: 0` for gate-check) and `governance.yml`, a thin caller of the umbrella's reusable governance gate pinned to the immutable tag `governance-v1` (ADR-ECO-004 D5). The governance gate is **advisory** until `governance / gate` is added as a required status check in the `master` ruleset — that last mile is an open TODO item, not an oversight.

## Ecosystem rules (non-negotiable)

- steward is **READ-ONLY toward other repos**; its output artifacts go to `_cowork_output/`.
- `_cowork_output/` is dev-only coordination space — shipped/runtime code must never read or resolve paths under it. Cross-repo contracts are vendored in as pinned copies, not referenced externally.
- Four contracts are vendored this way: `contracts/prograph-intended-graph/v1/schema.json`, `contracts/prograph-conformance-report/v1/schema.json`, `contracts/impresario-product-proposal/v1/schema.json`, `contracts/impresario-gate-decision/v1/schema.json`. Each carries **two separate guarantees**, never conflated: copy-integrity (the vendored file is byte-identical to its producer's, checked offline in this repo's own CI — `tests/contract/test_vendored_schemas_pinned.py` auto-discovers every `contracts/*/v*/PIN`) and upstream-drift (whether the producer's schema has since moved, watched on a schedule outside this repo's gate: `arch-evidence-freshness.yml` for prograph, `impresario-contract-drift.yml` for impresario). steward is a read-only, offline consumer of this evidence — it never regenerates a report or a proposal itself.
- `steward proposal-intake <bundle>` (steward#64) — admission check for the product-governance handoff: a bundle (`proposal.yaml` + `decisions/*.yaml`, impresario forconcept layout) is admitted only on evidence — schema-valid `product-proposal/v1` with `status: approved` **and** an active (non-superseded) `approve` `gate-decision/v1` for both `qg5_business` and `qg5_committee` referencing exactly that proposal at `subject.version <= proposal.version`. The status field alone is never trusted; a steward waiver is never a product decision record. Finding codes are `INTAKE-*` (not `GC-*` — that namespace is minted only by the gate catalog, steward#62); exit codes mirror gate-check (0 admit / 1 reject / 2 config error).
- Ownership boundaries: steward owns `profiles/`, gate-check, git-approval, and compile-down (delegation). It does **not** own the formats it consumes/emits: `tasks.md` / SpecMeta belong to **spec-runner**; `project.yaml` belongs to **Maestro**.
- Approval model: artifact = file with frontmatter; approval = PR merged to `main` after review by the artifact's CODEOWNERS role. `status: approved` in frontmatter is a **mirror** of git — git is primary.

## Architecture (target)

- `profiles/*.yaml` — governance profiles (`lite`, `team`): declarative data, not code. Each defines the artifact DAG (charter → requirements → design → acceptance → decomposition → tasks) with `owner_role`, `upstream` edges, and optional `delegate`/`compile` targets. `lite` (requirements → design → tasks, solo auto-approve) is the default — ceremony is risk #1.
- `src/steward/meta.py` — thin wrapper over spec-runner's `split_frontmatter`/`SpecMeta` plus governance fields (`owner_role`, `traces_to`, `upstream_hashes`). Dependency strategy: **vendored pinned copy** in `src/steward/_vendor/spec_meta.py` (DEC-003; supersedes BOOTSTRAP.md option A) — re-vendor when spec-runner's `SPEC_META_CONTRACT` bumps. **Re-vendored to v2 2026-08-09** (spec-runner tag v2.22.0, `de9a31c4`): `owner_role` is first-class on the vendored `SpecMeta` (`base.owner_role`), so `meta.py::parse_artifact` no longer reads it from the raw frontmatter dict — that documented v1 workaround is gone. Steward's own governance-only fields (`traces_to`, `upstream_hashes`, `reviewer_roles`, `allowed_approver_roles`) are not part of spec-runner's canonical set and keep being parsed from the raw dict directly; the vendored `meta_from_dict` merely proves they survive as verbatim pass-through in `SpecMeta.extra` (pinned by `tests/test_spec_meta_vendor.py`). `meta_from_dict` can now raise `SpecMetaError` on a malformed canonical field (bad `status`/`version`/non-string key) — `parse_artifact` translates that to `MetaError`, so a defect stays a config error, never a traceback.
- `src/steward/graph.py` — SpecGraph + profile loader (WS-001).
- `src/steward/gatecheck/` — WS-002 linter: completeness / traceability / status↔git / stale cascade, `--no-fs` mode, exit codes for CI (`checks.py`, `git_facts.py`, `cli.py` as a Typer app exposed as the `gate-check` script). CI workflow needs `fetch-depth: 0`.
- `src/steward/gatecheck/architecture.py` — GC-ARCH-SCHEMA / GC-ARCH-EVIDENCE / GC-ARCH-CONFORMANCE (WS-005 follow-on, slice PR-3): gates are active only when a bundle carries an `intended-graph.yaml` manifest next to its (optional but mandatory-for-conformance) co-located `conformance-report.json`. steward validates both against the two vendored prograph schemas and consumes the report as frozen, offline evidence — it is a pure consumer, never a producer, of architecture conformance. The stage policy (`authoring` vs `release`: which finding classes/verdicts block, permanent-unknown allowlists, self-freshness, max snapshot age) lives in `profiles/arch-policy.yaml`, selected via `gate-check --stage` (`--arch-stage` is deprecated alias).
- `profiles/roles.yaml` — role catalog v1, the SSOT for governance **role identity** (DEC-007, 2026-07-26): `owner_role` is exactly one accountable role, a slug without `@`; multiplicity is modelled by separate `reviewer_roles` / `allowed_approver_roles` fields, never a tuple inside `owner_role`. dispatcher and spec-runner vendor a pinned copy and carry slugs; they do not define the form. **The loader and gate-check now exist** (`src/steward/roles.py`): steward loads and validates the catalog fail-closed (slug uniqueness, `slug_pattern` fullmatch, closed key set) and gate-check resolves every frontmatter role reference against it — unresolvable slug is a config error, exit 2. `roles.yaml` is now a **mandatory sibling** of the profile for every `gate-check` run: external consumers running the pinned binary need it next to their profile (dispatcher's live smoke already ships one; its minimal catalog will need the real slugs at the next re-vendor — that lands with the D8 handoff). Data migration has landed — `profiles/{lite,team,team-exp}.yaml` and both spec bundles (the repo's own `spec/*.md` and `workstreams/WS-005-gate-verdicts/spec/`) are canonical v2 (singular `owner_role` + `reviewer_roles`/`allowed_approver_roles`). The legacy `"@role[,@role]"` reader remains only for external/spec-runner-authored data — spec-runner still writes that form until its SpecMeta v2 lands (`TODO.md` §2). `profiles/role-assignments.yaml` is the identity → roles mapping (DEC-007 D6, governance data like `roles.yaml` — PR review, no runtime writer): it is now a **mandatory sibling** alongside `roles.yaml` for any non-solo `gate-check` run. `GC-GIT-ROLE` now authorizes against `allowed_approver_roles` (falling back to `owner_role` when unset) resolved through that mapping, node-level only — frontmatter-level `allowed_approver_roles` parses but its precedence over the node value deliberately awaits an owner ruling (see the code comment in `checks.py::check_status_git`). Live approvals stay `None` (no facts source yet), so the gate never fires outside tests. Remaining `TODO.md` §1 items: the dispatcher roles-catalog handoff and the `structural_coverage` owner-role-form ruling.
- `src/steward/riskclassify/` — WS-006 risk model: `steward risk-classify` (tier = `max(profile floor, change_class, blast_radius, trust_boundary)`, ex-ante over a declared scope / ex-post over a diff, byte-stable JSON on stdout) and `steward waivers-check` (SHA-bound waiver files; a waiver on `critical` is forbidden). Canonical model data: `profiles/risk-model.yaml`. Docs: `docs/risk-classify.md`.
- `profiles/authority.yaml` — authority policy v1 SSOT (RD-006 M2): role/phase-scoped agent allowlist, default deny. Governance **data**, changed via PR like the gate profiles. arbiter vendors a pinned TOML copy guarded by `AUTHORITY_PINNED_SHA` in its CI — any edit here needs a re-vendor handoff to arbiter (it is a neighbor repo; do not edit it).
- `src/steward/compile/` — compile-down emitters (WS-004, C5): `steward-compile project-yaml` renders Maestro `project.yaml` from the normalized ```` ```yaml steward-compile ```` block inside the decomposition artifact (deployment knobs pass through from `spec/maestro-base.yaml`); `steward-compile delegation` renders the WS → spec-runner authoring manifest. Golden tests in `tests/contract/` keep the root `project.yaml` byte-equal to the emitter output.

**Historical trap** (from `emitter-contract-check.md`, 2026-07-05): Maestro `validate --no-fs` used to miss dangling `depends_on` references. Fixed in Maestro on 2026-07-06 (PR #47, `dangling-dep` error, runs in `--no-fs` too). gate-check still validates dep-link integrity itself (`GC-COMPILE`, `check_compile_block`) upstream of compilation — defense in depth, and it fails earlier, at the governance layer.

## Build-order constraints

Per `TODO.md` (open items) and `NEXT-STEPS.md` (why that order) — do not start blocked items, do not build all of steward at once:

| Work | Status |
|---|---|
| Bootstrap + G1 profiles + `graph.py` (WS-001) | ✅ done |
| `meta.py` | ✅ done, re-vendored to SpecMeta v2 2026-08-09 (`_vendor/spec_meta.py` @ spec-runner `de9a31c4`); `owner_role` first-class via the vendored `SpecMeta`, `traces_to`/`upstream_hashes`/`reviewer_roles`/`allowed_approver_roles` remain steward-only pass-through fields |
| gate-check (WS-002, C3) | ✅ done incl. stale-cascade (C2); deferred: OSS bridge (REQ-209, P2) |
| compile-down emitters (C5, WS-004) | ✅ done (`steward-compile`) |
| risk model + waivers (WS-006 M1) | ✅ done (`steward risk-classify` / `waivers-check`, RD-004 verified) |
| authority policy v1 (RD-006 M2) | ✅ done (`profiles/authority.yaml`; arbiter vendored it, M3) |
| role identity (DEC-007) | 🟡 loader + data canonical + slug→identity mapping + `GC-GIT-ROLE` on `allowed_approver_roles` all done (§1 PR-1/PR-2/PR-3); open: dispatcher catalog handoff, `structural_coverage` owner-role-form ruling (`TODO.md` §1) |
| git approval (WS-003) | ⛔ **invalidated by ADR-ECO-004 D4** — required-owner-review is structurally unsatisfiable solo. Close it as superseded rather than silently re-scoping; the replacement is a separate item, "solo-compatible merge evidence policy", built on typed `human_merge` / `agent_merge` evidence |
| dispatcher panel (WS-005) | ⬜ open — gated on steward stabilizing the `gate_verdicts.jsonl` schema + gate-id catalog that Maestro and dispatcher both vendor; verdict records must use the DEC-007 role slugs |
| governance gate → required check | 🟡 advisory by design until an **evidence-based promotion gate** is met (V1 live run, real PRs through the gate, FP/FN triaged, working break-glass path, named runtime owner) — not a calendar decision |
| V1 live gated run | ⬜ open — has an explicit evidence DoD in `TODO.md` §4; never mark it done from tests or implementation alone |
| Maestro delegation (C4) | Maestro-side (neighbor repo) — handoff, not steward code |
| gates-in-DAG (WS-006 M-1…M-4) | Maestro-side — handoff `../prograph-vault/authored/notes/2026-07-12-ws006-gates-maestro-handoff.md` |

## Repo scope & boundaries

- **Этот репо:** `steward` — git-корень `all_ai_orchestrators/steward/`, remote `git@github.com:andrei-shtanakov/steward.git`.
- **Соседи (READ-ONLY reference):** `../arbiter/`, `../atp-platform/`, `../deployer/`, `../dispatcher/`, `../maestro/`, `../libretto/`, `../proctor/`, `../prograph/`, `../prograph-vault/`, `../robin-runtime/`, `../robin-toolkit/`, `../spec-runner/`, `../spec-runner-vscode/` — их код не редактировать.
- Нужна правка у соседа → **стоп**: запиши handoff в `../prograph-vault/authored/notes/`
  (кросс-проектное) или `../_cowork_output/` (черновик), не трогай его файлы.
- Кросс-репные контракты — **вендорить пиненой копией внутрь**, не ссылаться наружу.
- Полное правило (SSOT): `../prograph-vault/authored/rules/repo-boundaries.md`.

## Git workflow (у репо есть remote)

- Ветка `<type>/<slug>` → push → `gh pr create`. **Прямые коммиты в `master` запрещены.**
- После открытия PR — прочитать ревью **GitHub Copilot**: валидные замечания исправлять
  новыми коммитами в ту же ветку; невалидные — ответить с обоснованием, **не применять
  вслепую**; итерировать, пока не останется открытых замечаний.
- **Не мержить.** Мерж делает пользователь.
- После мержа пользователем: `git switch master && git pull --ff-only`, затем удалить
  влитую ветку (`git branch -d <branch>`) и `git fetch --prune`; убрать прочие влитые ветки.
- Никогда не делать force-push в общие ветки; не трогать другие репо (см. scope выше).
- Полное правило (SSOT): `../prograph-vault/authored/rules/git-workflow.md`.

## Входящие запросы (inbox)

В начале работы проверь входящие: `gh issue list --label inbox --state open`.
Issue с лейблом `inbox` — запрос от соседнего репо, ещё **не** пункт плана.
Принять = завести пункт в `TODO.md` с указанным `slug:`; принял под другим
именем — поправь `slug:` в теле issue.
Отказать = `gh issue close --reason "not planned"`.
Нужна работа в соседнем репо — не редактируй его: заведи там issue
(`slug:` + `from:` + проза). Правило: ADR-ECO-006 — канон в `ecosystem-kb`
(каталог `prograph-vault/` в корне воркспейса),
`authored/decisions/2026-07-28-adr-eco-006-cross-repo-issue-inbox.md`.
