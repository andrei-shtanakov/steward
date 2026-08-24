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

- [x] Загрузчик читает `profiles/roles.yaml` и валидирует: уникальность slug, соответствие `slug_pattern`, разрешимость ссылок `owner_role`/`reviewer_roles`/`allowed_approver_roles` @owner:github:andrei-shtanakov @id:roles-catalog-loader — PR этой ветки (D1+D3+D5): `src/steward/roles.py` fail-closed, canonical-v2 поля в `ArtifactMeta` (legacy reader сохранён, без молчаливого выбора владельца), разрешимость frontmatter-ссылок в gate-check (exit 2), composition-pin `roles.yaml`; `roles.yaml` — обязательный сосед профиля на каждом прогоне
- [x] Запрет удаления используемой роли без явной миграции и бампа `version` каталога @owner:github:andrei-shtanakov @id:role-deletion-guard — composition-pin (PR-1) + разрешимость ссылок во всех загрузчиках (профили PR-2, frontmatter PR-1, gate-catalog PR-1): удаление используемой роли ломает загрузку громко; assignments-файл появится в PR-3 и валидируется так же
- [x] `meta.py`: reader принимает legacy `"@a,@b"`, writer выпускает только canonical v2; `parse_owner_roles` уходит в legacy-путь @owner:github:andrei-shtanakov @id:meta-owner-roles-v2 — reader canonical+legacy (PR-1), все данные steward canonical (PR-2); писателя frontmatter у steward нет — canonical закреплён данными и строгим profile-loader; legacy-путь останется до SpecMeta v2 (§2)
- [x] Мигрировать `profiles/{team,lite}.yaml` на singular + `reviewer_roles` (`requirements` → owner `product`, reviewer `architects`) @owner:github:andrei-shtanakov @blocked_by:todo://steward/roles-catalog-loader @id:migrate-profiles-singular-roles — PR этой ветки: team/lite/team-exp canonical, collision rulings applied
- [x] Мигрировать frontmatter собственного `spec/*.md` (у `10-requirements.md` сейчас две роли) @owner:github:andrei-shtanakov @blocked_by:todo://steward/roles-catalog-loader @id:migrate-spec-frontmatter-roles — PR этой ветки: оба бандла; WS-005 пины пересчитаны в topo-порядке, GC-STALE 0 на итоговом дереве
- [x] Маппинг `slug → @github-handle` на границе с CODEOWNERS (в модель ролей не тащить) @owner:github:andrei-shtanakov @id:role-slug-github-handle-mapping — PR этой ветки (D6): `profiles/role-assignments.yaml` + `src/steward/roleassignments.py`, identity `github:<login>` → roles, fail-closed (грамматика, дубли, неразрешимые slugs); единственное место, где identity приобретает роль — заявленная роль внутри approval-фактов никогда не авторитетна
- [x] `GC-GIT-ROLE` сверять с `allowed_approver_roles`, а не с `owner_role` — сейчас проверка смешивает ownership и authorization @owner:github:andrei-shtanakov @blocked_by:todo://steward/roles-catalog-loader @id:gc-git-role-authorization — PR этой ветки (D7): `Approval` несёт только identity (провайдеры не могут заявлять роль), `check_status_git` резолвит approvers через `role-assignments.yaml` и сверяет с `allowed_approver_roles` (по умолчанию — `owner_role`, явный список ЗАМЕНЯЕТ дефолт); только node-level — precedence инстанс-уровневого `allowed_approver_roles` над node ещё не решён владельцем (см. комментарий в коде), reviewer_roles НЕ энфорсится. Live approvals остаются `None` до появления facts-источника — гейт вживую не срабатывает
      2026-08-08: GC-GIT-ROLE запускается только при авторитетных role-facts
      (approvals: None = unavailable, live всегда None) — ложные got:none в live
      сняты; полный fix — после DEC-007 mapping.
- [x] Handoff в dispatcher: их предложение (одна строка-роль без `@`) принято; прислать пиненую копию каталога @owner:github:andrei-shtanakov @id:dispatcher-roles-catalog-handoff — dispatcher#128 (2026-08-08): пиненая копия roles.yaml @ `b79c858` + канон к следующему перепину gate-check (canonical-профиль в смоуке, реальные слаги в sibling roles.yaml, role-assignments только для non-solo, identity без case-folding); ход за dispatcher
- [x] 15-behaviour-spec.md несёт вложенные `structural_coverage[].obligation.owner_role: "@architects"` — РЕШЕНО 2026-08-09 (DEC-009, `spec/20-design.md`): канонический slug без `@`, без множественности, резолвится через roles.yaml (неизвестный slug = config error), поле не переименовывается. Осталась ИМПЛЕМЕНТАЦИЯ: миграция значений в 15-behaviour-spec (×2, с пересчётом пин-каскада design/acceptance/decomposition), резолюция slug'а в валидации structural_coverage — СДЕЛАНО в PR этой ветки: значения canonical (architects ×2), пин-каскад пересчитан (оракул 0/0), резолюция в unresolved_role_refs (config error exit 2 с индексом entry) @owner:github:andrei-shtanakov @id:structural-coverage-owner-role-form

### 2. C2 (хвост): ре-вендоринг SpecMeta v2 — ЗАКРЫТ ЦЕЛИКОМ 2026-08-09

steward-часть была закрыта 2026-07-15; полный ре-вендоринг — этой веткой, PR #61.
Формат принадлежит spec-runner (DEC-003).

