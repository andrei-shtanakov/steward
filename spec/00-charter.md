---
spec_stage: charter
status: draft
version: 1
owner_role: "@product"
generated_by: claude@claude-opus-4-8
generated_at: 2026-07-05
approved_by: null
approved_at: null
traces_to: []
---

# Charter — steward (spec governance layer)

## Проблема

Экосистема умеет авторить спеку для *простого*: spec-runner ведёт `requirements → design → tasks`,
Maestro one-shot'ом декомпозит проект. Для **сложных задач и командной работы** этого мало:
нет широкого набора аппрувнутых артефактов, нет ролей и человеческого аппрува, нет трассируемости
и хендоффов между людьми. sdd-framework показал ценную идею (широкий gated-lifecycle), но она
data-специфична, форсит гейты только текстом и завязана на внешнего автора.

Дополнительно уже существует **долг дублирования**: `maestro/maestro/decomposer.py` хранит копию
формата `tasks.md` в prompt-строке и пишет спеки мимо gated-режима spec-runner.

## Scope

**In:** конфигурируемый artifact-DAG (профили `lite`/`team`); frontmatter-схема поверх SpecMeta
spec-runner (+ роль, человеческий approver); CI-линтер `gate-check` (полнота/трассировка/stale/
Status↔git); аппрув через git-PR + CODEOWNERS; компиляция вниз делегированием (Decomposition→Maestro,
Task→spec-runner); панель состояния в dispatcher (read-only).

**Out:** исполнение спек (остаётся у spec-runner/Maestro); генерация `tasks.md`/`project.yaml`
самим steward (только делегирование); собственная идентичность/RBAC/локи (используем git); тяжёлый
процесс на простых задачах (профиль `lite` обязателен).

## Success-критерии

- **SC-1** Команда доводит сложную спеку через 5 гейтов с ролевыми аппрувами до `APPROVED`-бандла.
- **SC-2** Ноль дублирования формата: листовые таски и декомпозиция компилируются делегированием.
- **SC-3** Профиль `lite` + соло-авто-аппрув — простая задача проходит без церемонии.
- **SC-4** `gate-check` запускается в CI и блокирует неполную/непрослеживаемую/stale спеку.
- **SC-5** Dogfood: спека самого steward написана и проведена через steward.

## Стейкхолдеры / роли

`@product` (charter, requirements), `@architects` (requirements, design), `@qa` (acceptance),
`@tech-lead` (decomposition), `@stream-owner` (листовые task-спеки), Ops (Gate 5, deployer).
