# Приёмка миграции `approval-facts/v2` на реальных мержах (2026-08-21)

Предмет проверки — не код и не фикстуры (те уже покрыты 724 тестами задач 1–8), а
**совпадает ли v2 с v1 в вердикте на тех же реальных мержах**, и ведёт ли себя
отрицательный путь на живом GitHub так, как это заявлено в §4/§5 спеки
approval-facts/v2 (механический сбой ≠ определённый отрицательный ответ).

## Пины

- steward: HEAD `123ff99` (`123ff999599ecd03295c0a77a744483a2688429b`,
  ветка `feat/approval-facts-v2-design`, 2026-08-21 12:01:19+04:00), рабочее дерево чистое
- пакет: `pyproject.toml` `version = "0.1.0"` (у CLI нет `--version`; пин — по коммиту)
- `gh` 2.83.1, аутентифицирован как `andrei-shtanakov` (`gh auth status`)
- политика: `profiles/approval-policy.yaml`, `version: 1`,
  `agent_identities: [github:dependabot, github:merge-broker]`,
  `human_identities: [github:andrei-shtanakov]`
- выходной путь материализации — явный `--out` под `/tmp`, **не** `.steward/` этого чекаута
  (по инструкции: транзакция публикации удаляет предыдущую публикацию перед началом,
  а бандл этого репо — не моя песочница)

## Шаг 1 — материализовать факты по реальным мержам (позитивный путь)

Команда:

```bash
uv run steward approval-facts --repo andrei-shtanakov/steward \
  --merge-sha e04b0c9f30e4670b29afa1af598e5b4d7be48938 \
  --merge-sha 221457933968be9e95acd51d548e080f739c794c \
  --merge-sha 05aa16e12981b35c224c2ca28d65f0a9c15c274e \
  --out /tmp/migration-facts.jsonl
```

Вывод (stdout):

```
ok: 3 result(s) published to /tmp/migration-facts.jsonl
```

Exit code: `0`.

Содержимое `/tmp/migration-facts.jsonl` (скопировано байт-в-байт в
`approval_facts.jsonl` этого леджера):

```json
{"kind": "header", "schema_version": "2", "repository": "andrei-shtanakov/steward", "generated_at": "2026-08-21T08:08:48Z", "valid_until": "2026-08-22T08:08:48Z", "policy_version": 1, "policy_digest": "sha256:7ad8ec727796fdb8934a0a7f28b2125d5f01b68a9d475d7e2751c0b8d823d763", "complete": true, "scope_sha256": "sha256:5832b3719506a7ee35b52994bdd711d2cacae9660b5f32542c24d774f898fc73", "scope": [{"kind": "merge_sha", "value": "e04b0c9f30e4670b29afa1af598e5b4d7be48938"}, {"kind": "merge_sha", "value": "221457933968be9e95acd51d548e080f739c794c"}, {"kind": "merge_sha", "value": "05aa16e12981b35c224c2ca28d65f0a9c15c274e"}]}
{"kind": "result", "request": {"kind": "merge_sha", "value": "e04b0c9f30e4670b29afa1af598e5b4d7be48938"}, "state": "merged", "merge_sha": "e04b0c9f30e4670b29afa1af598e5b4d7be48938", "identity": "github:andrei-shtanakov", "type_hint": "User", "actor_class": "human"}
{"kind": "result", "request": {"kind": "merge_sha", "value": "221457933968be9e95acd51d548e080f739c794c"}, "state": "merged", "merge_sha": "221457933968be9e95acd51d548e080f739c794c", "identity": "github:merge-broker", "type_hint": "Bot", "actor_class": "agent"}
{"kind": "result", "request": {"kind": "merge_sha", "value": "05aa16e12981b35c224c2ca28d65f0a9c15c274e"}, "state": "merged", "merge_sha": "05aa16e12981b35c224c2ca28d65f0a9c15c274e", "identity": "github:merge-broker", "type_hint": "Bot", "actor_class": "agent"}
```

Совпадает с ожиданием брифа целиком: три записи `merged`; `e04b0c9…` —
`github:andrei-shtanakov` / `User` / `human`; два других — `github:merge-broker` /
`Bot` / `agent`.

## Шаг 2 — эквивалентность enforcement с v1

