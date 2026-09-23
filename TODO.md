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

## Текущее состояние (2026-09-05, master `dee9631`)

Прошлая редакция шапки — снимок 2026-07-26 (`72467d2`, 190 тестов). Заменена целиком:
за месяц устарела каждая её строка (484 коммита / 94 смерженных PR с 2026-08-02).
Провенанс закрытых работ не потерян — он в самих пунктах ниже, с номерами PR.

- ✅ **Открытых PR нет**, открытая issue одна (#147, inbox: харнесс-слой ревьюера);
  сьют **1225 passed**. Догфуд обоих бандлов зелёный, сверено живым прогоном 2026-09-05:
  `gate-check --profile team spec/` → 0 err / 0 warn,
  `gate-check --profile team-exp workstreams/WS-005-gate-verdicts/spec/` → 0 err / 0 warn.
- ✅ **Фундамент** (WS-001/002/004/006, C2/C3/C5) — профили как данные + `graph.py`;
  `gate-check` (completeness, traceability, status↔git, stale-каскад `GC-STALE*`,
  `--no-fs`, exit 0/1/2); C2 steward-половина — PR #14; `steward-compile`
  (`project-yaml` + `delegation`, корневой `project.yaml` держится byte-equal
  golden-тестами) — PR #15; risk-модель и waivers — PR #5→#8, #12; authority policy
  v1 (RD-006 M2) — `5816ab5`; governance-каллер в CI — PR #19. Номера оставлены
  здесь намеренно: отдельных чекбоксов у этих работ в файле нет, и без них
  провенанс жил бы только в git-истории вопреки правилу ведения.
  Из исходного scope отложен только OSS-мост (REQ-209, P2).
- ✅ **DEC-007 role identity — закрыт целиком** (§1): `roles.py` + `roleassignments.py`,
  `profiles/roles.yaml` + `profiles/role-assignments.yaml`, канонический singular
  `owner_role`. `GC-GIT-ROLE` сверяет `allowed_approver_roles`, а не `owner_role` —
  ownership и authorization больше не смешаны; `Approval` несёт только identity,
  провайдер фактов не может заявить роль.
- ✅ **SpecMeta v2 ре-вендорен** (§2, PR #61; пин spec-runner `v2.22.0` / `de9a31c4`,
  copy-integrity AST-сверкой байт-в-байт) — внешних блокировок кода steward не осталось.
- ✅ **Каталог `gate_id` v2 — 20 active гейтов** (§3, steward#50): ось
  `obligation: quality|approval`; `GC-` — зарезервированный закрытый namespace
  (ruling steward#62 ↔ maestro#160: `enforcement` — поле Maestro, здесь его нет никогда).
- ✅ **V1 живой прогон — PASS** (§4, 2026-08-08/09), evidence по DoD:
  `docs/evidence/2026-08-08-v1-live-run/`.
- ✅ **Контракты — два разных класса, не путать направление**:
  **наружу** (steward — producer и владелец схемы, соседи вендорят копию):
  `contracts/gate-verdicts/v1` (+ `prev_hash` hash-chain и `steward verdicts-verify`;
  ре-вендоринг dispatcher сверен по форджу 2026-08-28) и `contracts/approval-facts/v2`
  (§9; приёмка на реальных мержах — `docs/evidence/2026-08-21-approval-facts-v2-migration/`),
  плюс SSOT-политики `profiles/{authority,roles,approval-policy}.yaml` для
  arbiter / dispatcher / devtools. **Внутрь** (чужой контракт, вендоренная пиненая
  копия, steward — офлайн-потребитель): обе схемы prograph и оба контракта impresario.
- ✅ **CLI сверх `gate-check` и `steward-compile`**: `steward {risk-classify, waivers-check,
  approval-facts, verdicts-verify, adoption-scan, proposal-intake}`; плюс
  `gate-check --candidate` — prospective-прогон по кандидатной ревизии (§6c).
- 🟡 **Governance gate в CI по-прежнему advisory** — `.github/workflows/governance.yml`.
  Четыре evidence-пункта §5 не сдвинулись с июля: FP/FN не разобраны, break-glass не
  описан, ownership гейта не определён, в required-чеки гейт не переведён. Главный
  незакрытый долг репо. Пятый пункт §5 (перепин каллера) в неоднозначном состоянии и
  ждёт сверки владельцем: `uses:` пинует **голый SHA** `51513e8a`, а комментарий того же
  файла называет целью тег `governance-v2` и утверждает, что SHA ему соответствует —
  тега `governance-v1` из прежней редакции шапки в дереве нет вообще.
- 🟡 **Мерж — агент по умолчанию с 2026-08-31** (ADR-ECO-011: `agent_merge_allowed: true`,
  `github:ai-prosto` в `agent_identities`), но **типизированный `agent_merge`-evidence
  ещё не написан** (§6, `@blocked_by:todo://prograph-vault/adr-eco-004-deferred`).
  Практика обогнала governance-модель; асимметрия названа, а не замолчана.
- 🟡 **Ревью**: CI-контур `codex-review` снят 2026-08-31 (`3f0e06b`, −811 строк — платные
  прогоны OpenAI API + исчерпанный лимит Actions). Дефолт — терминальный `review-pr.sh`
  от ai-prosto. Переоценки под это решение ждут не только §10/§10a (issue #147), но и два
  пункта §6, чьи посылки дерево уже опровергает: `codex-review-promotion` продвигает чек,
  которого в `.github/workflows/` нет, а `codex-review-rollout` раскатывает снятый workflow.
- ⛔ **WS-003 (git approval)** — invalidated ADR-ECO-004 D4; но узел
  `git-approval-integration` всё ещё в compile-блоке и DAG (§6, `ws-003-compile-dag-fate`).
- ⬜ **WS-005 (dispatcher panel)** — бандл, аппрувы и контракт закрыты; открыт точный
  остаток: `checked_by`-evidence в панели не материализован.
- 📊 **TODO-плоскость**: 60 `[x]` / 50 `[ ]`.

## Правила ведения

- Выполненный пункт → `[x]` + хеш коммита/номер PR.
- Прямые коммиты в `master` запрещены: ветка `<type>/<slug>` → PR → ревью → мерж. Дефолты
  сместились в августе: ревью — терминальный `review-pr.sh` от ai-prosto (Copilot по умолчанию
  НЕ запрашивается, решение владельца 2026-08-25), **мержит агент** от учётки ai-prosto
  (ADR-ECO-011 «DarkFactory», ратифицирован 2026-08-30). Человеческий мерж — opt-in, и
  **всегда, без переопределения**: PR по authority-root путям, PR без предъявленного
  evidence базового слоя, request-changes или **неприбывшее ревью** (`unknown` ⇒ не мержим —
  неизвестность здесь запрещает, а не разрешает). Механика — `CLAUDE.md`;
  SSOT — `../prograph-vault/authored/rules/git-workflow.md`.
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
- [x] Hash-chain для `gate_verdicts.jsonl` — тампер-эвидентный леджер @owner:github:andrei-shtanakov @id:gate-verdicts-hash-chain — приём входящего steward#105 (from ai-repos-research#proposal-v3-harvest). PR этой ветки: каждая запись после строки 1 несёт `prev_hash` (SHA-256 hex байтов предыдущей строки без `\n`; header — якорь, поле не несёт никогда — его `$def` в схеме поля не объявляет); эмиссия в `verdicts/chain.py::serialize_chained` (хеш от УЖЕ сериализованной строки — проверка перегоняется байт-в-байт), верификатор `steward verdicts-verify` (chained|legacy → 0, broken → 1, config → 2) + библиотека `verify_chain`; правило аддитивности из issue дословно: файл без поля — legacy-валиден, цепочка обязательна с первой записи, несущей поле. Схема v1 расширена опциональным `prev_hash` (artifact/finding; прецедент — `obligation`), фикстуры `chained.jsonl`/`broken_chain.jsonl`, README: раздел «Целостность» с честными границами (усечение хвоста и полная перезапись с пересчётом цепочкой НЕ ловятся — нужен внешний якорь, вне scope v1). Ре-вендоринг пиненой копии у dispatcher — inbox-handoff по ADR-ECO-006, ожидание — следующим пунктом; до него их прежняя копия классифицирует новые файлы unreadable — fail-closed, не тихое зелёное
- [x] dispatcher перевендоривает `contracts/gate-verdicts/v1` с `prev_hash` (dispatcher#173) @owner:repo:dispatcher @id:gate-verdicts-prev-hash-dispatcher-revendor — до ре-вендоринга их панель классифицировала сцепленные файлы unreadable (красное, но fail-closed); признак «сделано» — их copy-integrity зелёная на byte-equal копии @epic:eco.governance-plane

  Закрыт 2026-08-28 по PF-BLOCKER-STALE (dispatcher свой пункт завершил):
  признак сверен по форджу, не со слов — `SCHEMA.json` в
  `dispatcher/contracts/steward-gate-verdicts/v1/` на их дефолтной ветке
  git-sha-идентичен steward master (`2d3c7d3a`), manifest.json пинует
  producer_commit `9916787f`. Ожидание жило тегом
  `@blocked_by:dispatcher#gate-verdicts-v1-prev-hash-revendor`; по
  плану-доку gate-id-catalog зависимость закрытого пункта хранится прозой,
  тег с чекбокса снят.
- [ ] Read-only панель состояния бандла в dispatcher (рендер — на их стороне) @owner:github:andrei-shtanakov @id:dispatcher-bundle-status-panel @epic:eco.governance-plane
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
- [ ] Handoff в spec-runner: остаток authoring-контракта по DEC-008 — gated approve материализует `traces_to` (по стадийной цепочке) и пинует `upstream_hashes` blob-хешами при approve; steward валидирует, не переписывает @owner:github:andrei-shtanakov @id:spec-runner-authoring-contract-ask @epic:eco.governance-plane

### 5. Promotion гейта: advisory → required

Гейт бежит на каждом PR, но не блокирует, пока `governance / gate` не добавлен required-чеком в
ruleset `master`. Триггер перевода — **не календарь и не решение «пора», а evidence**.

- [ ] Разобрать накопленные false-positive / false-negative срабатывания гейта @owner:github:andrei-shtanakov @id:gate-fp-fn-triage @epic:eco.governance-plane
- [ ] Документировать рабочий break-glass / waiver path для governance-гейта (и проверить, что он работает) @owner:github:andrei-shtanakov @id:gate-break-glass-path @epic:eco.governance-plane
- [ ] Определить runtime и ownership самого гейта (кто чинит, кто владеет правилами) @owner:github:andrei-shtanakov @id:gate-runtime-ownership @epic:eco.governance-plane
- [ ] Перевести `governance / gate` в required status checks ruleset'а `master` @owner:github:andrei-shtanakov @trigger:"V1 выполнен + несколько реальных PR прошли гейт + FP/FN разобраны + break-glass path работает" @id:promote-gate-required @epic:eco.governance-plane
- [ ] Перепиновать caller на `governance-v2` @owner:github:andrei-shtanakov @trigger:"в workspace-manifest.toml [tools] объявлен governance-v2" @id:repin-caller-governance-v2 @epic:eco.governance-plane

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
- [ ] Решить судьбу `WS-003` в compile-блоке и DAG: `git-approval-integration` всё ещё узел `steward-compile` и upstream для `dispatcher-panel-dogfood`. Удаление/замена меняет `project.yaml` → регенерация emitter'ом + обновление golden-тестов; висячий `depends_on` поймает `GC-COMPILE` @owner:github:andrei-shtanakov @blocked_by:todo://steward/solo-merge-evidence-policy @id:ws-003-compile-dag-fate @epic:eco.governance-plane
- [ ] **Определить solo-compatible merge evidence policy** — опереться на будущие `human_merge` / `agent_merge` (I1–I4), а не имитировать невозможный owner review; `solo-mode` = значение конфига (набор аппруверов из одного + явно разрешённое и логируемое self-approval) @owner:github:andrei-shtanakov @id:solo-merge-evidence-policy @epic:eco.governance-plane
- [ ] Типизированное evidence `human_merge`: merge существует, actor ∈ humans, чеки зелёные (GitHub API) @owner:github:andrei-shtanakov @blocked_by:todo://steward/solo-merge-evidence-policy @id:human-merge-evidence @epic:eco.governance-plane
- [ ] Evidence `agent_merge` с инвариантами I1–I4 (scoped change-class, agent-immutable authority root, adversarial verifier, ревокация) @owner:github:andrei-shtanakov @blocked_by:todo://prograph-vault/adr-eco-004-deferred @trigger:"ADR-ECO-004 снял deferred после batch-2" @id:agent-merge-evidence @epic:eco.governance-plane
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
- [ ] **Ключ брокера доступен любой ветке репо — сузить экспозицию и пересмотреть OQ-2** @owner:github:andrei-shtanakov @id:broker-key-exposure-per-repo — найдено вердиктом codex-review на PR #81 (blocker, посылки проверены). `workflow_dispatch` принимает произвольный `ref` и исполняет версию файла **с него**; repo-секреты доступны прогону на любой ветке. Значит ветка с подменённым `jobs.merge.uses` плюс диспач по этой ветке отдают `MERGE_BROKER_APP_KEY` в чужой workflow — **мерж для этого не нужен**, поэтому authority-root не защищает. Дыра не создана переездом, она была и во встроенной копии; переезд лишь не закрывает её, вопреки первоначальной формулировке. @epic:eco.governance-plane
      Внутри per-repo схемы починки нет: `environment:` не принимается job'ом с `uses:`, а ссылка на секрет разрешается у каллера до любой проверки; ограничить ref диспача GitHub не даёт (право у любого с write); вынести ключ в зонтик нельзя — reusable workflow видит секреты вызывателя, а не свои.
      Работающий ответ — центральный брокер: живёт в одном репо, мержит в остальные App-токеном, в целевых репозиториях ключа нет вовсе. Это отвергнутый действием вариант OQ-2, и его стоит пересмотреть с этим доводом. **Немедленно, независимо от решения:** убрать ключ из 21 репозитория, где брокера нет, — там он даёт ветке право мержа и не даёт ничего взамен.
- [ ] Живая приёмка брокера в reusable-форме @owner:github:andrei-shtanakov @id:i4-acceptance-reusable — приёмка I4 (2026-08-20, PR #75) доказана для встроенной копии, но **не** для вызова через `workflow_call`. Три свойства держатся на документации и должны быть подтверждены прогоном: `github.repository` внутри вызванного workflow указывает на репозиторий-**вызыватель**, а не на зонтик (иначе брокер полезет мержить не туда); `secrets.app-private-key` доезжает через границу workflow; `vars.MERGE_BROKER_APP_ID` резолвится у каллера и приезжает входом. Этот PR и есть прогон — изменение намеренно неавторитетное @epic:eco.governance-plane
- [ ] Перепинить каллер брокера с SHA на тег @owner:github:andrei-shtanakov @trigger:"владелец завёл линейку тегов брокера (напр. broker-v1)" @id:broker-caller-tag-repin — сейчас пин на SHA мержа `97c84db`, что законно («SHA or tag, never a branch»), но при раскатке на флот пятнадцать каллеров на голых SHA читаются как случайные: тег называет версию контракта. Линейку лучше отдельную от `governance-v*` — у брокера своя частота изменений, и общий тег гнал бы по флоту перепины из-за чужих правок @epic:eco.governance-plane
- [ ] Проверять пины механически, а не спрашивать модель @owner:github:andrei-shtanakov @id:verify-pins-mechanically — вердикт на PR #83 верно заметил, что опечатка во внешнем идентификаторе (снапшот модели, тег action, версия пакета) проходит гейт: ревьюеру такие факты недоступны, и правило ограничивает их `minor`. Но просить модель гадать о том, что проверяется одной командой, — плохой размен. Оракул должен быть детерминированным: шаг CI, проверяющий существование каждого пина (`npm view <pkg>@<ver>`, наличие тега/SHA у action, доступность модели ключу). Тогда класс «опечатка в пине» ловится фактом, а не суждением, и потолок `minor` перестаёт быть дырой @epic:eco.governance-plane
- [ ] Решить судьбу `codex-review` после замера качества вердиктов @owner:github:andrei-shtanakov @trigger:"накопилось 5–10 PR с вердиктами Codex" @id:codex-review-promotion — сейчас чек блокирует только брокер (тот требует зелёным весь rollup) и не внесён в ruleset, то есть человеческий путь не трогает. Решение по итогам замера: вносить ли в `governance-gate` (станет обязательным всем, включая владельца — но у того ruleset'а bypass пуст, и недоступность Codex заморозит репо целиком), либо в отдельный ruleset с admin-bypass, либо оставить как есть. Отдельно оценить долю ложных находок: ложная дороже пропущенной, потому что учит игнорировать вердикты @epic:eco.codex-review-rollout
      **Вход в решение, найденный вердиктом самого ревьюера (2026-08-20, PR #82):**
      пин снапшота модели превращает плановое снятие модели с обслуживания в отказ
      гейта. Сегодня это не outage — чек не обязателен в ruleset и блокирует только
      брокера, человек мержит свободно, и направление отказа правильное (fail-closed).
      Но **если чек станет обязательным, тот же снапшот остановит и человеческие
      мержи**. Значит решение о promotion неотделимо от политики обновления пинов:
      либо обязательность + регулярный осознанный бамп, либо необязательность и
      громкий отказ как сигнал. Взять пин обратно на алиас — не выход: алиас меняет
      поведение молча, а молчаливую подмену чинить нечем.
- [ ] Раскатать `CODEX_REVIEW_API_KEY` и workflow по флоту @owner:github:andrei-shtanakov @blocked_by:todo://steward/codex-review-promotion @id:codex-review-rollout — второй ключ к ротации по 21 репо тем же скриптом `_cowork_output/ops/install-merge-broker-credentials.sh`; делать только после того, как вердикты доказали пользу на одном репо @epic:eco.codex-review-rollout
- [ ] Прикрыть authority-root steward сторожем I2: `DEFAULT_GLOBS` зонтика не покрывают ни `.github/workflows/merge-broker.yml`, ни `profiles/approval-policy.yaml` @owner:github:andrei-shtanakov @trigger:"в `ci/governance/authority_root_guard.py` зонтика добавлены оба пути" @id:authority-guard-coverage — `authority-guard: true` в нашем каллере включать НЕЛЬЗЯ до этого: сторож отработает по чужим глобам, не найдёт ничего и покажет зелёное — ровно «неизвестность как зелёное». Сегодня I2 держит сам брокер (свой guard-список внутри workflow), а сторож зонтика — вторая, независимая линия; правка нужна в соседнем репо, у которого нет TODO-узла в Robin, поэтому здесь триггер, а не `@blocked_by` @epic:eco.governance-plane
- [x] **update-branch-дисциплина брокера против stale-merge-дерева** — условие включения D1, не делать до снятия блокера @owner:github:andrei-shtanakov @blocked_by:todo://dispatcher/agent-merge-observability @id:merge-broker-update-branch-discipline @epic:eco.governance-plane — приём входящего steward#116 (from atp-platform, вердикт гейта codex-review на atp-platform#306). Правило: перед мержем, если PR отстал от base → `update-branch` → дождаться зелёного rollup на обновлённом SHA → мержить. Дыра: зелёный rollup относится к merge-дереву на момент прогонов, движение base ничего не перезапускает (событие в PR не приходит) — пока мержит человек, границей служит его взгляд (плашка out-of-date), при включении D1 взгляда в контуре нет. Отклонённые альтернативы (довод в atp-platform#306): ruleset «Require branches to be up to date» — каскад O(N²) полных CI-прогонов при серийном мерже агентских PR; merge queue — гоняет только required-чеки на событии `merge_group`, которого нет ни в одном workflow флота (вернуться, когда/если required-чеки переведут на merge_group). Цена принята: каждый update-branch на не-драфте = платный прогон codex-review, при мерж-очередях брокера это штатный расход. Реализация брокера живёт в зонтике (`ai-orchestrators-workspace`) — при снятии блокера понадобится issue соседу, здесь останется пин каллера — СДЕЛАНО 2026-08-26 решением владельца (реализация — предусловие D1 и делается ДО снятия блокера; блокер @blocked_by держит только ВКЛЮЧЕНИЕ D1): зонтик #25 (`938a3318`) + пин каллера #118 (`63ced69`); живая приёмка I4-style — сам этот PR: после мержа PR-X (#119) ветка отстала от master → диспатч брокера → отказ с автоматическим update-branch → зелёный rollup → повторный диспатч → мерж `merge-broker[bot]`; свидетельства — логи workflow merge-broker и таймлайн этого PR, протокол — docs/superpowers/acceptance/2026-08-26-broker-update-branch-acceptance.md
- [x] **Включение D1: `agent_merge_allowed: true`** @owner:github:andrei-shtanakov @id:agent-merge-d1-enablement @epic:eco.governance-plane — блокер `todo://dispatcher/agent-merge-observability` СНЯТ не выполнением, а решением: ADR-ECO-011 «DarkFactory» (ратифицирован 2026-08-30) в OQ-1 постановил, что для DarkFactory достаточно `merged_by`-аудита (`gh pr list --json mergedBy`), а отдельный дашборд агентских мержей в dispatcher предусловием не является — «пересмотр при первом инциденте». Это ровно то возражение, которое несло поле, поэтому пункт закрывается, а не переставляет тег. Остальные предусловия были выполнены раньше: различимость личности (I4-приёмка 2026-08-20), update-branch-дисциплина (`@id:merge-broker-update-branch-discipline`), стыковые дыры steward#121/#122 (закрыты steward#123).
      **Сделано** (PR этой ветки): `agent_merge_allowed: true` + переписанный довод в `profiles/approval-policy.yaml` (все три прежние причины помечены снятыми — довод не должен пережить факт, который он объяснял), регрессионный тест на канонический профиль. Мержит человек: файл — authority-root, и по ADR-ECO-011 D1 такой PR «всегда человеку» без переопределения.
      **Не закрыто этим пунктом:** `@id:broker-key-exposure-per-repo` — дыра относится к пути merge-broker (App-ключ доступен любой ветке), а DarkFactory мержит от учётки ai-prosto другим путём. Дыра не создана и не снята включением D1; брокер живёт дальше для автоматических прогонов (OQ-2 ADR-ECO-011 отложил их в арку issue-runner).
- [x] **Личность DarkFactory-мержера в `agent_identities`: `github:ai-prosto`** @owner:github:andrei-shtanakov @id:agent-identities-ai-prosto @epic:eco.governance-plane — приём входящего steward#139 (from devtools#behaviour-runner): классификатор знал только `dependabot`/`merge-broker`, ai-prosto падал в `unknown` → fail-closed, и runner behaviour-конвейера devtools всегда вставал в `waiting_human_merge` (S7), даже при включённом agent-мерже. PR этой ветки: строка добавлена. Отличие от двух соседних записей названо в комментарии профиля и закреплено тестом: ai-prosto — **machine user**, а не GitHub App, GraphQL отдаёт `__typename: User`, хинт `"Bot"` его НЕ ловит — эта строка единственное, что делает его `agent`, тогда как `merge-broker`/`dependabot` распознались бы и без allowlist'а. Ход за devtools: осознанный pin-bump вендоренной копии `contracts/steward-actor-policy/v1/`.
- [ ] Перейти с `app-id` на `client-id` в `actions/create-github-app-token` @owner:github:andrei-shtanakov @trigger:"вышел v4 действия либо app-id перестал приниматься" @id:merge-broker-client-id — в v3 `app-id` помечен deprecated. Переход требует раскатать по 21 репо ещё одну переменную (`MERGE_BROKER_CLIENT_ID`); делать это заранее ради предупреждения в логах смысла нет, но и забыть нельзя: когда вход уберут, мерж встанет во всём флоте разом @epic:eco.governance-plane
- [x] Переформулировать обоснование `agent_merge_allowed: false` в `profiles/approval-policy.yaml` @owner:github:andrei-shtanakov @trigger:"ADR-ECO-008 ратифицирован (prograph-vault#80 смержен)" @id:agent-merge-policy-rationale-refresh — сейчас комментарий объясняет запрет статусом `proposed`. После ратификации запрет остаётся, но уже по другой причине: I4 не выполнен и различимой merge-личности не существует. Причина не должна протухнуть раньше факта, который она объясняет — иначе профиль начнёт ссылаться на снятое возражение и будет читаться как забытый
      **Историческая запись:** довод, переписанный этим пунктом, прожил до 2026-08-31 и переписан ещё раз — теперь под разрешение (`@id:agent-merge-d1-enablement`). Пункт оставлен как есть: он про дисциплину «причина не переживает факт», а не про конкретное значение поля.

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

### 6c. gate-check · prospective-режим по кандидатной ревизии

- [x] **`gate-check --candidate`: прогон по содержимому каталога без коммита** @owner:github:andrei-shtanakov @id:gate-check-candidate-mode @epic:eco.governance-plane — приём входящего steward#140 (from devtools#behaviour-runner): шаг S4 их behaviour-конвейера проверяет кандидатную ревизию бандла и до сих пор обходился content-check API поверх трёх пиненых внутренних символов (`checks.collect_bundle`, `behaviour.check_behaviour_spec`, `trace_matrix.build_trace_matrix`, пин steward `4a1c7c4`) — работало, но не покрывало git-зависимые проверки и держало devtools на внутренней структуре пакета вместо CLI-контракта.
      **Форма выбрана владельцем — режим, а не `ref_kind: candidate` в `InjectedGitFacts`.** Довод: вопросы к git делятся надвое и одним адаптером честно не закрываются. `blob_hash` — чистая функция от байтов (git-адрес блоба), её кандидат отвечает сам и **точнее живого прогона**: тот читает `HEAD:<путь>` и сравнил бы пины с последним коммитом, а не с файлами перед ним — поэтому весь stale-каскад в prospective-режиме работает, а не отключается. Вопросы к истории (`on_default_branch`, `is_ancestor`, `changed_paths_since`, `merge_provenance`) у кандидата не имеют ответа «нет» — они не задаются; выдуманное `False` от `on_default_branch` выдало бы `GC-GIT-BRANCH` на каждый approved-артефакт бандла. `CandidateGitFacts` на них **бросает**, а режим их не зовёт.
      **Сделано** (PR этой ветки): `src/steward/gatecheck/candidate.py` (`CandidateGitFacts`, `blob_hash_of`, `NOT_EVALUATED`), флаг `--candidate` (не требует чекаута вообще), ветка `prospective` внутри самого `run_checks` — чтобы добавленная позже проверка не попала в prospective-прогон случайно, а её автор был вынужден выбрать сторону; ref-зависимые гейты **объявляются** каждым прогоном (текстом и ключом `not_evaluated` в JSON), а не молча опускаются; `--format json` теперь всегда несёт `mode` (`live|injected|candidate`); конфликтующие флаги (`--no-fs`, `--emit-verdicts`, `--approval-facts`, `--stage release`) — config error, а не тихий no-op. Docs: `docs/gate-check-candidate.md`. Ход за devtools: миграция S4 с internal-API на CLI осознанным pin-bump'ом.
      **Находка ревью-гейта на этом же PR (blocker, посылки проверены, исправлено):** первая версия пропускала `GC-ARCH-CONFORMANCE` целиком. Гейт читает историю ровно одной клаузой (D9 self-freshness); всё остальное в нём — наличие отчёта, разбор JSON, схема, `manifest.sha256`, `snapshot.complete`, политика findings/verdicts/unknown, возраст снапшота — выводится из байтов и на кандидате работает. Пропуск целиком прятал весь этот класс ошибок за строкой «не проверено» — тот же fail-open, ради которого режим и объявляет пропущенное. Подавляется теперь только клауза D9, а объявление стало поклаузным (`NotEvaluated.scope`), причём `gate_id` остался голым — иначе механическая сверка с каталогом гейтов сломалась бы ровно там, где объявление стало точным.
      **Названное ограничение** (находка приёмочного ревью, minor): `blob_hash_of` считает git-формат объектов **SHA-1** — дефолтный и единственный во флоте. Репо с `objectformat = sha256` писало бы пины на 64 символа, и неизменённый бандл собрал бы `GC-STALE`. Определить формат режим не может (он намеренно работает без репозитория); поддержка означает протащить формат исходного репо внутрь прогона — новый вход и решение владельца, поэтому ограничение записано, а не угадано.

- [x] **`gate-check --candidate --upto <node>`: гейт неполного бандла по уровню DAG** @owner:github:andrei-shtanakov @id:gate-check-upto-level @epic:eco.governance-plane — приём входящего steward#187 (from devtools#346, sequential-node-approval S5): в волновом режиме конвейера devtools на гейте волны в бандле лежат только узлы уровней ≤ k, полный профиль красит его `GC-COMPLETENESS`; сегодняшний обход devtools — раннер пишет усечённую копию профиля и сверяет её sha256 (`policy_sources.wave_profile_dir`), гейт судит по копии, а не по файлу target. Флаг убирает копию.
      **Форма выбрана владельцем 2026-09-23** (три решения, заявка в исходной формулировке смешивала две семантики):
      (1) **граница — уровень узла, а не его upstream-замыкание.** `--upto <node>` = все узлы профиля с `level ≤ level(node)`, `level` = 0 у корня, иначе `1 + max` по прямым upstream (та же формула, что `bundle_dag.levels` у devtools — часть контракта, закрепить тестом). По замыканию `--upto design` в `team`/`team-exp` выкинул бы `acceptance` того же уровня (обе в волне W4). Неизвестный узел — config error, exit 2.
      (2) **профиль не усекается; ослабляется только `check_completeness`** для узлов выше границы. Усечение графа сделало бы лежащий в бандле артефакт вне границы `GC-STAGE` warn с `node_id=None` (`checks.py::collect_bundle`) — traceability / stale-каскад / upstream-approved его бы молча не видели: fail-open. Полный граф ⇒ `_validate_edges` не трогается, делегаты пропускаются как сегодня, каждый присутствующий артефакт проверяется целиком. Открыто для реализации: артефакт выше границы, лежащий в бандле, — ошибка или штатная проверка (решить в PR, по умолчанию — проверять целиком).
      (3) **граница объявляется явно**: JSON несёт `upto` (узел + уровень), текстовый вывод — строку в stderr (как `not_evaluated`), чтобы частичный pass не читался как полный; `--upto` с `--stage release` или `--emit-verdicts` — config error, не тихий no-op. Профиль и siblings читаются из target как обычно.
      **Сделано =** флаг + тесты (уровни на `team-exp`, сосед того же уровня входит, узел выше границы не требуется, конфликтующие флаги → exit 2) + `docs/gate-check-candidate.md`. Ход за devtools: замена `wave_profile_dir`/`verify_wave_profile_dir` на `--upto` в `runner._step_gate` pin-bump'ом.
      **Сделано** (PR этой ветки): `SpecGraph.levels()` (`graph.py`), `run_checks(..., not_required=)` → `check_completeness` (граф не усекается), флаг `--upto` + `UptoScope` в `cli.py` (ключ `upto` в JSON, строка на stderr; `not_required` — только обязательные не-делегированные узлы выше границы: делегат и так не требуется, перечислять его значило бы завысить объём). Открытый вопрос из формы закрыт умолчанием: артефакт выше границы в бандле проверяется целиком, без отдельной находки. Флаг не привязан к `--candidate`; конфликты с `--stage release` / `--emit-verdicts` — в любом режиме; `--approval-facts` (читается только на release — был бы гарантированно пустым override'ом) и `--trace-matrix` тоже отвергаются (находки локального ревью; у пейлоада матрицы нет места для `upto`, а ниже уровня behaviour-spec отсутствие матрицы выдавалось бы config error про сломанный бандл; потребителя пары нет — devtools строит матрицу через internal API). Тесты: `tests/gatecheck/test_upto.py`, уровни `team-exp` — `tests/test_graph.py`. Docs: `docs/gate-check-candidate.md`.

### 7. Постоянные обязательства и отложенное

- [ ] Handoff в arbiter на ре-вендоринг `config/authority.toml` + бамп `AUTHORITY_PINNED_SHA` @owner:github:andrei-shtanakov @trigger:"любая правка profiles/authority.yaml" @id:arbiter-authority-revendor-handoff @epic:eco.governance-plane
- [ ] **D2 · лицензия sdd** — спросить Dmytro Honcharuk, можно ли брать тексты шаблонов (LICENSE в репо нет); до ответа берём только идею гейтов @owner:github:andrei-shtanakov @id:sdd-license-question @epic:eco.governance-plane
- [ ] **REQ-209 · OSS-мост в gate-check** (P2): presence через repolinter, ownership через codeowners-validator; `gate-check` остаётся оркестратором @owner:github:andrei-shtanakov @id:req-209-oss-bridge @epic:eco.governance-plane
- [x] Pre-adoption скан внешних инструментов до первого запуска @owner:github:andrei-shtanakov @id:external-tool-adoption-scan — приём входящего steward#104 (from ai-repos-research#proposal-v3-harvest; происхождение — живой инцидент 2026-08-23: fileless-загрузчик с hardcoded C2 и top-level триггером в клонированном репо). PR этой ветки: `steward adoption-scan <чекаут>` (`src/steward/adoptionscan.py`) — детерминированный AST-only скан `*.py` без импорта/исполнения цели; три проверки `SCAN-TOPLEVEL-EFFECT` (fail-closed allowlist top-level statements, `if`/`try` рекурсируются, `__main__`-guard пропускается) / `SCAN-NET-LITERAL` (URL-схемы + валидный IPv4 в литералах кода, докстринги исключены) / `SCAN-DYNAMIC-EXEC` (`exec`/`eval`/`compile` над нелитеральным аргументом, сетевые импорты модуля называются в находке); трёхзначный вердикт `clean|failed|not_checked` — «не сканировалось» ≠ «чисто» (нечитаемый файл или дерево без Python = not_checked); exit 0/1/2 зеркалят gate-check (not_checked = 1: adoption без глаз блокируется); байт-стабильный JSON; docs: `docs/adoption-scan.md`

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
- [ ] Инвариант 9 читателя `approval-facts/v2` не ловит противоречащий отрицательный алиас (`pr:42 → merged, merge_sha=X` вместе с `merge_sha:X → not_found` валидны одновременно, потому что сравнение работает только по записям с `merge_sha != null`); гейт разрешает это через **приоритет индекса разрешённых SHA над scope-проверкой по идентичности запроса** (§8.2), то есть такой файл резолвится в `merged`, а не в конфликт — семантика ПОКА НЕ МЕНЯЕТСЯ этим пунктом, это решение владельца контракта @owner:github:andrei-shtanakov @id:approval-facts-index-precedence-over-negative-alias — найдено финальным ревью 2026-08-21 (`.superpowers/sdd/2026-08-21-approval-facts-v2/final-review.md`, Important #4); приоритет задокументирован явно в §8.2/§8.3 спеки (`docs/superpowers/specs/2026-08-21-approval-facts-v2-design.md`) и в контрактном README (`contracts/approval-facts/v2/README.md`, инвариант 9); не атакующая поверхность (кто может писать `.steward/`, может просто написать `merged`+`human` напрямую), но дыра в контракте, которую унаследует любой сторонний читатель, реализующий инвариант 9 по README буквально @epic:eco.governance-plane
- [ ] Явный `--approval-facts` **неприменим вне опознанного чекаута** — не только «негодный файл там читается неверно», а шире: `approval_facts_outcome` возвращает `FactsUnavailable("absent")`, когда чекаут не опознан (нет git/`origin`/`origin` не разбирается), **до** проверки `explicit`, поэтому дело не в классе ошибки для невалидного файла — ГОДНЫЙ файл, переданный через `--approval-facts` на распакованном бандле, в не-git каталоге или в чекауте без `origin`, вообще не читается, хотя §8.4 называет `--approval-facts` override'ом @owner:github:andrei-shtanakov @id:approval-facts-explicit-path-subordinate-to-repo-id — найдено Codex-гейтом на PR #86 (раунд 2, переформулировано и усилено раундом 3); направление отказа верное (находка, не пропуск) во всех случаях; не правится этим пунктом — честно исполнить override здесь означало бы валидировать файл по инварианту 11 без `expected_repository`, с которым сравнивать, а закрыть это можно только новым способом ОБЪЯВИТЬ ожидаемый репозиторий, когда его нельзя вывести из `origin` (например, отдельная опция-компаньон к `--approval-facts`) — новая CLI-опция и решение владельца о её форме, не правка в конце ветки; докстринг `approval_facts_outcome` (`src/steward/gatecheck/cli.py`) объясняет это явно @epic:eco.governance-plane
- [ ] Регулярный сбор `approval-facts` — Stage A0 (steward-only soak) @owner:github:andrei-shtanakov @id:approval-facts-scheduled-collection @epic:eco.governance-plane
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
- [ ] Stage B: охват формирует потребитель, коллектор получает собственную личность @owner:github:andrei-shtanakov @trigger:"появился потребитель, который формирует scope, различает no-source / out-of-scope / stale / unreadable / classified_unknown и умеет запросить refresh" @id:approval-facts-consumer-driven-scope @epic:eco.governance-plane
      Статический список A0 достаточен ровно до появления такого потребителя; тогда же
      уместна read-only GitHub App для коллектора (наблюдатель не должен владеть ключом
      от наблюдаемого действия), а не раньше — App без единого читателя данных это
      раскатка ключа по флоту вперёд потребности.
      **Критерий приёмки потребителя, зафиксированный заранее:** `now >= valid_until`
      никогда не проецируется в факт об акторе; состояние — `stale`/`unknown`. Без этой
      строки B унаследует дефект «неизвестность как зелёное» этажом выше — тот самый,
      который сегодня виден на единственном бандле флота, истёкшем 20 часов назад.
- [ ] `verdicts/emitter.py`: атомарная публикация `gate_verdicts.jsonl` (temp + `os.replace`) без fsync — слабее требований §6.1 спеки approval-facts/v2 @owner:github:andrei-shtanakov @id:verdicts-emitter-fsync-debt — отдельный хвост, не расширяющий этот воркстрим; см. `docs/superpowers/specs/2026-08-21-approval-facts-v2-design.md` §10 @epic:eco.governance-plane
- [ ] dispatcher — стадия 2 хендоффа `approval-facts/v2`: вендорить пиненую копию `contracts/approval-facts/v2/` + написать `core/merge_actor.py` по образцу `core/governance.py` (+ тесты) @owner:repo:dispatcher @id:approval-facts-dispatcher-vendoring-handoff — предпосылка на нашей стороне выполнена (бандл эмитится, контракт опубликован, приёмка на реальных мержах пройдена); формальный inbox-issue в dispatcher по ADR-ECO-006 этой задачей не заведён — см. `docs/superpowers/specs/2026-08-21-approval-facts-v2-design.md` §10 @epic:eco.governance-plane

---

### 10a. codex-review kit: дорожная карта качества (план владельца, 2026-08-23)

План целиком: `docs/plans/2026-08-23-review-kit-quality-roadmap.md`. Контекст:
шесть зрячих раундов на #96 дали 19/20 подтверждённых находок, но цикл «чинить
до пустого вердикта» не сходится и стоит ~$0.5/раунд; качество дальше повышают
пункты ниже, в порядке владельца.

- [ ] Итоговое дерево PR для ревьюера @owner:github:andrei-shtanakov @id:review-kit-final-tree @epic:eco.codex-review-rollout
      Control plane (промпт, схема, скрипты) из base во временный доверенный
      каталог, чекаут — head PR, codex read-only по получившемуся дереву,
      диф — указатель на область ревью; ничего из PR не исполнять.
- [ ] Переписать шкалу severity: blocker сужен (эксплуатация, необратимая потеря, @owner:github:andrei-shtanakov @id:review-kit-severity-rewrite @epic:eco.codex-review-rollout
      обход authority, гарантированная невозможность основного сценария), для
      blocker/major обязательны файл+строка, вход, наблюдаемый результат, ссылка
      на проверенный код и почему существующие проверки не ловят
- [ ] Сократить промпт до 4 разделов (~700–1200 слов): что ревьюируется, @owner:github:andrei-shtanakov @id:review-kit-prompt-diet @epic:eco.codex-review-rollout
      инструменты/файлы, условия валидной находки, шкала+формат; механику
      доверия обеспечивает runner, а не проза
- [ ] Статический контекст — только архитектурные контракты и инварианты; обычные @owner:github:andrei-shtanakov @id:review-kit-context-demotion @epic:eco.codex-review-rollout
      исходники уходят (доступны деревом), в промпт — требование читать callers,
      callees и тесты изменённого кода
- [ ] Усилить схему вердикта @owner:github:andrei-shtanakov @id:review-kit-verdict-schema-v2 @epic:eco.codex-review-rollout
      `file`/`line`/`scenario`/`observed`/`expected`/`evidence[]`/`confidence`;
      блокируют только blocker/major с `confidence: high` и заполненным evidence.
- [ ] Большие PR: до ~20–30 файлов один прогон; крупнее — chunked по подсистемам + @owner:github:andrei-shtanakov @id:review-kit-large-pr-mode @epic:eco.codex-review-rollout
      финальный межмодульный проход; generated/lock/snapshots не ревьюировать как
      код; обрезка дифа не молча, а явным infrastructure failure

  Гардрейл влит 2026-08-23: `build-prompt.sh` (общая точка CI и local.sh)
  отказывает кодом 2 на дифе шире 30 файлов / 400 000 байт, называя причину и
  оставляя явный подъём потолка флагами; generated/lock/snapshots — правило в
  промпте (согласованность с источником, не построчное ревью). Открытым
  остаётся сам chunked-режим: группировка по подсистемам + финальный
  межмодульный проход; при любой схеме чанкинга нужен dedup-ключ находок
  `(file, line, нормализованное сообщение)` — одна находка приедет из
  нескольких чанков.
- [x] **Машинный тип находки в вердикте: `kind: defect | file-missing`** @owner:github:andrei-shtanakov @id:review-kit-file-missing-finding-type — приём входящего steward#141 (from devtools#behaviour-runner). Известный ложный класс: ревьюер заявляет «файлов нет» на файлы, которые в PR есть (опровергается `git cat-file -e <head>:<путь>`); behaviour-runner devtools на таком request-changes останавливался на человеке, потому что находка приезжала только прозой и машинно не отличалась от настоящей. PR этой ветки: поле `kind` в `.github/codex/review-schema.json` — **обязательное**, как и остальные поля схемы: необязательное не дало бы потребителю отличить «старый кит без типов» от «находка не про отсутствие файла». Путь отдельным полем не заводится — при `kind: file-missing` субъектом объявлен сам `file` (один путь на находку, `line: 0`), правило записано в промпт §3. `apply-threshold.sh` тип **валидирует** (значение вне enum = негодный вердикт, код 2, наравне с severity/confidence) и рендерит, но порога не меняет: опровержение — работа потребителя, у которого есть дерево, а скрипт дерева не видит. Там же единственный оракул правила `line: 0` для этого класса — JSON-схема условных конструкций не принимает (структурированный вывод модели), поэтому без проверки в скрипте требование промпта разошлось бы с вердиктами молча (находка ревью-гейта на этом PR, minor). Ход за devtools: авто-ветка опровержения в runner (спека §7) отдельным PR.
- [ ] Generated-фильтр не разбирает кавыченные `diff --git`-заголовки (пути со @owner:github:andrei-shtanakov @id:review-kit-quoted-diff-headers @epic:eco.codex-review-rollout
      спецсимволами/пробелами): такой путь не совпадает с сырым членом
      `--generated-list` и остаётся в дифе — худший исход сегодня это явный
      отказ по потолку (fail в сторону ревью, находка minor гейта на #99,
      подтверждена шестым заходом). Правка — нормализация кавыченной формы в
      awk `build-prompt.sh` согласованно с `core.quotePath=false` у сборки
      списка в local.sh
- [x] CI передаёт `--generated-list` в `build-prompt.sh` — включается @owner:github:andrei-shtanakov @id:review-kit-ci-generated-list
      ДЕТЕКЦИЕЙ литерала флага в извлечённой из base механике (деплой-
      ограничение head-YAML × base-скрипты обойдено без второго PR; до мержа
      кита фильтра в CI нет — явный отказ по потолку, честный и временный)
- [ ] Вето head-стороны generated-деклараций скоупить до фактически @id:review-kit-attr-veto-scope @epic:eco.codex-review-rollout
      изменённых `.gitattributes`: сейчас правка одного файла деклараций
      включает пересечение целиком и роняет base-side декларацию из другого
      (multi-file топология; minor четырнадцатого захода на #99, край назван
      в комментарии local.sh) — расхождение local↔CI в сторону ложного
      отказа по потолку @owner:github:andrei-shtanakov
- [ ] Накопление вердиктов codex-review в jsonl-корпус (PR, head_sha, модель, @owner:github:andrei-shtanakov @id:review-kit-verdict-corpus @epic:eco.codex-review-rollout
      effort, находки, что стало блокирующим) — жанр `gate_verdicts.jsonl` с
      header-записью уже есть (`src/steward/verdicts/emitter.py`); без
      накопления eval-харнесс упрётся в ручной сбор прошлых PR. ОТКРЫТЫЙ
      ДИЗАЙН-ВОПРОС владельцу: кто и куда пишет из CI — у джобы нет права
      коммитить в master; варианты «аггрегация артефактов по расписанию» и
      «ветка-корпус» дают разные гарантии

  Переоценка по спеке review-eval §14 (2026-09-15): для **терминального
  канала** пункт потерял остроту — корпус вердиктов уже на GitHub, в телах
  PR-ревью от ai-prosto, и `review-eval corpus candidates --repo R --pr N`
  читает его штатно (`gh api` на чтение, парсинг формата
  `apply-threshold.sh`, маркер `codex-terminal-review head=`), так что
  «ручной сбор прошлых PR», которого пункт боялся, не понадобился.
  Открытым остаётся только **CI-канал**: у джобы `report` по-прежнему нет
  права коммитить в master, и вердикты CI-прогонов нигде не накапливаются.
  То есть исходный дизайн-вопрос владельцу сужается до «нужен ли нам
  CI-канал отдельно, если терминальный канал даёт корпус бесплатно», и
  пункт до этого ответа остаётся `[ ]`.
- [ ] Детерминированный пре-фильтр в report-джобе (без ключа, без LLM): @owner:github:andrei-shtanakov @id:review-kit-import-detector @epic:eco.codex-review-rollout
      детектор галлюцинированных импортов — импорт, которого нет ни в
      pyproject.toml, ни в uv.lock. Один язык, один пакет-менеджер — вся
      таблица детекторов ai-review не нужна
- [x] Бамп пина openai/codex-action v1.11 → v1.12 (8636508, 2026-08-20): @owner:github:andrei-shtanakov @id:review-kit-action-pin-bump
      усиление изоляции привилегий и отклонение оверрайдов, конфликтующих с
      protected execution settings — прямо наша модель угроз (ключ в джобе,
      читающей недоверенный текст). Перед бампом проверить CHANGELOG и
      требование unprivileged user namespaces на ubuntu-раннерах
      — PR этой
      ветки, батчем со steward#115 (`review-kit-base-staleness-rationale`).
      Пин — на коммит `86365089eb2b84e0a8fb0717b304f8bdcb13b20e`, разыменован
      из аннотированного тега v1.12 напрямую у форджи (тег → tag-объект
      `cac0877` → commit; короткий `8636508` из этого пункта совпал). Предчек
      выполнен: CHANGELOG v1.12 подтверждает оба заявленных свойства;
      требование unprivileged user namespaces закрывает сам action — его
      префлайт включает `kernel.unprivileged_userns_clone` ДО drop-sudo
      (дефолтная safety-strategy), а наш джоб review sudo/Docker/
      привилегированные сокеты не использует, довод записан комментарием у
      шага в workflow
- [ ] Инлайн-аннотации из вердикта: report-джоба печатает @owner:github:andrei-shtanakov @id:review-kit-inline-annotations @epic:eco.codex-review-rollout
      `::error file=…,line=…,title=…::` для блокирующих и `::warning::` для
      остальных — находки появляются в Files changed, новых прав не нужно
- [ ] Дедуп сводок в треде PR: скрытый маркер в теле комментария + поиск @owner:github:andrei-shtanakov @id:review-kit-comment-dedup @epic:eco.codex-review-rollout
      своего последнего + правка вместо создания (10 раундов на #99 = 10
      сводок, актуальна одна). Маркер обязан пережить смену формата тела
- [x] Дедуп ревью по снимку диффа — вердикт не перегоняется на байт-идентичном входе @owner:github:andrei-shtanakov @id:review-dedup-diff-hash

  Отпечаток входа = sha256(канонизированный `git diff base...head` + промпт +
  схема + порог); вердикт публикуется с отпечатком в маркере (рядом с
  `head=…`), прогон перед вызовом codex ищет вердикт с тем же отпечатком →
  найден → «вердикт унаследован от прогона N, дифф не менялся», вызов
  пропущен; изменился хоть байт входа (включая инструкции кита) → обычное
  ревью. Довод измерен: 26–28.08 минимум 5–6 из ~15 платных CI-прогонов шли
  по байт-идентичным диффам (rerun после инфрафлейка, close/reopen против
  bot-actor, update-branch брокера); после смены дефолта (vault#106) аргумент
  сильнее — всю нагрузку несёт терминальный канал, повторы жгут подписочные
  лимиты, тот самый ресурс, ради которого дефолт менялся. Приём входящего
  steward#126 (from ecosystem-kb, PROPOSAL §6 C4; прототип prime-agent).

  Триггер сработал (владелец, 2026-08-28; исходный @trigger снят — «дефолт
  закреплён» констатирован), дизайн утверждён с поправками. Кит-половина
  ВЛИТА (PR #132): `local.sh --fingerprint-only` — оффлайн
  (несовместим с `--fetch`, ls-remote пропущен), framed-хеширование с версией
  протокола `codex-terminal-review-fingerprint-v1` (имя компоненты + точная
  длина байт), состав: prompt.txt + схема + apply-threshold.sh + эффективный
  REVIEW_CMD; stdout — ровно одна строка 64-hex для непустого входа, пустой
  диф — штатное «ревьюировать нечего» без отпечатка. Интеграция в
  `review-pr.sh` ВЛИТА (devtools#72 принят, обработан и закрыт 2026-08-28;
  плюс два боевых фикса — pre-fetch базы с явным refspec, сверка головы на
  пути same-head-наследования), но первая живая проверка со стороны steward
  (PR #134) вскрыла: дедуп МЁРТВ на установленном gh 2.83.1 — комбинация
  `--slurp`+`--jq` не поддерживается, чтение прошлых ревью падает на каждом
  прогоне, «не прочитались» fail-open'ится в полный прогон (направление
  верное, экономия нулевая и молчаливая). Дефект devtools#75 ВЫПОЛНЕН и
  закрыт 2026-08-28 (фикс devtools PR #76, merge `87b7e76`): фильтр внешним
  jq вместо отвергаемого комбо, раздельные причины отказа gh/jq — мёртвый
  кэш больше не маскируется под «нет ревью», стаб gh в тестах сам отвергает
  `--slurp`. Живой инцидент наследования по критерию приёмки состоялся:
  steward#135 — боевой прогон опубликовал approve с fp-маркером, повторный
  dry-run на неизменном head ответил «вердикт унаследован (--approve)» за
  10.3 сек без вызова codex.

  Baseline-замер 2026-08-28 (скан маркеров по всему флоту): fp-маркеры
  только в steward — 6 платных прогонов (#134×2, #135, #136, #137×2),
  опубликованных наследований 0, плюс 1 same-head наследование (приёмочный
  инцидент #135). Методологическая оговорка: same-head наследование
  завершает прогон БЕЗ публикации и следа на GitHub не оставляет —
  опубликованные маркеры видят только кросс-head наследования
  (update-branch брокера, close/reopen); замер по маркерам — нижняя
  оценка экономии. В остальные репо кит ещё не разъехался re-vendor'ом,
  `review-pr.sh` feature-detect'ит `--fingerprint-only` по-репно.
  Остаток: снять долю унаследованных против платных после накопления
  органических событий (первый органический кросс-head инцидент —
  update-branch брокера — станет виден прямо в опубликованных маркерах).
  Остаток («снять долю унаследованных против платных») снят 2026-09-14 на
  органических событиях после волны: на выборке 6 репо × 8 последних
  смерженных PR — 37 fp-маркированных платных прогонов и 1 опубликованное
  кросс-head наследование (devtools#231). Нижняя оценка ~1/38; same-head
  наследования (dry-run на неизменном head) следа на GitHub не оставляют и в
  замер не попадают. Фича доставлена и работает по флоту; экономия пока
  определяется частотой update-branch/close-reopen, а не механикой. Закрыт;
  повторный замер — при следующем изменении дефолта ревью.

- [x] Волна re-vendor fp-кита по флоту: `local.sh --fingerprint-only` есть @owner:github:andrei-shtanakov @id:review-kit-fp-wave
      только в steward, `review-pr.sh` feature-detect'ит флаг по-репно —
      до волны дедуп на остальном флоте молча выключен, каждый прогон
      платный, наследовать не из чего. Исходящее devtools#79 (состав:
      `scripts/review/local.sh` >= `fee3159`, мерж #132; попутный срез
      автотриггеров CI-гейта #137 — на усмотрение волны). Сделано =
      вендор-копии байт-совпадают, первый терминальный прогон в чужом
      репо публикует маркер с `fp=`
  Закрыт 2026-09-14 по evidence: волна devtools#228 (PR-1 + PR-2) разнесла
  `local.sh` @ `a2d7e71` (≥ `fee3159`, с `--fingerprint-only`) на все 22 копии;
  терминальные прогоны в чужих репо публикуют маркеры с `fp=` — по 6 репо
  (dispatcher, spec-runner, devtools, maestro, kapelle, arbiter) на последних 8
  смерженных PR каждого — 37 fp-маркированных ревью, в т.ч. dispatcher#250/252/255,
  kapelle#90/91/92, spec-runner#512/513, devtools#226/227/231.

- [x] Линза подмены тестов в `review-prompt.md`: удаление/ослабление проверки
      без замены (снятые assertions, skip/xfail на живых тестах, сужение
      параметризации, выключение проверки в конфиге) = находка minimum major.
      Сейчас такой дифф читается гейтом как обычный код — tdd-gate держит
      байт-лок только в TDD-режиме, обычные PR не покрыты вовсе. Размер S:
      секция в промпте, разъезжается по 7 репо штатным re-vendor'ом; промпт
      общий для CI и терминального канала — линза ужесточает оба — приём
      входящего steward#127 (from ecosystem-kb, PROPOSAL §6 C9; прототип
      CodeJury) @owner:github:andrei-shtanakov @id:review-lens-test-tampering —
      PR этой ветки: абзац-линза в §4 промпта; форма «явный довод в описании
      PR» из issue заменена на «довод, видимый в дереве» — описание PR в вход
      ревьюера не попадает (ни в CI, ни в local.sh), требование, которое
      ревьюер не может проверить, было бы мёртвой буквой
- [ ] Измеримый eval: 10–20 прошлых PR (с дефектами, чистые, крупные), метрики @owner:github:andrei-shtanakov @id:review-kit-eval-harness @epic:eco.codex-review-rollout
      precision блокирующих/recall major+blocker/ложные блокировки/доля без
      evidence/стоимость; для гейта precision важнее полноты

  Дизайн согласован владельцем 2026-09-14 —
  `docs/superpowers/specs/2026-09-14-review-eval-harness-design.md` (D1–D12):
  gold-набор поверх прокси из истории, детерминированный 1:1 матчер с
  adjudication-очередью, `precision_lower_bound` до разбора очереди,
  `blocking_recall` vs `detection_recall_any_severity`, эксплуатационные
  метрики, стоимость и для неуспешных прогонов, повторения + paired
  bootstrap; каждый кейс — detached worktree на историческом `head_sha`, объект
  измерения — текущий кит steward; sidecar-артефакты через `REVIEW_VERDICT_OUT`
  (local.sh) и `REVIEW_USAGE_OUT` (harness-claude). Код —
  `src/steward/review_eval/` + `review-eval` (отклонение от `tools/`: hatchling
  и pyrefly видят только `src/`). Закрывается по живому первому прогону на gold
  ≥ 10 кейсов, не по тестам.

  **Код влит четырьмя PR** (#165 корпус/кэш/порог, #166 матчер и генератор
  кандидатов, #167 раннер, #168 метрики и отчёт, плюс CLI этой ветки): пакет
  `src/steward/review_eval/` (corpus, cache, threshold, matcher, runner,
  metrics, report, candidates, cli) и точка входа `review-eval` с командами
  `corpus validate [--register [--retire-deleted] [--reidentify ID,…]]`,
  `corpus candidates --repo --pr`, `corpus materialize`,
  `run --variant H:M[:E] --out`, `metrics <run_dir>`, `compare <a> <b>`;
  правки кита `REVIEW_VERDICT_OUT`/`REVIEW_USAGE_OUT`/`REVIEW_EFFORT`;
  корпус-черновики steward#152/#155/#156/#157/#159/#161; док
  `docs/review-eval.md`. Пункт остаётся `[ ]`: **закрывается по живому
  прогону на gold ≥ 10 кейсов с evidence-копией прогона в
  `docs/evidence/`** — не по тестам и не по влитому коду (то же правило, что
  закрыло V1 live run, §4).

  **Разметка закрыта 2026-09-18 — корпус готов к живому прогону.** Тринадцать
  gold-кейсов (draft: 0), шестнадцать дефектов, одна запись `non_defects`:
  шесть исходных черновиков адъюдицированы, добор — steward#130, #137, #151,
  #162, #167, #168 и крупный #86.

  **Состав знаменателя `blocking_recall` — три дефекта, и его надо называть, а
  не читать как «recall по репозиторию»:** #130-1 (`TODO.md`), #162-1
  (дизайн-спека) и #137-1 (`.github/workflows/codex-review.yml`). Ребро
  матчера строится только когда путь находки входит в `match.files` gold,
  поэтому пока все блокирующие gold лежали в Markdown, находка в исполняемом
  файле не могла поднять метрику ПО ПОСТРОЕНИЮ — вариант, хорошо читающий
  прозу и плохо код, получал бы 1.00 (2/2) (находка ревью-контура на PR #170).
  Добор #137 это закрыл: его major — в исполняемой конфигурации CI, и ловится
  он пониманием поведения брокера, а не сверкой текста с текстом. Блокирующий
  major в Python есть (#167-1, `runner.py`), но его кейс несёт
  `blocking_complete: false`, поэтому в знаменатель recall он НЕ входит — см.
  пункт ниже. Из четырёх кандидатов истории подтверждены три (#155-1, #157-1,
  #161-1), а #157-2 ОТКЛОНЁН как ложный по эмпирической проверке (git 2.54.0
  возвращает одну запись, сквозной прогон collect-context.sh — код 0) и
  перенесён в `non_defects` под `NF-…-157-1`; id `D-…-157-2` списан
  надгробием. Блокирующих gold-дефектов ТРИ (#130-1, #137-1 и #162-1, все major,
  все в кейсах с `blocking_complete`; состав и его чтение — абзац выше) —
  знаменатель `blocking_recall` впервые непуст, и это пинует тест
  `test_repo_corpus_is_valid_and_registered`.
  У #168 `blocking_complete: false` сознательно: диапазон +5166 строк не
  прочитан сплошь, кейс входит в precision и не входит в recall (D8).
  Остаток до живого прогона: разовый bump `MATCHER_VERSION`
  (`review-eval-matcher-version-bump`) и бюджет на прогон.

  Кейсы #137 и #167 запинованы на головы ПРОМЕЖУТОЧНЫХ раундов ревью, то есть
  на головы до фикса — это предмет открытого пункта `review-eval-prefix-heads`
  (такие головы достижимы только через `refs/pull/<n>/head`, и материализация
  такого объекта — отдельный шаг). Для этих двух кейсов вопрос ЗАКРЫТ
  проверкой, а не допущением, и проверка выполнена ИМЕННО последовательностью
  `ensure_objects`: `git clone --bare --no-hardlinks <remote_url>`, затем
  `git cat-file -e <sha>^{commit}` (как `has_object`), затем — где объекта нет
  — `git fetch <remote_url> <sha>` без `--depth`, при пинах
  `GIT_CONFIG_GLOBAL/SYSTEM=/dev/null` и `LC_ALL=C`. Результат различается по
  кейсам и потому записан по отдельности: `8002bae5` (#137) присутствует уже
  после bare-клона — сетевого fetch'а не требуется вовсе; `5ed67ec2` (#167)
  после клона отсутствует, тянется fetch'ем по SHA, и `has_object` его
  подтверждает. Отказ `corpus materialize` на чистой машине не наступает, и
  единственный дефект вне прозы из знаменателя не выпадает. Первая запись этой
  проверки называла её «тем самым путём, которым идёт cache.py», хотя
  фактическая команда несла `--depth 1` и шла в пустой репозиторий — поймано
  приёмочным dry-run на этом же PR и здесь исправлено. Пункт
  `review-eval-prefix-heads` остаётся открытым как общее правило для будущих
  кейсов, но эти два от него не блокированы.

  Требование DoD «крупные» закрыто кейсом `class: large` — steward#86, заведён
  руками (`source: manual`): ревью ai-prosto на нём нет, он старше контура, а
  для `large` gold и не нужен — измеряется отказ гардрейла, ревьюер не
  вызывается. Диапазон превышает ОБА умолчательных потолка `build-prompt.sh`
  (35 файлов против 30 и 527980 байт против 400000; `local.sh` своих значений
  не держит и пробрасывает только оверрайды оператора), поэтому
  `expected_outcome: guardrail_rejection` без `local_args`. `base_sha` взят
  первым родителем merge-коммита, а не merge-base с текущим master: PR влит,
  и наивный merge-base даёт саму голову, то есть пустой диапазон — капкан,
  о котором предупреждает §1 дока, проверен на этом кейсе.

  Открытые решения владельца, вскрытые ревью-контуром харнесса (каждое —
  отдельный пункт ниже, чтобы не потерялось в прозе): граница секции находок и
  экранирование evidence в ките, разовый bump `MATCHER_VERSION`, эвристика
  секретных имён, промежуточные метрики идущего прогона, дайджест дифа вместо
  прокси по версиям инструментов, головы PR до фикса для gold-кейсов.

  То же правило применено к самой разметке: она подтвердила как РЕАЛЬНЫЕ и не
  исправленные три дефекта этого репо, и каждый заведён пунктом ниже, а не
  оставлен в `notes` корпуса — `review-eval-contradicted-gold-second-path`
  (живой дефект `metrics.py`), `review-eval-spec-starting-corpus-duplicate-case`
  и `review-eval-contract-table-missing-field` (оба — действующая дизайн-спека).
  Плюс `review-eval-candidates-review-round` про сам генератор черновиков.
  Находка ревью-контура на этом же PR: без пунктов они были видны только внутри
  YAML-кейсов, то есть ни владельцу, ни дайджесту.
- [ ] Кит: граница секции находок и экранирование evidence в `apply-threshold.sh` @owner:github:andrei-shtanakov @id:review-kit-findings-boundary @epic:eco.codex-review-rollout
      `note` модели рендерится **раньше** находок и не экранируется, границы
      перед секцией находок нет. Поэтому note, оформленный как
      `### [major] … — \`file:line\``, для `corpus candidates` неотличим от
      настоящей находки: генератор ловит только пустой вердикт (строка
      `Находок нет.` рядом с заголовком находки), а в непустом лишняя находка
      попадёт в черновик, и снимать её — работа разметчика. Второе там же:
      текст вида `; \`path:line\` — ` внутри `reason` неотличим от второй
      записи evidence, потому что разделитель записей и допустимый текст
      причины — одни и те же символы. Лечится на стороне кита — маркер границы
      в рендере и экранирование (или структура вместо строки), — а не догадками
      в парсере
- [ ] Разовый bump `MATCHER_VERSION` перед первым живым прогоном @owner:github:andrei-shtanakov @blocked_by:todo://steward/review-kit-eval-harness @id:review-eval-matcher-version-bump @epic:eco.codex-review-rollout
      Правила матчера менялись после объявления версии 1 (согласие по `kind`,
      общее правило номера строки, уникальные ключевые слова), а версия не
      поднималась: прогонов ещё нет, сравнивать отчёты не с чем, и версия,
      прокрученная до пяти до первого запуска, потеряла бы смысл метки
      несравнимости. `rules_digest()` расхождение фиксирует, но собственная
      декларация в `matcher.py` требует поднимать версию на любое
      семантическое изменение. Решение: поднять **один раз** перед первым
      прогоном либо снять требование из декларации
- [ ] Эвристика секретных имён окружения: allow-list вместо подстроки @owner:github:andrei-shtanakov @blocked_by:todo://steward/review-kit-eval-harness @id:review-eval-secret-name-heuristic @epic:eco.codex-review-rollout
      `provider_env_fingerprint` не хэширует значения переменных, в имени
      которых есть `KEY`/`TOKEN`/`SECRET`/`PASSWORD`/`CREDENTIAL`. Это
      подстрока, а не знание: переменная с ключом в значении и безобидным
      именем (`ANTHROPIC_AUTH`) попадёт в отпечаток вместе со значением, а
      безобидная переменная с `TOKEN` в имени (`*_TOKEN_LIMIT`) из отпечатка
      выпадет молча. Решение — закрытый список имён, чьи значения хэшируются,
      вместо чёрного списка подстрок
- [ ] Промежуточные метрики идущего прогона (`metrics --partial`) @owner:github:andrei-shtanakov @blocked_by:todo://steward/review-kit-eval-harness @id:review-eval-partial-metrics @epic:eco.codex-review-rollout
      `load_results` требует полноты и закрытого манифеста, поэтому посмотреть
      на половину большого прогона нельзя вовсе: оператору остаётся читать
      `result.json` глазами. Правило верное (`status: ok` по половине прогона
      выглядел бы измерением), но нужен явный режим, который печатает числа с
      пометкой «прогон не завершён» и **не** пишет `metrics.json`
- [ ] Дайджест отревьюированного дифа в `result.json` вместо прокси по версиям @owner:github:andrei-shtanakov @blocked_by:todo://steward/review-kit-eval-harness @id:review-eval-diff-digest @epic:eco.codex-review-rollout
      Сейчас «тот же вход или нет» доказывается косвенно: версии `git` и
      клиентов ревьюера плюс дайджест конфига репозитория кэша. Поэтому
      обновление системы прерывает растянутый на дни прогон, хотя диф мог не
      измениться. Дайджест самого дифа (того, что кит передал модели) отвечал
      бы на вопрос прямо, и сверку версий можно было бы ослабить
- [ ] Головы PR до фикса (`refs/pull/<n>/head`) для gold-кейсов @owner:github:andrei-shtanakov @blocked_by:todo://steward/review-kit-eval-harness @id:review-eval-prefix-heads @epic:eco.codex-review-rollout
      Черновики из истории пинуют `head_sha` из маркера ревью — то дерево,
      которое ревьюер видел. Но для кейса «дефект должен быть найден» нужна
      голова **до** фикса, а она у смерженного PR доступна только через
      `refs/pull/<n>/head`, и материализация такого объекта — отдельный шаг
      (`corpus materialize` тянет только `base_sha`/`head_sha` кейса). Без
      этого часть gold-кейсов придётся пинить руками
- [ ] Выбор модели и reasoning-уровня — только по eval (минимум два варианта @owner:github:andrei-shtanakov @blocked_by:todo://steward/review-kit-eval-harness @id:review-kit-model-selection @epic:eco.codex-review-rollout
      модели × два уровня), не по рассуждению в комментарии workflow
- [ ] Поднять `blocking_complete` у кейса #167 сплошным чтением диапазона — иначе блокирующий gold в Python вне знаменателя recall @owner:github:andrei-shtanakov @id:review-eval-167-blocking-complete @epic:eco.codex-review-rollout
      Кейс `andrei-shtanakov.steward-167` несёт единственный в корпусе
      блокирующий gold в исполняемом Python (`D-…-167-1`, лексическое
      несовпадение resolved-целей и нерезолвленного `out_dir` в `run_all`,
      подтверждён и исправлен в master добавлением `.resolve()`). Флаг стоит
      `false` честно: диапазон — 6 файлов и 6085 добавленных строк, из них
      ~2350 строк нового исполняемого кода (`runner.py` создаётся этим PR
      целиком), сплошь он не прочитан. Следствие по D8: кейс входит в
      precision и НЕ входит в знаменатели `blocking_recall`/`false_block_rate`,
      то есть recall по-прежнему не содержит ни одного дефекта в Python.
      Работа — сплошное чтение диапазона и, если блокирующих находок больше
      нет, поднятие флага в `true`. До этого состав знаменателя надо называть
      при чтении отчёта (см. прозу выше).
- [ ] Второй путь `_contradicted_gold` безусловен: верный gold чернится и выпадает из знаменателей recall @owner:github:andrei-shtanakov @id:review-eval-contradicted-gold-second-path @epic:eco.codex-review-rollout
      Подтверждено адъюдикацией 2026-09-18 как живой дефект шипнутого кода
      (`D-…-168-1`, находка ревью PR #168, в master НЕ исправлена). Первый путь
      проверяет `file_lines(defect.file) is not None` и потому корректен; второй
      (`ids.update(matched.assigned[pos] for pos in refuted …)`) повторной
      проверки не делает. С alias-путями в `match.files` (заявленная фича §5)
      находка, назвавшая СУЩЕСТВУЮЩИЙ алиас, назначается на gold, чей
      собственный `file` и правда отсутствует, — и верная разметка объявляется
      противоречащей дереву. Следствия: gold уходит из знаменателей
      `blocking_recall`/`detection_recall`, печатается в очереди как «gold
      противоречит дереву head» и держит вариант вне `status: ok`. Сработает на
      первом же платном прогоне с alias-путями. Класс — minor: нужна особая
      раскладка, затронута бухгалтерия метрик, не решение гейта.
- [ ] Спека §13 заводит #155 двумя кейсами при уникальном `case_id` — пара непредставима @owner:github:andrei-shtanakov @id:review-eval-spec-starting-corpus-duplicate-case @epic:eco.codex-review-rollout
      Подтверждено адъюдикацией 2026-09-18 (`D-…-162-4`, находка ревью PR #162,
      в спеке НЕ исправлена): §13 называет #155 строкой 785 как `defective` и
      строкой 788 как `large`, а §5 требует `case_id` вида `<owner.name>-<pr>`,
      уникальный в корпусе, — `load_cases` отвергает дубликат кодом 2. Разметчик
      упирается в это на практике: два кейса на один PR корпус не принимает.
      Лечится не сверкой, а решением: стартовый корпус УЖЕ собран (13 кейсов,
      см. пункт выше) и от плана §13 отличается, поэтому вероятная правка —
      списать план §13 как исполненный и сослаться на фактический состав, а не
      примирять две записи про #155.
- [ ] Контрактная таблица §9 требует кода 0/1 там, где порог выходит кодом 2 @owner:github:andrei-shtanakov @id:review-eval-contract-table-missing-field @epic:eco.codex-review-rollout
      Подтверждено адъюдикацией 2026-09-18 (`D-…-162-3`, находка ревью PR #162,
      в спеке НЕ исправлена): §9 велит прогнать через настоящий
      `apply-threshold.sh` таблицу, где «каждое поле по отдельности пустое /
      пробельное / ОТСУТСТВУЮЩЕЕ», и требует, чтобы «код выхода 0/1 обязан
      совпасть с предикатом на каждой строке». Для строки с отсутствующим
      обязательным полем скрипт не доходит до порога вовсе: префлайт схемы
      (`jq -e … all(type == "string")`) даёт код 2. Требование неисполнимо
      буквально, и реализация это уже обошла — `tests/review_eval/test_threshold.py`
      держит отдельную `SCHEMA_INVALID_TABLE`, которая ждёт именно кода 2.
      Правка — привести формулировку §9 к тому, что реализовано (две таблицы с
      разными контрактами), иначе следующий автор будет искать несуществующее
      расхождение кода с спекой.
- [ ] `corpus candidates` умеет только ПОСЛЕДНЕЕ ревью ai-prosto — кейс с настоящим блокирующим дефектом им не собрать @owner:github:andrei-shtanakov @id:review-eval-candidates-review-round @epic:eco.codex-review-rollout
      Вскрыто разметкой 2026-09-18 при доборе корпуса. Закономерность, а не
      случай: PR, где ревьюер нашёл РЕАЛЬНЫЙ major, — это ровно тот PR, где
      автор его принял и починил до мержа, поэтому последний раунд ревью там
      всегда чистый. Из семи PR с major-находкой в истории репо у пяти (#129,
      #137, #153, #163, #167) major лежит на раннем раунде, а генератор берёт
      `ai_reviews[-1]` (`candidates.py::draft_case`) и его маркер головы.
      Следствие: черновик из истории в принципе не может принести блокирующий
      gold — кроме случайных PR с единственным раундом (#130, #162, ими и
      закрыт добор). Довод генератора («у раннего ревью другой head, номера
      строк указали бы не в то дерево») снимается выбором головы ТОГО ЖЕ
      раунда. Просится опция вида `--review-id`/`--round`: брать тело и маркер
      выбранного ревью, `commits_after` считать от него же. Не сделано в
      разметке сознательно — это правка продуктового кода, а не данных.
- [ ] `corpus materialize` без локального чекаута соседа уходит в сеть с @owner:github:andrei-shtanakov @blocked_by:todo://steward/review-kit-eval-harness @id:review-eval-materialize-network-git-config @epic:eco.codex-review-rollout
      `scrubbed_git_env(None)` — тот же пин `GIT_CONFIG_GLOBAL`/
      `GIT_CONFIG_SYSTEM=/dev/null`, что и у чтения дерева ради
      воспроизводимости диффа (`diff.context`/`diff.algorithm`), но здесь он
      попадает и на сетевые `git clone --bare`/`git fetch` (ревью-находка
      части 3, minor). Выключает `credential.helper`, `url.<…>.insteadOf` и
      `http.proxy` пользователя: материализация из GitHub у репозиториев,
      требующих аутентификации или сетевого сетапа через `~/.gitconfig`,
      отказывает кодом 2, хотя обычный `git clone` того же пользователя
      работает. ОТКРЫТЫЙ ВОПРОС ВЛАДЕЛЬЦУ: пин конфига для сетевого пути —
      осознанный периметр («материализация только для анонимного HTTPS») или
      недосмотр, который надо развести от пина для чтения дерева
- [x] Экономный триггер ревью: драфты без лейбла `codex-review` не ревьюятся @owner:github:andrei-shtanakov @id:review-kit-on-demand-trigger
      (итерация бесплатна); запрос = снятие драфта (автозапуск) или лейбл
      `codex-review` (действует на следующие пуши драфта). Форма отказа —
      синтетический вердикт + красный report с причиной, не skipped-джобы
      (пропущенный джоб засчитывается required-чеку как пройденный)

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

- [ ] Пуш в явно названный чужой ref ревьюится от ветки по умолчанию @owner:github:andrei-shtanakov @id:review-kit-explicit-target-base @epic:eco.codex-review-rollout

  При `git push origin hotfix:release/1.0` хук считает форму поддержанной и зовёт
  `local.sh` без `--base`, поэтому диапазон строится от `origin/HEAD`. README это
  признаёт и советует ручной `--base` — но код всё равно трактует зелёный вердикт как
  успех и пропускает пуш, то есть пропускает зелёное, которое сам документ объявил
  несопоставимым с CI. Брать базой `remote_ref` **отклонено** (решение 2026-08-21): для
  обычного `git push origin hotfix` он равен самой ветке, и диапазон схлопнулся бы —
  тихий fail-open, потому что пустой диф у нас законно зелёный.

- [ ] `--base` на remote-tracking ref чужого remote тихо ломает `--fetch` @owner:github:andrei-shtanakov @id:review-kit-base-remote-mismatch @epic:eco.codex-review-rollout

  При настроенных `origin` и `upstream` вызов `--base upstream/release/1.0 --fetch`
  оставляет переменную remote равной `origin`, `track_branch` не распознаётся, `--fetch`
  объявляется игнорируемым, свежесть не проверяется — и прогон может дать 0 на
  устаревшем диапазоне. Обход, описанный в README (добавить только `--base`), для такого
  репозитория недостаточен.

- [ ] Отличать «скрипт вернул 1 намеренно» от «скрипт умер с кодом 1» @owner:github:andrei-shtanakov @id:review-kit-threshold-exit-provenance @epic:eco.codex-review-rollout

  Вызывающий трактует `1` от `apply-threshold.sh` как «есть находки выше порога».
  Но `1` достижим и изнутри скрипта ДО его финальной развилки — например, `jq -e`
  или `test` под `set -e` на кривом входе. Тогда сбой инструмента снова
  предъявляется как вердикт о патче: последний оставшийся слой класса, который
  PR #92 закрывал четырьмя заходами (коды вызываемого → место хранения входа →
  факт вызова → создание файла вывода). Починка требует различать источник кода
  внутри самого скрипта, то есть менять контракт **вендоримой** части кита —
  поэтому вынесено отдельно, а не сделано наспех в #92.

- [ ] `VERDICT_FILE` предполагает плоскую раскладку скачанного артефакта @owner:github:andrei-shtanakov @id:review-kit-artifact-layout @epic:eco.codex-review-rollout

  Если джоб `review` начнёт публиковать артефакт каталогом, а не одиночным
  файлом, `download-artifact` восстановит его на уровень глубже, и страж скажет
  «вердикта нет» при успешном скачивании. Ошибка в безопасную сторону — чек
  краснеет, — но диагноз указывает не туда. Низкий приоритет ровно поэтому.

- [x] `checksum.sh`, PIN и watch дрейфа — при первом потребителе кита @owner:github:andrei-shtanakov @id:review-kit-vendoring @epic:eco.codex-review-rollout

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
  Закрыт 2026-09-14: потребителей уже 22 (волна devtools#228), у каждого PIN
  из 7 строк; `checksum.sh` у продюсера с семантикой переходных членов,
  `noglob` и отказом на незапинованный присутствующий член (#155–#159).
  Бутстрап-контракт «чекер исполняется ИЗ BASE» и drift-watch
  (`review-kit-drift.yml`) приняты в CI как минимум 7 потребителей
  (spec-runner, dispatcher, maestro, kapelle, arbiter, atp-platform,
  ai-orchestrators-workspace — по локальным чекаутам). Покрытие остальных 15
  копий CI-чекером и drift-вахтой — вопрос волн devtools, отмечен в
  `review-kit-next-wave`.

- [x] Волна ре-вендора среза B обходит `arbiter` и `atp-platform`, пока те не @id:review-scope-wave-arbiter
      подтянут кит к текущему релизу @owner:github:andrei-shtanakov
 — закрыт 2026-09-19: оба догнались сами
      по нашим inbox-запросам (arbiter#107, atp-platform#326 — оба CLOSED),
      `prose-paths.env` и переходная строка инвентаря есть в дефолтных ветках
      обоих. Их догоняющие PR прошли своим ревью-контуром и вернули четыре
      находки в кит — steward#173…#176 ниже.
- [x] То же по `atp-platform` @owner:github:andrei-shtanakov @id:review-scope-wave-atp-platform
 — см. строку выше, один факт.
- [x] Приём входящих по срезу B: `checksum.sh` — шапка инвентаря утверждала @id:checksum-inventory-header-contradiction
      «переходных членов нет» на три строки выше абзаца, вводящего
      `?prose-paths.env`, и заодно заявляла охват «все 22 копии», из которого
      сама же волна вывела исключения @owner:github:andrei-shtanakov
 — приём steward#173 (from
      arbiter) и steward#174 (from atp-platform), одна находка двумя каналами.
- [x] `local.sh`: режимы-запросы (`--print-review-cmd`, `--fingerprint-only`, @id:local-sh-query-modes-invalidate-sidecars
      опечатка во флаге) стирали sidecar-артефакты предыдущего прогона, ни
      разу не вызвав ревьюера @owner:github:andrei-shtanakov
 — приём steward#175 (from
      arbiter). Решение владельца кита: инвариант «файл есть = результат
      ИМЕННО этого прогона» этого не требует — сброс перенесён за разбор
      аргументов и ранние выходы, валидация ФОРМЫ осталась в префлайте.
- [x] `local.sh` среза B: частичное усечение дифа фильтром нигде не @id:review-kit-scope-filter-followups
      объявлялось (ни оператору, ни модели), а пустое значение ключа в
      `review-scope.env` печатало «фильтр не применяется» и тут же роняло
      прогон кодом 2 @owner:github:andrei-shtanakov
 — приём steward#176 (from
      atp-platform); третья находка того же issue — та же, что в
      `local-sh-query-modes-invalidate-sidecars`.
- [ ] Окно инвалидации sidecar: конфигурационные отказы резолва харнесса @owner:github:andrei-shtanakov @id:review-kit-sidecar-reset-window @epic:eco.codex-review-rollout
      (нет `harness-claude` на полуобновлённом ките, `REVIEW_MODEL=""`,
      небезопасное слово в `REVIEW_EFFORT`, кривой `REVIEW_INCLUDE_PROSE`)
      физически стоят ВЫШЕ точки сброса `REVIEW_VERDICT_OUT`/`REVIEW_USAGE_OUT`
      и прежний файл не снимают: они обязаны отработать до
      `--print-review-cmd`, а тот — до разбора аргументов. Потребитель с
      фиксированным путём sidecar прочитает на коде 2 чужой вердикт; для
      «код 2 + схемно негодный старый файл» `review_eval.classify` вернёт
      `invalid_verdict` (вина модели) вместо `config_failure`. Закрывается
      только перестановкой резолва харнесса ниже разбора аргументов. Штатный
      потребитель (`review_eval/runner.py`) сам делает unlink и не затронут —
      находка приёмочного ревью PR #177 (minor/high), граница названа в
      комментарии `local.sh` на самой точке сброса.
- [ ] Перевод строки В ИМЕНИ файла ломает разбор фильтра области ревью @owner:github:andrei-shtanakov @id:review-scope-newline-in-path @epic:eco.codex-review-rollout
      `git diff -z` отдаёт пути сырыми, но `tr '\0' '\n'` схлопывает
      разделитель записи с байтом внутри самого имени, и такой путь
      разъезжается на две несуществующие записи. Страж (счётчик NUL-байтов
      в `changed-paths.z` против числа фактически разобранных путей) ловит
      расхождение целиком и отказывает кодом 3 fail-closed — файл больше не
      выпадает молча, но и не ревьюируется: полноценная поддержка таких
      имён (пропустить их через ревью, а не отказывать) требует другого
      приёма разбора NUL, например `read -d ''` (bash), которого в POSIX sh
      нет.
- [x] Сторож upstream-drift для `scripts/review/prose-paths.env`: сверка с SSOT @owner:github:andrei-shtanakov @id:review-scope-upstream-drift
      `devtools/contracts/review-scope/v1/prose-paths.env` по расписанию, как
      `impresario-contract-drift.yml`. Copy-integrity у копии уже есть
      (инвентарь checksum.sh), расхождения с SSOT не заметит ничто
 —
      `.github/workflows/review-scope-drift.yml`, 2026-09-19. Вахта нашла
      предмет сразу же, и это НЕ просто «апстрим уехал»: копия была
      ОТРЕДАКТИРОВАНА НА МЕСТЕ — то, что её собственная шапка запрещает
      («Правка — только ре-вендором, не на месте»). При заведении (`84ce30a`)
      тело копии побайтно совпадало с SSOT на пине `f523d82`; коммит
      `6a58c0a` (правка `*requirements*.txt` по кругу 2 ревью steward#172)
      изменил её ЛОКАЛЬНО, а тот же по смыслу фикс независимо приехал в
      devtools как `33bb8b1` с другой формулировкой. Поймать это было нечем.
      Рабочие ключи (`PROSE`/`CODE_OVERRIDE`) при этом совпадают побайтно —
      поведение сошлось, разошлась проза.
- [x] Ре-вендор `prose-paths.env` с devtools HEAD (`33bb8b1`) + бамп шапки @owner:github:andrei-shtanakov @id:review-scope-revendor-prose-paths
      `# VENDORED:`; гасит обе находки вахты `review-scope-drift`
      (несовпадение со своим пином и движение SSOT). Не срочно по поведению —
      рабочие ключи совпадают, — но это возврат копии в дисциплину вендоринга,
      из которой её вывели правкой на месте. Дешевле сделать прицепом к
      следующей ПОВЕДЕНЧЕСКОЙ правке кита: отдельная волна по 22 репо ради
      переформулировки комментария не окупается
 —
      закрыт ровно тем прицепом, который сам и предписывал: копия ре-вендорена
      с пина `8cd6456` (он же несёт ПОВЕДЕНЧЕСКУЮ правку —
      `review-kit-scope-agent-instructions` ниже), тело побайтно совпадает с
      SSOT на пине, шапка бампнута. Промежуточный `33bb8b1` перепрыгнут: пин
      называет ревизию SSOT, а не каждый коммит по пути к ней.

- [x] Приём steward#180 (from devtools#265 ← atp-platform#329): инструкции агентов — код, а не проза @owner:github:andrei-shtanakov @id:review-kit-scope-agent-instructions @epic:eco.codex-review-rollout — апстрим PR #182, потребители — волна devtools#292 (2026-09-21)
      Инструкции агентов — код, а не проза. `CODE_OVERRIDE` получает восемь глобов
      (`.claude/*`, `*/.claude/*`, `.agents/*`, `*/.agents/*`, `CLAUDE.md`,
      `*/CLAUDE.md`, `AGENTS.md`, `*/AGENTS.md`). Это возврат покрытия, а не
      его расширение: до среза B диф шёл ревьюеру целиком, а после — ветка,
      трогающая только `.claude/skills/*/SKILL.md` или корневой `CLAUDE.md`,
      проваливалась в `PROSE=*.md` и получала код 5 «всё отфильтровано»,
      вердикт не выносился вовсе. `CLAUDE.md` опасен отдельно: authority-root
      его НЕ накрывает, а в нём живут `merge_policy`, «Мерж: человек» и
      бюджет платных прогонов. Якорь `*/CLAUDE.md` намеренно на `/`-сегменте —
      `CLAUDE-migration.md` и `claude-notes.md` остаются прозой. Наблюдаемый
      признак: такая ветка перестаёт получать код 5 и уходит модели.
      **Апстрим закрыт PR #182**; чекбокс был намеренно оставлен снятым:
      признак «сделано» заявителя наблюдается у ПОТРЕБИТЕЛЯ с обновлённым
      китом, а у нас правило лежит только в апстрим-копии. Закрывалось волной.
      **Закрыто 2026-09-22 по признаку заявителя:** волна devtools#292
      (закрыт 2026-09-21) ре-вендорила steward @ `5bfd829` на 21 потребителя —
      `chore(review-kit): ре-вендор steward @ 5bfd829 — --trusted-base и
      инструкции агентов в CODE_OVERRIDE` в arbiter#113, atp-platform#333,
      deployer#68, dispatcher#266 и далее; исключение — `atp-platform-testing`
      (devtools `@id:atp-platform-testing-kit-catchup`). Ожидание
      `@blocked_by:todo://devtools/review-kit-wave-trusted-base-and-agent-instructions`
      снято: такого пункта у devtools нет и не было — волна велась issue
      devtools#292, а не пунктом плана (правило devtools: работа для соседей
      пунктом не является), поэтому ссылка висела [PF-ID-DANGLING]. Регрессия проверена, а не предположена: на
      старом правиле ветка из одних `.claude/skills/*/SKILL.md` + `CLAUDE.md`
      даёт код 5, на новом уходит модели
      (`test_agent_instructions_are_code_override_not_prose`,
      `test_agent_instruction_lookalikes_stay_prose`).

- [x] Приём steward#181 (from devtools#260, их PR #281 в черновике): в ките @owner:github:andrei-shtanakov @id:review-kit-split-diff-range-from-trusted-base
      `--base` отвечал СРАЗУ на два разных вопроса — «какой диапазон показать
      модели» и «откуда читать входы, которым кит верит» (декларация
      `linguist-generated`, курируемый контекст, repo-owned
      `review-scope.env`). Пока ответы совпадали, это было незаметно;
      адресный recheck (ревью только фикс-коммитов после красного вердикта)
      разводит их: сужающая база лежит НА ВЕТКЕ PR, и вместе с диапазоном
      внутрь PR уезжает граница доверия — автору достаточно объявить свой код
      generated коммитом до отревьюированной головы, и узкий диф состоит из
      одних маркеров опущения, модель не видит ни строки, кит выходит нулём,
      а обвязка публикует approve поверх доставленного request-changes.
      Довод «прошлая голова уже отревьюирована» границу не восстанавливает:
      тот круг кончился ОТКАЗОМ, а «прошло ревью» у кита значит «влито в
      base». Решение: `--trusted-base` отдельным параметром, умолчание —
      сегодняшнее поведение (кит вендорится в ~22 репо, сдвиг умолчания
      инвалидировал бы наследование вердиктов у всех сразу). Наблюдаемый
      признак (негативный контроль из заявки): прогон с суженным `--base` и
      `--trusted-base` на влитой базе НЕ применяет generated-декларацию,
      добавленную коммитом ветки PR
 —
      **закрыт PR этой ветки** по двум из трёх пунктов DoD заявки (третий,
      «правило приезжает потребителям», — волна, `review-kit-next-wave`).
      Доверенных входов оказалось ТРИ, а не два: заявка перечислила
      generated-декларацию и курируемый контекст, но из той же точки читался
      и repo-owned `.github/codex/review-scope.env`. Дыры в третьем нет (он
      умеет только расширить ревью), но граница доверия обязана быть одним
      местом — переведены все три. Точки чтения сохранены как были (merge-base
      для контекста и конфига, ВЕРХУШКА для декларации) — сдвинулась ревизия,
      не правило. Негативный контроль:
      `test_trusted_base_keeps_the_generated_declaration_on_the_merged_base`
      плюс `test_narrowed_base_alone_lets_the_branch_declare_its_own_code_generated`,
      который фиксирует дефект БЕЗ флага — иначе «умолчание не тронуто» было
      бы утверждением без наблюдения.
      Круг 1 терминального ревью вернул major/high, и он про ОБРАТНОЕ
      направление декларации: вето снятой декларации включается вопросом
      «правит ли PR `.gitattributes`», а тот гейтился по `changed-paths.txt` —
      списку СУЖЕННОГО диапазона. Пока базы совпадали, это был один отрезок;
      разведя их, тот же гейт менял направление отказа на противоположное —
      в сторону СОКРЫТИЯ: влитая база объявляет `dist/*` генератом, ветка
      первым коммитом переводит `dist/` в рукописный код и снимает
      декларацию, а recheck по README её уже не видит в диапазоне — фикс
      коммиты по `dist/` вырезались бы маркером, и получался тот же approve
      поверх request-changes, только через другую дверь. Вопрос про PR
      целиком, значит и отрезок `trusted_mb..head`; отдельный `git diff`
      только при разведённых базах
      (`test_trusted_base_still_honours_a_declaration_removed_by_the_branch`).
      Приёмочное ревью вернуло три minor, две закрыты выводом (отпечаток от
      stdout не зависит, наследования флота не сдвигаются): прогон с явным
      `--base` без флага теперь ГОВОРИТ, что граница совпала с базой — это и
      есть эксплуатируемое состояние «обвязка сузила диапазон, флаг передать
      забыла», и кит про него знал, но молчал; и объявлено вслух, что
      свежесть доверенной базы кит не проверяет (`ls-remote`/`--fetch` — про
      `--base`). Диагноз «база внутри PR» намеренно НЕ ставится: у
      неразошедшейся ветки-цели `mb` тоже равен верхушке базы, и признак
      кричал бы на штатном полном прогоне — тот же класс догадки, из-за
      которого отклонён `remote_ref` в `review-kit-explicit-target-base`.
      Третья minor — байтовый провенанс вендор-копии, проверить который
      изнутри репо нечем; воспроизведены все три проверки вахты локально
      против чекаута devtools (тело == SSOT на пине `8cd6456`, 4836 байт;
      шапка — только комментарии; SSOT после пина не двигался).
      Круг 3 вернул ещё две minor, обе закрыты. (а) `--base ""` включал
      `base_explicit`, и прогон, фактически идущий ПУТЁМ УМОЛЧАНИЯ (пустая
      база ниже молча заменяется веткой по умолчанию), печатал совет про
      суженную базу — дефект строки, добавленной кругом раньше; частная
      форма закрыта, общая семантика вынесена владельцу
      (`review-kit-empty-base-ruling`). (б) Асимметрия проверок свежести:
      у `$base` активная проверка с числом отставания, у границы доверия —
      безусловный дисклеймер. Активная проверка была написана и **откачена
      решением владельца 2026-09-21**: заявка steward#181 её не просила, а
      поверхность добавляла. Край остаётся ОБЪЯВЛЕННЫМ (строка вывода +
      комментарий + README), не спрятанным.

- [ ] Решение владельца: `--base ""` — молчаливая ветка по умолчанию или отказ кодом 2? @owner:github:andrei-shtanakov @id:review-kit-empty-base-ruling @epic:eco.codex-review-rollout
      Вопрос: Соседние опции (`--trusted-base`, `--max-diff-bytes`,
      `--max-diff-files`) на пустом значении отказывают с доводом «явная, но
      сломанная настройка не читается молча как её противоположность»; у
      `--base` такого стража нет, и пустая переменная у вызывающего молча
      превращается в прогон против ветки по умолчанию. Найдено кругом 3
      терминального ревью на ветке приёма #180/#181 в частной форме
      (`--base ""` включал `base_explicit` и печатал совет про суженную
      базу) — частная форма закрыта там же, общая семантика НЕТ: это смена
      контракта `--base` у ~22 потребителей, и решать её внутри приёма двух
      чужих заявок нельзя.

- [ ] Усиление разделителя дифа: литеральные маркеры → уже сделано суффиксом от хеша; @owner:github:andrei-shtanakov @id:review-kit-diff-marker-hardening @epic:eco.codex-review-rollout
      осталось решить, нужен ли полноценный nonce

  Парковка снята частично: маркер несёт первые 12 hex-символов sha256 от содержимого
  дифа, подделка требует знать хеш содержимого, включающего подделку. Промпт отдельно
  называет содержимое между маркерами недоверенными данными.

- [x] Док-абзац: потолок `timeout-minutes` — не гарантия во время аварии Actions @owner:github:andrei-shtanakov @id:review-kit-ceiling-vs-actions-outage @epic:eco.codex-review-rollout

  Приём входящего steward#124 (from dispatcher; боевой день 2026-08-26, major
  outage Actions) — принят с понижением: приоритет низкий, правка — не код, а
  абзац документации; ценность — сохранить измеренный операционный урок
  (dispatcher потерял время на диагностику, считая потолок гарантией). Cancel и
  сам потолок исполняет тот же контроллер Actions, который лежал: dispatcher
  PR#196/#200 висели 21–25+ минут против `timeout-minutes: 20`, снял только
  ручной `force-cancel` через API; второй режим — ран в пре-очередном лимбо без
  джобов («Cannot cancel a workflow run that has not been queued yet», 40+
  минут) — ни cancel, ни force-cancel, только ждать GC платформы; такой ран не
  создаёт check-runs, required-чеки висят как Expected. Формулировка для дока:
  «потолок превращает зависание в именованный отказ в норме, но НЕ во время
  аварии Actions; диагностика — githubstatus components (Actions !=
  operational); ручной рычаг — force-cancel».

  Закрыт PR этой ветки: абзац дописан к существующему доводу потолка у
  `timeout-minutes` в `codex-review.yml` (steward#121), а не в README —
  workflow разъезжается синком caller'ов по всем потребителям кита, README
  steward остаётся дома; довод живёт там, где будущий читатель «посчитает
  потолок гарантией».

- [x] jq-префлайт в `apply-threshold.sh` + доводка комментария прохода 2
      `collect-context.sh` @owner:github:andrei-shtanakov @id:review-kit-jq-preflight —
      приём входящего steward#102 (from spec-runner#312, Copilot-ревью вендор-копии);
      PR #106: `command -v jq` → код 2 с причиной (по образцу
      sha256sum/shasum-префлайтов), тест с PATH без jq закрепляет «2, не 127»;
      `local.sh` проверен — jq не зовёт (порог применяет через apply-threshold.sh,
      префлайт покрывает и его); комментарий «внутренние пробелы сохраняются»
      переформулирован как defense-in-depth (пути с пробелами отвергает проход 1 —
      разбор записи не space-safe и опорой быть не может). Пункт про `mktemp` без
      шаблона отклонён доказательством в самом issue — не заводится

- [x] Харнесс-слой ревьюера (claude|codex) в самом ките @owner:github:andrei-shtanakov @id:review-kit-harness-layer @epic:eco.codex-review-rollout

  Приём входящего steward#147 (from devtools `review-harness-claude`, 2026-09-03).
  Лимиты codex перевели ai-prosto на claude через переходник devtools
  `scripts/harness/claude-review` (codex-диалект снаружи, claude внутри) — он
  покрывает `review-pr.sh`, но не pre-push хук и не ручной `local.sh`.
  Дизайн согласован владельцем 2026-09-14 —
  `docs/superpowers/specs/2026-09-14-review-kit-harness-layer-design.md`:
  адаптер `scripts/review/harness-claude` как член кита (переходный `?path` в
  `checksum.sh`), `local.sh` резолвит `REVIEW_HARNESS`/`REVIEW_MODEL` только из
  окружения; умолчание `codex` (строка `codex exec` в отпечатке не меняется,
  наследования валидны); `REVIEW_CMD` побеждает; `REVIEW_MODEL=""` — отказ
  (`${REVIEW_MODEL+x}`); в `PATH` — `$kit_dir`, в `review_cmd` — голое имя;
  протокол `codex-terminal-review` не переименовывается; конверт claude
  разбирает `jq`, не `python3`. Наблюдаемый признак «сделано» (из issue):
  свежевендоренный кит ревьюит claude по одному env, включая хук, без внешних
  переходников. После мержа — handoff в devtools: `review-pr.sh` переходит на
  `REVIEW_HARNESS`, переходник удаляется (их `review-harness-shim-removal`).

  Закрыт PR этой ветки: `scripts/review/harness-claude` (100755, вызывается
  `local.sh` по абсолютному пути — PATH у вызова ревьюера не трогается,
  находка терминального ревью ветки), резолв в `local.sh` +
  `--print-review-cmd`, переходный член в `checksum.sh`, README/спека.
  Handoff в devtools заводится issue-ом `review-pr-harness-env` (inbox)
  после мержа: `review-pr.sh` переходит на `REVIEW_HARNESS`/`REVIEW_MODEL`,
  `reviewer_label` — из `local.sh --print-review-cmd` (feature-detect по
  литералу), переходник `scripts/harness/claude-review` удаляется. Волна
  ре-вендора по флоту — по образцу `review-kit-fp-wave`; PIN у потребителей —
  `checksum.sh`.

  - [x] Волна ре-вендора кита с харнесс-слоем по флоту (22 копии, двухшаговая) @owner:github:andrei-shtanakov @id:review-kit-harness-fleet-wave @epic:eco.codex-review-rollout

    Шаг 1 devtools#222 ДОСТАВЛЕН (devtools#227, 2026-09-14): `review-pr.sh`
    выставляет `REVIEW_HARNESS`/`REVIEW_MODEL` и берёт reviewer_label из
    `local.sh --print-review-cmd`; боевой зонд — steward → `harness-claude
    --model claude-opus-5`, dispatcher (старая копия) → `claude-review` прежней
    веткой. Удаление переходника (их `review-harness-shim-removal`) ждёт этой
    волны. Волна заведена в devtools как флотскому оператору (прецедент
    devtools#79): devtools#228, slug `review-kit-harness-wave` — двухшаговый
    ре-вендор на потребителя (PR-1: 6 прежних файлов @ `a2d7e71`, новый
    `checksum.sh` с `?harness-claude`; PR-2: сам адаптер `100755` + 7-я строка
    PIN — иначе старый base-чекер даёт код 2 на «non-kit entry»), целевые
    sha256 и проверка на месте — в теле issue. Состав на 2026-09-14: 19 копий
    @ `e4c43cc`, kapelle `9916787`, maestro `1634af7`, devtools `2c71ed7`,
    spec-runner `9d5f8e7`. Сделано = все 23 `# SOURCE: steward @ a2d7e71`
    (или новее) с 7 строками PIN, и `review-pr.sh … --harness claude` печатает
    `harness-claude` в теле ревью.
    **PR-1 волны закрыт 2026-09-14** (сверено продюсером по default-веткам через
    API): все 22 копии с GitHub-remote — `SOURCE @ a2d7e71`, `checksum.sh`
    байт-в-байт с master, `review-prompt.md` синхронизирован (решение владельца:
    схема v2 `kind` + apply-threshold + промпт атомарно, исключение spec-runner
    @ `761285f` снято), 6 строк PIN, адаптера нигде нет — ожидаемое состояние
    между шагами. `atp-platform-testing-en` — локальная папка без remote, вне
    флота (ранее считалась 23-й копией). Осталось PR-2: адаптер `100755` +
    7-я строка PIN; волна взяла `a2d7e71`, а `collect-context.sh` с тех пор
    изменён (#157, steward#154) — предложено поднять его тем же PR-2, чтобы
    флот стал `57170da` целиком.
    **ЗАКРЫТ 2026-09-14 — PR-2 волны завершён.** Сверено продюсером по
    default-веткам через API и живыми shallow-клонами dispatcher и
    spec-runner: все 22 копии — 7 строк PIN, `harness-claude` байт-в-байт с
    master и `100755`, `checksum.sh --pin` → 0, `REVIEW_HARNESS=claude sh
    scripts/review/local.sh --print-review-cmd` → `harness-claude --model
    claude-opus-5`. Признак «сделано» выполнен. Остаток: `collect-context.sh`
    у 21 копии @ `a2d7e71` (фикс #154 не разъехался; spec-runner взял
    свежий) — уходит следующей волной (`review-kit-next-wave`). Удаление
    переходника в devtools (`review-harness-shim-removal`) разблокировано,
    уведомлено в devtools#222/#228.

  - [x] Перевести `?scripts/review/harness-claude` из переходного в обязательный член инвентаря `checksum.sh` @owner:github:andrei-shtanakov @id:review-kit-harness-member-promotion @epic:eco.codex-review-rollout

    Находка терминального ревью ветки `review-kit-harness-layer` (#2, затем
    ужесточена заходом #3): пока член `?path` (переходный), незапинованный,
    но ПРИСУТСТВУЮЩИЙ `harness-claude` у потребителя раньше проходил
    copy-integrity и затем реально выполнялся при `REVIEW_HARNESS=claude` —
    закрыто заходом #3 (`checksum.sh` теперь отказывает кодом 1: «переходный
    член присутствует, но не запинован»). Остаточное свойство переходности —
    только про ОТСУТСТВИЕ: адаптер может не существовать у потребителя вовсе,
    и это легально. Этот пункт переводит требование с «сверяется, если есть»
    на «обязан существовать» — промоция члена в обязательные на следующем
    релизе кита.
    **Закрыт PR этой ветки** (сразу после закрытия волны): `?` снят в
    `required_kit_default`, отсутствие адаптера — код 2 «PIN не покрывает
    состав»; тесты стенда потребителя несут 7 файлов; glob-свойство `?path`
    закреплено на generic-члене через `CHECKSUM_KIT_EXTRA`. Разъезжается по
    флоту следующей волной (`review-kit-next-wave`); до неё копии
    `checksum.sh` @ `a2d7e71` дают при 7 строках PIN тот же результат.

  - [ ] Следующая волна кита: `collect-context.sh` #154 (21 копия) + промоция члена + spec-runner-lint @owner:github:andrei-shtanakov @id:review-kit-next-wave @epic:eco.codex-review-rollout

    Ожидание волны #292 снято 2026-09-22: devtools#292 закрыт 21.09, кит @
    `5bfd829` у 21 потребителя; следующая волна ждёт своего окна, не прошлого.
    **Окно запрошено 2026-09-21 — devtools#292** (заявка с sha256-таблицей и
    списком 22 потребителей). Решение владельца: раскатывать сразу на все 22,
    без узкого среза-замера. Форма — одношаговая для 21 (состав кита не
    меняется, оба файла у потребителей есть: правится содержимое плюс две
    строки `PIN`); `atp-platform-testing` — исключение, ему нужен двухфазный
    ре-вендор: у него нет `prose-paths.env` вовсе, а `local.sh` на более
    старом релизе (`4214c3e2…` против общего `18e87312…`). devtools свою
    копию подтянул сам (их #283), в списке его нет.

    Накопленная дельта после волны devtools#228 (взяла `a2d7e71`):
    `collect-context.sh` @ `57170da` (отказ на `dir/` и pathspec-магию,
    `ls-tree -z` — steward#154) у 21 копии не разъехался; `checksum.sh` с
    промоцией адаптера в обязательные (этот PR). Одношаговая волна (состав
    кита не меняется — оба члена уже у всех): заводится как inbox-issue в
    devtools с sha256-таблицей, когда владелец решит открыть окно; триггер —
    любой следующий фикс кита или запрос потребителя (spec-runner#491
    ждёт #154).
    Волна несёт и sidecar/effort-слой eval-харнесса (steward#164):
    `REVIEW_VERDICT_OUT` и `REVIEW_EFFORT` в `local.sh`, `REVIEW_USAGE_OUT` и
    `--effort` в `harness-claude`. Для потребителей это добавка к трём
    существующим `REVIEW_*` (умолчания не меняются, отпечаток ревью не
    трогают: пути sidecar-ов в него не входят), но дайджесты обоих членов
    уехали — значит та же sha256-таблица, тот же одношаговый ре-вендор, и
    гнать их отдельной волной незачем.
    Та же волна несёт приёмы steward#180 и steward#181 (PR этой ветки):
    ре-вендоренный `prose-paths.env` @ devtools `8cd6456` (восемь глобов под
    инструкции агентов) и `local.sh` с `--trusted-base`. Оба — та же
    одношаговая форма: состав кита не меняется, умолчания не меняются,
    отпечаток ревью при умолчании тот же. Но у #180 признак «сделано»
    заявителя (devtools#265) выполняется ТОЛЬКО у потребителя с обновлённым
    китом — у нас закрыт лишь апстрим, — поэтому волна для этого приёма не
    «попутно», а условие закрытия. У #181 наоборот: закрытие на нашей
    стороне полное (кит принимает параметр), а devtools#260 разблокирован
    ре-вендором своей копии, которым распоряжается их владелец.
    Попутно проверить у 15 копий без `review-kit-drift.yml`/checksum-шага в
    CI (по локальным чекаутам их нет вне 7 репо), нужен ли им бутстрап-контракт
    «чекер из base» — или это репо без PR-гейта, где copy-integrity держится
    только на ревью ре-вендор-PR.

- [x] Догфуд WS-005 в `--stage release` красный по `GC-APPROVAL-MISSING`: наблюдения аппрувов под прежним дайджестом `approval-policy.yaml` @owner:github:andrei-shtanakov @id:approval-facts-policy-digest-refresh

  Найдено 2026-09-14 при закрытии steward#149: `gate-check --profile team-exp
  --stage release workstreams/WS-005-gate-verdicts/spec/` даёт
  `GC-APPROVAL-MISSING` на всех артефактах — `policy_digest` материализованных
  observation'ов (`7ad8ec72…`) не совпадает с текущими байтами
  `profiles/approval-policy.yaml` (`4b6d4087…`), изменённого 2026-08-31 при
  включении агентского мержа (ADR-ECO-011, §6). Стадия `release` в CI не
  используется, authoring зелёный — никого не ломает, но это датированный хвост
  ADR-ECO-011 рядом с §6: наблюдения надо перематериализовать под текущей
  политикой (`steward approval-facts`, см. evidence
  `docs/evidence/2026-08-21-approval-facts-v2-migration/`), либо зафиксировать,
  что release-стадия для бандла WS-005 не претендует на зелёный до этого.
  Закрыт 2026-09-14 на живом состоянии, evidence —
  `docs/evidence/2026-09-14-approval-facts-policy-refresh/`. Диагноз в два шага:
  перематериализация под текущей политикой (`collect_approval_facts.py`,
  дайджест `4b6d4087…`) сменила причину красного на «merge 02840df…/cde0a007…
  is outside the declared observation scope» — это аппрув-мержи PR #55/#60
  артефактов бандла, которых в охвате A0 не было. Правка
  `profiles/approval-facts-scope.yaml`: `prs: [55, 60, …]` с доводом
  (release-стадия догфуда бессмысленна, если охват не покрывает сам догфуд).
  Итог: `gate-check --stage release` над WS-005 — 0 err / 0 warn, authoring —
  0/0. Оговорка: файл фактов локальный с lease 24 ч, зелёный держится, пока
  host-local расписание A0 собирает факты — свойство Stage A0 по построению.

- [ ] generated-фильтр `local.sh` из подкаталога: `check-attr` приклеивает cwd-префикс @owner:github:andrei-shtanakov @id:review-kit-generated-filter-cwd @epic:eco.codex-review-rollout

  Найдено приёмочным ревью #151 (дважды, вне рамки патча). `collect_declared`
  кормит `git check-attr --stdin --source=<tree>` root-относительными путями из
  `git diff --name-only`, а `check-attr` трактует их относительно cwd — из `src/`
  атрибут ищется у `src/<путь>`. Тот же класс, что закрытый
  `review-context-root-relative-manifest`, но направление деградации другое: файл
  остаётся в дифе (в сторону ревью, не fail-open) — поэтому не закрыто тем же PR.
  Форма: `git -C "$repo_root"` для вызова check-attr либо `--full-tree`-аналог;
  регресс-тест — объявленный generated-файл фильтруется из подкаталога с
  АНКОРНЫМ паттерном в `.gitattributes` (существующий
  `test_declared_generated_is_filtered_from_subdir` зелёный только потому, что
  неанкорный `uv.lock` совпадает и с `src/uv.lock`).

- [x] Запись манифеста `dir/` проходила как файл — в пакет попадал листинг каталога @owner:github:andrei-shtanakov @id:review-context-trailing-slash-entry @epic:eco.codex-review-rollout

  Приём входящего steward#154 (from spec-runner#491, терминальное ревью их
  ре-вендора @ `9d5f8e7`; minor, пре-существующее). Сторож формы пути не
  отвергал хвостовой `/`: `git ls-tree --full-tree <base> -- dir/` печатает
  содержимое каталога, проверка режима брала первую внутреннюю запись
  (100644), а `git show <base>:dir/` на tree-объекте выходил с 0 и печатал
  листинг — пакет собирался с кодом 0, «файл» был листингом. PR этой ветки:
  сторож (у `--manifest` и у записей) отвергает `*/` и pathspec-магию `:*`
  (тот же класс: путь разрешается не в себя), плюс структурная проверка
  поверх сторожа — `ls-tree` обязан вернуть ровно одну запись с путём,
  буквально равным запрошенному (defense-in-depth против любой формы
  pathspec, не только хвостового слэша). Регресс-тесты: `src/`, `docs/`,
  `:/src/producer.py`, `:(top)docs/contract.md` в манифесте и `.github/codex/`,
  `:/…` в `--manifest` → код 2, без `--- ФАЙЛ` в выводе; sh + dash. Доедет до
  флота волной devtools#228, если та берёт кит с master после этого мержа.

- [x] Base-контекст терялся при `local.sh` из подкаталога (fail-open) @owner:github:andrei-shtanakov @id:review-context-root-relative-manifest @epic:eco.codex-review-rollout
      —
      приём входящего steward#150 (from spec-runner#474, терминальное ревью их
      PR нашло fail-open у продюсера). `local.sh` резолвил промпт/схему от корня
      репо, а манифест оставался строкой `.github/codex/review-context.txt`;
      `collect-context.sh` отдавал её в `git ls-tree`, который трактует путь
      относительно cwd-префикса (из `src/` искал `src/.github/…`) — в отличие от
      `git show <base>:<путь>`, который всегда от корня. Манифест «не находился»,
      пусто читалось как штатный код 3, и настроенный обязательный контекст молча
      выпадал из промпта при зелёном прогоне. PR этой ветки: фикс в САМОМ сборщике,
      не в `local.sh` (вариант формы из issue) — `git ls-tree --full-tree` и для
      манифеста, и для перечисленных в нём путей, так что чинится и CI, и
      вендор-копии без правки вызывающего; абсолютный путь ФС и `..` в
      `--manifest` отвергаются кодом 2 (`--full-tree` принял бы абсолютный путь
      внутри рабочего дерева и молча превратил его в tree-путь, а `git show` на
      том же значении отказал бы). Реально отсутствующий манифест остаётся
      кодом 3 и из подкаталога. Регресс-тесты доказывают не код 0, а
      содержимое: пакет из подкаталога байт-в-байт равен пакету из корня, и
      файл контекста присутствует в промпте, реально полученном ревьюером.
      Потребители кита (spec-runner) ре-вендорят после мержа — их сторона.

- [x] Freshness-проверка SHA и в обычной ветке публикации codex-review
      workflow @owner:github:andrei-shtanakov @id:review-workflow-stale-verdict —
      приём входящего steward#103 (from spec-runner#313, minor их гейта по
      скопированному workflow; дыра общая, правка у продюсера); PR этой ветки:
      сверка `headRefOid == HEAD_SHA` вынесена в одну функцию `run_is_stale` и
      применяется в ОБЕИХ ветках публикации (раньше — только в ветке «вердикта
      нет»; вытесненный `cancel-in-progress` прогон, успевший записать
      verdict.json, публиковал комментарий для устаревшего SHA), тело
      комментария несёт SHA коммита — самоидентификация против гонки
      сверка↔публикация; код выхода чека устаревание не меняет (чек висит на
      старом коммите). spec-runner зеркалит после мержа

- [x] Довод триггеров codex-review расширен на `converted_to_draft`/`unlabeled` @owner:github:andrei-shtanakov @id:review-kit-trigger-metadata-rationale
      —
      приём входящего steward#111 (from maestro; major их гейта на maestro#214,
      отклонённый с доводом); PR этой ветки: комментарий у триггера в
      `codex-review.yml` теперь фиксирует, почему оба события намеренно
      отсутствуют — вердикт привязан к SHA и остаётся годным свидетельством о
      коде независимо от метаданных PR, драфт немержабелен форджей, прогон по
      `converted_to_draft` заместил бы настоящий вердикт синтетическим красным
      (уничтожение свидетельства), `unlabeled` приходит на снятие любого лейбла
      (та же болезнь, что у задокументированного отказа от `labeled`), возврат
      покрыт `ready_for_review`. Гейт своих вердиктов не помнит — без довода в
      файле каждый новый потребитель кита платил бы за находку заново
      (прецедент: spec-runner#313 → §13). Зеркала — обычным синком caller'ов
      потребителей после мержа

- [x] Довод «staleness при движении base — свойство всех PR-чеков, лекарство — @owner:github:andrei-shtanakov @id:review-kit-base-staleness-rationale
      ruleset» дописан в шапку `codex-review.yml`
      —
      приём входящего steward#115 (from atp-platform; major их гейта на
      atp-platform#306, отклонённый с доводом); PR этой ветки, батчем с
      `review-kit-action-pin-bump` (оба меняют один файл — одна волна синка
      caller'ов вместо двух, потребителей шесть; прецедент батч-дисциплины —
      steward#112): комментарий у триггера, рядом с доводом metadata-событий,
      фиксирует — вердикт ключуется head SHA, событие «base сдвинулся» в
      PR-контур форджи не приходит, перезапуск кодом workflow невыразим,
      merge-ref-OID-ключ тоже ничего не запускает; так стареет любой PR-чек,
      лекарство системное (ruleset «Require branches to be up to date» либо
      merge queue — у владельца, все чеки разом), агентский путь закрывает
      update-branch-дисциплина брокера
      (`todo://steward/merge-broker-update-branch-discipline`). Смежный minor
      того же вердикта (накопление комментариев) — существующий пункт
      `review-kit-comment-dedup`. Гейт вердиктов не помнит — без довода в
      файле каждый следующий потребитель платил бы за находку заново. После
      мержа — синк caller'ов по флоту; в синк-PR kapelle/atp-platform/
      arbiter/dispatcher попутно закрыть их чекбоксы `codex-review-caller`

## Ждём от других проектов

- [x] **devtools → догоняющая волна re-vendor кита после steward#129**: промпт с @id:review-kit-prompt-lens-wave-wait @blocked_by:todo://devtools/review-kit-prompt-lens-wave
      линзой ослабления тестов (22 репо с prompt+PIN) + caller-workflow с доводом
      потолка (6 репо); волна 2026-08-27 разъехалась с e4c43cc — ДО #129, а
      drift-вахта потребителей ни промпт, ни caller-yml не сравнивает — сама не
      догонит @owner:repo:devtools
      — волна прошла (devtools#69),
      доставка сверена по форджу 2026-08-28: git-sha `review-prompt.md`
      байт-совпадает со steward master у выборки 8 потребителей,
      `codex-review.yml` — у всех 6 caller-репо
- **spec-runner → C2**: **закрыто со стороны steward 2026-08-09** (§2, PR #61 — пин
  `v2.22.0` / `de9a31c4`; ask по DEC-007 доставлен spec-runner#125). Прежняя
  формулировка «единственная внешняя блокировка steward-кода» снята как неверная:
  блокировок кода не осталось. Открыт не блокирующий хвост —
  `todo://steward/spec-runner-authoring-contract-ask` (остаток authoring-контракта
  по DEC-008).
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
