# steward — маршрут (следующие шаги)

> Дата: 2026-07-05 · Сводит воедино 3 ADR: consolidation, governance-layer, dogfood-спеку.
> Принцип: сначала дешёвая проверка эргономики и одно ключевое изменение, потом обвязка.

## TL;DR порядок

Phase 0 (решения + дешёвая проверка) → Phase 1 (одно keystone-изменение) →
Phase 2 (governance MVP) → Phase 3 (замкнуть петлю, снять долг). Побочно — мелкие issue.

## Phase 0 — решить и пощупать (без нового кода)

- **D1 · Решение DEC-006 — ✅ решено (2026-07-11): gate-check живёт в steward**
  (`src/steward/gatecheck/`); обоснование в `spec/20-design.md` DEC-006.
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

- **C2 · Frontmatter-схема (WS-001) — steward-часть ✅ (2026-07-15):** `upstream_hashes`
  в ArtifactMeta (пиновка upstream-блобов при аппруве) + stale-cascade check (REQ-206,
  GC-STALE/GC-STALE-UNPINNED) — снят deferred-пункт C3. spec-runner-часть (owner_role +
  человеческий approver в SpecMeta, SPEC_META_CONTRACT v2 → ре-вендоринг) — handoff в
  `prograph-vault/authored/notes/`. (project: steward/spec-runner)
- **C3 · gate-check MVP (WS-002) — ✅ реализовано (2026-07-11):** completeness + traceability +
  upstream-approved + status↔git, `--no-fs facts.json`, exit-коды 0/1/2, CI-job (dogfood на
  собственном `spec/`). Отложено осознанно: ~~stale-cascade (REQ-206)~~ — реализован с C2
  (2026-07-15); OSS-мост (REQ-209, P2). Branch protection — включить руками после мержа.
  (project: steward)

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
