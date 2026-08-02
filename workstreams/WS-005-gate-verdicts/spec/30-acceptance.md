---
spec_stage: acceptance
status: draft
version: 1
owner_role: "@qa"
generated_by: claude@claude-fable-5
generated_at: 2026-08-02
approved_by: null
approved_at: null
traces_to: [requirements, behaviour-spec]
---

# Acceptance — governance-панель бандла (WS-005)

> Разграничение по D2 ADR: BEH-NN (15-behaviour-spec) — сценарии поведения с
> привязкой к проверкам; AC-NN здесь — release-критерии, агрегирующие сценарии
> и фиксирующие порог приёмки. AC не дублирует Given/When/Then.

## Release-критерии

- **AC-001 · Никакого ложного «чисто».** Все negative-классы — повреждено,
  неизвестная схема, устарело, нерезолвимо, IO-ошибка — рендерятся в не-pass
  состояния. Агрегирует BEH-03, BEH-04, BEH-05, BEH-06, BEH-08; порог — M-01:
  0 фикстур этих классов, показанных как pass, в golden-сьюте. → FR-03, FR-04
- **AC-002 · Панель строго read-only.** Ни одного мутирующего маршрута в
  governance-разделе; структурно — ARCH-C2 (постоянный unknown, закрыт
  review-evidence) + materialized BEH-09. → FR-01, OUT-01
- **AC-003 · Единственный источник — файл вердиктов.** Collector не импортирует
  steward и не вычисляет вердикты: ARCH-C1 (import-detector, conformant) +
  ARCH-C3 (manual-evidence) + BEH-02. → FR-02
- **AC-004 · Оператор решает с одного экрана.** Блокер бандла виден без открытия
  файлов; свежесть и происхождение — рядом со статусом. Агрегирует BEH-01,
  BEH-07; порог — M-02, подтверждается dogfood-прогоном на бандле steward. → FR-01, FR-05
- **AC-005 · Оффлайн-путь чтения.** Рендер при остановленных процессах
  наблюдаемых репо, без сети. Агрегирует BEH-02 + NFR-01-тест. → NFR-01

## Порог приёмки workstream'ов

Закрытие каждого WS требует `materialized` (или approved waiver) для всех его
blocking-сценариев — GC-CHECK-READY-стадия двухстадийного гейта; `planned`
достаточно только для compile-down.
