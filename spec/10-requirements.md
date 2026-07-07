---
spec_stage: requirements
status: draft
version: 1
owner_role: "@product,@architects"
generated_by: claude@claude-opus-4-8
generated_at: 2026-07-05
approved_by: null
approved_at: null
traces_to: [charter]
---

# Requirements — steward

## Функциональные

- **REQ-001** Профили как данные. Artifact-DAG описывается декларативно (`profiles/*.yaml`): узел =
  {id, template, owner_role, upstream[], опц. delegate}. Поставляются `lite` и `team`. → SC-1, SC-3
- **REQ-002** Frontmatter-схема артефакта — расширение SpecMeta spec-runner: добавить `owner_role`
  и человеческого `approved_by` (git-handle) рядом с agent-id. Обратная совместимость сохранена. → SC-2
- **REQ-003** `gate-check` — линтер для CI. Проверки: (a) полнота DAG (все требуемые артефакты
  есть); (b) трассируемость (каждый `traces_to` резолвится в существующий upstream ID); (c)
  upstream `APPROVED` до старта downstream; (d) Status↔git консистентны; (e) stale-детект по графу.
  Детерминирован, exit-код ≠0 при нарушении. → SC-4
- **REQ-004** Аппрув-маппинг через `CODEOWNERS`: папка артефакта → роль-владелец. Аппрув = PR-merge
  ревьюером-владельцем. steward читает git/CODEOWNERS, не хранит своих прав. → SC-1
- **REQ-005** Компиляция вниз делегированием: артефакт `decomposition` → Maestro `project.yaml`;
  листовой `task` → spec-runner authoring (`plan [--gated]`). steward не пишет эти форматы сам. → SC-2
- **REQ-006** Stale-каскад по DAG: merge изменения в approved upstream → downstream помечается
  `stale` (обобщение линейной механики spec-runner на граф). → SC-4
- **REQ-007** Видимость: dispatcher (read-only) отдаёт панель состояния бандла — какой артефакт на
  чьём аппруве, что stale, что блокирует. → SC-1

## Нефункциональные

- **NFR-001** Анти-ceremony: в соло-режиме (один owner на все папки) гейты авто-аппрувятся;
  профиль `lite` — дефолт для мелких задач. → SC-3
- **NFR-002** Обратная совместимость: файлы без frontmatter — «unmanaged», не блокируются (как в
  spec-runner). → SC-2
- **NFR-003** Git — единственный primary источник истины аппрува; frontmatter Status — зеркало.
  При расхождении `gate-check` падает и указывает git как источник. → SC-4
- **NFR-004** `gate-check` запускается без сети/без рабочего дерева (`--no-fs`-режим) для
  детерминированного CI. → SC-4
- **NFR-005** Ноль беспоук-идентичности/RBAC/нотификаций — всё на git-host (review, branch
  protection, CODEOWNERS). → SC-2, SC-3
