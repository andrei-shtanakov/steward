# steward — spec governance layer (dogfood spec)

> Рабочее имя проекта: **steward** (провизорно). Управляет жизненным циклом спек, **не** исполняет.
> Это спека самого steward, написанная в его собственном формате (dogfood).
> Статус бандла: DRAFT · Профиль: `team` (5 авторинговых артефактов) · Дата: 2026-07-05

## Что это

steward — тонкий слой поверх spec-runner/Maestro: проводит спеку через набор **аппрувнутых
артефактов** (гейтов), форсит порядок и трассируемость через git-PR/CODEOWNERS/CI, а на выходе
**компилирует вниз делегированием** (Decomposition→Maestro, Task Spec→spec-runner). Не пишет
`tasks.md`/`project.yaml` сам, не строит свою идентичность/RBAC.

Контекст и обоснование: `../decisions/2026-07-05-adr-spec-governance-layer.md`.

## Профиль `team` — 8 гейтов sdd схлопнуты в 5 авторинговых + 3 исполнительных

Andrei: «8 — много, буквального копирования не имел в виду». Свёртка:

| # | Артефакт steward | Что вобрал из sdd | Владелец (CODEOWNERS) | upstream |
|---|---|---|---|---|
| 1 | **charter** | Gate BR + «зачем» FRD | `@product` | — |
| 2 | **requirements** | FRD «что» (функц. + NFR) | `@product` `@architects` | charter |
| 3 | **design** | 0a Tech + 0b Assessment + Gate 1 Design Stack | `@architects` | requirements |
| 4 | **acceptance** | Gate 2 (DQ→quality/acceptance) | `@qa` | requirements |
| 5 | **decomposition** | (новый) split на workstreams | `@tech-lead` | design, acceptance |

Листовые **task-спеки** (на workstream) не пишутся здесь — делегируются spec-runner.
Исполнительные гейты sdd остаются, но форсятся инструментами, а не авторингом:
Gate 3 Code Review = PR-review · Gate 4 Testing = CI + atp · Gate 5 Deployment = deployer.

Профиль `lite` = `requirements → design → tasks`, charter опционален, в соло-режиме гейты
авто-аппрувятся (анти-ceremony).

## Схема трассировки ID

```
REQ-xxx / NFR-xxx  (requirements)
      └─▶ DEC-xxx  (design decisions)  ──┐
      └─▶ AC-xxx   (acceptance criteria) ─┼─▶ WS-xxx (decomposition) ─▶ TASK-xxx (spec-runner)
```
Правило: каждый downstream-артефакт ссылается на upstream ID в `traces_to`. CI `gate-check`
проверяет, что висячих ссылок нет и upstream `APPROVED`.

## Файлы бандла

- `spec/00-charter.md` — проблема, scope, success-критерии
- `spec/10-requirements.md` — REQ + NFR
- `spec/20-design.md` — архитектура, решения, frontmatter-схема, профиль-YAML, gate-check контракт, CODEOWNERS
- `spec/30-acceptance.md` — acceptance-критерии + стратегия тестов
- `spec/40-decomposition.md` — workstreams → Maestro/spec-runner

## Аппрув-модель (git)

Артефакт = файл с frontmatter. Аппрув = PR, отревьюенный CODEOWNERS-ролью артефакта, → merge в
`main`. `status: approved` во frontmatter — **зеркало** git (git — primary). `gate-check` в CI
блокирует downstream, пока upstream не merged+approved, и метит stale при изменении upstream.
