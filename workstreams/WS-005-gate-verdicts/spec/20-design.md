---
spec_stage: design
status: approved
version: 1
owner_role: architects
generated_by: claude@claude-fable-5
generated_at: 2026-08-02
approved_by: andrei-shtanakov
approved_at: 2026-08-02
upstream_hashes:
  requirements: 9ebae3b7c920d31d84fd9ca10d0ccb259cef93fb
  behaviour-spec: 3d3738377fa0307d16b4e2208da279b4b1e27ce2
traces_to: [requirements, behaviour-spec]
---

# Design — governance-панель бандла (WS-005)

> Узел называется `design` (rename design→architecture — открытый вопрос ADR,
> ждёт второго сигнала), но структурирован по D3: решения + intended graph.
> System Assessment — из approved engineer-брифа (существующие gate-check,
> dispatcher-collectors, практика вендоринга контрактов).

## Решения

- **ARCH-D1 · Где живёт файл вердиктов.** `<bundle-repo>/.steward/gate_verdicts.jsonl`,
  gitignored, локальный машинный артефакт (как `data/state.db` у proctor). Не
  коммитим: вердикты — производная, их место в git создало бы второй источник
  истины. Свежесть доказывается header'ом (source_commit + dirty) против текущего
  HEAD, а не git-историей файла.
- **ARCH-D2 · Классифицирует читатель.** Шесть состояний бандла — pass | blocked |
  no-data | unreadable | stale | unresolvable — вычисляет collector dispatcher'а из
  файла + git-фактов. Steward пишет только факты (findings, header), никогда —
  оценку свежести: прибор не свидетельствует о собственной годности.
- **ARCH-D3 · Схема — контракт с двумя гарантиями.** Канон `gate-verdicts/v1`
  (JSON Schema + golden fixtures, включая все negative-классы) — в steward;
  dispatcher вендорит пиненую копию с раздельными copy-integrity (PR-гейт) и
  upstream-drift (scheduled) проверками — без единого `in_sync` (CON-02).
- **ARCH-D4 · Retention.** Файл перезаписывается целиком каждым прогоном
  gate-check; история — вне scope (OUT-02), проблема роста снята.

## Intended graph

Машиночитаемый канон — **`intended-graph.yaml`** рядом с этим артефактом
(схема `intended-graph/v1`, спека принята prograph#22 2026-08-03). Этот
документ его не дублирует: компоненты, интерфейсы и ограничения живут в одном
месте, здесь — обоснование (Решения) и conformance-ожидания (ниже).
Указатель для prograph — `[tool.prograph] intended` в `pyproject.toml` репо.
ARCH-C4 (panel → file, оба конца внутри dispatcher) по правилам v1 даёт
честный `unknown/unsupported-resolution` до module-level резолюции (v1.1).

## Conformance-ожидания (трёхзначные)

| Ребро/ограничение | На старте | Перед release |
|---|---|---|
| I-01, I-02 (declared) | missing-required-edge — план, не блокер | conformant |
| I-03 (contract) | missing-required-edge | conformant |
| ARCH-C1 (import, cross-project) | unknown до появления кода | conformant / violation |
| I-04 (import), ARCH-C4 (declared) — **intra-project пары** внутри dispatcher | unknown (`unsupported-resolution`) — наблюдаемый долг v1 | unknown до module-level резолюции (v1.1); закрыто materialized BEH-09 и route-тестом |
| ARCH-C2, ARCH-C3 (manual-evidence) | **unknown — постоянно**: вердикт детектора не меняется никогда | unknown; release-obligation исполнена review-evidence + materialized BEH |
