---
spec_stage: design
status: draft
version: 1
generated_by: claude@claude-fable-5
generated_at: 2026-07-12
source_prompt_version: sha256:pending
validation: pending
approved_by: null
approved_at: null
---

# WS-006: risk model + mandatory gates — Technical Design

## Design Principles

### DESIGN-601: Профиль и риск-модель — разные оси, не смешивать
Профиль (`lite`/`team`) — статический baseline команды: какие проверки вообще запускаются и с
какой строгостью по умолчанию (**floor**). Риск-модель — динамическая эскалация per-change:
`risk(change) → tier`, поднимающая obligation конкретных гейтов. Правило монотонности: модель
может только **поднимать** относительно floor, никогда не опускать — легко рассуждать, легко
аудировать. Трасса: REQ-601, REQ-604.

### DESIGN-602: Выведение трёх входов
- **change_class** — per-file по path→class правилам (per-repo секция → `_generic` секция →
  встроенный default `unknown`); внутри секции first match wins. Класс диффа =
  max(классы файлов). Классы v1: `docs, config, code, state-machine, policy, contract,
  ci-deploy, secrets, unknown`. Репо/surface отдельной осью не является — он вшит в
  per-repo секции правил.
- **blast_radius** — default `single-repo`; изменённый путь под `contracts/**` репо-владельца →
  lookup в `consumer_registry`: 1 консюмер → `cross-repo`, ≥2 → `ecosystem-contract`. Реестр
  v1 статический (дублирует будущий реестр вендоринга — OQ-2); полный граф зависимостей
  (prograph) — v2: грубое-но-детерминированное лучше точного-но-ручного.
- **trust_boundary** — path-правила (`.github/workflows/**`, `Dockerfile*`, `deploy/**`,
  `.env*`, `*secret*`) + декларируемые флаги задачи (`external-api` — из task-спеки, по путям
  не детектится).

Трасса: REQ-602.

### DESIGN-603: Fail-closed default классификации
Движок правил всегда завершает цепочку встроенным `{glob: "**", class: unknown}`;
`class_tiers.unknown` — обязательный ключ со значением ≥ medium, его отсутствие — config error.
Golden-кейс №1 — файл в неучтённой директории. Трасса: REQ-603.

### DESIGN-604: Комбинатор
`tier = max(floor, class_tiers[change_class], blast_tiers[blast_radius],
trust_tiers[trust_boundary])`. Никаких weighted scores. Вывод содержит `dominant_axis` —
какая ось дала max (объяснимость в post-mortem). Трасса: REQ-604.

### DESIGN-605: Две фазы оценки

| фаза | вход | что гейтит | особенность |
|---|---|---|---|
| `ex_ante` | `workstream.scope` globs из `project.yaml` (существует сегодня) | spawn: READY→RUNNING | tier по заявленному периметру задачи |
| `ex_post` | `git diff base..head` | merge: RUNNING→MERGING и PR-переходы | фактические пути; `actual ⊄ declared` → `scope_violation`: эскалация до `max(tier, high)` + флаг |

Сравнение declared vs actual — бесплатная проверка «агент вылез за заявленный периметр»
(мост к RD-006 Capability/Authority split). Трасса: REQ-605.

### DESIGN-606: Verdict-контракт (семантика verdict × obligation)

| verdict | mandatory | advisory |
|---|---|---|
| `pass` | proceed | proceed |
| `fail` | **block** | proceed + аннотация в run-metadata |
| `waived` | proceed (запись ссылается на waiver) | proceed |
| `error` | **block** (= fail, fail-closed) | proceed + аннотация |
| *missing* | **block** (= fail, fail-closed) | аннотация |

Fail-closed — свойство **любого** mandatory-гейта на любом tier'е, не привилегия critical.
Critical отличается: `human.transition_approval` в require-наборе и запрет waiver'ов.
Трасса: REQ-606, REQ-609.

