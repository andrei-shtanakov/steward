---
spec_stage: tasks
status: draft
version: 1
generated_by: claude@claude-opus-4-8
generated_at: 2026-07-05
source_prompt_version: sha256:pending
validation: pending
approved_by: null
approved_at: null
---

# WS-002: gate-check linter — Tasks Specification

> Priority: 🔴 P0 · 🟠 P1 · 🟡 P2 · 🟢 P3 | Status: ⬜ TODO · 🔄 IN PROGRESS · ✅ DONE · ⏸️ BLOCKED

---

## Milestone 1: Core model

### TASK-201: SpecGraph + profile loader
🔴 P0 | ⬜ TODO | Est: 3-4h

**Description:**
Загрузка профиля в граф с топологической проверкой.

**Checklist:**
- [ ] `graph.py`: парсинг профиля YAML → `SpecGraph`
- [ ] Детект цикла и unknown upstream → config error (exit 2)
- [ ] Матчинг артефактов на узлы по `spec_stage`
- [ ] Юнит-тесты: валидный/циклический/битый профиль

**Traces to:** [REQ-201], [DESIGN-202]
**Depends on:** -
**Blocks:** [TASK-203], [TASK-204], [TASK-205], [TASK-206], [TASK-207]

---

### TASK-202: SpecMeta reader (vendored) + owner_role
🔴 P0 | ⬜ TODO | Est: 2-3h

**Description:**
Переиспользовать frontmatter-парсер spec-runner, расширить `owner_role`.

**Checklist:**
- [ ] Вендорить `split_frontmatter`/`SpecMeta` из spec-runner с marker-комментарием
- [ ] `meta.py`: обёртка + поле `owner_role`
- [ ] Unmanaged-файл (без frontmatter) → passthrough
- [ ] Юнит-тесты парсинга

**Traces to:** [REQ-208], [DESIGN-201]
**Depends on:** -
**Blocks:** [TASK-203], [TASK-204], [TASK-205], [TASK-206], [TASK-207]

---

## Milestone 2: Checks

### TASK-203: completeness check
🔴 P0 | ⬜ TODO | Est: 2h

**Checklist:**
- [ ] `checks.py::completeness(graph, states)` → findings
- [ ] Required отсутствует → error; optional отсутствует → ok
- [ ] Юнит-тесты на фикстурах

**Traces to:** [REQ-202], [DESIGN-203]
**Depends on:** [TASK-201], [TASK-202]
**Blocks:** [TASK-209]

---

### TASK-204: traceability check
🔴 P0 | ⬜ TODO | Est: 2-3h

**Checklist:**
- [ ] Резолв каждого `traces_to` в существующий upstream ID
- [ ] Висячая ссылка → error; downstream без ссылок → warn
- [ ] Юнит-тесты (битая/валидная трасса)

**Traces to:** [REQ-203], [DESIGN-203]
**Depends on:** [TASK-201], [TASK-202]
**Blocks:** [TASK-209]

---

### TASK-205: upstream-approved gate
🔴 P0 | ⬜ TODO | Est: 2h

**Checklist:**
- [ ] Approved downstream при не-approved upstream → error
- [ ] Порядок из `SpecGraph`
- [ ] Юнит-тесты

**Traces to:** [REQ-204], [DESIGN-203]
**Depends on:** [TASK-201], [TASK-202]
**Blocks:** [TASK-209]

---

### TASK-206: status↔git consistency check
🔴 P0 | ⬜ TODO | Est: 3-4h

**Checklist:**
- [ ] `approved` сверяется с `GitFacts` (branch=main + approval роли)
- [ ] Approval не от CODEOWNERS-роли узла → error
- [ ] Юнит-тесты с mock `GitFacts`

**Traces to:** [REQ-205], [DESIGN-203], [DESIGN-204]
**Depends on:** [TASK-201], [TASK-202], [TASK-208]
**Blocks:** [TASK-209]

---

### TASK-207: stale-cascade check
🟠 P1 | ⬜ TODO | Est: 3h

**Checklist:**
- [ ] Фиксация upstream-хешей при аппруве downstream
- [ ] Расхождение хеша → downstream `stale`, error
- [ ] Юнит-тесты каскада по графу

**Traces to:** [REQ-206], [DESIGN-207]
**Depends on:** [TASK-201], [TASK-202], [TASK-208]
**Blocks:** [TASK-209]

---

## Milestone 3: Git + CLI

### TASK-208: GitFacts adapter (live + injected)
🔴 P0 | ⬜ TODO | Est: 4h

**Checklist:**
- [ ] Интерфейс `GitFacts` (branch_of / approvals / blob_hash)
- [ ] live-реализация (git + gh)
- [ ] injected-реализация из `facts.json` (--no-fs)
- [ ] Юнит-тесты обеих

**Traces to:** [REQ-207], [DESIGN-204]
**Depends on:** -
**Blocks:** [TASK-206], [TASK-207], [TASK-209]

---

### TASK-209: CLI + exit codes + агрегатор
🔴 P0 | ⬜ TODO | Est: 3h

**Checklist:**
- [ ] `cli.py`: `--profile`, `SPEC_DIR`, `--no-fs`, `--format`
- [ ] Агрегатор findings → exit 0/1/2
- [ ] Text + JSON вывод
- [ ] E2E-тест на dogfood-бандле steward

**Traces to:** [REQ-207], [DESIGN-205]
**Depends on:** [TASK-203], [TASK-204], [TASK-205], [TASK-206], [TASK-207]
**Blocks:** [TASK-211], [TASK-212]

---

### TASK-210: OSS integration (repolinter / codeowners-validator)
🟡 P2 | ⬜ TODO | Est: 3-4h

**Checklist:**
- [ ] Presence-правила через repolinter-рулсет
- [ ] Ownership-сверка через codeowners-validator
- [ ] Fallback на встроенные проверки, если OSS недоступен

**Traces to:** [REQ-209], [DESIGN-206]
**Depends on:** [TASK-203], [TASK-206]
**Blocks:** -

---

## Milestone 4: CI + verification

### TASK-211: CI job + branch protection recipe
🔴 P0 | ⬜ TODO | Est: 2h

**Checklist:**
- [ ] GitHub Actions job: `gate-check --no-fs` на каждый PR
- [ ] Рецепт branch protection на downstream-папки
- [ ] Документация подключения

**Traces to:** [REQ-207], [DESIGN-205]
**Depends on:** [TASK-209]
**Blocks:** -

---

### TASK-212: verification — full-bundle e2e
🟠 P1 | ⬜ TODO | Est: 2-3h

**Description:**
Финальная проверка: прогнать `gate-check` по всему dogfood-бандлу steward, подтвердить, что чистый
бандл даёт exit 0, а инъекции нарушений (висячая трасса, approved-без-git, изменённый upstream) —
exit 1 с корректными findings.

**Checklist:**
- [ ] Golden: чистый бандл → exit 0
- [ ] Негативные фикстуры на каждый чек → exit 1
- [ ] `--no-fs` детерминизм (двойной прогон идентичен)

**Traces to:** [REQ-203], [REQ-205], [REQ-206], [REQ-207]
**Depends on:** [TASK-211]
**Blocks:** -