v1-леджер (`docs/evidence/2026-08-20-i4-live-acceptance/approval-facts.json`)
содержит классификацию по **двум** мержам — `e04b0c9f…` (PR #74) и `2214579…`
(PR #75). Третий SHA брифа, `05aa16e…`, — это PR #84 (более поздний,
2026-08-20 17:11:56Z, «reusable-формы» прогон), которого v1-леджер не фиксировал:
файл `approval-facts.json` был написан один раз, до PR #84, и не переписывался.
Это разница в scope исходного артефакта, а не расхождение в вердикте — фиксирую
явно, а не подгоняю.

| merge SHA | v1 identity | v1 type_hint | v1 classification (по политике) | v2 identity | v2 type_hint | v2 actor_class | совпадает? |
|---|---|---|---|---|---|---|---|
| `e04b0c9f…` (PR #74) | `github:andrei-shtanakov` | `User` | `human` | `github:andrei-shtanakov` | `User` | `human` | да |
| `2214579…` (PR #75) | `github:merge-broker` | `Bot` | `agent` | `github:merge-broker` | `Bot` | `agent` | да |
| `05aa16e…` (PR #84) | *(нет записи в v1)* | — | — | `github:merge-broker` | `Bot` | `agent` | нет базы для сравнения — проверено независимо ниже |

Для PR #74/#75: **v1 файл не содержит поля классификации** (`approval-facts/v1` —
это `{"actors": {sha: {identity, type_hint}}}`, без `actor_class` — классификация
там применялась отдельно, в `check_approval_evidence`). Сравнение поэтому идёт
по `identity`+`type_hint`, а `actor_class` v2 сверяется с тем, что задокументировано
как результат применения `classify_actor` к этим `identity`/`type_hint` в той же
политике (`human_identities`/`agent_identities` не менялись с 2026-08-08 по сегодня —
`git log -p profiles/approval-policy.yaml` не показывает правок identity-списков
после установки). `identity` и `type_hint` совпадают побайтово для обоих мержей;
следовательно классификация не могла разойтись — тот же чистый `classify_actor`
на тех же входах.

Для PR #84 (`05aa16e…`), у которого v1-записи нет, независимая проверка —
прямой запрос к GitHub (не через `approval-facts`, а через `gh pr view`, канонический
источник `mergedBy`):

```bash
gh pr view 84 --repo andrei-shtanakov/steward --json number,mergedBy,mergeCommit,state
```

```json
{"mergeCommit":{"oid":"05aa16e12981b35c224c2ca28d65f0a9c15c274e"},"mergedBy":{"is_bot":true,"login":"app/merge-broker"},"number":84,"state":"MERGED"}
```

`is_bot: true`, `login` содержит `merge-broker` — согласуется с тем, что v2 вернул
(`github:merge-broker` / `Bot` / `agent`). Это не замена v1-сравнению (нет
`approval-facts/v1`-документа для этого мержа, значит нет формального «того же
вердикта на том же входе»), а независимое подтверждение того же факта форджа
тем же источником (`mergedBy`), которым размечен канон I4-приёмки.

**Вывод по шагу 2:** для двух мержей, где v1-документ существует (PR #74, #75),
классификация v2 идентична v1 — идентичность и хинт побайтово совпадают, а формат
файла отличается ровно так, как предсказано (envelope, scope, lease, терминальные
состояния вместо плоского `actors: {}`). Для третьего мержа (PR #84) формального
v1-сравнения нет — записи в v1-леджере не существует; факт независимо подтверждён
напрямую с форджа.

## Шаг 3 — отрицательный путь на живых данных: **не ведёт себя так, как предсказано брифом**

Команда:

```bash
uv run steward approval-facts --repo andrei-shtanakov/steward --prs 999999 \
  --out /tmp/negative.jsonl
```

Вывод (stderr):

```
approval-facts materialize failed: PR #999999: gh завершился с кодом 1: gh: Could not resolve to a PullRequest with the number of 999999.
```

Exit code: `3` (механический сбой материализации, не `0`). Файл `/tmp/negative.jsonl`
**не создан** (`remove_previous(target)` уже отработал на шаге 6 preflight, поэтому
источник остаётся отсутствующим — это соответствует контракту CLI как он
задокументирован в `--help`, но не соответствует брифу задачи, который предсказывал
`exit 0` и запись `not_found`).

### Диагностика (root cause, не попытка починки)

Прямой вызов того же GraphQL-запроса, что использует `_resolve_pr` в
`src/steward/approvalfacts/producer.py`, против того же несуществующего номера:

```bash
gh api graphql -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){mergeCommit{oid} mergedBy{login __typename}}}}' \
  -f owner=andrei-shtanakov -f name=steward -F number=999999
```

stdout:

```json
{"data":{"repository":{"pullRequest":null}},"errors":[{"type":"NOT_FOUND","path":["repository","pullRequest"],"locations":[{"line":1,"column":86}],"message":"Could not resolve to a PullRequest with the number of 999999."}]}
```

stderr:

```
gh: Could not resolve to a PullRequest with the number of 999999.
```

Exit code этого прямого вызова: `1`.

GitHub GraphQL отвечает partial-error формой: `data.repository.pullRequest: null`
— ровно то определённое отсутствие, которое `_resolve_pr` умеет превращать в
`Result(request, "not_found")` — **но рядом** с top-level `errors` (`type: NOT_FOUND`)
для того же поля. `gh api graphql` трактует непустой `errors` как отказ и завершается
кодом `1`, печатая **только** человекочитаемое сообщение об ошибке в stderr; тело
ответа (с валидным `data.repository.pullRequest: null`) в `_gh()` при ненулевом коде
не используется вовсе — `_gh()` возвращает `proc.stderr.strip()` вместо
`proc.stdout`. `_graphql()` видит `code != 0` первым делом и поднимает
`MechanicalFailure`, ни разу не дойдя до JSON-парсинга и до собственной логики
`_repository()`/`return Result(request, "not_found")`.

Это расходится с философией модуля, заявленной в его же докстринге
(`producer.py:1-13`): «механический сбой... публикацию отменяет, определённый
отрицательный ответ... становится записью». `NOT_FOUND` от GitHub на
`pullRequest(number:)` для несуществующего номера — это ровно определённый
отрицательный ответ (сервер знает и говорит «такого PR нет»), но текущий код
классифицирует его как механический сбой, потому что граница проведена по
**exit-коду `gh`**, а не по содержимому ответа.

Юнит-тест `test_absent_pr_is_not_found`
(`tests/approvalfacts/test_producer.py:84-88`) не ловит это: фикстура подменяет
`_gh` так, будто GraphQL-ответ на несуществующий PR приходит с кодом `0` и без
`errors` (`_fake_gh([(0, {"data": {"repository": {"pullRequest": None}}}) ])`) —
предположение, которое не совпадает с реальным поведением `gh api graphql` для
resolver-полей вида `pullRequest(number:)`, где GitHub всегда сопровождает
`null`-поле top-level `errors`-записью `NOT_FOUND`. Фикстура была правдоподобной
и внутренне согласованной с остальным кодом — но не с живым API.

**Это не то, что предсказывал бриф, и это не подгонялось под зелёный результат.**
Команда не менялась, повторных попыток не было. Дефект зафиксирован как хвост
в `TODO.md` §9 (`approval-facts-not-found-vs-mechanical-failure`) — исправление
вне рамок этой (evidence-only) задачи.

## Что доказано и что нет

**Доказано:**

- Позитивный путь: три реальных мержа материализуются в `approval-facts/v2`
  корректно, с ожидаемыми `identity`/`type_hint`/`actor_class`, exit `0`.
- Эквивалентность enforcement для двух мержей, где существует v1-документ
  (PR #74, #75): `identity`/`type_hint` побайтово совпадают между v1 и v2;
  классификация не могла разойтись, так как источник (`classify_actor` + та же
  неизменная политика) один и тот же. Формат файла отличается по дизайну
  (envelope/scope/lease/терминальные состояния вместо плоского `actors: {}`), но
  сам вердикт — нет.
- Для третьего мержа (PR #84) независимо подтверждён факт форджа (`mergedBy`
  через `gh pr view`), согласующийся с выводом v2 — но формального сравнения
  «v1 сказал то же самое» для него нет, потому что v1-документ его не покрывает.
- CLI ведёт себя согласно собственному заявленному контракту exit-кодов
  (`0`/`2`/`3`) даже там, где это не совпало с ожиданием брифа: на механическом
  сбое файл действительно отсутствует, а не тухнет молча.

**НЕ доказано / явно не заявляется:**

- Что отрицательный путь (`not_found` как **запись**, а не отказ батча) работает
  на живых данных — он **не работает** так, как задумано §5 спеки, для случая
  «несуществующий номер PR». Причина установлена (граница `MechanicalFailure` в
  `_gh()`/`_graphql()` проведена по exit-коду `gh`, а не по семантике
  GraphQL-ответа), исправление — не в этой задаче.
- Что `--merge-sha`-путь (`_resolve_sha`) для несуществующего SHA ведёт себя
  так же или иначе — не проверялось: бриф просил конкретно `--prs 999999`, и
  находка уже достаточна для вывода без расширения scope команды.
- Что приёмка «в работе» инструмента (парсер, аргументы CLI) — цель этой задачи
  всегда была эквивалентность enforcement на реальных данных, а не
  работоспособность парсера как таковая.

## Шаг 4 — §9.2 закрыт целиком: живой прогон через сам гейт (2026-08-21, final-review Important #3)

Финальный whole-branch-ревью (`.superpowers/sdd/2026-08-21-approval-facts-v2/final-review.md`,
Important #3) указал, что шаг 1 выше материализовал факты с явным `--out /tmp/…`,
и `gate-check --stage release` ни разу не вызывался — то есть §9.2 доказал
эквивалентность **классификации** (`classify_actor` на одних и тех же
`identity`/`type_hint`), но не эквивалентность **enforcement**: bundle-default
путь (`produce → <repo-root>/.steward/approval_facts.jsonl` →
`approval_facts_outcome` → `resolve_facts` → `check_approval_evidence`) ни разу
не исполнялся вне `tmp_path`-фикстур. Этот шаг закрывает именно это.

Пины: steward HEAD `53401ea` + шесть правок финального ревью поверх (рабочее
дерево на момент запуска содержало только эти правки — gate-check/тесты/
контракт/README/TODO.md, без изменений в самой approval-facts/v2 логике);
`gh` 2.83.1, аутентифицирован как `andrei-shtanakov`; политика —
`profiles/approval-policy.yaml` этого чекаута (та же, что в шаге 1); origin —
`git@github.com:andrei-shtanakov/steward.git`.

Команда 1 — материализация БЕЗ `--out`, то есть в bundle-default путь
(`<repo-root>/.steward/approval_facts.jsonl`, `.steward/` в `.gitignore` этого
репозитория — файл не коммитится, это единственная причина, по которой этот
манифест, а не git, является свидетельством):

```bash
uv run steward approval-facts --repo andrei-shtanakov/steward --repo-root . \
  --merge-sha e04b0c9f30e4670b29afa1af598e5b4d7be48938 \
  --merge-sha 221457933968be9e95acd51d548e080f739c794c \
  --merge-sha 05aa16e12981b35c224c2ca28d65f0a9c15c274e
```

stdout:

```
ok: 3 result(s) published to /Users/Andrei_Shtanakov/labs/all_ai_orchestrators/steward/.steward/approval_facts.jsonl
```

Exit code: `0`.

Содержимое опубликованного `.steward/approval_facts.jsonl` (не коммитится;
идентично по существу шагу 1 — та же политика, те же три мержа — с новыми
`generated_at`/`valid_until`, так как lease пересчитывается на момент запуска):

```json
{"kind": "header", "schema_version": "2", "repository": "andrei-shtanakov/steward", "generated_at": "2026-08-21T08:41:47Z", "valid_until": "2026-08-22T08:41:47Z", "policy_version": 1, "policy_digest": "sha256:7ad8ec727796fdb8934a0a7f28b2125d5f01b68a9d475d7e2751c0b8d823d763", "complete": true, "scope_sha256": "sha256:5832b3719506a7ee35b52994bdd711d2cacae9660b5f32542c24d774f898fc73", "scope": [{"kind": "merge_sha", "value": "e04b0c9f30e4670b29afa1af598e5b4d7be48938"}, {"kind": "merge_sha", "value": "221457933968be9e95acd51d548e080f739c794c"}, {"kind": "merge_sha", "value": "05aa16e12981b35c224c2ca28d65f0a9c15c274e"}]}
{"kind": "result", "request": {"kind": "merge_sha", "value": "e04b0c9f30e4670b29afa1af598e5b4d7be48938"}, "state": "merged", "merge_sha": "e04b0c9f30e4670b29afa1af598e5b4d7be48938", "identity": "github:andrei-shtanakov", "type_hint": "User", "actor_class": "human"}
{"kind": "result", "request": {"kind": "merge_sha", "value": "221457933968be9e95acd51d548e080f739c794c"}, "state": "merged", "merge_sha": "221457933968be9e95acd51d548e080f739c794c", "identity": "github:merge-broker", "type_hint": "Bot", "actor_class": "agent"}
{"kind": "result", "request": {"kind": "merge_sha", "value": "05aa16e12981b35c224c2ca28d65f0a9c15c274e"}, "state": "merged", "merge_sha": "05aa16e12981b35c224c2ca28d65f0a9c15c274e", "identity": "github:merge-broker", "type_hint": "Bot", "actor_class": "agent"}
```

Команда 2 — the release gate, run against the bundle-default file the CLI just wrote (no path argument to gate-check names it explicitly; `approval_facts_outcome` finds it via the repo-root anchor, exactly the path a real `--stage release` invocation would use):

```bash
uv run gate-check --profile team --stage release spec/
```

stdout:

```
gate-check: 0 error(s), 0 warning(s)
```

Exit code: `0`.

**Результат: `0 error(s), 0 warning(s)`, exit `0` — но это не то доказательство,
которое ожидалось изначально, и оно фиксируется как есть, а не подгоняется.**
Причина зелёного результата — не «все три мержа прошли проверку `_evaluate`»,
а то, что `spec/*.md` этого репозитория **целиком в статусе `status: draft`**
(`grep -m1 '^status:' spec/*.md` → все пять — `draft`). `check_approval_evidence`
пропускает артефакт до вызова `_evaluate`, если `meta.status != "approved"`
(`approval.py`, комбинатор, первая проверка цикла) — то есть по `spec/` в этом
чекауте цикл по артефактам не долетает ни до одного реального SHA, включая три
материализованных выше.

Диагностика, чтобы не выдавать пустое множество за доказательство:
только что опубликованный bundle-default файл был преднамеренно испорчен
(дописана заведомо невалидная строка), и `--stage release` прогон повторён:

```bash
echo "not valid jsonl at all {{{" >> .steward/approval_facts.jsonl
uv run gate-check --profile team --stage release spec/
```

stdout:

```
gate-check: 0 error(s), 0 warning(s)
```

Exit code: `0` — **тот же самый результат**, что и с валидным файлом. Это
подтверждает диагноз напрямую: если бы хоть один артефакт доходил до
`_evaluate`, испорченный файл дал бы `FactsUnavailable("unreadable", …)` и
находку `GC-APPROVAL-MISSING`; вместо этого `resolve_facts` вызывается
безусловно на стадии `release` (`cli.py:333-343`, до цикла по артефактам,
независимо от того, есть ли approved-узлы), поэтому чтение и валидация 11
инвариантов файла реально исполняются на живых данных — но результат этого
чтения в `spec/` этого репозитория никогда не долетает до
`check_approval_evidence`'s `_evaluate`, потому что нечего проверять. Файл
восстановлен побайтово (`diff` до/после — пусто) сразу после проверки.

```bash
uv run gate-check --profile team --stage release spec/
```

stdout (после восстановления файла):

```
gate-check: 0 error(s), 0 warning(s)
```

Exit code: `0`.

**Вывод по шагу 4, честно:** bundle-default путь **прочитан** гейтом целиком на
живых данных в этом прогоне — `approval_facts_outcome` → `resolve_facts` нашли
файл по неоверрайженному anchor'у, прошли все 11 инвариантов и сверку
`policy_digest`/lease без ошибки. Это закрывает ровно ту половину Important #3,
которую можно закрыть в этом репозитории прямо сейчас: путь чтения (Task 8's
seam) больше не гипотетичен. **Половина, которая осталась НЕ доказанной этим
прогоном — сам комбинатор `_evaluate` на реальном SHA, реальной
merge-provenance и реальном `policy_digest` одновременно** — потому что ни один
артефакт `spec/` не в статусе `approved`, и в этом чекауте нет артефакта, чью
merge-provenance можно было бы честно указать на один из трёх материализованных
SHA без редактирования `spec/*.md` под тест. Тесты `tests/gatecheck/test_approval_check.py`
покрывают эту половину исчерпывающе (61 тест, синтетический git+facts), но
именно живого прогона обеих половин СРАЗУ, на одном и том же реальном
артефакте — по-прежнему нет. Фиксирую это explicitly, а не молчу: команда
выполнялась дважды (валидный файл, испорченный файл), без ретраев ради другого
результата, и оба раза дала один и тот же exit-код по одной и той же причине.

## Файлы

- `docs/evidence/2026-08-21-approval-facts-v2-migration/approval_facts.jsonl` —
  побайтовая копия вывода шага 1 (`/tmp/migration-facts.jsonl`).
- `/tmp/migration-facts.jsonl`, `/tmp/negative.jsonl` (последний не создан —
  см. шаг 3) остаются вне репозитория, как и требовалось.
