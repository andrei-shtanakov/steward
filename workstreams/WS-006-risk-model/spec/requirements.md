---
spec_stage: requirements
status: draft
version: 1
generated_by: claude@claude-fable-5
generated_at: 2026-07-12
source_prompt_version: sha256:pending
validation: pending
approved_by: null
approved_at: null
---

# WS-006: risk model + mandatory gates — Requirements Specification

> Листовая спека workstream'а steward (формат spec-runner, делегирование по DEC-005).
> Upstream-трасса: steward REQ-001, REQ-003, NFR-001, NFR-003. Внешний драйвер:
> contracts-roadmap **RD-004** (`prograph-vault/authored/roadmaps/contracts-v1.yaml`) —
> "Risk model + mandatory gates (gates-in-DAG evaluated)".

## Executive Summary

Риск-модель — детерминированный policy-слой поверх профилей и gate-check: чистая функция
`risk(change) → tier + mandatory-set`. Профиль (`lite`/`team`) остаётся статическим baseline
команды (**floor**); риск-модель — динамическая эскалация per-change, поднимающая advisory до
mandatory, и **никогда не ослабляющая** floor. Без неё есть только два плохих режима: всё
mandatory (трение на каждом тривиальном PR) или всё advisory (декоративные гейты).

Вторая часть WS — **оценённый** (design-level, без имплементации — так сформулировано в RD-004)
скелет gates-in-DAG для Maestro `project.yaml`: гейт = guard на transition-table существующей
state-machine, не узел DAG.

---

## Requirements

#### REQ-601: Risk model as versioned data
**Priority**: 🔴 P0
**Rationale**: Классификация должна быть воспроизводимой в post-mortem и меняться через PR-review,
а не через код.
**Description**: Риск-модель — YAML-файл (`profiles/risk-model.yaml`, предложение — OQ-4), данные,
не код. Версия = sha256 файла; входит в каждый verdict-record. Профиль задаёт floor; модель может
только эскалировать (монотонность).
**Acceptance Criteria**:
- [ ] YAML грузится и валидируется; структурная ошибка → config error (exit 2)
- [ ] sha256 модели вычисляется и попадает в вывод классификации
- [ ] Понижение ниже floor профиля невыразимо ни при какой конфигурации

#### REQ-602: Три вычислимых входа классификации
**Priority**: 🔴 P0
**Rationale**: Только детерминированные, механически вычислимые оси — иначе модель не тестируется
golden-кейсами и не объяснима.
**Description**: (a) `change_class` — из путей диффа по path→class правилам (first match wins);
отдельной «семантической» осью не является. (b) `blast_radius` — single-repo / cross-repo /
ecosystem-contract через статический consumer-registry контрактов. (c) `trust_boundary` —
none / ci / deploy / secrets / external-api по path-правилам + декларируемым флагам задачи.
`agent_role` в модель **не входит** (влияет на «кто аппрувит», не на tier); полный граф
зависимостей (prograph) для blast_radius — v2.
**Acceptance Criteria**:
- [ ] Все три входа вычисляются из диффа + risk-model.yaml без ручного ввода
- [ ] Golden-кейсы на каждую ось (реальные пути экосистемы)

#### REQ-603: Fail-closed классификация
**Priority**: 🔴 P0
**Rationale**: Unmapped-путь, молча падающий в low, — обход всей модели созданием файла в
неучтённой директории.
**Description**: Путь, не покрытый ни одним правилом, получает `class: unknown`;
`class_tiers.unknown` — обязательный ключ модели со значением не ниже `medium`.
**Acceptance Criteria**:
- [ ] Golden-кейс №1: файл в неизвестной директории → tier ≥ medium
- [ ] Отсутствие `class_tiers.unknown` в модели → config error

#### REQ-604: Комбинатор max, без весов
**Priority**: 🔴 P0
**Rationale**: Weighted scores непрозрачны и необъяснимы в post-mortem; max монотонен.
**Description**: `tier = max(floor(profile), lookup(change_class), lookup(blast_radius),
lookup(trust_boundary))`. Вывод обязан указывать доминирующую ось (argmax).
**Acceptance Criteria**:
- [ ] JSON-вывод содержит `dominant_axis`
- [ ] Property-тест монотонности: добавление файла в дифф не понижает tier

#### REQ-605: Двухфазная оценка (ex-ante / ex-post)
**Priority**: 🔴 P0
**Rationale**: На spawn-фазе диффа не существует — агент только пойдёт делать изменение;
«риск один раз на preflight» невычислим.
**Description**: **ex-ante** tier — из декларированного scope задачи (globs `workstream.scope`
в `project.yaml`, вход существует уже сегодня) — гейтит spawn (READY→RUNNING). **ex-post** tier —
из фактического диффа `base..head` — гейтит merge и PR-переходы. Фактический дифф вне
декларированного scope → `scope_violation`: эскалация до `max(tier, high)` + флаг в записи
(мост к RD-006 Capability/Authority).
**Acceptance Criteria**:
- [ ] Обе фазы дают Classification с полем `phase`
- [ ] Фикстура scope-violation: эскалация + флаг

