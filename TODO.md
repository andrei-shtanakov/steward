# TODO — steward (создан 2026-07-26)

> Роль в экосистеме: **governance-слой** над spec-runner/Maestro — проводит спеку через DAG
> аппрувнутых артефактов, форсит порядок/трассируемость через git-PR/CODEOWNERS/CI, компилирует
> вниз делегированием. Ничего не исполняет сам.
>
> Пункты уровня команды живут здесь; микрошаги реализации — в `workstreams/<WS>/spec/tasks.md`.
> Фазовый роадмап и его обоснование — `NEXT-STEPS.md`, дизайн-решения — `spec/20-design.md`.
>
> Открытые пункты размечены инлайн-тегами `@owner:` / `@blocked_by:` / `@trigger:` по формату из
> `../_cowork_output/2026-07-26-plan-fields-and-todo-coverage-handoff.md` §3. Теги опциональны и
> исключены из ключа идентичности пункта в Robin (robin-runtime#27); отсутствие тега значит
> «неизвестно» — придумывать значение не надо. Robin проверяет **синтаксис** тегов; семантика
> (существование роли, разрешимость `@blocked_by`) — ответственность steward tooling/CI, см.
> «Role identity model».
>
> Порядок разделов = принятый порядок работ (owner, 2026-07-26): role identity → C2 → WS-005 →
> V1 → promotion гейта → закрытие WS-003.

## Текущее состояние (2026-07-26, master `72467d2`)

- ✅ **Открытых PR нет**, сьют **190 passed**, dogfood `gate-check` по собственному `spec/` чистый.
- ✅ **WS-001 · профили + `graph.py`** — `profiles/{lite,team}.yaml` как данные, загрузчик +
  валидация DAG (циклы, висячие upstream, дубли id).
- ✅ **WS-002 / C3 · `gate-check`** — completeness, traceability, status↔git, `--no-fs facts.json`,
  exit-коды 0/1/2, CI-job с dogfood'ом. Отложен только OSS-мост (REQ-209, P2).
- ✅ **C2 steward-половина** (PR #14) — `upstream_hashes` + stale-cascade (`GC-STALE` /
  `GC-STALE-UNPINNED` / `GC-STALE-KEY`).
- ✅ **WS-004 / C5 · `steward-compile`** (PR #15) — `project-yaml` и `delegation`; корневой
  `project.yaml` держится byte-equal golden-тестами. `GC-COMPILE` ловит висячий `depends_on`.
- ✅ **WS-006 · risk model** (PR #5→#8, #12) — `profiles/risk-model.yaml`, `steward risk-classify`,
  `steward waivers-check` (SHA-bound, waiver на `critical` запрещён). RD-004 verified.
- ✅ **RD-006 M2 · authority policy v1** (`5816ab5`) — `profiles/authority.yaml` как SSOT; arbiter
  вендорит `config/authority.toml` с `AUTHORITY_PINNED_SHA` (M3 сделан на его стороне).
- ✅ **DEC-007 · role identity model** (owner, 2026-07-26) — `owner_role` singular; каталог ролей
  `profiles/roles.yaml` заведён как SSOT. Код и данные пока legacy — миграция ниже, не молча.
- 🟡 **Governance gate в CI** (PR #19) — `.github/workflows/governance.yml`, пиненый
  `governance-v1`. Статус **advisory** и меняется только через evidence-based promotion (ниже).
- ⛔ **WS-003 (git approval)** — предпосылка снята ADR-ECO-004 D4; workstream закрывается как
  invalidated, работа переносится в отдельный пункт про merge-evidence (не молчаливая подмена).
- ⬜ **WS-005 (dispatcher panel)** — открыт; упирается в контракт `gate_verdicts.jsonl`.

## Правила ведения

- Выполненный пункт → `[x]` + хеш коммита/номер PR.
- Прямые коммиты в `master` запрещены: ветка `<type>/<slug>` → PR → ревью Copilot → мержит человек.
- Чужие репо не правим: нужна правка у соседа — handoff в `../prograph-vault/authored/notes/`.
- Пункт, который блокирует steward, но делается не здесь, живёт в «Ждём от других проектов».
- Legacy-формы не нормализуем молча: неоднозначность выносится в чекбокс с явным выбором.

---

## Активные задачи

### 1. Role identity model (DEC-007) — singular `owner_role` + миграция

Решение (owner, 2026-07-26): `owner_role` — ровно одна accountable роль, slug без `@`, стабильное
машинное имя. Множественность моделируется отдельными полями `reviewer_roles[]` /
`allowed_approver_roles[]`. Каталог ролей и `slug_pattern` — SSOT steward (`profiles/roles.yaml`,
заведён); dispatcher и spec-runner вендорят пиненую копию, но формы не определяют. Полный текст
и правила миграции — `spec/20-design.md` (DEC-007, «Модель идентичности ролей»).

Коллизия разрешена: `requirements` → `owner_role: product`, `reviewer_roles: [architects]`.

- [ ] Загрузчик читает `profiles/roles.yaml` и валидирует: уникальность slug, соответствие `slug_pattern`, разрешимость ссылок `owner_role`/`reviewer_roles`/`allowed_approver_roles` @owner:andrei @id:roles-catalog-loader
- [ ] Запрет удаления используемой роли без явной миграции и бампа `version` каталога @owner:andrei @id:role-deletion-guard
- [ ] `meta.py`: reader принимает legacy `"@a,@b"`, writer выпускает только canonical v2; `parse_owner_roles` уходит в legacy-путь @owner:andrei @id:meta-owner-roles-v2
- [ ] Мигрировать `profiles/{team,lite}.yaml` на singular + `reviewer_roles` (`requirements` → owner `product`, reviewer `architects`) @owner:andrei @blocked_by:steward#roles-catalog-loader @id:migrate-profiles-singular-roles
- [ ] Мигрировать frontmatter собственного `spec/*.md` (у `10-requirements.md` сейчас две роли) @owner:andrei @blocked_by:steward#roles-catalog-loader @id:migrate-spec-frontmatter-roles
- [ ] Маппинг `slug → @github-handle` на границе с CODEOWNERS (в модель ролей не тащить) @owner:andrei @id:role-slug-github-handle-mapping
- [ ] `GC-GIT-ROLE` сверять с `allowed_approver_roles`, а не с `owner_role` — сейчас проверка смешивает ownership и authorization @owner:andrei @blocked_by:steward#roles-catalog-loader @id:gc-git-role-authorization
- [ ] Handoff в dispatcher: их предложение (одна строка-роль без `@`) принято; прислать пиненую копию каталога @owner:andrei @id:dispatcher-roles-catalog-handoff

### 2. C2 (хвост): ре-вендоринг SpecMeta v2

steward-часть закрыта 2026-07-15. `_vendor/spec_meta.py` держится на `SPEC_META_CONTRACT = 1`,
`owner_role` читается временным обходом из сырого frontmatter-dict (`meta.py::parse_artifact`).
Формат принадлежит spec-runner (DEC-003). Статус там (проверено 2026-07-26): ветка
`feat/specmeta-contract-v2`, `owner_role` уже first-class (`35f47ff`), в master не влито.

⚠️ **Ask изменился после DEC-007**: handoff от 2026-07-15 просил `"@role[,@role]"`; теперь нужен
singular slug без `@`. spec-runner пишет это прямо сейчас — правка ask'а срочная, иначе v2
зафиксирует форму, от которой мы отказались.

- [ ] Довести до spec-runner пересмотренный ask: `owner_role: <slug>` singular, `@` не входит в значение @owner:andrei @id:spec-runner-owner-role-ask
- [ ] Ре-вендорить `split_frontmatter`/`SpecMeta`/`meta_from_dict` как contract v2 @owner:andrei @blocked_by:spec-runner#specmeta-v2 @trigger:"SPEC_META_CONTRACT = 2 в master spec-runner" @id:revendor-specmeta-v2
- [ ] Убрать обход «`owner_role` из сырого frontmatter-dict» в `meta.py` @owner:andrei @blocked_by:spec-runner#specmeta-v2 @id:remove-owner-role-raw-workaround
- [ ] Round-trip тест: `upstream_hashes`, `reviewer_roles`, `allowed_approver_roles` переживают v2-парсер как pass-through @owner:andrei @blocked_by:spec-runner#specmeta-v2 @id:specmeta-v2-roundtrip-test

### 3. WS-005 · gate catalog + `gate_verdicts.jsonl`

dispatcher несёт `owner_role` сквозным полем (TASK-105) и ждёт стабильный контракт, чтобы завести
verification-rule поверх verdict-записей. Maestro (WS-006 M-1) собирается писать
`logs/<ULID>/gate_verdicts.jsonl` — **схема принадлежит steward**, оба потребителя вендорят
пиненую копию. Записи обязаны ссылаться на ту же role identity model, что и артефакты (DEC-007).

- [ ] Зафиксировать схему `gate_verdicts.jsonl` (`gate_id`, `obligation`, `verdict`, `tier`, `phase`, `sha`, `risk_model_version`, `ts`, `waiver_ref?`) с версией контракта @owner:andrei @id:gate-verdicts-schema
- [ ] Каталог стабильных `gate_id` + маппинг `owner_role` → obligation @owner:andrei @blocked_by:steward#gate-verdicts-schema @id:gate-id-catalog
- [ ] Решить OQ-1 про approval-evidence: `obligation: approval` в тех же записях против нового типа правила в dispatcher @owner:andrei @blocked_by:steward#gate-verdicts-schema @id:oq-1-approval-evidence
- [ ] Read-only панель состояния бандла в dispatcher (рендер — на их стороне) @owner:andrei @blocked_by:steward#gate-verdicts-schema @id:dispatcher-bundle-status-panel

### 4. V1 · живой прогон `spec-runner plan --gated`

Не считается выполненным по факту реализации или зелёных тестов — только по evidence.
**DoD живого прогона** (owner, 2026-07-26):

- зафиксированы commit/version steward и consumer;
- сохранена точная команда;
- использован реальный, не специально упрощённый spec;
- гейт отработал без bypass и ручной правки промежуточных артефактов;
- сохранены plan output, verdict и exit code;
- `gate_id`, `owner_role`, waiver state и artifact identity коррелируются между собой;
- результат классифицирован: PASS / ожидаемый policy rejection / infrastructure ERROR;
- ссылка на evidence добавлена сюда и в run journal владельца;
- обнаруженные frictions заведены **отдельными пунктами**, а не спрятаны в описании V1.

- [ ] Выполнить живой прогон и приложить evidence по DoD выше @owner:andrei @id:v1-live-gated-run

### 5. Promotion гейта: advisory → required

Гейт бежит на каждом PR, но не блокирует, пока `governance / gate` не добавлен required-чеком в
ruleset `master`. Триггер перевода — **не календарь и не решение «пора», а evidence**.

- [ ] Разобрать накопленные false-positive / false-negative срабатывания гейта @owner:andrei @id:gate-fp-fn-triage
- [ ] Документировать рабочий break-glass / waiver path для governance-гейта (и проверить, что он работает) @owner:andrei @id:gate-break-glass-path
- [ ] Определить runtime и ownership самого гейта (кто чинит, кто владеет правилами) @owner:andrei @id:gate-runtime-ownership
- [ ] Перевести `governance / gate` в required status checks ruleset'а `master` @owner:andrei @blocked_by:steward#gate-promotion-evidence @trigger:"V1 выполнен + несколько реальных PR прошли гейт + FP/FN разобраны + break-glass path работает" @id:promote-gate-required
- [ ] Перепиновать caller на `governance-v2` @owner:andrei @blocked_by:ai-orchestrators-workspace#governance-batch-2 @trigger:"в workspace-manifest.toml [tools] объявлен governance-v2" @id:repin-caller-governance-v2

### 6. WS-003 · закрыть как invalidated + solo-compatible merge evidence

WS-003 («git approval integration»: role-resolver над CODEOWNERS, зеркало status↔git, branch
protection) **не переносится и не переписывается молча**. ADR-ECO-004 D4: «require code owner
review» структурно невыполним для соло-репо — владелец не может аппрувнуть свой PR. Зеркало
status↔git уже даёт `gate-check`; role-resolver переехал в пункт 1 как часть DEC-007.

- [x] Пометить WS-003 в `spec/40-decomposition.md` как superseded / invalidated-by ADR-ECO-004 D4, со ссылкой на пункт-заместитель (прозаический раздел «Workstreams»)
- [ ] Решить судьбу `WS-003` в compile-блоке и DAG: `git-approval-integration` всё ещё узел `steward-compile` и upstream для `dispatcher-panel-dogfood`. Удаление/замена меняет `project.yaml` → регенерация emitter'ом + обновление golden-тестов; висячий `depends_on` поймает `GC-COMPILE` @owner:andrei @blocked_by:steward#solo-merge-evidence-policy @id:ws-003-compile-dag-fate
- [ ] **Определить solo-compatible merge evidence policy** — опереться на будущие `human_merge` / `agent_merge` (I1–I4), а не имитировать невозможный owner review; `solo-mode` = значение конфига (набор аппруверов из одного + явно разрешённое и логируемое self-approval) @owner:andrei @id:solo-merge-evidence-policy
- [ ] Типизированное evidence `human_merge`: merge существует, actor ∈ humans, чеки зелёные (GitHub API) @owner:andrei @blocked_by:steward#solo-merge-evidence-policy @id:human-merge-evidence
- [ ] Evidence `agent_merge` с инвариантами I1–I4 (scoped change-class, agent-immutable authority root, adversarial verifier, ревокация) @owner:andrei @blocked_by:prograph-vault#adr-eco-004-deferred @trigger:"ADR-ECO-004 снял deferred после batch-2" @id:agent-merge-evidence

### 7. Постоянные обязательства и отложенное

- [ ] Handoff в arbiter на ре-вендоринг `config/authority.toml` + бамп `AUTHORITY_PINNED_SHA` @owner:andrei @trigger:"любая правка profiles/authority.yaml" @id:arbiter-authority-revendor-handoff
- [ ] **D2 · лицензия sdd** — спросить Dmytro Honcharuk, можно ли брать тексты шаблонов (LICENSE в репо нет); до ответа берём только идею гейтов @owner:andrei @id:sdd-license-question
- [ ] **REQ-209 · OSS-мост в gate-check** (P2): presence через repolinter, ownership через codeowners-validator; `gate-check` остаётся оркестратором @owner:andrei @id:req-209-oss-bridge

---

## Ждём от других проектов

- **spec-runner → C2**: `owner_role` + `SPEC_META_CONTRACT = 2`. В работе (ветка
  `feat/specmeta-contract-v2`). Единственная внешняя блокировка steward-кода — и туда срочно
  уходит уточнённый ask по DEC-007 (singular slug).
- **Maestro → WS-006 M-1…M-4**: guard-hook на переходах `WorkstreamStatus` + persistent
  verdict-record, аннотации advisory-fail, SHA-инвалидация вердиктов, `kind: gate-verdict` в
  evidence-ref v2. Tier не считает — консюмит JSON `risk-classify`.
- **Умбрелла → governance batch-2**: `ls-files`-скан GOV-003, split `authority-strict`/`strict`,
  meta-enforcer `check-release-drift`. После него — тег `governance-v2`.
- **dispatcher**: `owner_role` проброшен; ждёт схему verdict-записей и каталог ролей, чтобы
  вендорить пиненые копии. Свою governance-модель не строит (анти-цель).
- **arbiter**: RD-006 M3 сделан — `config/authority.toml` вендорен, `AUTHORITY_PINNED_SHA` в CI.

## НЕ делаем здесь

- ❌ Не владеем форматами, которые потребляем/эмитим: `tasks.md`/SpecMeta — spec-runner,
  `project.yaml` — Maestro. Их изменения — ask'ом наружу, не правкой у соседа.
- ❌ Не строим свою identity/RBAC и не исполняем задачи — runtime-безопасность у arbiter/ATP.
  `profiles/roles.yaml` — идентичность **governance-ролей** артефактов, не агентов.
- ❌ Не делаем dispatcher вторым SSOT: он рендерит declared-vs-observed, смысл — здесь.
- ❌ Не держим здесь микрошаги реализации — они в `workstreams/<WS>/spec/tasks.md`.
