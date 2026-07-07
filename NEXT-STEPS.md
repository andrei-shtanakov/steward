# steward — маршрут (следующие шаги)

> Дата: 2026-07-05 · Сводит воедино 3 ADR: consolidation, governance-layer, dogfood-спеку.
> Принцип: сначала дешёвая проверка эргономики и одно ключевое изменение, потом обвязка.

## TL;DR порядок

Phase 0 (решения + дешёвая проверка) → Phase 1 (одно keystone-изменение) →
Phase 2 (governance MVP) → Phase 3 (замкнуть петлю, снять долг). Побочно — мелкие issue.

## Phase 0 — решить и пощупать (без нового кода)

- **D1 · Решение DEC-006 (твоё).** Где живёт gate-check: (a) governance-подкоманда spec-runner
  (реюз SpecMeta) или (b) отдельный линтер-репо. Рекомендация — (a): меньше кода, один владелец
  состояния. Блокирует Phase 2.
- **D2 · Лицензия sdd (параллельно).** Спросить Dmytro Honcharuk — можно ли брать тексты
  шаблонов (LICENSE в репо нет). До ответа берём только идею гейтов. Не блокирует код.
- **V1 · Живой прогон spec-runner gated.** На маленькой задаче прогнать `plan --gated` → увидеть
  цикл «LLM драфтит стадию → ты аппрувишь → следующая». Ноль нового кода, проверяет эргономику
  ядра до стройки.

## Phase 1 — keystone (разблокирует всё)

- **C1 · STAGES → загружаемый профиль в spec-runner.** Вынести захардкоженные
  `requirements/design/tasks` в данные; профиль `lite` = текущие 3 стадии, **zero behaviour
  change**, все тесты зелёные. Самое высокоэффективное изменение: без него нет ни профилей, ни
  богатых наборов. (project: spec-runner)

## Phase 2 — governance MVP

- **C2 · Frontmatter-схема (WS-001).** Расширить SpecMeta полями `owner_role` + человеческий
  approver. (project: steward/spec-runner)
- **C3 · gate-check MVP (WS-002, спека готова).** completeness + traceability + status↔git,
  `--no-fs`, CI-job + branch protection. Здесь git-PR/CODEOWNERS-энфорс становится реальным.
  Начать с одного пилотного репо. (project: steward)

## Phase 3 — замкнуть петлю и снять долг

- **C4 · Maestro decomposer → делегирование.** Убрать встроенный `SPEC_GENERATION_PROMPT`,
  вызывать spec-runner authoring. Снимает существующий дубль формата. (project: Maestro)
- **C5 · Emitters compile-down.** `decomposition → project.yaml` (контракт уже проверен),
  `WS → spec-runner`. Golden-тесты. (project: steward)

## Побочно (мелко, можно сейчас)

- **I1 · Баг Maestro:** `validate --no-fs` не ловит висячую `depends_on` (см.
  `emitter-contract-check.md`). Завести в бэклог Maestro. (project: Maestro)

## Критическая заметка

Не строить весь steward разом. Порядок C1 → C3 даёт работающий вертикальный срез (профиль +
линтер + git-аппрув) на простом профиле. Богатый `team`-набор и Maestro-делегирование (C4) —
только после того, как срез подтвердит эргономику. Риск №1 — ceremony: держать `lite` дефолтом.
