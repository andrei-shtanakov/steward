---
spec_stage: design
status: draft
version: 1
owner_role: "@architects"
generated_by: claude@claude-opus-4-8
generated_at: 2026-07-05
approved_by: null
approved_at: null
traces_to: [REQ-001, REQ-002, REQ-003, REQ-004, REQ-005, REQ-006, REQ-007, NFR-001, NFR-002, NFR-003, NFR-004, NFR-005]
---

# Design — steward

## Архитектура

Тонкий governance-слой; ядро состояния переиспользует SpecMeta spec-runner.

```
profiles/*.yaml ─▶ [profile loader] ─┐
CODEOWNERS ──────▶ [role resolver]   ├─▶ [gate-check linter] ─▶ CI exit-code
spec/*.md (frontmatter) ─────────────┘         │
                                               ▼ approved bundle
                              [compile-down] ──┬─▶ Maestro project.yaml (decomposition)
                                               └─▶ spec-runner authoring (task)
dispatcher ◀── читает spec/*.md + git state (read-only панель)
```

Компоненты: profile-loader (REQ-001), frontmatter-ext поверх SpecMeta (REQ-002), gate-check
линтер (REQ-003/006), compile-down делегаторы (REQ-005), role-resolver над CODEOWNERS (REQ-004).

## Ключевые решения

- **DEC-001** Аппрув на git-PR + CODEOWNERS + CI, не беспоук. → REQ-004, NFR-005. Причина:
  боевая командная инфра; гейт форсит код (branch protection), не LLM.
- **DEC-002** Набор артефактов — sdd, схлопнутый до 5 авторинговых (charter/requirements/design/
  acceptance/decomposition). → REQ-001. Исполнительные гейты sdd (3/4/5) форсят PR/CI/deployer.
- **DEC-003** Ядро состояния — SpecMeta spec-runner, обобщённый со «стадии» (линейно) на «узел
  графа». → REQ-002, REQ-006. Не плодить второй state-движок.
- **DEC-004** gate-check строится на OSS-блоках (repolinter / codeowners-validator / MegaLinter),
  а не с нуля. → REQ-003. См. reference-scope-linter-oss.
- **DEC-005** Компиляция вниз — только делегирование через существующие границы
  (`maestro/spec_runner.py`, `plan --gated`). steward не знает форматов листовых артефактов. → REQ-005.
- **DEC-006 (решено 2026-07-11, owner)** Размещение gate-check: **в steward**
  (`src/steward/gatecheck/`). Ранняя рекомендация (a) устарела к моменту реализации: WS-001 уже
  вендорил SpecMeta в steward (аргумент «реюз без копии» снят), а границы владения фиксируют
  «steward owns gate-check». Второй state-движок не создан: используется вендоренный SpecMeta
  + ArtifactMeta-обёртка (DEC-003).

## Frontmatter-схема артефакта (REQ-002)

```yaml
spec_stage: charter|requirements|design|acceptance|decomposition|task
status: draft|approved|stale
version: <int>
owner_role: "@role[,@role]"      # маппится на CODEOWNERS
generated_by: <harness>@<model>  # agent-id (как в spec-runner)
approved_by: <git-handle>|null   # человек, проставляется при PR-merge
approved_at: <iso>|null
traces_to: [<upstream artifact id | REQ-/NFR-/DEC-/AC-/WS- id>]
```

## Профиль `team` (REQ-001)

```yaml
profile: team
artifacts:
  - {id: charter,        template: charter.md,        owner_role: "@product",              upstream: []}
  - {id: requirements,   template: requirements.md,   owner_role: "@product,@architects",  upstream: [charter]}
  - {id: design,         template: design.md,         owner_role: "@architects",           upstream: [requirements]}
  - {id: acceptance,     template: acceptance.md,     owner_role: "@qa",                    upstream: [requirements]}
  - {id: decomposition,  template: decomposition.md,  owner_role: "@tech-lead",            upstream: [design, acceptance]}
  - {id: task,           owner_role: "@stream-owner", upstream: [decomposition], delegate: spec-runner, per: workstream}
compile:
  decomposition: {to: maestro,     artifact: project.yaml}
  task:          {to: spec-runner, artifact: tasks.md}
solo_auto_approve: false
```

Профиль `lite`: `[requirements → design → task]`, `charter` опционален, `solo_auto_approve: true`. (NFR-001)

## gate-check контракт (REQ-003, REQ-006)

Вход: `--profile <name>`, путь к `spec/`, git-состояние (`--no-fs` для CI). Проверки → exit-код:
completeness, traceability (нет висячих `traces_to`), upstream-approved-before-downstream,
status↔git (approved ⇒ на `main` + PR-approval), stale-cascade (изменён approved upstream ⇒
downstream stale). `0` — чисто, `1` — нарушение (с перечнем), `2` — ошибка конфигурации.
CI: job на каждый PR + branch protection на папки downstream-артефактов.

## CODEOWNERS (REQ-004)

```
/spec/00-charter.md        @product
/spec/10-requirements.md   @product @architects
/spec/20-design.md         @architects
/spec/30-acceptance.md     @qa
/spec/40-decomposition.md  @tech-lead
```

## Интерфейсы компиляции вниз (REQ-005, DEC-005)

- `decomposition` (WS-список со scope/deps) → рендер в Maestro `project.yaml` (владелец формата —
  Maestro; steward отдаёт нормализованный список, Maestro валидирует `maestro validate`).
- каждый `WS-xxx` → вызов spec-runner authoring в директории workstream'а (`plan [--gated]
  --profile ...`) → `spec/{requirements,design,tasks}.md`. Формат — у spec-runner.
