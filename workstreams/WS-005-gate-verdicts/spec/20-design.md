---
spec_stage: design
status: approved
version: 1
owner_role: "@architects"
generated_by: claude@claude-fable-5
generated_at: 2026-08-02
approved_by: andrei-shtanakov
approved_at: 2026-08-02
upstream_hashes:
  requirements: 3ea49935b5f28bc21c5d65d42cb766ed2f076b54
  behaviour-spec: 27f8b15e6c387dc1a3719e289c30f380699a4e87
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

```yaml
schema: intended-graph/v0-draft   # schema v1 — открытый вопрос ADR (prograph)
components:
  - {id: steward.gatecheck,             kind: cli,      owner: "@architects",
     responsibility: "прогон проверок, модель Finding, exit 0/1/2",
     evidence: [CON-02]}
  - {id: steward.verdicts-emitter,      kind: module,   owner: "@architects",
     responsibility: "сериализация прогона в gate_verdicts.jsonl; header: schema_version, source_commit, dirty, generated_at",
     evidence: [BEH-01, BEH-07]}
  - {id: contract.gate-verdicts-v1,     kind: contract, owner: "@architects",
     responsibility: "JSON Schema + golden fixtures (positive + все negative-классы)",
     evidence: [BEH-03, BEH-04, BEH-06, CON-02]}
  - {id: dispatcher.contract-vendor,    kind: module,   owner: "@architects",
     responsibility: "пиненая копия схемы; copy-integrity + upstream-drift раздельно",
     evidence: [CON-02]}
  - {id: dispatcher.governance-collector, kind: module, owner: "@architects",
     responsibility: "чтение файла + git-фактов; классификация в 6 состояний ARCH-D2",
     evidence: [BEH-02, BEH-03, BEH-04, BEH-05, BEH-06, BEH-08, NFR-01, NFR-02]}
  - {id: dispatcher.governance-panel,   kind: ui,       owner: "@architects",
     responsibility: "read-only отображение состояния бандлов",
     evidence: [BEH-01, BEH-07, BEH-09, FR-01]}
interfaces:
  - {id: I-01, producer: steward.verdicts-emitter, consumer: "file:.steward/gate_verdicts.jsonl",
     protocol: "jsonl / gate-verdicts/v1", detector: declared}
  - {id: I-02, producer: "file:.steward/gate_verdicts.jsonl", consumer: dispatcher.governance-collector,
     protocol: "jsonl / gate-verdicts/v1", detector: declared}
  - {id: I-03, producer: contract.gate-verdicts-v1, consumer: dispatcher.contract-vendor,
     protocol: "вендоринг пиненой копии", detector: contract}
  - {id: I-04, producer: dispatcher.governance-collector, consumer: dispatcher.governance-panel,
     protocol: "in-process read model", detector: import}
constraints:
  - {id: ARCH-C1, rule: "forbidden: dispatcher.* -> import steward.*",
     detector: import, evidence: [FR-02, CON-02]}
  - {id: ARCH-C2, rule: "forbidden: governance-panel -> запись в наблюдаемые репо; только GET",
     detector: manual-evidence, evidence: [BEH-09, OUT-01]}
  - {id: ARCH-C3, rule: "forbidden: governance-collector вычисляет вердикты (только классификация)",
     detector: manual-evidence, evidence: [FR-02]}
  - {id: ARCH-C4, rule: "layering: panel -> collector -> file; панель не читает файл напрямую",
     detector: import, evidence: []}
resources:
  - "runtime: существующий FastAPI dispatcher; новых сервисов нет"
  - "storage: локальный диск, gitignored файл на бандл (ARCH-D1)"
exceptions: []
```

## Conformance-ожидания (трёхзначные)

| Ребро/ограничение | На старте | Перед release |
|---|---|---|
| I-01, I-02 (declared) | missing-required-edge — план, не блокер | conformant |
| I-03 (contract) | missing-required-edge | conformant |
| I-04, ARCH-C1, ARCH-C4 (import) | unknown до появления кода | conformant / violation |
| ARCH-C2, ARCH-C3 (manual-evidence) | **unknown — постоянно**: вердикт детектора не меняется никогда | unknown; release-obligation исполнена review-evidence + materialized BEH |
