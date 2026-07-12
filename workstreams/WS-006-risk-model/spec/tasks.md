---
spec_stage: tasks
status: draft
version: 1
generated_by: claude@claude-fable-5
generated_at: 2026-07-12
source_prompt_version: sha256:pending
validation: pending
approved_by: null
approved_at: null
---

# WS-006: risk model + mandatory gates — Tasks Specification

> Priority: 🔴 P0 · 🟠 P1 · 🟡 P2 | Status: ⬜ TODO · 🔄 IN PROGRESS · ✅ DONE · ⏸️ BLOCKED
>
> RD-004 закрывается Milestone 0 (design evaluated); Milestone 1 — имплементация, стартует
> только после аппрува design (по правилу upstream-approved самого steward).

---

## Milestone 0: Design (deliverable RD-004)

### TASK-601: Ревью и аппрув спек-бандла WS-006
🔴 P0 | 🔄 IN PROGRESS | Est: —

**Description:**
Этот бандл (requirements + design + risk-model.example.yaml) проходит PR-review; открытые
вопросы OQ-1..OQ-4 закрываются решениями владельца прямо в ревью или отдельными заметками.

**Checklist:**
- [ ] PR-review бандла
- [ ] Решения по OQ-1..OQ-4 зафиксированы (в design или ADR-заметке)

**Traces to:** [REQ-601]..[REQ-611]
**Depends on:** -
**Blocks:** [TASK-602], [TASK-603], [TASK-604], [TASK-605]

---

### TASK-602: Handoff-заметка Maestro
🔴 P0 | ⬜ TODO | Est: 1h

**Description:**
Maestro-side работы из DESIGN-611 (guard-hook + verdict-record, аннотации advisory-fail,
SHA-инвалидация, evidence-ref v2 `kind: gate-verdict`) — заметка в
`../prograph-vault/authored/notes/` (steward чужой код не правит).

**Traces to:** [REQ-611], [DESIGN-611], [DESIGN-612]
**Depends on:** [TASK-601]

---

### TASK-603: evidence_rules для RD-004 в contracts-v1.yaml
🟠 P1 | ⬜ TODO | Est: 1h

**Description:**
После фиксации путей деливераблов (этим бандлом) добавить evidence_rules RD-004 в
`prograph-vault/authored/roadmaps/contracts-v1.yaml` (vault PR): `file_exists` на спек-бандл
и (после M1) на `profiles/risk-model.yaml` — dashboard начнёт честно показывать статус.

**Traces to:** [REQ-611]
**Depends on:** [TASK-601]

---

## Milestone 1: Implementation ⏸️ BLOCKED до аппрува design

### TASK-604: RiskModel loader + валидация
🔴 P0 | ⏸️ BLOCKED | Est: 3h

**Checklist:**
- [ ] Парсинг risk-model.yaml → `RiskModel`; структурная ошибка → config error (exit 2)
- [ ] Обязательный `class_tiers.unknown` ≥ medium (REQ-603)
- [ ] sha256 файла как `risk_model_version`
- [ ] Юнит-тесты: валидная/битая модель

**Traces to:** [REQ-601], [REQ-603], [DESIGN-603]

---

### TASK-605: Классификатор — три оси + комбинатор + двухфазность
🔴 P0 | ⏸️ BLOCKED | Est: 4-5h

**Checklist:**
- [ ] change_class per-file (per-repo → _generic → default), max по диффу
- [ ] blast_radius через consumer_registry; trust_boundary path-rules + declared flags
- [ ] `tier = max(floor, lookups)` + `dominant_axis`
- [ ] ex_ante (scope globs) / ex_post (diff); scope_violation ⇒ эскалация + флаг
- [ ] Golden-кейсы на каждую ось + unmapped path; property-тест монотонности

**Traces to:** [REQ-602], [REQ-604], [REQ-605], [DESIGN-602], [DESIGN-604], [DESIGN-605]

---

### TASK-606: CLI `risk-classify` + JSON shape
🔴 P0 | ⏸️ BLOCKED | Est: 3h

**Checklist:**
- [ ] `--declared | --diff`, `--risk-model`, `--no-fs`, `--format json`
- [ ] Байт-идентичный вывод при одинаковом входе (двойной прогон)
- [ ] Exit 0/2; shape по DESIGN-610

**Traces to:** [REQ-610], [DESIGN-610]

---

### TASK-607: Waiver-файлы + проверка в gate-check
🟠 P1 | ⏸️ BLOCKED | Est: 3h

**Checklist:**
- [ ] Формат `spec/waivers/<gate_id>-<shortsha>.md` (frontmatter)
- [ ] `waived` требует waiver с совпадающим sha; critical → error
- [ ] Юнит-тесты: валидный/просроченный (чужой sha)/critical

**Traces to:** [REQ-606], [REQ-607], [REQ-609], [DESIGN-606], [DESIGN-608], [DESIGN-609]