- [x] Довести до spec-runner пересмотренный ask: `owner_role: <slug>` singular, `@` не входит в значение @owner:github:andrei-shtanakov @id:spec-runner-owner-role-ask — spec-runner#125 (2026-08-08): старый ask 2026-07-15 явно отменён, грамматика слага и правило «reviewer_roles/allowed_approver_roles только после согласования контракта» переданы; ход за spec-runner
- [x] Ре-вендорить `split_frontmatter`/`SpecMeta`/`meta_from_dict` как contract v2 @owner:github:andrei-shtanakov @id:revendor-specmeta-v2 — PR этой ветки: пин spec-runner тег `v2.22.0` (`de9a31c4`, чистый клон), copy-integrity проверена AST-сверкой байт-в-байт всех шести символов + всех модульных констант против апстрима; scope расширен (`SpecMetaError`, `canonical_fields`) — write-side (`meta_to_dict`/`_render`/`write_spec`) и профильная система spec-runner сознательно НЕ вендорятся (steward их не использует; DEC-008 — steward только валидатор, не переписывает артефакты)
- [x] Убрать обход «`owner_role` из сырого frontmatter-dict» в `meta.py` @owner:github:andrei-shtanakov @id:remove-owner-role-raw-workaround — СДЕЛАНО в PR этой ветки: `parse_artifact` читает `base.owner_role` (первоклассное поле v2), `SpecMetaError` транслируется в `MetaError` (config error, не traceback)
- [x] Round-trip тест: `upstream_hashes`, `reviewer_roles`, `allowed_approver_roles` переживают v2-парсер как pass-through @owner:github:andrei-shtanakov @id:specmeta-v2-roundtrip-test — СДЕЛАНО: `tests/test_spec_meta_vendor.py` (13 тестов: pass-through всех четырёх steward-полей в `SpecMeta.extra`, owner_role first-class, SpecMetaError на всех отказах v2-матрицы, нормализация unquoted-date — реальный кейс всех spec/*.md файлов репо)

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
- [x] Ответить Maestro как владелец контракта: оси obligation/enforcement и gate_id вне каталога @owner:github:andrei-shtanakov @id:maestro-gate-catalog-contract-ruling — steward#62 (зеркало maestro#160), решение владельца 2026-08-12
      **Q1 — две оси, два поля.** `obligation: quality|approval` — интент и часть
      идентичности гейта, принадлежит steward; `enforcement: mandatory|advisory` —
      политика прогона потребителя, принадлежит Maestro и живёт в его схеме
      `maestro.gate-verdict-record/v1` (дискриминатор сохраняется). Вариант (a)
      Maestro принят; расширение словаря steward отклонено — смешивает статическую
      идентичность гейта с контекстной runtime-политикой. Встречное обязательство
      steward зафиксировано как часть контракта: каталог никогда не заводит ключ
      `enforcement` и никогда не принимает токены `mandatory`/`advisory` в
      `obligation_vocabulary`.
      **Q2 — правило namespace, без маппинга.** `GC-` зарезервирован и закрыт
      (минтит только каталог; неизвестный `GC-*` — fail-closed у любого писателя);
      producer-specific id разрешены как `<namespace>.<name>`; наличие `obligation`
      не означает членства в каталоге — членство даёт только резолв id. Трём
      существующим id Maestro (`steward.risk_classify_*`, `human.owner_approval`,
      `maestro.validate_strict`) канонические соответствия НЕ выдаются: это точки
      энфорсмента чужого рантайма, а не гейты gate-check, и GC-псевдоним передал бы
      steward владение проверками, которых он не исполняет. Форма их уже конформна —
      переименование не требуется.
      Фиксация (PR этой ветки): машиночитаемое зеркало `gate_id_namespaces` +
      `obligation_reserved_tokens` в `profiles/gate-catalog.yaml`, проверки в
      `gatecatalog.py` (закрытый набор top-level ключей — им и держится запрет на
      `enforcement`; producer-форма в каталоге отвергается адресно), нормативный
      раздел в `contracts/gate-verdicts/v1/README.md`. **Композиция каталога не
      менялась → `version: 2` остаётся** — потребителю пересматривать состав не
      нужно, только перевендорить файл.
- [x] Hash-chain для `gate_verdicts.jsonl` — тампер-эвидентный леджер @owner:github:andrei-shtanakov @id:gate-verdicts-hash-chain — приём входящего steward#105 (from ai-repos-research#proposal-v3-harvest). PR этой ветки: каждая запись после строки 1 несёт `prev_hash` (SHA-256 hex байтов предыдущей строки без `\n`; header — якорь, поле не несёт никогда — его `$def` в схеме поля не объявляет); эмиссия в `verdicts/chain.py::serialize_chained` (хеш от УЖЕ сериализованной строки — проверка перегоняется байт-в-байт), верификатор `steward verdicts-verify` (chained|legacy → 0, broken → 1, config → 2) + библиотека `verify_chain`; правило аддитивности из issue дословно: файл без поля — legacy-валиден, цепочка обязательна с первой записи, несущей поле. Схема v1 расширена опциональным `prev_hash` (artifact/finding; прецедент — `obligation`), фикстуры `chained.jsonl`/`broken_chain.jsonl`, README: раздел «Целостность» с честными границами (усечение хвоста и полная перезапись с пересчётом цепочкой НЕ ловятся — нужен внешний якорь, вне scope v1). Ре-вендоринг пиненой копии у dispatcher — inbox-handoff (ADR-ECO-006), см. `@blocked_by` следующего пункта не заводится: их прежняя копия классифицирует новые файлы unreadable — fail-closed, не тихое зелёное
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

- [x] Выполнить живой прогон и приложить evidence по DoD выше @owner:github:andrei-shtanakov @id:v1-live-gated-run — выполнен 2026-08-08/09, PR этой ветки: evidence `docs/evidence/2026-08-08-v1-live-run/` (manifest с пинами steward `c2414f7`+spec-runner v2.21.0+claude 2.1.226/sonnet; steps.md — команды/exit/классификация; verdicts JSONL с корреляцией gate_id↔каталог, owner_roles↔roles.yaml, identity↔бандл). Итог: **PASS** — оба негативных среза дали ожидаемые отказы (pre-commit GC-GIT-BRANCH; pre-approval run --strict, но с exit 0 — дефект spec-runner), позитив 12/12 задач, break-glass верификация PASS против живого steward (waiver valid / stale-sha rejected / critical-tier rejected), финальный gate-check 0 err / 3 warn — все три warn = измеренный шов (см. пункты ниже). Журнал: handoff в prograph-vault (derived/journal/steward)
- [x] Шов authoring-контракта spec-runner↔steward, измеренный V1 — РЕШЁН 2026-08-09 (DEC-008, `spec/20-design.md`) и steward-часть СДЕЛАНА в PR этой ветки: канонические имена стадий у spec-runner (`requirements → design → tasks`), узел `task` → `tasks` во всех профилях/доках/тестах, БЕЗ постоянного alias; (б)+(в) — upstream-работа spec-runner (материализация `traces_to`/`upstream_hashes` владельцем SpecMeta), steward остаётся валидатором → handoff-issue @owner:github:andrei-shtanakov @id:authoring-seam-ruling
- [ ] Handoff в spec-runner: остаток authoring-контракта по DEC-008 — gated approve материализует `traces_to` (по стадийной цепочке) и пинует `upstream_hashes` blob-хешами при approve; steward валидирует, не переписывает @owner:github:andrei-shtanakov @id:spec-runner-authoring-contract-ask

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
- [ ] Evidence `agent_merge` с инвариантами I1–I4 (scoped change-class, agent-immutable authority root, adversarial verifier, ревокация) @owner:github:andrei-shtanakov @blocked_by:todo://prograph-vault/adr-eco-004-deferred @trigger:"ADR-ECO-004 снял deferred после batch-2" @id:agent-merge-evidence
- [x] **Разрешение `agent_merge` — значение политики, а не константа кода**: вынести разрешение в `profiles/approval-policy.yaml` с fail-closed дефолтом, `unknown` остаётся fail-closed @owner:github:andrei-shtanakov @id:agent-merge-policy-driven — приём входящего steward#69 (from prograph-vault#adr-eco-008); PR этой ветки: `agent_merge_allowed: bool` в `ApprovalPolicy` (опционально, дефолт `false`, truthy-скаляр = `PolicyError`, не приведение), `check_approval_evidence` больше не отказывает агенту безусловно, сообщение называет поле политики; канонический профиль остаётся запрещающим — ADR-ECO-008 в статусе `proposed`, это готовность, не включение
- [x] Внести merge-личность GitHub App в `agent_identities` @owner:github:andrei-shtanakov @trigger:"App заведён и известен его `<slug>[bot]`" @id:agent-merge-app-identity — вторая половина steward#69, отделена намеренно: тип личности владелец зафиксировал (GitHub App, не machine user), конкретный логин ещё не существует, и выдумывать строку в закрытой классификации нельзя. Классификация от неё уже работает — хинт `"Bot"` даёт `agent` без allowlist; запись в `agent_identities` нужна, чтобы личность была заявлена явно, а не опозналась по форме имени
      **Сделано:** `github:merge-broker` (форма из живых фактов, не из REST). Тем же PR приведена к той же грамматике запись `dependabot` и добавлен регрессионный тест «ни одна личность в каноническом профиле не несёт суффикс `[bot]`» — иначе дефект возвращается молча.
- [x] **Workflow брокера агентского мержа** (`.github/workflows/merge-broker.yml`, ADR-ECO-008a): `workflow_dispatch` + installation-токен App, fail-closed предусловия, squash с `--match-head-commit` @owner:github:andrei-shtanakov @id:merge-broker-workflow — ключ и App ID раскатаны по флоту 2026-08-20 (21/22 репо). Проверок семь, каждая доказывается положительно: PR открыт и не черновик, `mergeable == MERGEABLE` (`UNKNOWN` — неизвестность, отказ), rollup чеков `SUCCESS` (пустой rollup = «чеков нет», не «прошли»), ноль неразрешённых review threads (операционализация «ревью Copilot отработано»), I3 — App не аппрувил этот PR, I2 — PR не трогает authority-root, и все три страницы (файлы, threads, reviews) непагинированы, иначе полноту не доказать. Репозиторий не выкачивается: брокер не исполняет код PR, который мержит
- [x] Живая приёмка I4: смержить PR брокером и доказать, что `mergedBy` = `merge-broker[bot]` @owner:github:andrei-shtanakov @id:i4-live-acceptance — до этого прогона выполнение I4 остаётся утверждением о коде, а не фактом. Приёмкой считается только `PullRequest.mergedBy` с форджа: author/committer merge-коммита ставятся при создании и мержером не являются. Этот PR и есть прогон — он намеренно трогает только `TODO.md`: `profiles/approval-policy.yaml` лежит в guard-списке брокера, поэтому PR с внесением личности брокер обязан отклонить, и приёмку на нём провести нельзя
      **Пройдена 2026-08-20** на PR #75 (прогон `32364093143`): `merged_by.login =
      merge-broker[bot]`, `type: Bot`, GraphQL `__typename: Bot`, merge-коммит `2214579`.
      Различают их не глаза, а сам steward: `approval-facts` по PR 75 и 74 дал
      `github:merge-broker`/`Bot` против `github:andrei-shtanakov`/`User`. Леджер и разбор
      четырёх прогонов (три красных, все информативные) —
      `docs/evidence/2026-08-20-i4-live-acceptance/`. **I4 целиком этим не выполнен:**
      по ECO-004 I4 это detection loop, а различимость — лишь его предпосылка; наблюдатель
      (`todo://dispatcher/agent-merge-observability`) не сделан, D1 остаётся выключенным.
- [x] Строка merge-личности в `agent_identities` — `github:merge-broker`, **без** `[bot]` @owner:github:andrei-shtanakov @id:merge-identity-string-form — найдено живой приёмкой: `approval-facts` кладёт в `identity` GraphQL-логин (`merge-broker`), а REST отдаёт `merge-broker[bot]`; в steward#69 комментарием была передана именно REST-форма. `classify_actor` сверяет `identity in policy.agent_identities` **точным** сравнением, поэтому запись с `[bot]` не совпадёт никогда — и это не будет заметно: классификация всё равно вернёт `agent` по `hint == "Bot"`, то есть заявленная личность окажется мёртвой строкой при внешне исправном поведении. Проверять надо не «работает ли классификация», а совпала ли строка
- [x] Дожидаться определённости `mergeable`, а не отказывать с первого взгляда @owner:github:andrei-shtanakov @id:broker-mergeable-poll — прогон `32364028210` отказал по `mergeable=UNKNOWN`: мерж соседнего PR сдвинул base, GitHub сбросил вычисленную mergeability. Отказ правильный, но воспроизводится после **любого** сдвига base-ветки, то есть в автоматическом прогоне с очередью PR первый вызов брокера будет упираться в него систематически. Нужен опрос с паузой и отказ, только если `UNKNOWN` устоял. Это не ослабление правила: неизвестность доводится до определённости **до** применения правила, а не подменяется допущением
      **Сделано:** опрос до 5 попыток с паузой 6s, выход по первому определённому значению; правило не смягчено — `UNKNOWN`, устоявший весь опрос, по-прежнему отказ, а `CONFLICTING` отказывает сразу без опроса. Худший случай +24s. Проверено на подменённых `gh`/`sleep`: 1 вызов без пауз при готовом ответе, выход на 2-й/4-й попытке при позднем ответе, 5 вызовов и 4 паузы перед отказом.
- [x] `codex-review` — независимое ревью дифа другой моделью как чек (не аппрув) @owner:github:andrei-shtanakov @id:codex-review-check — ADR-ECO-004 I3: adversarial verifier может быть «one more required-check, never the sole authority». Порог: красным делают только `blocker`/`major`, `minor`/`nit` уезжают в сводный комментарий и никого не держат. Два джоба: читающий диф и ключ — БЕЗ прав записи и без checkout'а кода PR; пишущий — видит только вердикт, ни ключа, ни дифа. Промпт и схема берутся из checkout'а **base**, иначе автор патча переписал бы инструкции своему ревьюеру
- [x] Перевести брокер на канонический исполнитель в зонтике @owner:github:andrei-shtanakov @id:broker-via-reusable — реализация переехала в `ai-orchestrators-workspace` (их PR #17), здесь остался тонкий каллер, побайтово равный шаблону `ci/governance/vendor/merge-broker.yml` с подставленным пином. предусловия лежат в чужом для агента репозитории, и guard-список брокера перестал быть себе свидетелем **в части мержа**. Формулировку «I2 выполняется конструкцией» пришлось снять как переоценённую — вердикт codex-review на PR #81 показал, что она верна только при доверенном ref диспача: `workflow_dispatch` исполняет версию файла с указанного ref, а секреты репо доступны на любой ветке, поэтому ветка с подменённым `uses:` уводит ключ App без всякого мержа. См. `todo://steward/broker-key-exposure-per-repo`
- [ ] **Ключ брокера доступен любой ветке репо — сузить экспозицию и пересмотреть OQ-2** @owner:github:andrei-shtanakov @id:broker-key-exposure-per-repo — найдено вердиктом codex-review на PR #81 (blocker, посылки проверены). `workflow_dispatch` принимает произвольный `ref` и исполняет версию файла **с него**; repo-секреты доступны прогону на любой ветке. Значит ветка с подменённым `jobs.merge.uses` плюс диспач по этой ветке отдают `MERGE_BROKER_APP_KEY` в чужой workflow — **мерж для этого не нужен**, поэтому authority-root не защищает. Дыра не создана переездом, она была и во встроенной копии; переезд лишь не закрывает её, вопреки первоначальной формулировке.
      Внутри per-repo схемы починки нет: `environment:` не принимается job'ом с `uses:`, а ссылка на секрет разрешается у каллера до любой проверки; ограничить ref диспача GitHub не даёт (право у любого с write); вынести ключ в зонтик нельзя — reusable workflow видит секреты вызывателя, а не свои.
      Работающий ответ — центральный брокер: живёт в одном репо, мержит в остальные App-токеном, в целевых репозиториях ключа нет вовсе. Это отвергнутый действием вариант OQ-2, и его стоит пересмотреть с этим доводом. **Немедленно, независимо от решения:** убрать ключ из 21 репозитория, где брокера нет, — там он даёт ветке право мержа и не даёт ничего взамен.
- [ ] Живая приёмка брокера в reusable-форме @owner:github:andrei-shtanakov @id:i4-acceptance-reusable — приёмка I4 (2026-08-20, PR #75) доказана для встроенной копии, но **не** для вызова через `workflow_call`. Три свойства держатся на документации и должны быть подтверждены прогоном: `github.repository` внутри вызванного workflow указывает на репозиторий-**вызыватель**, а не на зонтик (иначе брокер полезет мержить не туда); `secrets.app-private-key` доезжает через границу workflow; `vars.MERGE_BROKER_APP_ID` резолвится у каллера и приезжает входом. Этот PR и есть прогон — изменение намеренно неавторитетное
- [ ] Перепинить каллер брокера с SHA на тег @owner:github:andrei-shtanakov @trigger:"владелец завёл линейку тегов брокера (напр. broker-v1)" @id:broker-caller-tag-repin — сейчас пин на SHA мержа `97c84db`, что законно («SHA or tag, never a branch»), но при раскатке на флот пятнадцать каллеров на голых SHA читаются как случайные: тег называет версию контракта. Линейку лучше отдельную от `governance-v*` — у брокера своя частота изменений, и общий тег гнал бы по флоту перепины из-за чужих правок
- [ ] Проверять пины механически, а не спрашивать модель @owner:github:andrei-shtanakov @id:verify-pins-mechanically — вердикт на PR #83 верно заметил, что опечатка во внешнем идентификаторе (снапшот модели, тег action, версия пакета) проходит гейт: ревьюеру такие факты недоступны, и правило ограничивает их `minor`. Но просить модель гадать о том, что проверяется одной командой, — плохой размен. Оракул должен быть детерминированным: шаг CI, проверяющий существование каждого пина (`npm view <pkg>@<ver>`, наличие тега/SHA у action, доступность модели ключу). Тогда класс «опечатка в пине» ловится фактом, а не суждением, и потолок `minor` перестаёт быть дырой
- [ ] Решить судьбу `codex-review` после замера качества вердиктов @owner:github:andrei-shtanakov @trigger:"накопилось 5–10 PR с вердиктами Codex" @id:codex-review-promotion — сейчас чек блокирует только брокер (тот требует зелёным весь rollup) и не внесён в ruleset, то есть человеческий путь не трогает. Решение по итогам замера: вносить ли в `governance-gate` (станет обязательным всем, включая владельца — но у того ruleset'а bypass пуст, и недоступность Codex заморозит репо целиком), либо в отдельный ruleset с admin-bypass, либо оставить как есть. Отдельно оценить долю ложных находок: ложная дороже пропущенной, потому что учит игнорировать вердикты
      **Вход в решение, найденный вердиктом самого ревьюера (2026-08-20, PR #82):**
      пин снапшота модели превращает плановое снятие модели с обслуживания в отказ
      гейта. Сегодня это не outage — чек не обязателен в ruleset и блокирует только
      брокера, человек мержит свободно, и направление отказа правильное (fail-closed).
      Но **если чек станет обязательным, тот же снапшот остановит и человеческие
      мержи**. Значит решение о promotion неотделимо от политики обновления пинов:
      либо обязательность + регулярный осознанный бамп, либо необязательность и
      громкий отказ как сигнал. Взять пин обратно на алиас — не выход: алиас меняет
      поведение молча, а молчаливую подмену чинить нечем.
- [ ] Раскатать `CODEX_REVIEW_API_KEY` и workflow по флоту @owner:github:andrei-shtanakov @blocked_by:todo://steward/codex-review-promotion @id:codex-review-rollout — второй ключ к ротации по 21 репо тем же скриптом `_cowork_output/ops/install-merge-broker-credentials.sh`; делать только после того, как вердикты доказали пользу на одном репо
- [ ] Прикрыть authority-root steward сторожем I2: `DEFAULT_GLOBS` зонтика не покрывают ни `.github/workflows/merge-broker.yml`, ни `profiles/approval-policy.yaml` @owner:github:andrei-shtanakov @trigger:"в `ci/governance/authority_root_guard.py` зонтика добавлены оба пути" @id:authority-guard-coverage — `authority-guard: true` в нашем каллере включать НЕЛЬЗЯ до этого: сторож отработает по чужим глобам, не найдёт ничего и покажет зелёное — ровно «неизвестность как зелёное». Сегодня I2 держит сам брокер (свой guard-список внутри workflow), а сторож зонтика — вторая, независимая линия; правка нужна в соседнем репо, у которого нет TODO-узла в Robin, поэтому здесь триггер, а не `@blocked_by`
- [ ] Перейти с `app-id` на `client-id` в `actions/create-github-app-token` @owner:github:andrei-shtanakov @trigger:"вышел v4 действия либо app-id перестал приниматься" @id:merge-broker-client-id — в v3 `app-id` помечен deprecated. Переход требует раскатать по 21 репо ещё одну переменную (`MERGE_BROKER_CLIENT_ID`); делать это заранее ради предупреждения в логах смысла нет, но и забыть нельзя: когда вход уберут, мерж встанет во всём флоте разом
- [x] Переформулировать обоснование `agent_merge_allowed: false` в `profiles/approval-policy.yaml` @owner:github:andrei-shtanakov @trigger:"ADR-ECO-008 ратифицирован (prograph-vault#80 смержен)" @id:agent-merge-policy-rationale-refresh — сейчас комментарий объясняет запрет статусом `proposed`. После ратификации запрет остаётся, но уже по другой причине: I4 не выполнен и различимой merge-личности не существует. Причина не должна протухнуть раньше факта, который она объясняет — иначе профиль начнёт ссылаться на снятое возражение и будет читаться как забытый

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

### 8. Product-governance вход: приём approved ProductProposal (impresario)

Запрос steward#64 (inbox, from: impresario). Канонический handoff контура product-governance:
инициатива принимается только по evidence — `status: approved` + два АКТИВНЫХ (не перекрытых
`supersedes`) `approve` GateDecision (`qg5_business`, `qg5_committee`) про ровно этот proposal.
Статусное поле без decision-evidence не авторитетно; waiver steward не переиспользуется как
product decision record (и наоборот). Как approved proposal становится charter/spec-бандлом —
отдельное будущее решение владельца, не этот пункт.

- [x] Вендорить пинованные копии `product-proposal/v1` + `gate-decision/v1` (impresario@`a2672a8`) и команду `steward proposal-intake <bundle>` с evidence-проверкой @owner:github:andrei-shtanakov @id:product-proposal-intake — PR этой ветки: `contracts/impresario-*/v1` (PIN), copy-integrity тест обобщён автообнаружением PIN-каталогов, `src/steward/proposalintake.py` (INTAKE-* findings, exit 0/1/2; `GC-*` не минтится — closed namespace каталога, steward#62), 14 тестов-мутаций; живой смоук: настоящий PP-101 → admit, де-approved копия → reject
- [x] Приёмка drift-вахты `impresario-contract-drift.yml` (guarantee B): smoke workflow_dispatch + контролируемый synthetic=drift красный + первый штатный cron-прогон clean; закрывать по наблюдаемым прогонам, не по мержу @owner:github:andrei-shtanakov @id:impresario-contract-drift-acceptance — приёмка 2026-08-13, все три прогона наблюдены:
      ✅ smoke workflow_dispatch clean — run 31568781929 (2026-08-12); clean честный
         при уехавшем master impresario (сравниваются байты схем по PIN, не коммиты);
      ✅ synthetic=drift — run 31568789553 (2026-08-12): красный ровно на шаге
         `compare vendored copies to upstream HEAD`;
      ✅ штатный cron-прогон №1 — run 31676218838 (2026-08-13 07:03 UTC,
         event=schedule): conclusion=success, compare clean. Задержка 07:03 vs
         05:50 — штатный дрейф очереди GitHub cron (как у arch-evidence-freshness).

---

### 9. `approval-facts` как внешний контракт (dispatcher)

Запрос steward#72 (inbox, from `dispatcher#agent-merge-observability`). Задача — **graduation**
внутреннего формата в переносимый evidence-контракт, а не расширение сегодняшнего payload:
`approval-facts/v1` существует только как Python (`src/steward/approvalfacts.py`), в `contracts/`
его нет, вендорить пиненой копией нечего. Оба обходных пути dispatcher отвергнуты по делу:
чтение нашего `profiles/approval-policy.yaml` из чекаута — нарушение границ полирепо, а повтор
`classify_actor` у себя сделал бы steward и dispatcher двумя policy engine, способными
разойтись. Уровень работы — отдельный архитектурный workstream масштаба `gate-verdicts/v1`.

Четыре обязательных свойства (решение владельца 2026-08-19):

1. **Контракт не зависит от появления GitHub App.** `human | agent | unknown` — стабильный
   словарь; отсутствие сегодняшнего agent-субъекта не блокирует реализацию. Отсюда: пункт
   намеренно БЕЗ `@blocked_by` на `agent-merge-app-identity`.
2. **steward остаётся единственным классификатором.** Наружу выходят raw facts, итоговый
   `actor_class` И provenance применённой политики — чтобы потребителю не пришлось
   воспроизводить policy-семантику.
3. **`unknown` означает только успешную классификацию при доступной политике.** Отсутствие,
   неполнота или ошибка materialization выражаются ОТДЕЛЬНЫМ состоянием и не сваливаются в
   `unknown`: outage классификатора не должен читаться как характеристика актора.
4. **Публикация в `.steward/` атомарна и защищена от ложной свежести.** Одного
   temp-file + `os.replace` мало: после неудачного нового прогона прежний успешный файл
   остаётся целым и выглядит актуальным.

Из (4) следует требование к envelope — читатель должен уметь ДОКАЗАТЬ свежесть и полноту, а не
предположить их: `generated_at`, `repository`, `policy_version` / `policy_digest`, объявленный
**scope materialization** (какие PR / merge SHA запрашивались) и признак полного успешного
результата. Без объявленного scope пустой `actors: {}` неотличим от «ничего не запрашивали», а
старый файл — от текущего результата.

- [x] Опубликовать `contracts/approval-facts/v1` в дисциплине `gate-verdicts/v1` (SCHEMA + fixtures + README), пригодный к вендорингу пиненой копией @owner:github:andrei-shtanakov @id:approval-facts-external-contract — реализовано как `contracts/approval-facts/v2/` (SCHEMA.json + fixtures + README); steward — продюсер этого контракта, не консюмер, поэтому `PIN` здесь не применим (`PIN` фиксирует upstream sha ВЕНДОРЕННОЙ копии — у собственного контракта продюсера его нет, как и у соседнего `contracts/gate-verdicts/v1/`); все обязательные свойства выполнены; приёмка на реальных мержах — `docs/evidence/2026-08-21-approval-facts-v2-migration/manifest.md`
- [x] Материализация в `.steward/` рядом с `gate_verdicts.jsonl` — файл попадает в наблюдаемый бандл, а не остаётся артефактом вызова с `--out` @owner:github:andrei-shtanakov @id:approval-facts-bundle-emission — `resolve_bundle_target` + транзакция публикации (preflight 1-5 / remove_previous+materialize на шаге 6) реализованы в `steward approval-facts`; живой прогон против реальных PR/SHA — `docs/evidence/2026-08-21-approval-facts-v2-migration/manifest.md`
- [x] `approval-facts` producer: несуществующий PR-номер у `--prs` не становится записью `not_found`, а обрушивает батч как `MechanicalFailure` (exit 3, файла нет) @owner:github:andrei-shtanakov @id:approval-facts-not-found-vs-mechanical-failure — найдено живой приёмкой 2026-08-21 (`docs/evidence/2026-08-21-approval-facts-v2-migration/manifest.md`, шаг 3): `gh api graphql` возвращает `data.repository.pullRequest: null` (валидный «нет такого PR») **вместе** с top-level `errors: [{type: NOT_FOUND}]`, `gh` из-за непустого `errors` завершается кодом 1, `_gh()`/`_graphql()` в `producer.py` поднимают `MechanicalFailure` по одному лишь ненулевому exit-коду `gh`, не дойдя до JSON с `pullRequest: null`. Юнит-тест `test_absent_pr_is_not_found` не ловит это — его фикстура подменяет `_gh` так, будто такой ответ приходит с кодом 0 и без `errors`, что не совпадает с реальным поведением `gh api graphql` для resolver-полей вида `pullRequest(number:)` — **исправлено 2026-08-23**: дефект оказался трёхслойным, а не в одном месте. (1) `_gh` на ненулевом коде возвращал stderr, выбрасывая stdout, — тело ответа исчезало раньше, чем кто-либо мог в него заглянуть; теперь возвращаются оба потока. (2) `_graphql` падал на `code != 0` до разбора JSON. (3) Он же падал на ЛЮБОМ непустом `errors`. Введено правило `_only_absence`: ненулевой код терпим, только если stdout — валидный JSON, `data` присутствует и ВСЕ ошибки имеют `type: NOT_FOUND`; смесь типов, `data: null` и чистый JSON при недовольном `gh` остаются `MechanicalFailure`. Решение «`repository: null` — недоступность, а не отсутствие» сохранено и покрыто отдельным тестом. Характеризационный тест не удалён, а перевёрнут — он для того и писался.
- [ ] Инвариант 9 читателя `approval-facts/v2` не ловит противоречащий отрицательный алиас (`pr:42 → merged, merge_sha=X` вместе с `merge_sha:X → not_found` валидны одновременно, потому что сравнение работает только по записям с `merge_sha != null`); гейт разрешает это через **приоритет индекса разрешённых SHA над scope-проверкой по идентичности запроса** (§8.2), то есть такой файл резолвится в `merged`, а не в конфликт — семантика ПОКА НЕ МЕНЯЕТСЯ этим пунктом, это решение владельца контракта @owner:github:andrei-shtanakov @id:approval-facts-index-precedence-over-negative-alias — найдено финальным ревью 2026-08-21 (`.superpowers/sdd/2026-08-21-approval-facts-v2/final-review.md`, Important #4); приоритет задокументирован явно в §8.2/§8.3 спеки (`docs/superpowers/specs/2026-08-21-approval-facts-v2-design.md`) и в контрактном README (`contracts/approval-facts/v2/README.md`, инвариант 9); не атакующая поверхность (кто может писать `.steward/`, может просто написать `merged`+`human` напрямую), но дыра в контракте, которую унаследует любой сторонний читатель, реализующий инвариант 9 по README буквально
- [ ] Явный `--approval-facts` **неприменим вне опознанного чекаута** — не только «негодный файл там читается неверно», а шире: `approval_facts_outcome` возвращает `FactsUnavailable("absent")`, когда чекаут не опознан (нет git/`origin`/`origin` не разбирается), **до** проверки `explicit`, поэтому дело не в классе ошибки для невалидного файла — ГОДНЫЙ файл, переданный через `--approval-facts` на распакованном бандле, в не-git каталоге или в чекауте без `origin`, вообще не читается, хотя §8.4 называет `--approval-facts` override'ом @owner:github:andrei-shtanakov @id:approval-facts-explicit-path-subordinate-to-repo-id — найдено Codex-гейтом на PR #86 (раунд 2, переформулировано и усилено раундом 3); направление отказа верное (находка, не пропуск) во всех случаях; не правится этим пунктом — честно исполнить override здесь означало бы валидировать файл по инварианту 11 без `expected_repository`, с которым сравнивать, а закрыть это можно только новым способом ОБЪЯВИТЬ ожидаемый репозиторий, когда его нельзя вывести из `origin` (например, отдельная опция-компаньон к `--approval-facts`) — новая CLI-опция и решение владельца о её форме, не правка в конце ветки; докстринг `approval_facts_outcome` (`src/steward/gatecheck/cli.py`) объясняет это явно
- [ ] Регулярный сбор `approval-facts` — Stage A0 (steward-only soak) @owner:github:andrei-shtanakov @id:approval-facts-scheduled-collection
      Механика собрана: явный статический охват `profiles/approval-facts-scope.yaml`,
      раннер `scripts/collect_approval_facts.py` (только маршрутизация и preflight,
      никакой классификации), шаблон host-local расписания
      `scripts/com.steward.approval-facts.plist.template` — период 6 ч при lease 24 ч.
      **Пункт открыт не по недоделке, а по DoD:** доказательство — наблюдаемые прогоны
      по расписанию плюс зелёный `--check`, а не мерж. Закрывать по факту мержа здесь —
      ровно та подмена доказательства, против которой написан соседний пункт
      `arch-evidence-freshness`.
      Почему локально, а не в Actions: продюсер пишет `<checkout>/.steward/`, потребитель
      читает файл из чекаута, у CI чекаутов флота нет по построению. Возражение против
      локального планировщика («выключенный ноутбук не сообщит, что не проснулся»)
      закрывает сам артефакт: `valid_until` делает молчание **обнаружимым при чтении**.
      Обнаружимым — не сообщаемым: до появления потребителя смотреть надо глазами
      (`--check`), и это указано в шаблоне.
      Stage A0 намеренно steward-only. Расширение на dispatcher и maestro (A1) требует
      их согласия на generated `.steward/` в их дереве — то есть обычных PR с
      `.gitignore` в их репозитории, а не скрытой мутации из скрипта; сейчас `.steward/`
      игнорируется только здесь.
      **Решение о поверхности (2026-08-23).** Раннер защищает ДАННЫЕ, а из защит
      топологии отказался ровно от трёх: hardlink бандла не проверяется;
      containment проверяется до прогона и не перепроверяется после; ssh-алиасы
      не разрешаются (хост origin обязан быть настоящим хостом GitHub, алиас
      отвергается). Остальное на месте и работает: бандл обязан лежать внутри
      чекаута, повторные записи охвата ловятся по идентичности каталога в ФС —
      она же покрывает регистровые алиасы. Область определения A0 — один
      репозиторий, одна машина, воркспейс оператора; допущение названо в
      докстринге раннера, а не подразумевается.
      Причина — замеренная: каждая такая защита живёт в двух местах (сбор и `--check`)
      и обязана с собой согласовываться, а расхождение двух проходов за эту ветку
      случалось **пять раз**, каждый раз с одинаковым следствием — обещанное
      доказательство установки зелёное, плановый сбор на том же охвате падает. То есть
      парные защиты сами порождали дефект того класса, который должны предотвращать.
      Ssh-алиас отдельно: попытка разрешить его полем `origin_host` снимала защиту от
      зеркал для ЛЮБОГО хоста — дыра шире закрываемой. Теперь это названное
      ограничение с лекарством в тексте отказа.
      Настоящая модель угроз появится вместе со Stage B (чужие машины, общая ФС) —
      тогда и защиты вернутся вместе с ней.
- [ ] Stage B: охват формирует потребитель, коллектор получает собственную личность @owner:github:andrei-shtanakov @trigger:"появился потребитель, который формирует scope, различает no-source / out-of-scope / stale / unreadable / classified_unknown и умеет запросить refresh" @id:approval-facts-consumer-driven-scope
      Статический список A0 достаточен ровно до появления такого потребителя; тогда же
      уместна read-only GitHub App для коллектора (наблюдатель не должен владеть ключом
      от наблюдаемого действия), а не раньше — App без единого читателя данных это
      раскатка ключа по флоту вперёд потребности.
      **Критерий приёмки потребителя, зафиксированный заранее:** `now >= valid_until`
      никогда не проецируется в факт об акторе; состояние — `stale`/`unknown`. Без этой
      строки B унаследует дефект «неизвестность как зелёное» этажом выше — тот самый,
      который сегодня виден на единственном бандле флота, истёкшем 20 часов назад.
- [ ] `verdicts/emitter.py`: атомарная публикация `gate_verdicts.jsonl` (temp + `os.replace`) без fsync — слабее требований §6.1 спеки approval-facts/v2 @owner:github:andrei-shtanakov @id:verdicts-emitter-fsync-debt — отдельный хвост, не расширяющий этот воркстрим; см. `docs/superpowers/specs/2026-08-21-approval-facts-v2-design.md` §10
- [ ] dispatcher — стадия 2 хендоффа `approval-facts/v2`: вендорить пиненую копию `contracts/approval-facts/v2/` + написать `core/merge_actor.py` по образцу `core/governance.py` (+ тесты) @owner:repo:dispatcher @id:approval-facts-dispatcher-vendoring-handoff — предпосылка на нашей стороне выполнена (бандл эмитится, контракт опубликован, приёмка на реальных мержах пройдена); формальный inbox-issue в dispatcher по ADR-ECO-006 этой задачей не заведён — см. `docs/superpowers/specs/2026-08-21-approval-facts-v2-design.md` §10

---

### 10a. codex-review kit: дорожная карта качества (план владельца, 2026-08-23)

План целиком: `docs/plans/2026-08-23-review-kit-quality-roadmap.md`. Контекст:
шесть зрячих раундов на #96 дали 19/20 подтверждённых находок, но цикл «чинить
до пустого вердикта» не сходится и стоит ~$0.5/раунд; качество дальше повышают
пункты ниже, в порядке владельца.

- [ ] Итоговое дерево PR для ревьюера: control plane (промпт, схема, скрипты) из
      base во временный доверенный каталог, чекаут — head PR, codex read-only по
      получившемуся дереву, диф — указатель на область ревью; ничего из PR не
      исполнять @owner:github:andrei-shtanakov @id:review-kit-final-tree
- [ ] Переписать шкалу severity: blocker сужен (эксплуатация, необратимая потеря,
      обход authority, гарантированная невозможность основного сценария), для
      blocker/major обязательны файл+строка, вход, наблюдаемый результат, ссылка
      на проверенный код и почему существующие проверки не ловят
      @owner:github:andrei-shtanakov @id:review-kit-severity-rewrite
- [ ] Сократить промпт до 4 разделов (~700–1200 слов): что ревьюируется,
      инструменты/файлы, условия валидной находки, шкала+формат; механику
      доверия обеспечивает runner, а не проза
      @owner:github:andrei-shtanakov @id:review-kit-prompt-diet
- [ ] Статический контекст — только архитектурные контракты и инварианты; обычные
      исходники уходят (доступны деревом), в промпт — требование читать callers,
      callees и тесты изменённого кода
      @owner:github:andrei-shtanakov @id:review-kit-context-demotion
- [ ] Усилить схему вердикта: file/line/scenario/observed/expected/evidence[]/
      confidence; блокируют только blocker/major с confidence: high и заполненным
      evidence @owner:github:andrei-shtanakov @id:review-kit-verdict-schema-v2
- [ ] Большие PR: до ~20–30 файлов один прогон; крупнее — chunked по подсистемам +
      финальный межмодульный проход; generated/lock/snapshots не ревьюировать как
      код; обрезка дифа не молча, а явным infrastructure failure
      @owner:github:andrei-shtanakov @id:review-kit-large-pr-mode

  Гардрейл влит 2026-08-23: `build-prompt.sh` (общая точка CI и local.sh)
  отказывает кодом 2 на дифе шире 30 файлов / 400 000 байт, называя причину и
  оставляя явный подъём потолка флагами; generated/lock/snapshots — правило в
  промпте (согласованность с источником, не построчное ревью). Открытым
  остаётся сам chunked-режим: группировка по подсистемам + финальный
  межмодульный проход; при любой схеме чанкинга нужен dedup-ключ находок
  `(file, line, нормализованное сообщение)` — одна находка приедет из
  нескольких чанков.
- [ ] Generated-фильтр не разбирает кавыченные `diff --git`-заголовки (пути со
      спецсимволами/пробелами): такой путь не совпадает с сырым членом
      `--generated-list` и остаётся в дифе — худший исход сегодня это явный
      отказ по потолку (fail в сторону ревью, находка minor гейта на #99,
      подтверждена шестым заходом). Правка — нормализация кавыченной формы в
      awk `build-prompt.sh` согласованно с `core.quotePath=false` у сборки
      списка в local.sh
      @owner:github:andrei-shtanakov @id:review-kit-quoted-diff-headers
- [x] CI передаёт `--generated-list` в `build-prompt.sh` — включается
      ДЕТЕКЦИЕЙ литерала флага в извлечённой из base механике (деплой-
      ограничение head-YAML × base-скрипты обойдено без второго PR; до мержа
      кита фильтра в CI нет — явный отказ по потолку, честный и временный)
      @owner:github:andrei-shtanakov @id:review-kit-ci-generated-list
- [ ] Вето head-стороны generated-деклараций скоупить до фактически
      изменённых `.gitattributes`: сейчас правка одного файла деклараций
      включает пересечение целиком и роняет base-side декларацию из другого
      (multi-file топология; minor четырнадцатого захода на #99, край назван
      в комментарии local.sh) — расхождение local↔CI в сторону ложного
      отказа по потолку @owner:github:andrei-shtanakov
      @id:review-kit-attr-veto-scope
- [ ] Накопление вердиктов codex-review в jsonl-корпус (PR, head_sha, модель,
      effort, находки, что стало блокирующим) — жанр `gate_verdicts.jsonl` с
      header-записью уже есть (`src/steward/verdicts/emitter.py`); без
      накопления eval-харнесс упрётся в ручной сбор прошлых PR. ОТКРЫТЫЙ
      ДИЗАЙН-ВОПРОС владельцу: кто и куда пишет из CI — у джобы нет права
      коммитить в master; варианты «аггрегация артефактов по расписанию» и
      «ветка-корпус» дают разные гарантии
      @owner:github:andrei-shtanakov @id:review-kit-verdict-corpus
- [ ] Детерминированный пре-фильтр в report-джобе (без ключа, без LLM):
      детектор галлюцинированных импортов — импорт, которого нет ни в
      pyproject.toml, ни в uv.lock. Один язык, один пакет-менеджер — вся
      таблица детекторов ai-review не нужна
      @owner:github:andrei-shtanakov @id:review-kit-import-detector
- [ ] Бамп пина openai/codex-action v1.11 → v1.12 (8636508, 2026-08-20):
      усиление изоляции привилегий и отклонение оверрайдов, конфликтующих с
      protected execution settings — прямо наша модель угроз (ключ в джобе,
      читающей недоверенный текст). Перед бампом проверить CHANGELOG и
      требование unprivileged user namespaces на ubuntu-раннерах
      @owner:github:andrei-shtanakov @id:review-kit-action-pin-bump
- [ ] Инлайн-аннотации из вердикта: report-джоба печатает
      `::error file=…,line=…,title=…::` для блокирующих и `::warning::` для
      остальных — находки появляются в Files changed, новых прав не нужно
      @owner:github:andrei-shtanakov @id:review-kit-inline-annotations
- [ ] Дедуп сводок в треде PR: скрытый маркер в теле комментария + поиск
      своего последнего + правка вместо создания (10 раундов на #99 = 10
      сводок, актуальна одна). Маркер обязан пережить смену формата тела
      @owner:github:andrei-shtanakov @id:review-kit-comment-dedup
- [ ] Измеримый eval: 10–20 прошлых PR (с дефектами, чистые, крупные), метрики
      precision блокирующих/recall major+blocker/ложные блокировки/доля без
      evidence/стоимость; для гейта precision важнее полноты
      @owner:github:andrei-shtanakov @id:review-kit-eval-harness
- [ ] Выбор модели и reasoning-уровня — только по eval (минимум два варианта
      модели × два уровня), не по рассуждению в комментарии workflow
      @owner:github:andrei-shtanakov @blocked_by:todo://steward/review-kit-eval-harness @id:review-kit-model-selection
- [x] Экономный триггер ревью: драфты без лейбла `codex-review` не ревьюятся
      (итерация бесплатна); запрос = снятие драфта (автозапуск) или лейбл
      `codex-review` (действует на следующие пуши драфта). Форма отказа —
      синтетический вердикт + красный report с причиной, не skipped-джобы
      (пропущенный джоб засчитывается required-чеку как пройденный)
      @owner:github:andrei-shtanakov @id:review-kit-on-demand-trigger

### 10. codex-review kit: известные хвосты

Кит влит PR #88 (`639719e`): `scripts/review/{build-prompt,apply-threshold,local}.sh`,
`install-hook.sh`, `.github/hooks/pre-push`. Ниже — то, что найдено гейтом на самом
PR и осознанно не закрыто; список полон, других известных хвостов у кита нет.

- [x] Перевести CI на общие скрипты кита: джоб `review` зовёт `build-prompt.sh`
      (инлайн-логика маркера уходит), джоб `report` получает чекаут base и зовёт
      `apply-threshold.sh`, проверка присутствия в base расширяется до четырёх
      файлов @owner:github:andrei-shtanakov @id:review-kit-ci-migration

  Закрыт арками #92/#93; чекбокс снят 2026-08-23 по сверке с workflow: `review`
  зовёт `build-prompt.sh`, `report` — только `apply-threshold.sh` с полной
  лесенкой кодов {0,1,2,прочее}, инлайн-разбора JSON/severity в YAML нет,
  base-проверка присутствия покрывает четыре файла кита.

- [ ] Пуш в явно названный чужой ref ревьюится от ветки по умолчанию @owner:github:andrei-shtanakov @id:review-kit-explicit-target-base

  При `git push origin hotfix:release/1.0` хук считает форму поддержанной и зовёт
  `local.sh` без `--base`, поэтому диапазон строится от `origin/HEAD`. README это
  признаёт и советует ручной `--base` — но код всё равно трактует зелёный вердикт как
  успех и пропускает пуш, то есть пропускает зелёное, которое сам документ объявил
  несопоставимым с CI. Брать базой `remote_ref` **отклонено** (решение 2026-08-21): для
  обычного `git push origin hotfix` он равен самой ветке, и диапазон схлопнулся бы —
  тихий fail-open, потому что пустой диф у нас законно зелёный.

- [ ] `--base` на remote-tracking ref чужого remote тихо ломает `--fetch` @owner:github:andrei-shtanakov @id:review-kit-base-remote-mismatch

  При настроенных `origin` и `upstream` вызов `--base upstream/release/1.0 --fetch`
  оставляет переменную remote равной `origin`, `track_branch` не распознаётся, `--fetch`
  объявляется игнорируемым, свежесть не проверяется — и прогон может дать 0 на
  устаревшем диапазоне. Обход, описанный в README (добавить только `--base`), для такого
  репозитория недостаточен.

- [ ] Отличать «скрипт вернул 1 намеренно» от «скрипт умер с кодом 1» @owner:github:andrei-shtanakov @id:review-kit-threshold-exit-provenance

  Вызывающий трактует `1` от `apply-threshold.sh` как «есть находки выше порога».
  Но `1` достижим и изнутри скрипта ДО его финальной развилки — например, `jq -e`
  или `test` под `set -e` на кривом входе. Тогда сбой инструмента снова
  предъявляется как вердикт о патче: последний оставшийся слой класса, который
  PR #92 закрывал четырьмя заходами (коды вызываемого → место хранения входа →
  факт вызова → создание файла вывода). Починка требует различать источник кода
  внутри самого скрипта, то есть менять контракт **вендоримой** части кита —
  поэтому вынесено отдельно, а не сделано наспех в #92.

- [ ] `VERDICT_FILE` предполагает плоскую раскладку скачанного артефакта @owner:github:andrei-shtanakov @id:review-kit-artifact-layout

  Если джоб `review` начнёт публиковать артефакт каталогом, а не одиночным
  файлом, `download-artifact` восстановит его на уровень глубже, и страж скажет
  «вердикта нет» при успешном скачивании. Ошибка в безопасную сторону — чек
  краснеет, — но диагноз указывает не туда. Низкий приоритет ровно поэтому.

- [ ] `checksum.sh`, PIN и watch дрейфа — при первом потребителе кита @owner:github:andrei-shtanakov @id:review-kit-vendoring

  Триггер «кит вендорится во второй репозиторий» НАСТУПИЛ (пилот —
  spec-runner, решение владельца 2026-08-23). `checksum.sh` написан у
  продюсера (тесты §10: совпадение, расхождение всех файлов разом,
  отсутствие, лишний файл игнорируется по перечню §5, битый/пустой PIN =
  код 2). PIN и drift-watch — на стороне потребителя, едут в PR
  spec-runner. `@id:review-kit-checksum-bootstrap` решён КОНТРАКТОМ
  вызывающего (не доводом: чекер не может защитить сам себя — шестой заход
  гейта на #101): CI потребителя обязан исполнять checksum.sh, извлечённый
  из base, — обязательное требование к workflow пилота spec-runner;
  согласованная правка кит+PIN ловится upstream-drift вахтой и ревью
  ре-вендор-PR.

- [ ] Усиление разделителя дифа: литеральные маркеры → уже сделано суффиксом от хеша; @owner:github:andrei-shtanakov @id:review-kit-diff-marker-hardening
      осталось решить, нужен ли полноценный nonce

  Парковка снята частично: маркер несёт первые 12 hex-символов sha256 от содержимого
  дифа, подделка требует знать хеш содержимого, включающего подделку. Промпт отдельно
  называет содержимое между маркерами недоверенными данными.

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
  `approval-facts` как внешний контракт (§9, steward#72) со стороны steward закрыт
  целиком 2026-08-21: `contracts/approval-facts/v2/` опубликован, бандл эмитится,
  приёмка на реальных мержах пройдена (`docs/evidence/2026-08-21-approval-facts-v2-migration/`).
  Их `agent-merge-observability` теперь разблокирована формально — предпосылка выполнена;
  сам хендофф (вендоринг + `core/merge_actor.py`) — их сторона, `approval-facts-dispatcher-vendoring-handoff`
  в §9, формальный inbox-issue у них по ADR-ECO-006 ещё не заведён. До их хендоффа их
  модель остаётся «источника нет», и по ADR-ECO-008 D6 прогон обязан вести себя как
  `merge_authority: human`.
- **arbiter**: RD-006 M3 сделан — `config/authority.toml` вендорен, `AUTHORITY_PINNED_SHA` в CI.

## НЕ делаем здесь

- ❌ Не владеем форматами, которые потребляем/эмитим: `tasks.md`/SpecMeta — spec-runner,
  `project.yaml` — Maestro. Их изменения — ask'ом наружу, не правкой у соседа.
- ❌ Не строим свою identity/RBAC и не исполняем задачи — runtime-безопасность у arbiter/ATP.
  `profiles/roles.yaml` — идентичность **governance-ролей** артефактов, не агентов.
- ❌ Не делаем dispatcher вторым SSOT: он рендерит declared-vs-observed, смысл — здесь.
- ❌ Не держим здесь микрошаги реализации — они в `workstreams/<WS>/spec/tasks.md`.
