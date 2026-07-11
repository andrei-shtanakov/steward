# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**steward** — spec governance layer: gated multi-artifact authoring above spec-runner/Maestro. It shepherds a spec through a DAG of approved artifacts (gates), enforces order and traceability via git-PR/CODEOWNERS/CI, and compiles down by delegation (decomposition → Maestro, task specs → spec-runner).

No implementation code exists yet (`main.py` is a stub). Sources of truth, in order:

- `NEXT-STEPS.md` — the roadmap (Phase 0–3, items D1/V1/C1–C5); read this first to know what is unblocked.
- `BOOTSTRAP.md` — the bootstrap blueprint: target structure, file skeletons, dependency decision. Apply structure from it rather than inventing.
- `spec/` — steward's own dogfood spec (`00-charter` … `40-decomposition`), written in its own format. `spec/20-design.md` holds the frontmatter schema and key decisions DEC-001…DEC-006.
- `workstreams/WS-002-gate-check/spec/` — leaf spec for the gate-check linter (requirements/design/tasks in spec-runner format), ready to implement once unblocked.

`project.yaml` at the repo root is a **contract-check artifact**, not runtime config: it's the hand-compiled `decomposition → Maestro` output used to verify the emitter contract (see `emitter-contract-check.md`). steward never writes this file at runtime.

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
- `src/steward/meta.py` — thin wrapper over spec-runner's `split_frontmatter`/`SpecMeta` plus `owner_role`. Dependency strategy: **spec-runner as a pinned git/path dep** (BOOTSTRAP.md option A), not vendoring. `meta.py` defines the minimal consumed SpecMeta interface.
- `src/steward/graph.py` — SpecGraph + profile loader (WS-001).
- `src/steward/gatecheck/` — WS-002 linter: completeness / traceability / status↔git / stale cascade, `--no-fs` mode, exit codes for CI (`checks.py`, `git_facts.py`, `cli.py` as a Typer app exposed as the `gate-check` script). CI workflow needs `fetch-depth: 0`.
- `src/steward/compile/` — compile-down emitters (Phase 3). The `decomposition → project.yaml` contract is already verified against Maestro's loader/preflight.

**Known trap** (from `emitter-contract-check.md`): Maestro `validate --no-fs` does NOT catch dangling `depends_on` references. gate-check must validate dep-link integrity between workstreams itself, upstream of compilation — never rely on Maestro preflight for this.

## Build-order constraints

Per `NEXT-STEPS.md` — do not start blocked items, do not build all of steward at once:

| Work | Status |
|---|---|
| Bootstrap + G1 profiles + `graph.py` (WS-001) | ready now |
| `meta.py` | partially — SpecMeta contract freeze pending in spec-runner (C1/C2) |
| gate-check (WS-002, C3) | blocked on DEC-006 decision (where gate-check lives — user's call) and C2 frontmatter schema |
| compile-down (C5) + Maestro delegation (C4) | Phase 3, only after the C1→C3 vertical slice proves ergonomics |

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