### DESIGN-607: VerdictRecord и точки хранения
Shape: `{gate_id, obligation, verdict, tier, phase, sha, risk_model_version, ts,
waiver_ref?, note?}`. Точки записи:
- guard'ы Maestro-run → `logs/<ULID>/gate_verdicts.jsonl` → evidence_ref `kind: log`
  (`pipeline_id`);
- CI-вердикты steward (gate-check / risk-classify в job) → JSON-артефакт job'а, при
  необходимости коммитуемый → evidence_ref `kind: artifact` (`project` + `path`).

v1 не трогает чужой контракт: существующих kind'ов evidence-ref v1 достаточно. v2 —
предложение `kind: gate-verdict` (required: `gate_id`, `sha`) в Maestro
`contracts/observability/` — handoff, OQ-1. Записи видны в dispatcher work-items chain через
`WorkCorrelation.evidence_refs[]`. Трасса: REQ-608.

### DESIGN-608: SHA-инвалидация (TOCTOU)
Verdict-store ключуется `(gate_id, sha)`; guard принимает только записи текущего head SHA.
Новый коммит ⇒ все verdict'ы и waiver'ы инвалидированы, tier пересчитан (классификация —
дешёвая чистая функция, пересчёт не бутылочное горлышко). Это же отвечает на «когда
вычисляется риск»: ex-ante на spawn, ex-post на merge, штамп в run-metadata, инвалидация
по SHA. Трасса: REQ-607.

### DESIGN-609: Waiver-как-файл
`spec/waivers/<gate_id>-<shortsha>.md` с frontmatter `{gate_id, sha, waived_by, reason}`.
Аппрув = PR-merge ролью-владельцем гейта (CODEOWNERS на `spec/waivers/`) — переиспользует
всю machinery gate-check status↔git, ноль нового RBAC (NFR-003, NFR-005). Ребейз/новый
коммит убивает waiver (SHA-bound). На critical — запрещён (v1, OQ-3). Трасса: REQ-609.

### DESIGN-610: CLI и выходной shape
`steward risk-classify (--declared scope.json | --diff base..head) [--risk-model path]
[--no-fs facts.json] --format json`:

```json
{
  "tier": "high",
  "phase": "ex_post",
  "inputs": {
    "change_class": "contract",
    "blast_radius": "cross-repo",
    "trust_boundary": "none"
  },
  "dominant_axis": "change_class",
  "floor_profile": "team",
  "mandatory_gates": ["steward.gate_check", "maestro.validate_strict",
                      "human.owner_approval", "git.required_reviews"],
  "flags": [],
  "sha": "<head-sha>",
  "risk_model_version": "sha256:<risk-model.yaml>"
}
```

Maestro консюмит этот JSON и не вычисляет tier сам (одна точка истины). Контракт между
репо = схема этого вывода; Maestro вендорит её пиненой копией при имплементации.
Трасса: REQ-610.

### DESIGN-611: Gates-in-DAG skeleton (evaluated) для Maestro project.yaml
Гейт — **guard на transition-table** существующей state-machine workstream'а
(`WorkstreamStatus`: PENDING → DECOMPOSING → READY → RUNNING → MERGING → PR_CREATED → DONE),
не узел DAG: узел-гейт дал бы Maestro второй планировщик внутри графа. Скелет (поле `gates`
рядом с `workstreams`):

