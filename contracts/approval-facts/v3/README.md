# `approval-facts/v3` — факты акта approve, история источника политики, векторы сбора

> **Канон живёт здесь** (репо steward). `../v2/` **заморожен**: опубликованная версия
> контракта не меняется ни байтом. Потребители (devtools, discovery) вендорят пиненую копию
> этого каталога и сверяют её целостность по `MANIFEST.json`.

Нормативная семантика — спека steward `docs/superpowers/specs/2026-10-30-approval-facts-v3-design.md`
(§3–§5) поверх сквозной спеки devtools «Акт утверждения через approve человека» (§5, §6.1,
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
`effective[]` (эффективные ревью на T, по логину) и `complete: true`; `delivery_record` —
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

**Прогон.** Транспорт — `replay_transport(spec["transport"])`; ключ обмена —
`(method, path, op, variables, query)`, текст GraphQL-запроса — канонический текст
`queries.py`. Кроме ожидания тест steward требует: запросы библиотеки идут **ровно в
порядке** записанных обменов, `unexpected == []`, `unused() == []` — отрицательный вектор
несёт только потреблённые обмены (чтение останавливается на первом отказе), иначе он
проходил бы вакуумно. Векторы акта не вызывают `effective` при `waiting_delivery` (у
несмёрженного PR фактов ревью нет — ветвление до `effective`, отступление О6 спеки).

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
| `select(keep)` | оставить обмены с индексами `keep` в этом порядке: потреблённый префикс отказа; составной вектор из обменов разных снимков в порядке библиотеки |

Положительные векторы акта `live_pr657.json` (merge-коммит) и `live_pr636.json` (squash) и
`not_merged.json` (#606) — **неизменённые** живые последовательности (`transforms: []`).
Отрицательный вектор несёт **ровно одно** нарушение. Узел `ReviewDismissedEvent` живьём не
снят (в разрешённых репо нет ни одного отзыва): его форма — по схеме GitHub и тексту запроса
E4, вектор помечен `"unverified_shape": "ReviewDismissedEvent"`, проверка формы — живой
смоук. Персоны `alice`/`bob` — `set_field` логинов авторов двух живых ревью #657.

Перегенерация (повторный запуск даёт те же байты; запись атомарна, манифест обновляется):

```bash
uv run python scripts/gen_approval_facts_v3_vectors.py --out contracts/approval-facts/v3/vectors
```

## Вендоринг потребителем

Копировать `vectors/` целиком и `MANIFEST.json`; целостность копии — sha256 каждого файла
по манифесту в обе стороны (нет лишних, нет недостающих). Это гарантия copy-integrity;
дрейф апстрима — отдельный вопрос подъёма пина.
