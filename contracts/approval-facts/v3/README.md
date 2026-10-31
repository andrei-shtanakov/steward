# `approval-facts/v3` — факты акта approve, история источника политики, векторы сбора

> **Канон живёт здесь** (репо steward). `../v2/` **заморожен**: опубликованная версия
> контракта не меняется ни байтом. Потребители (devtools, discovery) вендорят пиненую копию
> этого каталога и сверяют её целостность по `MANIFEST.json`.

Нормативная семантика — спека steward `docs/superpowers/specs/2026-10-30-approval-facts-v3-design.md`
(§3–§6) поверх сквозной спеки devtools «Акт утверждения через approve человека» (§5, §6.1,
§8.2). Этот файл — справочник по форме; при расхождении права спека.

## Состав

| Файл | Что |
|---|---|
| `SCHEMA.json` | JSON Schema draft 2020-12, `additionalProperties: false` у каждого объекта; `$defs`: `review_record`, `delivery_record`, `policy_history_record`, `header`/`result` (файл release-гейта) |
| `vectors/*.json` | векторы акта (`read_act_facts`) — форма, которую читает тест devtools Task 11 |
| `vectors/{open_review,history,activation}/*.json` | векторы `read_open_review`, `read_policy_history`, `read_activation` (вне нерекурсивного `glob("*.json")` devtools) |
| `MANIFEST.json` | `{"vectors": {"<путь от vectors/>": "<sha256>"}}` — каждый файл `vectors/` |

Библиотека — `steward.approvalfacts.v3` (сеть только через внедрённый `Transport`); записи
строит `steward.approvalfacts.v3.records`.

## Факты (спека §3.1)

| Тип | Поля |
|---|---|
| `Review` | `review_id` (десятичная строка `databaseId`), `login` (GraphQL-форма, у бота без `[bot]`), `type_hint` (`User`\|`Bot`), `state` (`PENDING`\|`COMMENTED`\|`APPROVED`\|`CHANGES_REQUESTED`\|`DISMISSED`), `commit_id`, `submitted_at` (`null` только у `PENDING`) |
| `Dismissal` | `review_id`, `actor`, `created_at`, `previous_state` (`APPROVED`\|`CHANGES_REQUESTED`\|`COMMENTED`) |
| `ReviewFacts` | `repository`, `pr`, `head_sha`, `base_ref`, `pr_state`, `reviews`, `dismissals` |
| `Delivery` | `merged`, `merge_commit`, `merged_at`, `merged_by_login`, `merged_by_type_hint`, `head_at_merge`, `in_base`, `paths[]` |
| `PathBlob` | `path`, `blob_at_merge` (`null` — путь установленно отсутствует в merge-коммите), `blob_at_head` |
| `Activation` | `pr`, `merge_commit`, `merged_at` (из PR, не из коммита), `files_sha256` (hex без префикса; отсутствующий файл — без ключа) |
| `Unavailable` / `HistoryViolation` | `reason` = `<code>` или `<code>: <detail>`, `code` — из закрытого словаря |

Записи `SCHEMA.json` — сериализация тех же фактов: `review_record` добавляет `observed_at`,
`effective[]` (эффективные ревью по логину), `t` и `before` — момент и ключи равных меток, на
которых `effective[]` вычислен (решение M0-I: `effective[]` — состояние на `t`, **не** на
`observed_at`), — и `complete: true`; `delivery_record` —
`merged_by{identity: "github:<login>", type_hint, actor_class}` (`actor_class` ставит
классифицирующий вызывающий, иначе `null`); `policy_history_record` — `activations[]` |
`violation` (`complete: true`) | `unavailable` (`complete: false`).

## Канон меток

Одна форма — `YYYY-MM-DDTHH:MM:SSZ` (`^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$`). Метки форджа
приводятся к ней при чтении (`+00:00` → `Z`); доли секунды и метки без зоны —
`Unavailable: bad_timestamp`.

## Восстановление на T (спека §3.5)

`effective_reviews(facts, t, *, before)`: сравнение события с T — `<` до, `>` после, `==` до
**тогда и только тогда**, когда ключ события ∈ `before` (`("review", id)`,
`("dismissal", review_id)`); перебор порядков равных меток — у потребителя (§8.2 devtools).

0. Безусловно и до сравнения с T: отзыв без `previous_state`, отправленное ревью без
   `submitted_at`, `DISMISSED` без события отзыва — `Unavailable`; `PENDING` исключается.
1. Ревью после T отбрасывается; отзыв до T → `DISMISSED`, после T → `previous_state`, нет
   отзыва → `state`.
2. По логину — последнее по `(submitted_at, int(review_id))` ревью с восстановленным
   состоянием ∈ {`APPROVED`, `CHANGES_REQUESTED`, `DISMISSED`}; `COMMENTED`/`PENDING` его не
   меняют; отозванное последнее даёт `DISMISSED`, раннее `APPROVED` не восстанавливается.

