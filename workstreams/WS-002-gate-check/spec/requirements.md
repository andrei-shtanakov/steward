---
spec_stage: requirements
status: draft
version: 1
generated_by: claude@claude-opus-4-8
generated_at: 2026-07-05
source_prompt_version: sha256:pending
validation: pending
approved_by: null
approved_at: null
---

# WS-002: gate-check linter — Requirements Specification

> Листовая спека одного workstream'а steward, сгенерированная в формате spec-runner
> (делегирование, DEC-005). Upstream-трасса: steward REQ-003, REQ-006, AC-001..004, AC-008.

## Executive Summary

`gate-check` — детерминированный линтер governance-бандла для CI. Читает профиль artifact-DAG и
набор спек-артефактов с frontmatter, проверяет полноту, трассируемость, порядок аппрувов,
согласованность Status↔git и stale-каскад. Возвращает exit-код для блокировки PR.

---

## Requirements

#### REQ-201: Profile-driven DAG model
**Priority**: 🔴 P0
**Rationale**: Проверки зависят от структуры гейтов; она приходит из профиля, не хардкодится.
**Description**: Загрузить профиль (`team`/`lite`) в граф узлов {id, required, owner_role, upstream[]}.
**Acceptance Criteria**:
- [ ] Парсинг YAML-профиля в `SpecGraph`
- [ ] Детект цикла и ссылки на несуществующий upstream → config error (exit 2)
- [ ] Артефакты матчатся на узлы по `spec_stage`

#### REQ-202: Completeness check
**Priority**: 🔴 P0
**Description**: Все `required`-артефакты профиля присутствуют в `spec/`.
**Acceptance Criteria**:
- [ ] Отсутствие required-артефакта → finding (exit 1)
- [ ] Опциональные узлы (charter в `lite`) отсутствие не роняет

#### REQ-203: Traceability check
**Priority**: 🔴 P0
**Rationale**: Ядро ценности — нет висячих ссылок между слоями.
**Description**: Каждый `traces_to` резолвится в существующий upstream-артефакт/ID.
**Acceptance Criteria**:
- [ ] Висячий `traces_to` → finding с указанием артефакта и битой ссылки
- [ ] Downstream без единой upstream-ссылки → warning

#### REQ-204: Upstream-approved gate
**Priority**: 🔴 P0
**Description**: Downstream не может быть `approved`, пока любой upstream не `approved`.
**Acceptance Criteria**:
- [ ] Approved downstream при не-approved upstream → finding (exit 1)
- [ ] Порядок берётся из `SpecGraph`, не из имён файлов

#### REQ-205: Status↔git consistency
**Priority**: 🔴 P0
**Rationale**: git — primary источник аппрува (steward NFR-003).
**Description**: `status: approved` обязан подтверждаться git: артефакт на `main` + PR-approval
CODEOWNERS-ролью узла.
**Acceptance Criteria**:
- [ ] `approved` во frontmatter без git-подтверждения → finding
- [ ] Approval не от роли-владельца (по CODEOWNERS) → finding

#### REQ-206: Stale-cascade
**Priority**: 🟠 P1
**Description**: Изменение approved upstream метит downstream `stale` до ре-аппрува.
**Acceptance Criteria**:
- [ ] Хеш upstream на момент аппрува downstream сверяется с текущим
- [ ] Расхождение → downstream `stale`, `gate-check` падает

#### REQ-207: Deterministic --no-fs CI mode
**Priority**: 🔴 P0
**Description**: Работа из инъецированных git-фактов (JSON), без сети и рабочего дерева.
**Acceptance Criteria**:
- [ ] `--no-fs facts.json` даёт идентичный результат без git
- [ ] Exit-коды: `0` чисто, `1` findings, `2` config error

#### REQ-208: Unmanaged passthrough
**Priority**: 🟠 P1
**Description**: Файлы без frontmatter — «unmanaged», не блокируют (steward NFR-002).
**Acceptance Criteria**:
- [ ] Файл без frontmatter игнорируется всеми проверками

#### REQ-209: Build on OSS primitives
**Priority**: 🟡 P2
**Rationale**: Не изобретать presence/ownership-проверки (steward DEC-004).
**Description**: Полнота и ownership — через repolinter / codeowners-validator; `gate-check`
оркестрирует и добавляет трассировку/stale/status↔git.
**Acceptance Criteria**:
- [ ] Presence-правила выражены рулсетом repolinter
- [ ] Ownership сверяется codeowners-validator
