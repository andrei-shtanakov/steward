# steward — bootstrap kit (блюпринт для применения в репо)

> Дата: 2026-07-05 · Claude работает read-only по репо → это блюпринт, применяется вручную/агентом.
> Репо: `steward/` (сейчас: пустой uv-скелет, `requires-python >=3.12`).
> Покрывает: repo bootstrap + WS-001 (profiles + frontmatter core, G1). WS-002 (gate-check) —
> отдельная спека, стартует после заморозки SpecMeta-контракта (см. «Definition of ready»).

## 1. Целевая структура

```
steward/
├── pyproject.toml
├── README.md
├── CLAUDE.md                      # правило read-only/_cowork (конвенция экосистемы)
├── CODEOWNERS
├── .github/workflows/gate-check.yml
├── profiles/                      # G1 — данные, не код
│   ├── lite.yaml
│   └── team.yaml
├── src/steward/
│   ├── __init__.py
│   ├── meta.py                    # обёртка над SpecMeta spec-runner + owner_role
│   ├── graph.py                   # SpecGraph, profile-loader (WS-001)
│   ├── gatecheck/                 # WS-002 (после Phase 1)
│   │   ├── __init__.py
│   │   ├── checks.py
│   │   ├── git_facts.py
│   │   └── cli.py
│   └── compile/                   # E1/E2 (Phase 3)
├── spec/                          # dogfood: спека самого steward (уже написана)
│   ├── 00-charter.md … 40-decomposition.md
└── tests/
    ├── gatecheck/
    └── contract/
```

## 2. Решение по зависимости от SpecMeta (bootstrap-развилка)

steward читает frontmatter спек тем же парсером, что spec-runner. Варианты:

| Вариант | Как | Плюс | Минус |
|---|---|---|---|
| **A. Зависимость на spec-runner** (рек.) | `uv add` git/path-dep, `from spec_runner.spec import split_frontmatter, SpecMeta` | Один источник, автоапдейт по пину | Тянет пакет spec-runner |
| B. Вендоринг | копия `split_frontmatter`/`SpecMeta` с marker (как `maestro/_vendor/obs.py`) | Нет рантайм-зависимости | Ручной re-copy при апдейте |

**Рекомендация — A с пином версии** (полирепа, spec-runner packageable). Зафиксировать в
`meta.py` минимальный публичный интерфейс, который steward использует, — это и есть потребляемая
часть SpecMeta-контракта.

## 3. Файлы-скелеты

### pyproject.toml
```toml
[project]
name = "steward"
version = "0.1.0"
description = "Spec governance layer: gated multi-artifact authoring above spec-runner/Maestro"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
  "pyyaml>=6",
  "typer>=0.12",
  # вариант A: spec-runner как git/path-dep (пин версии)
  # "spec-runner @ git+https://…@<pinned-sha>",
]

[project.scripts]
gate-check = "steward.gatecheck.cli:app"

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.5"]

[tool.ruff]
line-length = 100
```

### profiles/lite.yaml  (G1 — совместим с C1 spec-runner)
```yaml
profile: lite
solo_auto_approve: true
artifacts:
  - {id: requirements, template: requirements.md, owner_role: "@owner", upstream: []}
  - {id: design,       template: design.md,       owner_role: "@owner", upstream: [requirements]}
  - {id: task,         owner_role: "@owner",       upstream: [design], delegate: spec-runner}
```

### profiles/team.yaml  (G1 — из dogfood-дизайна)
```yaml
profile: team
solo_auto_approve: false
artifacts:
  - {id: charter,       template: charter.md,       owner_role: "@product",             upstream: []}
  - {id: requirements,  template: requirements.md,  owner_role: "@product,@architects", upstream: [charter]}
  - {id: design,        template: design.md,        owner_role: "@architects",          upstream: [requirements]}
  - {id: acceptance,    template: acceptance.md,    owner_role: "@qa",                  upstream: [requirements]}
  - {id: decomposition, template: decomposition.md, owner_role: "@tech-lead",           upstream: [design, acceptance]}
  - {id: task,          owner_role: "@stream-owner", upstream: [decomposition], delegate: spec-runner, per: workstream}
compile:
  decomposition: {to: maestro,     artifact: project.yaml}
  task:          {to: spec-runner, artifact: tasks.md}
```

### CODEOWNERS  (G3 — роли на артефакты; в соло все = один владелец)
```
/spec/00-charter.md        @andrei
/spec/10-requirements.md   @andrei
/spec/20-design.md         @andrei
/spec/30-acceptance.md     @andrei
/spec/40-decomposition.md  @andrei
```

### .github/workflows/gate-check.yml  (G3 — CI-гейт)
```yaml
name: gate-check
on: [pull_request]
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }   # нужен для status↔git/stale
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run gate-check --profile team spec/ --no-fs facts.json
```

### CLAUDE.md  (конвенция экосистемы — продублировать правило)
```
# steward
Spec governance layer. READ-ONLY для чужих репо; выходные артефакты — в _cowork_output/.
Владеет: profiles/, gate-check, git-approval, compile-down (делегирование).
НЕ владеет форматами: tasks.md/SpecMeta → spec-runner; project.yaml → Maestro.
```

## 4. Что скопировать сразу
- `spec/00-charter.md … 40-decomposition.md` → из
  `_cowork_output/spec-governance-dogfood/spec/` (dogfood-спека самого steward).
- Спека gate-check (WS-002) → из
  `_cowork_output/spec-governance-dogfood/workstreams/WS-002-gate-check/spec/`.

## 5. Definition of ready — что можно стартовать в steward сейчас

| Работа | Готово стартовать? | Условие |
|---|---|---|
| Bootstrap (структура, pyproject, CI-скелет) | ✅ Да | этот блюпринт |
| G1 profiles + `graph.py` loader (WS-001) | ✅ Да | не зависит от SpecMeta-контракта |
| `meta.py` (обёртка SpecMeta) | ⚠️ Частично | нужен минимум: интерфейс `split_frontmatter`/`SpecMeta` из spec-runner (есть сейчас), но **заморозка контракта** — после C1/C2 |
| G2 gate-check (checks/git_facts/cli) | ⛔ Позже | после Phase 1: замороженный SpecMeta + `owner_role` (C2) |
| E1/E2 compile-down | ⛔ Позже | Phase 3 |

## 6. Рекомендуемые действия
- **[steward]** Применить блюпринт (структура + pyproject + profiles + CI + CODEOWNERS), скопировать
  dogfood-спеку в `spec/`. Это WS-001 + bootstrap — не блокировано.
- **[spec-runner]** Заморозить публичный интерфейс SpecMeta (`split_frontmatter`, `SpecMeta`) +
  `owner_role` (C2) → это разблокирует `meta.py` и G2.
- **[COWORK_CONTEXT]** Зарегистрировать steward (роль «spec governance», Python 3.12, зависит от
  spec-runner SpecMeta; рёбра к Maestro/dispatcher/deployer/atp).