## Закрытые словари причин (спека §3.6)

`Unavailable`: `transport`, `http`, `graphql_errors`, `malformed`, `pagination`,
`bad_timestamp`, `unknown_state`, `missing_commit_id`, `missing_submitted_at`,
`dismissal_without_previous_state`, `dismissed_without_event`, `duplicate_dismissal`,
`orphan_dismissal`, `delivery_mismatch`, `bot_login_mismatch`, `unstable`, `object_missing`,
`path_absent_at_head`, `not_a_blob`, `chain_limit`, `branch_not_default`, `no_pr_for_commit`,
`tree_truncated`, `actor_unresolved`.

`HistoryViolation`: `commit_without_pr`, `rebase_intermediate`, `pr_base_mismatch`,
`anchor_not_reached`.

`Unavailable` — никогда не «пустой список» и не разрешение: потребитель трактует его как
отказ продвижения.

## Пределы обнаружения (спека §3.7)

- Многокоммитный rebase-merge — `HistoryViolation` при обеих гипотезах об API:
  промежуточный коммит, ассоциированный со смёрженным PR с другим `merge_commit_sha`, —
  `rebase_intermediate` (живьём подтверждено для коммитов ветки по умолчанию); без
  ассоциации — `commit_without_pr`. В решении коды не различаются.
- **Однокоммитный rebase-merge** неотличим от squash и возвращается как `Activation` —
  **предел, не нарушение**: результат не доказывает отсутствия rebase-merge (вектор
  `history/rebase_single_commit_accepted.json`).
- Indirect merge с формально связанным PR и force-push на предка с сохранённым якорем не
  обнаруживаются.
- HTTP 404 — никогда не «установленное отсутствие»: отсутствие файла устанавливается только
  полным (`truncated = false`) успешно прочитанным деревом коммита.

## Векторы (спека §5)

Вектор — вход, записанные обмены `ReplayTransport` и ожидание:

```json
{
  "kind": "act",
  "title": "…",
  "repo": "andrei-shtanakov/devtools", "pr": 657, "paths": ["…"],
  "t": "2026-10-30T12:09:17Z", "before": [["review", "5711633458"]],
  "transport": {"exchanges": [{"request": {…}, "response": {…}}]},
  "expect": {"effective": {"alice": "APPROVED"}, "delivery": {"merged": true}},
  "derived_from": ["pr657_act_sequence"],
  "transforms": [{"op": "set_field", "path": [4, "response", "…"], "value": "…"}]
}
```

Входы по виду: `act` — `repo`, `pr`, `paths`, `t` (`null` у несмёрженного), `before`;
`open_review` — `repo`, `pr`, `t`, `before`; `history` — `repo`, `branch`, `anchor`,
`until`, `files`; `activation` — `repo`, `merge_commit`, `files`.

`expect` — ровно одно из:

| Вид | Ожидание |
|---|---|
| `act` | `{"unavailable": "<code>"}` (от `read_act_facts`); `{"waiting_delivery": true}`; `{"effective": {login: state}, "effective_commit"?: {login: sha}, "delivery"?: {подмножество полей Delivery; paths — список объектов}}` |
| `open_review` | `{"unavailable": "<code>"}`; `{"effective": {…}, "effective_commit"?: {…}}` на `t` |
| `history` | `{"activations": [{pr, merge_commit, merged_at, files_sha256}, …]}`; `{"violation": "<code>"}`; `{"unavailable": "<code>"}` |
| `activation` | `{"activation": {…}}`; `{"unavailable": "<code>"}` |

**Прогон (нормативно для любого потребителя, решение M0-J).** Векторы **обязательно**
гоняются через `steward.approvalfacts.v3.run_vector(vector) -> VectorOutcome` (пройден, если
`outcome.ok`; иначе `outcome.problems` называет расхождения) или через эквивалент с **обеими**
строгими проверками:

1. **полный порядок запросов** = порядку записанных обменов (`ReplayTransport` — FIFO на ключ
   `(method, path, op, variables, query)` и общий порядок сам не проверяет);
2. **полное потребление**: `unexpected == []` и `unused() == []`;

и с точным сравнением исхода: код `Unavailable` равен `expect.unavailable` (не только тип),
`waiting_delivery` — ветка **до** `effective` (у несмёрженного PR фактов ревью нет, `t: null`),
`delivery` и `effective_commit` сверяются. Отрицательный вектор несёт только обмены,
потреблённые до отказа: библиотека, пропустившая дефект, дочитает запись до конца и получит
`Unavailable: transport`, поэтому проверка «результат — какой-то отказ» проходит вакуумно
(замер ревью Task 7: мутанты «51 страница», «`errors` при 200 игнорируются», «`ghost`
установлен» выживают без строгих проверок и убиваются `run_vector`). Текст GraphQL-запроса в
обменах — канонический текст `queries.py`.

