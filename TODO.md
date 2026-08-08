# TODO — steward (создан 2026-07-26)

> Роль в экосистеме: **governance-слой** над spec-runner/Maestro — проводит спеку через DAG
> аппрувнутых артефактов, форсит порядок/трассируемость через git-PR/CODEOWNERS/CI, компилирует
> вниз делегированием. Ничего не исполняет сам.
>
> Пункты уровня команды живут здесь; микрошаги реализации — в `workstreams/<WS>/spec/tasks.md`.
> Фазовый роадмап и его обоснование — `NEXT-STEPS.md`, дизайн-решения — `spec/20-design.md`.
>
> Пункты могут быть размечены опциональными тегами на строке чекбокса:
> `@owner:<principal>` / `@blocked_by:<reference>` / `@trigger:"…"` /
> `@id:<node-id>`. Канонические владельцы: `github:<login>`,
> `github-team:<org>/<team>`, `repo:<manifest-key>` или `TBD`; отсутствующий
> `@owner` (`missing`) отличается от явно отложенного `@owner:TBD`. Канонический
> блокер — `todo://<repo>/<id>`, legacy `<repo>#<slug>` поддерживается переходно.
> Теги исключены из ключа идентичности пункта в Robin (robin-runtime#27);
> отсутствие тега значит «неизвестно» — придумывать значение не надо.
>
> `@id:<node-id>` — канонический идентификатор пункта (ADR-ECO-005 PF-2B): строчная грамматика
> `[a-z0-9][a-z0-9._-]{0,63}`, из него строится URI `todo://steward/<id>`.
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

- [ ] Загрузчик читает `profiles/roles.yaml` и валидирует: уникальность slug, соответствие `slug_pattern`, разрешимость ссылок `owner_role`/`reviewer_roles`/`allowed_approver_roles` @owner:github:andrei-shtanakov @id:roles-catalog-loader
- [ ] Запрет удаления используемой роли без явной миграции и бампа `version` каталога @owner:github:andrei-shtanakov @id:role-deletion-guard
- [ ] `meta.py`: reader принимает legacy `"@a,@b"`, writer выпускает только canonical v2; `parse_owner_roles` уходит в legacy-путь @owner:github:andrei-shtanakov @id:meta-owner-roles-v2
- [ ] Мигрировать `profiles/{team,lite}.yaml` на singular + `reviewer_roles` (`requirements` → owner `product`, reviewer `architects`) @owner:github:andrei-shtanakov @blocked_by:todo://steward/roles-catalog-loader @id:migrate-profiles-singular-roles
- [ ] Мигрировать frontmatter собственного `spec/*.md` (у `10-requirements.md` сейчас две роли) @owner:github:andrei-shtanakov @blocked_by:todo://steward/roles-catalog-loader @id:migrate-spec-frontmatter-roles
- [ ] Маппинг `slug → @github-handle` на границе с CODEOWNERS (в модель ролей не тащить) @owner:github:andrei-shtanakov @id:role-slug-github-handle-mapping
- [ ] `GC-GIT-ROLE` сверять с `allowed_approver_roles`, а не с `owner_role` — сейчас проверка смешивает ownership и authorization @owner:github:andrei-shtanakov @blocked_by:todo://steward/roles-catalog-loader @id:gc-git-role-authorization
      2026-08-08: GC-GIT-ROLE запускается только при авторитетных role-facts
      (approvals: None = unavailable, live всегда None) — ложные got:none в live
      сняты; полный fix — после DEC-007 mapping.
- [ ] Handoff в dispatcher: их предложение (одна строка-роль без `@`) принято; прислать пиненую копию каталога @owner:github:andrei-shtanakov @id:dispatcher-roles-catalog-handoff

### 2. C2 (хвост): ре-вендоринг SpecMeta v2

steward-часть закрыта 2026-07-15. `_vendor/spec_meta.py` держится на `SPEC_META_CONTRACT = 1`,
`owner_role` читается временным обходом из сырого frontmatter-dict (`meta.py::parse_artifact`).
Формат принадлежит spec-runner (DEC-003). Статус там (проверено 2026-07-26): ветка
`feat/specmeta-contract-v2`, `owner_role` уже first-class (`35f47ff`), в master не влито.

⚠️ **Ask изменился после DEC-007**: handoff от 2026-07-15 просил `"@role[,@role]"`; теперь нужен
singular slug без `@`. spec-runner пишет это прямо сейчас — правка ask'а срочная, иначе v2
зафиксирует форму, от которой мы отказались.

- [ ] Довести до spec-runner пересмотренный ask: `owner_role: <slug>` singular, `@` не входит в значение @owner:github:andrei-shtanakov @id:spec-runner-owner-role-ask
- [ ] Ре-вендорить `split_frontmatter`/`SpecMeta`/`meta_from_dict` как contract v2 @owner:github:andrei-shtanakov @blocked_by:spec-runner#specmeta-v2 @trigger:"SPEC_META_CONTRACT = 2 в master spec-runner" @id:revendor-specmeta-v2
- [ ] Убрать обход «`owner_role` из сырого frontmatter-dict» в `meta.py` @owner:github:andrei-shtanakov @blocked_by:spec-runner#specmeta-v2 @id:remove-owner-role-raw-workaround
- [ ] Round-trip тест: `upstream_hashes`, `reviewer_roles`, `allowed_approver_roles` переживают v2-парсер как pass-through @owner:github:andrei-shtanakov @blocked_by:spec-runner#specmeta-v2 @id:specmeta-v2-roundtrip-test

### 3. WS-005 · gate catalog + `gate_verdicts.jsonl`

dispatcher несёт `owner_role` сквозным полем (TASK-105) и ждёт стабильный контракт, чтобы завести
verification-rule поверх verdict-записей. Maestro (WS-006 M-1) собирается писать
`logs/<ULID>/gate_verdicts.jsonl` — **схема принадлежит steward**, оба потребителя вендорят
пиненую копию. Записи обязаны ссылаться на ту же role identity model, что и артефакты (DEC-007).

- [x] **Governance-бандл WS-005 заведён и АППРУВНУТ насквозь** (`workstreams/WS-005-gate-verdicts/spec/`, профиль `team-exp`, линтуется в CI): бандл — PR #28; аппрув-след по DAG-порядку — PR #29 (L1) / #30 (L2) / #31 (L3), каждое ребро запиновано настоящим blob-хешом, stale-каскад покрывает полный DAG @owner:github:andrei-shtanakov @id:ws005-bundle-approvals
- [x] Зафиксировать схему `gate_verdicts.jsonl` с версией контракта — `contracts/gate-verdicts/v1/` (schema+README+5 фикстур) + emitter `gate-check --emit-verdicts`; поля obligation/tier/phase/risk_model_version/waiver_ref объявлены reserved до каталога (PR #33) @owner:github:andrei-shtanakov @id:gate-verdicts-schema
- [x] Каталог стабильных `gate_id` + каталог правил obligation: v1 включает 19 active/quality + GC-APPROVAL-MISSING declared/approval с матрицей применимости к owner_role / стадии; три гарантии полноты — emitter-гейт на active, sync через Finding-конструкторы, обратная сверка; obligation активирован на эмиссии @owner:github:andrei-shtanakov @id:gate-id-catalog — steward#50
      Unblocked by steward#33 (gate-verdicts-schema доставлен; PF-BLOCKER-STALE
      снят 2026-08-06). Блокер `oq-1-approval-evidence` РЕШЁН 2026-08-08 и
      снят — каталог actionable. Порядок сработал как задумано: решение
      определило состав каталога, минимум которого (владелец, 2026-08-08):
      (1) словарь `obligation: quality | approval`; (2) стабильные
      `GC-APPROVAL-*` gate_id; (3) связь gate → obligation; (4) применимость
      правила к owner_role и стадии — НЕ прямой маппинг owner_role →
      obligation единственной функцией (одной роли может соответствовать
      несколько obligations), а каталог правил с полями `owner_role` /
      `applicable_roles` + `obligation`. Заголовок пункта обновлён
      соответственно (был «маппинг owner_role → obligation»).
- [x] Решить OQ-1 про approval-evidence: `obligation: approval` в тех же записях против нового типа правила в dispatcher @owner:github:andrei-shtanakov @id:oq-1-approval-evidence — РЕШЕНО владельцем 2026-08-08, вариант A с поправкой по схеме
      **Решение (формулировка владельца):** approval enforcement и его
      findings живут в steward `gate_verdicts.jsonl` с `obligation: approval`.
      Steward получает merge/review-факты и применяет solo-compatible policy;
      dispatcher НЕ вводит отдельного approval-rule и только классифицирует
      прочитанные findings (ARCH-C3/D1: steward — enforcer, dispatcher —
      read model). Положительные типизированные `human_merge`/`agent_merge`
      НЕ добавляются в закрытую схему v1 — переносимый audit-record требует
      отдельного evidence-контракта или gate-verdicts/v2.
      Проверено по SCHEMA.json: v1 = ровно header/artifact/finding, все с
      `additionalProperties: false`; finding требует
      `kind/gate_id/verdict(fail|warn)/artifact/message` — позитивному
      merge-evidence в v1 места нет; зарезервированное `obligation`
      позволяет пометить нарушение (`GC-APPROVAL-MISSING`, verdict fail,
      obligation approval), но не добавить новый тип записи.
      Типизированные human_merge/agent_merge (ADR-ECO-004 D4) живут ВНУТРИ
      fact-provider'а steward уже сейчас; наружу — только findings.
      Отклонённый вариант B (rule в dispatcher) ломал бы ARCH-C3 манифеста
      WS-005 и раздваивал производителей вердиктов (Maestro как второй
      потребитель ledger не видел бы approval вовсе).
      NB: в экосистеме два разных «OQ-1» (второй — WS-006 про Maestro-
      контракт); ссылаться на этот — только по @id.
      Unblocked by steward#33 (2026-08-06). Первый в очереди секции: его ответ
      питает дизайн каталога (obligation-маппинг). Контекст: WS-003
      инвалидирован ADR-ECO-004 D4; замена — solo-compatible merge evidence
      на типизированных human_merge/agent_merge.
- [x] Approval policy enforcement: fact-provider merge/review-фактов + solo-compatible policy → эмит GC-APPROVAL-MISSING @owner:github:andrei-shtanakov @id:approval-policy-enforcement
      Резолюция @id:oq-1-approval-evidence (steward#49) установила, что approval
      enforcement живёт в steward `gate_verdicts.jsonl` с `obligation: approval`.
      Steward получает merge/review-факты через `steward approval-facts` (GitHub API,
      mergedBy) и применяет solo-compatible policy; провенанс актора локально недоступен
      принципиально, но GitHub возвращает authoritatively typed human_merge (mergedBy).
      Классификация закрыта: unknown/agent НЕ проходят release (E-01/E-02). Dispatcher
      только классифицирует прочитанные findings (ARCH-C3/D1: steward — enforcer, dispatcher
      — read model). GC-APPROVAL-MISSING active (каталог v2, 20 gates), GC-GIT-ROLE unavailable-контур
      (approvals: None = unavailable, live всегда None). Флаг `--stage` канонический, `--arch-stage`
      deprecated. PR #51
- [ ] Read-only панель состояния бандла в dispatcher (рендер — на их стороне) @owner:github:andrei-shtanakov @id:dispatcher-bundle-status-panel
      Unblocked by steward#33 (2026-08-06). Acceptance-сверка с фактической
      панелью dispatcher (2026-08-06): 5/6 критериев подтверждены кодом —
      6 состояний ARCH-D2 (`core/governance.py` BundleState), отсутствующее/
      невалидное evidence НЕ читается clean (no-data / unreadable / unknown
      freshness = stale-grade), источник `.steward/gate_verdicts.jsonl`,
      строго read-only (единственный GET `/api/projects/{name}/governance`),
      4 файла тестов включая live-smoke. **Точный остаток пункта:**
      checked_by-evidence в панели не материализован — его нет ни в модели
      `VerdictFinding`, ни в истории dispatcher; появится осмысленно после
      каталога gate_id (reserved-поля obligation/tier/phase уже в модели).

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

- [ ] Выполнить живой прогон и приложить evidence по DoD выше @owner:github:andrei-shtanakov @id:v1-live-gated-run

### 5. Promotion гейта: advisory → required

Гейт бежит на каждом PR, но не блокирует, пока `governance / gate` не добавлен required-чеком в
ruleset `master`. Триггер перевода — **не календарь и не решение «пора», а evidence**.

- [ ] Разобрать накопленные false-positive / false-negative срабатывания гейта @owner:github:andrei-shtanakov @id:gate-fp-fn-triage
- [ ] Документировать рабочий break-glass / waiver path для governance-гейта (и проверить, что он работает) @owner:github:andrei-shtanakov @id:gate-break-glass-path
- [ ] Определить runtime и ownership самого гейта (кто чинит, кто владеет правилами) @owner:github:andrei-shtanakov @id:gate-runtime-ownership
- [ ] Перевести `governance / gate` в required status checks ruleset'а `master` @owner:github:andrei-shtanakov @trigger:"V1 выполнен + несколько реальных PR прошли гейт + FP/FN разобраны + break-glass path работает" @id:promote-gate-required
- [ ] Перепиновать caller на `governance-v2` @owner:github:andrei-shtanakov @trigger:"в workspace-manifest.toml [tools] объявлен governance-v2" @id:repin-caller-governance-v2

  Оба пункта — evidence/event watches, не ссылки на принятые plan-узлы:
  `steward#gate-promotion-evidence` и
  `ai-orchestrators-workspace#governance-batch-2` отсутствуют в TODO/issues владельцев.
  Готовность полностью определяется проверяемыми triggers на строках пунктов.

### 6. WS-003 · закрыть как invalidated + solo-compatible merge evidence

WS-003 («git approval integration»: role-resolver над CODEOWNERS, зеркало status↔git, branch
protection) **не переносится и не переписывается молча**. ADR-ECO-004 D4: «require code owner
review» структурно невыполним для соло-репо — владелец не может аппрувнуть свой PR. Зеркало
status↔git уже даёт `gate-check`; role-resolver переехал в пункт 1 как часть DEC-007.

- [x] Пометить WS-003 в `spec/40-decomposition.md` как superseded / invalidated-by ADR-ECO-004 D4, со ссылкой на пункт-заместитель (прозаический раздел «Workstreams»)
- [ ] Решить судьбу `WS-003` в compile-блоке и DAG: `git-approval-integration` всё ещё узел `steward-compile` и upstream для `dispatcher-panel-dogfood`. Удаление/замена меняет `project.yaml` → регенерация emitter'ом + обновление golden-тестов; висячий `depends_on` поймает `GC-COMPILE` @owner:github:andrei-shtanakov @blocked_by:todo://steward/solo-merge-evidence-policy @id:ws-003-compile-dag-fate
- [ ] **Определить solo-compatible merge evidence policy** — опереться на будущие `human_merge` / `agent_merge` (I1–I4), а не имитировать невозможный owner review; `solo-mode` = значение конфига (набор аппруверов из одного + явно разрешённое и логируемое self-approval) @owner:github:andrei-shtanakov @id:solo-merge-evidence-policy
- [ ] Типизированное evidence `human_merge`: merge существует, actor ∈ humans, чеки зелёные (GitHub API) @owner:github:andrei-shtanakov @blocked_by:todo://steward/solo-merge-evidence-policy @id:human-merge-evidence
- [ ] Evidence `agent_merge` с инвариантами I1–I4 (scoped change-class, agent-immutable authority root, adversarial verifier, ревокация) @owner:github:andrei-shtanakov @blocked_by:prograph-vault#adr-eco-004-deferred @trigger:"ADR-ECO-004 снял deferred после batch-2" @id:agent-merge-evidence

### 6b. ADR behaviour-lifecycle · Фаза 1 (slice утверждён 2026-08-02)

> ADR: `../_cowork_output/decisions/2026-08-02-adr-behaviour-architecture-lifecycle.md`;
> golden run: `../_cowork_output/golden-run-ws005/`. `team`/`lite` не меняются —
> всё в экспериментальном профиле `team-exp`.

- [x] **Slice PR-1**: узел `behaviour-spec` в `profiles/team-exp.yaml` + гейты `GC-BEH-TRACE` / `GC-BEH-COVERAGE` (verification obligation chain, FL-03) / `GC-CHECK-PLANNED` (PR #25, merged `9bbcd1b`) @owner:github:andrei-shtanakov @id:behaviour-spec-gates
- [x] **Slice PR-2**: derived trace matrix — `gate-check --trace-matrix` (FL-09) + live stale-тест с настоящими blob hashes (FL-10) (PR #26, merged `c18d3cd`) @owner:github:andrei-shtanakov @id:behaviour-trace-matrix-stale
- [x] **Slice PR-3**: `GC-ARCH-*` гейты + первое живое evidence (Tasks 1-4 + choreography 3b) @owner:github:andrei-shtanakov @id:behaviour-arch-gates
  Схемы prograph завендорены пиненой копией (`contracts/prograph-intended-graph/v1`,
  `contracts/prograph-conformance-report/v1`; copy-integrity PR-гейт отдельно от
  upstream-drift). `GC-ARCH-SCHEMA` / `GC-ARCH-EVIDENCE` / `GC-ARCH-CONFORMANCE` —
  offline-потребители отчёта; декларативная stage policy
  (`profiles/arch-policy.yaml`, `--arch-stage authoring|release`, постоянные unknown
  проходят release по причине); D9 self-freshness: ancestor + path-scoped diff
  (петля commit==HEAD исключена). Отдельный узел `architecture` в DAG не понадобился
  (D1: file-presence activation). WS-005: манифест получил `evidence` на всех
  интерфейсах (D7), первый настоящий `conformance-report.json` закоммичен рядом
  (umbrella snapshot #8, provenance commit = ancestor); dogfood: authoring exit 0,
  release exit 1 ровно на I-01/I-02 — реальный остаток реализации workstream'а
  (остаток закрыт 2026-08-04: dispatcher#117 → I-02 conformant; steward#40 →
  I-01 manual-evidence с capability-триггером возврата; release 0/0 exit 0).
- [x] Scheduled workspace-обязательство (вне CI этого репо): upstream-drift обеих вендоренных prograph-схем + freshness манифест/отчёта WS-005; отсутствует/просрочено ⇒ unknown, не clean @owner:github:andrei-shtanakov @id:arch-evidence-freshness-watch — исполнено в devtools (`todo://devtools/arch-evidence-freshness-watch`, devtools#26); приёмка 2026-08-06
      Сенсор: `devtools/check-arch-evidence-freshness.py` + launchd (ЯВНО
      interim до CI devtools); durable статус-файл с `next_expected_at`;
      `unknown` выводит ЧИТАТЕЛЬ (просрочка/отсутствие статуса), сенсор пишет
      только clean|drift|stale|unavailable. Приёмка 2026-08-06: два штатных
      прогона по расписанию, оба clean — при том что prograph master уехал с
      пина (8deb730 → efb4a5d): clean честный, сравниваются файлы контрактной
      поверхности, не коммиты. Красное → inbox-issue сюда с дедуп-ключом
      `arch-evidence-freshness-watch:<class>`.
- [x] Scheduled-workflow arch-evidence-freshness в CI этого репо — расписание к владельцу обязательства @owner:github:andrei-shtanakov @id:arch-evidence-freshness-schedule — PR #43; приёмка пройдена 2026-08-08 (триггер «второй штатный cron-прогон» сработал)
      **КОД ДОСТАВЛЕН PR #43; приёмка пройдена 2026-08-08** (итог — блок
      «ПРИЁМКА ПРОЙДЕНА» ниже; абзац ниже — исторический контекст периода
      ожидания, сохранён как provenance).
      `.github/workflows/arch-evidence-freshness.yml` смержен PR #43 (2026-08-06);
      реализационной работы не осталось. Открыт ровно потому, что DoD пункта —
      наблюдаемые прогоны (см. «Приёмка» ниже), а не merge; закрывать по факту
      мержа — ровно та подмена доказательства, против которой пункт и написан.
      Сессия B перехода launchd→CI (дизайн принят владельцем 2026-08-06;
      прецедент — advisory-watcher dispatcher#110: владелец вендоренных копий
      hostит свою вахту). ИНВАРИАНТ: это scheduled-НАБЛЮДЕНИЕ (guarantee B,
      two-contract-guarantees), НЕ PR-гейт — workflow никогда не добавлять
      required-чеком; прежняя формулировка «вне CI этого репо» у пункта выше
      значила именно «вне PR-гейта», не «вне Actions вообще».
      Механика: daily cron (05:40 UTC = каденция launchd-приёмки) +
      workflow_dispatch (вход synthetic=drift — контролируемый non-clean
      правкой вендоренной копии в ephemeral-чекауте); multi-checkout
      steward/prograph HEAD + devtools@пин; сенсор devtools БЕЗ изменений.
      Crash-envelope `arch-evidence-freshness-run/v1` публикуется через
      `if: always()`: сенсор domain-статус не подделывает (краш = нет
      status.json), оркестрация честно фиксирует падение исполнения.
      Публикация: job summary + artifact (status.json + envelope) +
      check-run на steward SHA + dedup inbox-issue (эскалация сенсора,
      ключ `arch-evidence-freshness-watch:<class>`). PAT не нужен: репо
      публичные, GITHUB_TOKEN с issues/checks write. Actions — полные SHA.
      Приёмка (2 из 3 закрыто на 2026-08-07):
      ✅ контролируемый non-clean — dispatch synthetic=drift, run 31092873091
         (2026-08-06): красный ТОЛЬКО на шаге `verdict`, ПОСЛЕ публикации
         (envelope + artifact + check-run — success); inbox-issue #44
         `arch-evidence-freshness-watch:drift` создан по дедуп-ключу.
      ✅ штатный cron-прогон №1 — run 31155437323 (2026-08-07 06:51 UTC,
         event=schedule): status clean, `next_expected_at` 2026-08-08,
         envelope `execution_status: completed`, `domain_exit: 0`. Задержка
         06:51 vs 05:40 — штатный дрейф очереди GitHub cron, не отказ.
      ⬜ штатный cron-прогон №2 — ожидается 2026-08-08 ~05:40 UTC. Это
         единственное, чего ждёт пункт. Проверять:
         `gh run list --workflow arch-evidence-freshness.yml`
         + артефакт `arch-evidence-freshness-status`.
      После приёмки — сессия C: независимый reader freshness
      runs (Robin/dispatcher — не самонаблюдение; 60-дневная cron-ловушка),
      затем снятие launchd (`make arch-freshness-unschedule`) и уборка
      install-целей в devtools (launchd снят владельцем 2026-08-08,
      уборка — devtools#38; reader — robin-runtime#42).
      ПРИЁМКА ПРОЙДЕНА 2026-08-08, все три пункта: (1) smoke
      workflow_dispatch 2026-08-06 — success + artifact; (2) контролируемый
      non-clean (synthetic=drift) — run красный ровно по domain exit сенсора,
      envelope/artifact опубликованы, inbox-issue #44 создан по дедуп-ключу
      с CI-раннера и НЕМЕДЛЕННО закрыт (открытый синтетический drift-issue
      подавлял бы дедупом эскалацию настоящего дрейфа тем же ключом);
      (3) два штатных cron-прогона — 2026-08-07 06:51Z и 2026-08-08 06:23Z,
      оба success (задержка 40–70 мин от 05:40 — нормальная очередь
      scheduled-событий GitHub, учитывать в deadline читателя). Сессия C:
      независимый reader — inbox-запрос в robin-runtime; снятие launchd —
      действие владельца на машине-хосте.

### 7. Постоянные обязательства и отложенное

- [ ] Handoff в arbiter на ре-вендоринг `config/authority.toml` + бамп `AUTHORITY_PINNED_SHA` @owner:github:andrei-shtanakov @trigger:"любая правка profiles/authority.yaml" @id:arbiter-authority-revendor-handoff
- [ ] **D2 · лицензия sdd** — спросить Dmytro Honcharuk, можно ли брать тексты шаблонов (LICENSE в репо нет); до ответа берём только идею гейтов @owner:github:andrei-shtanakov @id:sdd-license-question
- [ ] **REQ-209 · OSS-мост в gate-check** (P2): presence через repolinter, ownership через codeowners-validator; `gate-check` остаётся оркестратором @owner:github:andrei-shtanakov @id:req-209-oss-bridge

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
