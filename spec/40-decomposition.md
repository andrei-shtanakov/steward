---
spec_stage: decomposition
status: draft
version: 1
owner_role: "@tech-lead"
generated_by: claude@claude-opus-4-8
generated_at: 2026-07-05
approved_by: null
approved_at: null
traces_to: [DEC-001, DEC-002, DEC-003, DEC-004, DEC-005, AC-001, AC-002, AC-003, AC-004, AC-005, AC-006, AC-007, AC-009]
---

# Decomposition — steward → workstreams

Каждый `WS-xxx` компилируется в один Maestro-workstream (`project.yaml`), а его листовая спека
авторится spec-runner. Scope — непересекающийся (проверяется `maestro validate`/preflight).

## Workstreams

- **WS-001 · profiles + frontmatter core** — profile-loader, схема артефакта поверх SpecMeta,
  `lite`/`team`. Трасса: REQ-001, REQ-002. Scope: `profiles/**`, `steward/spec_meta_ext.*`.
  depends_on: []. → spec-runner task-спека.
- **WS-002 · gate-check linter** — completeness/traceability/status↔git/stale + `--no-fs`; база
  repolinter/codeowners-validator. Трасса: REQ-003, REQ-006, AC-001..004, AC-008. Scope:
  `steward/gatecheck/**`, `tests/gatecheck/**`. depends_on: [WS-001].
- **WS-003 · git approval integration** — role-resolver над CODEOWNERS, зеркало Status↔git,
  CI-job + branch-protection рецепт. Трасса: REQ-004, NFR-003, AC-005. Scope: `steward/git/**`,
  `.github/**`, `CODEOWNERS`. depends_on: [WS-001].
- **WS-004 · compile-down delegation** — `decomposition`→Maestro `project.yaml`, `WS`→spec-runner
  authoring; golden-контракты. Трасса: REQ-005, DEC-005, AC-006. Scope: `steward/compile/**`,
  `tests/contract/**`. depends_on: [WS-001, WS-002].
- **WS-005 · dispatcher panel + dogfood/docs** — read-only панель состояния бандла; прогон этого
  бандла через steward (E2E). Трасса: REQ-007, AC-007, AC-009, SC-5. Scope: `dispatcher/**` (через
  контракт), `docs/**`, `examples/dogfood/**`. depends_on: [WS-002, WS-003, WS-004].

## Порядок (DAG)

```
WS-001 ─┬─▶ WS-002 ─┬─▶ WS-004 ─▶ WS-005
        └─▶ WS-003 ─┘        ▲
                    └────────┘
```

## Компиляция вниз

- `project.yaml`: `project: steward`, workstreams = WS-001..005 со scope/depends_on выше,
  `branch_prefix: feature/steward-`. Отдаётся Maestro (`maestro validate` перед orchestrate).
- Листовые таски: `maestro orchestrate` на каждый WS вызывает spec-runner authoring (профиль
  `lite`) — steward свой `tasks.md` не пишет (DEC-005).