```yaml
gates:
  mode: fail_closed            # missing/error verdict на mandatory = fail (DESIGN-606)
  risk_model:
    source: steward-risk-classify   # Maestro консюмит JSON (DESIGN-610), сам не вычисляет
    pin: sha256:<risk-model.yaml>
  transitions:
    - from: pending
      to: [decomposing, ready]
      require:
        - {gate: steward.risk_classify_ex_ante, obligation: mandatory}   # штамп tier в run-metadata
    - from: ready
      to: running
      require:
        - {gate: maestro.preflight,     obligation: mandatory}   # существующий fail-fast
        - {gate: human.owner_approval,  obligation: by_tier}     # mandatory при tier >= high
    - from: running
      to: merging
      require:
        - {gate: steward.risk_classify_ex_post, obligation: mandatory}  # фактический дифф; scope_violation => эскалация
        - {gate: steward.gate_check,    obligation: by_tier}     # mandatory при tier >= medium
        - {gate: tests.passed,          obligation: mandatory}
    - from: pr_created
      to: done
      require:
        - {gate: git.required_reviews,        obligation: by_tier}  # CODEOWNERS при high+
        - {gate: human.transition_approval,   obligation: by_tier}  # только critical
```

`obligation: by_tier` резолвится через `tier_gates` из risk-model.yaml — require-список
единообразно является функцией tier'а, без разнобоя unconditional и `when: risk >= high`.
Advisory-fail пишет аннотацию в run-metadata и verdict-record, но не режет граф.

**Evaluation findings** (суть «evaluated» из RD-004):
1. Guard ложится в точку валидации переходов существующей state-machine
   (`maestro/models.py` `WorkstreamStatus.transitions`) — второго планировщика не появляется;
   preflight уже fail-fast перед запуском.
2. ex-ante вход существует сегодня: `workstream.scope` globs в `project.yaml`.
3. Требуются Maestro-side работы (handoff, не steward-код): (a) guard-hook на переходах +
   запись verdict-record в `logs/<ULID>/gate_verdicts.jsonl`; (b) канал аннотаций
   advisory-fail в run-metadata; (c) SHA-инвалидация verdict'ов при новом коммите.
4. evidence-ref v2 `kind: gate-verdict` — предложение в контракт Maestro (OQ-1).
5. Известная ловушка остаётся в силе: `maestro validate --no-fs` не ловит висячий
   `depends_on` (`emitter-contract-check.md`) — целостность dep-link остаётся за
   `gate_check`, гейты `tests`/`validate` её не заменяют.

Трасса: REQ-611.

### DESIGN-612: Ownership и границы
- **steward владеет**: `risk-model.yaml`, `risk-classify`, gate-check, waiver-механика.
- **Maestro владеет**: схема `project.yaml` (включая будущее поле `gates`), state-machine,
  guard-hook'и, контракт evidence-ref.
- Контракт между ними — JSON-вывод `risk-classify` (DESIGN-610), вендорится Maestro пиненой
  копией. Maestro-side работы из DESIGN-611 оформляются handoff-заметкой в
  `../prograph-vault/authored/notes/` — steward чужой код не правит.

## Data Model

```
RiskModel      { version, class_tiers, blast_tiers, trust_tiers, tier_gates,
                 path_class{repo: [rule]}, trust_rules, consumer_registry, waiver_policy }
Classification { tier, phase: ex_ante|ex_post, inputs{class,blast,trust}, dominant_axis,
                 floor_profile, mandatory_gates[], flags[], sha, risk_model_version }
VerdictRecord  { gate_id, obligation, verdict, tier, phase, sha, risk_model_version, ts,
                 waiver_ref?, note? }
Waiver         { gate_id, sha, waived_by, reason }   # файл с frontmatter, аппрув через PR
```

## Open Questions

- **OQ-1**: evidence-ref v2 `kind: gate-verdict` vs навсегда остаться на `log`/`artifact` —
  решение за Maestro (владелец контракта), запускается handoff-заметкой.
- **OQ-2**: где живёт `consumer_registry` — внутри risk-model.yaml (v1, дублирование) или в
  общем реестре вендоринга контрактов (появится в параллельном треке вендоринга).
- **OQ-3**: waiver на critical: forbidden (v1) vs 2-approver — пересмотреть после первых
  живых прогонов.
- **OQ-4**: путь risk-model.yaml — предложение `profiles/risk-model.yaml` (рядом с floor'ами,
  один PR-review-канал).