#### REQ-606: Verdict-контракт и fail-closed для mandatory
**Priority**: 🔴 P0
**Rationale**: Fail-closed только на critical — дыра: mandatory-гейт на medium/high обходится
«проверка не запустилась».
**Description**: `verdict ∈ {pass, fail, waived, error}`; `obligation ∈ {mandatory, advisory}`
вычисляется из `tier_gates` (require-список — функция tier'а) поверх floor. Для **mandatory**
отсутствие verdict'а или `error` ⇒ fail — **на любом tier'е**. Advisory-fail ⇒ аннотация в
run-metadata + verdict-record, граф не режется. Critical отличается не fail-closed'ом, а
human transition approval и запретом waiver'ов (REQ-609).
**Acceptance Criteria**:
- [ ] Семантическая таблица verdict × obligation зафиксирована в design
- [ ] Негативные фикстуры: missing-verdict и error на mandatory → block

#### REQ-607: SHA-привязка и инвалидация verdict'ов
**Priority**: 🔴 P0
**Rationale**: TOCTOU: verdict, полученный на preflight, не значит ничего для доехавшего позже
коммита.
**Description**: Verdict (и waiver) валиден только для head SHA, для которого вычислен. Новый
коммит инвалидирует все verdict'ы; tier пересчитывается (классификация — дешёвая чистая функция).
**Acceptance Criteria**:
- [ ] Verdict-record содержит `sha`; guard сверяет его с текущим head
- [ ] Фикстура: новый коммит → старый verdict отвергнут

#### REQ-608: Персистентный verdict-record, адресуемый как evidence_ref
**Priority**: 🔴 P0 (запись) / 🟠 P1 (типизированный kind)
**Rationale**: Узел DAG давал run-record бесплатно; guard — нет. Без персистентной записи
аудируемость теряется молча. Параллельный аудит-формат строить нельзя — RD-003 уже дал
типизированный указатель.
**Description**: Каждая оценка guard'а пишет запись `{gate_id, obligation, verdict, tier, sha,
risk_model_version, ts}`. Запись адресуема через evidence_ref: v1 — существующими kind'ами
(`log` для JSONL в Maestro `logs/<ULID>/`, `artifact` для CI-вердиктов steward), без правки
чужого контракта. v2 — предложение `kind: gate-verdict` в контракт Maestro (handoff, OQ-1).
**Acceptance Criteria**:
- [ ] Shape записи зафиксирован в design
- [ ] v1-ссылка выражается существующими kind'ами evidence-ref v1

#### REQ-609: Waiver-семантика
**Priority**: 🟠 P1
**Rationale**: Без явного waiver'а получается либо обход через «попроси владельца», либо
жёсткость, которую сломают выключением гейтов.
**Description**: Waiver = файл с frontmatter в репо (`spec/waivers/`), мержится PR'ом
роли-владельца гейта — git-primary, никакого нового RBAC (steward NFR-003, NFR-005). Поля:
`{gate_id, sha, waived_by, reason}`. SHA-bound: новый коммит убивает waiver. На `critical`
waiver **запрещён** в v1 (OQ-3).
**Acceptance Criteria**:
- [ ] `waived` verdict требует существующего waiver-файла с совпадающим sha
- [ ] Waiver на critical-гейте → finding (error)

#### REQ-610: CLI `steward risk-classify`
**Priority**: 🔴 P0
**Rationale**: Одна точка истины: Maestro консюмит готовый вердикт, сам tier не вычисляет.
**Description**: `steward risk-classify (--declared scope.json | --diff base..head)
[--risk-model path] [--no-fs facts.json] --format json`. Детерминизм как у gate-check
(инъецируемые входы). Exit: 0 — классифицировано, 2 — config error. Классификация не «падает»
findings'ами — это не проверка, а функция.
**Acceptance Criteria**:
- [ ] Одинаковый вход → байт-идентичный JSON (двойной прогон)
- [ ] Выходной shape зафиксирован в design (DESIGN-610)

#### REQ-611: Gates-in-DAG skeleton — evaluated
**Priority**: 🔴 P0
**Rationale**: Это evidence RD-004: «gates-in-DAG skeleton evaluated for project.yaml» —
оценка, не имплементация.
**Description**: Дизайн-уровневый скелет для Maestro `project.yaml`: гейт = guard на переходах
существующей state-machine workstream'а (PENDING→…→DONE), **не** узел DAG (второй планировщик
внутри DAG — реальная угроза); require-список = функция tier'а (`obligation: by_tier`); fallback
при отсутствии verdict'а = fail для mandatory. Результат — оценка: что ложится на текущий
Maestro, что требует его изменений (handoff-список), без кода.
**Acceptance Criteria**:
- [ ] Скелет + evaluation findings в design (DESIGN-611)
- [ ] Maestro-side работы оформлены как handoff-список (DESIGN-612)