**Происхождение.** Векторы не пишутся руками: генератор
`scripts/gen_approval_facts_v3_vectors.py` выводит каждый из живых read-only снимков
`tests/fixtures/approvalfacts_v3/live/` (`derived_from`) закрытым списком преобразований
(`transforms`, реализация — `scripts/approval_facts_v3_derive.py`):

| `op` | Действие |
|---|---|
| `set_field(path, value)` | заменить существующее поле; под `data` — только запрошенные поля |
| `drop_field(path)` | стереть существующий ключ |
| `append_node(path, node)` | дописать узел; ключи — ровно поля текста запроса |
| `retime(path, ts)` | заменить метку времени |
| `repeat_exchange(name)` | дописать копии обменов операции (второй проход) |
| `page_split(path, n)` | разрезать страницу `nodes` на две с курсором |
| `select(keep)` | решение контроллера M0-H: выбрать и упорядочить живые обмены (индексы `keep`, без повторов) — потреблённый префикс отказа; составной вектор из обменов разных снимков в порядке библиотеки; содержимого не меняет |

Имя, повторённое в `derived_from`, склеивает обмены снимка повторно: векторы `open_review`
`clean` и `review_on_non_head_commit` — **один** живой проход, повторённый вторым (у акта
второй проход — настоящий второй запрос снимка).

Положительные векторы акта `live_pr657.json` (merge-коммит) и `live_pr636.json` (squash) и
`not_merged.json` (#606) — **неизменённые** живые последовательности (`transforms: []`).
Отрицательный вектор несёт **ровно одно** нарушение. **Исключение —
`open_review/race_between_pages`:** проход 1 разорван между страницами и потому нецелен сам
по себе (отзыв ревью, которое на странице 1 ещё `APPROVED`) — нарушений больше одного по
природе сценария «между страницами»; библиотека сравнивает проходы раньше проверки фактов
(`unstable`). Его двойник с одним нарушением — `dismiss_and_cr_between_passes.json`.

**Одинаковые пары оставлены намеренно** — это двойники разных отрицательных векторов, не
дедуплицировать: `dismissal_normal_twin` = `dismissal_with_previous_state_twin` (различаются
только `title`; двойники `dismissal_actor_no_typename` и `dismissal_without_previous_state`),
`bot_login_two_forms` / `clean_approve_app_merge` (один транспорт, `expect` первого —
подмножество; группа §5.6 «две формы логина бота» и чистый акт). Узел `ReviewDismissedEvent` живьём не
снят (в разрешённых репо нет ни одного отзыва): его форма — по схеме GitHub и тексту запроса
E4, вектор помечен `"unverified_shape": "ReviewDismissedEvent"`, проверка формы — живой
смоук. Персоны `alice`/`bob` — `set_field` логинов авторов двух живых ревью #657.

Перегенерация (повторный запуск даёт те же байты; запись атомарна, манифест обновляется;
`--out` — только каталог `vectors` без посторонних файлов, иначе генератор отказывает):

```bash
uv run python scripts/gen_approval_facts_v3_vectors.py --out contracts/approval-facts/v3/vectors
```

## Файл release-гейта (спека §6)

`.steward/approval_facts.jsonl` с `schema_version: "3"`: `header` (поля v2) и по одной
`result` на элемент scope; поля мержа v2 переехали в `result.delivery` (`delivery_record`).
Читатель — `steward.approvalfacts.v3.release_reader.load_release_facts`; одиннадцать
инвариантов v2-читателя (`../v2/README.md`) действуют, инвариант 7 — по матрице полей §6.

**Известный пробел инварианта 9 унаследован из v2.** Алиасы сравниваются только по записям
с доставкой (`merged`/`actor_unavailable`, ключ — `delivery.merge_commit`); запись с
`delivery: null` (`not_found`, `no_matching_pr`, `not_merged`) в сравнение не входит. Файл
`pr:42 → merged, merge_commit=X` вместе с `merge_sha:X → not_found` валиден, хотя
утверждает о SHA `X` две несовместимые вещи; гейт разрешает его через индекс
(`by_merge_sha()` → `merged`), а не через отрицательный алиас. Это решение читающего гейта,
не форма файла; открытый хвост — `approval-facts-index-precedence-over-negative-alias` в
`TODO.md` steward'а.

## Вендоринг потребителем

Копировать `vectors/` целиком. Манифест — `contracts/approval-facts/v3/MANIFEST.json`
(**рядом** с `vectors/`, не внутри: `git archive … contracts/approval-facts/v3/vectors` его
не захватит; ключи — пути от `vectors/`). Целостность копии — sha256 каждого файла по этому
манифесту в обе стороны (нет лишних, нет недостающих) **или** по собственному списку
дайджестов, снятому с пинованного SHA steward. Это гарантия copy-integrity; дрейф апстрима —
отдельный вопрос подъёма пина. Векторы и манифест объявлены `linguist-generated`
(`.gitattributes`): их правят только перегенерацией.
