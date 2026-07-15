# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**steward** — spec governance layer: gated multi-artifact authoring above spec-runner/Maestro. It shepherds a spec through a DAG of approved artifacts (gates), enforces order and traceability via git-PR/CODEOWNERS/CI, and compiles down by delegation (decomposition → Maestro, task specs → spec-runner).

Implemented so far: profiles + `graph.py` (WS-001), `meta.py` + vendored SpecMeta, the gate-check linter with CI dogfood (WS-002), and the risk-model classifier (WS-006). Sources of truth, in order:

- `NEXT-STEPS.md` — the roadmap (Phase 0–3, items D1/V1/C1–C5); read this first to know what is unblocked.
- `BOOTSTRAP.md` — the bootstrap blueprint: target structure, file skeletons, dependency decision. Apply structure from it rather than inventing.
- `spec/` — steward's own dogfood spec (`00-charter` … `40-decomposition`), written in its own format. `spec/20-design.md` holds the frontmatter schema and key decisions DEC-001…DEC-006.
- `workstreams/WS-002-gate-check/spec/` — leaf spec for the gate-check linter (requirements/design/tasks in spec-runner format), ready to implement once unblocked.

`project.yaml` at the repo root is a **contract-check artifact**, not runtime config: it's the `decomposition → Maestro` output of `steward-compile project-yaml`, kept byte-equal to the emitter by a golden test in `tests/contract/` (shape verified against Maestro's loader/preflight — see `emitter-contract-check.md`). steward never writes this file at runtime; regenerate it explicitly when the decomposition block or emitter changes.

## Commands

Package management is **uv only** (never pip):

- `uv sync` — install dependencies
- `uv add <package>` — add a dependency
- `uv run pytest` — run tests (`uv run pytest tests/gatecheck/test_x.py::test_name` for a single test)
- `uv run ruff format .` / `uv run ruff check . --fix` — format and lint (line length 100 per the blueprint's pyproject)
- `uv run gate-check --profile team spec/` — the CLI entry point (once `src/steward/gatecheck/cli.py` exists)

Python >= 3.12.

## Ecosystem rules (non-negotiable)

- steward is **READ-ONLY toward other repos**; its output artifacts go to `_cowork_output/`.
- `_cowork_output/` is dev-only coordination space — shipped/runtime code must never read or resolve paths under it. Cross-repo contracts are vendored in as pinned copies, not referenced externally.
- Ownership boundaries: steward owns `profiles/`, gate-check, git-approval, and compile-down (delegation). It does **not** own the formats it consumes/emits: `tasks.md` / SpecMeta belong to **spec-runner**; `project.yaml` belongs to **Maestro**.
- Approval model: artifact = file with frontmatter; approval = PR merged to `main` after review by the artifact's CODEOWNERS role. `status: approved` in frontmatter is a **mirror** of git — git is primary.

## Architecture (target)

- `profiles/*.yaml` — governance profiles (`lite`, `team`): declarative data, not code. Each defines the artifact DAG (charter → requirements → design → acceptance → decomposition → task) with `owner_role`, `upstream` edges, and optional `delegate`/`compile` targets. `lite` (requirements → design → tasks, solo auto-approve) is the default — ceremony is risk #1.
- `src/steward/meta.py` — thin wrapper over spec-runner's `split_frontmatter`/`SpecMeta` plus governance fields (`owner_role`, `traces_to`, `upstream_hashes`). Dependency strategy: **vendored pinned copy** in `src/steward/_vendor/spec_meta.py` (DEC-003; supersedes BOOTSTRAP.md option A) — re-vendor when spec-runner's `SPEC_META_CONTRACT` bumps.
- `src/steward/graph.py` — SpecGraph + profile loader (WS-001).
- `src/steward/gatecheck/` — WS-002 linter: completeness / traceability / status↔git / stale cascade, `--no-fs` mode, exit codes for CI (`checks.py`, `git_facts.py`, `cli.py` as a Typer app exposed as the `gate-check` script). CI workflow needs `fetch-depth: 0`.
- `src/steward/compile/` — compile-down emitters (WS-004, C5): `steward-compile project-yaml` renders Maestro `project.yaml` from the normalized ```` ```yaml steward-compile ```` block inside the decomposition artifact (deployment knobs pass through from `spec/maestro-base.yaml`); `steward-compile delegation` renders the WS → spec-runner authoring manifest. Golden tests in `tests/contract/` keep the root `project.yaml` byte-equal to the emitter output.

**Historical trap** (from `emitter-contract-check.md`, 2026-07-05): Maestro `validate --no-fs` used to miss dangling `depends_on` references. Fixed in Maestro on 2026-07-06 (PR #47, `dangling-dep` error, runs in `--no-fs` too). gate-check still validates dep-link integrity itself (`GC-COMPILE`, `check_compile_block`) upstream of compilation — defense in depth, and it fails earlier, at the governance layer.

## Build-order constraints

Per `NEXT-STEPS.md` — do not start blocked items, do not build all of steward at once:

| Work | Status |
|---|---|
| Bootstrap + G1 profiles + `graph.py` (WS-001) | ✅ done |
| `meta.py` | ✅ steward side done (owner_role, traces_to, upstream_hashes); re-vendor SpecMeta when spec-runner ships contract v2 (owner_role + approver) |
| gate-check (WS-002, C3) | ✅ done incl. stale-cascade (C2); deferred: OSS bridge (REQ-209, P2) |
| compile-down emitters (C5, WS-004) | ✅ done (`steward-compile`) |
| Maestro delegation (C4) | Maestro-side (neighbor repo) — handoff, not steward code |

## Repo scope & boundaries

- **Этот репо:** `steward` — git-корень `all_ai_orchestrators/steward/`, remote `git@github.com:andrei-shtanakov/steward.git`.
- **Соседи (READ-ONLY reference):** `../arbiter/`, `../atp-platform/`, `../deployer/`, `../dispatcher/`, `../Maestro/`, `../open-prose/`, `../proctor/`, `../prograph/`, `../prograph-vault/`, `../robin-runtime/`, `../robin-toolkit/`, `../spec-runner/`, `../spec-runner-vscode/` — их код не редактировать.
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
