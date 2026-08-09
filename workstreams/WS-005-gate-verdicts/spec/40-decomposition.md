---
spec_stage: decomposition
status: approved
version: 1
owner_role: tech-lead
generated_by: claude@claude-fable-5
generated_at: 2026-08-02
approved_by: andrei-shtanakov
approved_at: 2026-08-02
upstream_hashes:
  design: 0d902a049dd6c8f32f7c6f382fe14756ba42d3ed
  acceptance: b4517a4aac7caff85386967153f9369d7d512b42
traces_to: [design, acceptance]
---

# Decomposition — governance-панель бандла (WS-005 → 3 workstream'а)

> Границы выведены из компонентов intended graph (20-design): один WS не
> пересекает границу репо; контрактная точка I-03 — единственная связь WS-A ↔
> WS-B, и она вендорится, а не резолвится путём.
>
> **Compile-down здесь намеренно нет** (нет блока `steward-compile`): WS-B и
> WS-C — работа в соседнем репо dispatcher; по правилам полирепо она стартует
> inbox-issue в dispatcher (ADR-ECO-006), а не `project.yaml` steward'а.
> Только WS-A — код этого репо.

## WS-A · steward: контракт + emitter

- **Scope**: `contract.gate-verdicts-v1`, `steward.verdicts-emitter`; пути —
  `contracts/gate-verdicts/v1/`, `src/steward/verdicts/`.
- **Контрактные границы**: производит I-01 (файл) и I-03 (канон схемы).
- **Покрывает**: BEH-01/BEH-07 (producer-половина); фикстуры negative-классов
  для BEH-03/04/06 — часть канона контракта; ARCH-D1/D2/D4.
- **Материализация**: golden fixtures positive + все negative-классы;
  emitter-тесты header'а (source_commit, dirty, generated_at); verdict-записи
  несут DEC-007 role slugs.
- **validation_cmd**: `uv run pytest tests/verdicts tests/contract`.

## WS-B · dispatcher: vendor + collector

- **Scope**: `dispatcher.contract-vendor`, `dispatcher.governance-collector`
  (репо dispatcher; старт = inbox-issue со slug `ws005-governance-collector`).
- **Контрактные границы**: потребляет I-02 (файл) и I-03 (вендор); соблюдает
  ARCH-C1/C3.
- **Покрывает**: BEH-02, BEH-03, BEH-04, BEH-05, BEH-06, BEH-08; NFR-01, NFR-02.
- **Материализация**: классификация 6 состояний на golden-фикстурах канона;
  property-тест IO-классов; mock git-фактов для BEH-05; copy-integrity +
  upstream-drift раздельно.

## WS-C · dispatcher: панель + сквозной smoke

- **Scope**: `dispatcher.governance-panel` (репо dispatcher; отдельная
  inbox-issue после WS-B).
- **Контрактные границы**: потребляет I-04; соблюдает ARCH-C2 (только GET) и
  ARCH-C4 (не читает файл напрямую).
- **Покрывает**: BEH-01 (UI), BEH-07 (UI), BEH-09.
- **Материализация**: тест перечня маршрутов (BEH-09); UI-тест pass-состояния с
  header-полями; cross-repo live-smoke через настоящие бинари (приём actions/v1).

## Порядок

WS-A → WS-B → WS-C (строго: B вендорит канон A; C читает модель B).
Гейт компиляции: все blocking BEH несут `planned` с kind/owner/target —
проверяется `GC-CHECK-PLANNED`; `materialized` — к закрытию каждого WS
(порог из 30-acceptance).
