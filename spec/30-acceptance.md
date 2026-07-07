---
spec_stage: acceptance
status: draft
version: 1
owner_role: "@qa"
generated_by: claude@claude-opus-4-8
generated_at: 2026-07-05
approved_by: null
approved_at: null
traces_to: [REQ-001, REQ-002, REQ-003, REQ-004, REQ-005, REQ-006, REQ-007, NFR-001, NFR-004]
---

# Acceptance & Test — steward

## Acceptance-критерии (ID: `AC-<seq>`)

- **AC-001** Загрузка профиля `team`/`lite` из YAML даёт корректный DAG; неизвестный узел/цикл →
  ошибка конфигурации (exit 2). → REQ-001
- **AC-002** Frontmatter парсится в SpecMeta+ext; файл без frontmatter трактуется как unmanaged и
  не блокирует. → REQ-002, NFR-002
- **AC-003** `gate-check` ловит: висячий `traces_to`, downstream при не-approved upstream,
  рассогласование Status↔git — каждый кейс даёт exit 1 с указанием артефакта. → REQ-003
- **AC-004** Merge изменения в approved upstream помечает всех downstream `stale`; `gate-check`
  после этого падает до ре-аппрува. → REQ-006
- **AC-005** Аппрув через PR-merge владельцем-CODEOWNERS переводит артефакт в `approved` (git),
  frontmatter-зеркало обновляется; чужой ревьюер — не проходит branch protection. → REQ-004
- **AC-006** `decomposition` компилируется в `project.yaml`, проходящий `maestro validate`;
  каждый `WS-xxx` порождает spec-runner-спеку, парсимую `task.py` spec-runner. → REQ-005
- **AC-007** Соло-режим с `solo_auto_approve` не требует ручных аппрувов; профиль `lite` проводит
  задачу за 2–3 артефакта. → NFR-001
- **AC-008** `gate-check --no-fs` детерминирован, без сети/рабочего дерева. → REQ-003, NFR-004
- **AC-009** dispatcher показывает состояние бандла (артефакт → статус → блокер) только на чтение.
  → REQ-007

## Стратегия тестов

- **Unit** — каждый чек gate-check (AC-001..004, 008): фикстуры спек-бандлов, mock git-состояния.
- **Contract/golden** (AC-006) — `decomposition`→`project.yaml`→`maestro validate`;
  `WS`→spec-runner authoring→`task.py`. Golden-файлы, ломаются при дрейфе формата.
- **E2E dogfood** (SC-5) — прогнать *этот* бандл через steward: 5 гейтов, ролевые PR, компиляция вниз.

## Привязка к исполнительным гейтам sdd

Gate 3 Code Review = обязательный PR-review (branch protection). Gate 4 Testing = CI зелёный +
atp-платформа как эталон качества агентных прогонов. Gate 5 Deployment = deployer
(author-not-execute, arbiter-gated) — вне scope авторинга, но замыкает трассу AC→WS→deploy.
