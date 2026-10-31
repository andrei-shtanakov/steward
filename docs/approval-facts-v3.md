# `approval-facts/v3`: инструкция потребителя

Библиотека `steward.approvalfacts.v3` читает с GitHub факты акта approve (ревью, отзывы,
доставку PR) и историю источника политики. Документ написан для devtools, часть B
(approval-act-node), и для discovery (M2). Нормативный текст — спека
`docs/superpowers/specs/2026-10-30-approval-facts-v3-design.md`. Ссылки «§N» ниже ведут
в неё. Если документ и спека расходятся, права спека.

## Публичный API

```python
from steward.approvalfacts import v3

v3.__all__ == [
    "HISTORY_VIOLATION_CODES", "POLICY_FILES", "UNAVAILABLE_CODES",
    "Activation", "Delivery", "Dismissal", "EventKey", "GhTransport", "HistoryViolation",
    "PathBlob", "ReplayTransport", "Request", "Response", "Review", "ReviewFacts",
    "Transport", "Unavailable", "VectorOutcome",
    "effective_reviews", "read_act_facts", "read_activation", "read_open_review",
    "read_policy_history", "replay_transport", "run_vector",
]
```

Вне `__all__` нормативны ещё две точки входа, которые называет контракт
(`contracts/approval-facts/v3/README.md`): сериализация в записи —
`steward.approvalfacts.v3.records` (см. раздел «Записи»), читатель файла release-гейта —
`steward.approvalfacts.v3.release_reader.load_release_facts`. Вспомогательные имена
подмодулей (`model.canon_ts`, `validate.validate_review_facts`, словари состояний) в API
не входят: их форма может меняться без смены версии контракта.

Импорт пакета не выполняет ввода-вывода: ни процессов, ни сокетов, ни чтения файлов. Это
проверяет тест `tests/approvalfacts/v3/test_api.py::test_import_has_no_io`, так что
ленивый импорт steward в devtools сохраняется.

## Транспорт

Библиотека ходит в сеть только через внедрённый объект с протоколом `Transport` (§3.2):

```python
class Transport(Protocol):
    def send(self, request: Request) -> Response: ...
```

- `Request(method, path, op=None, variables=None, query=None)`. REST — это `GET` с
  относительным путём API. GraphQL — это `POST` на `graphql` с именем операции `op` и
  точным текстом `query`. Равенство и хеш считаются по всем пяти полям, `variables`
  сравниваются как канонический JSON.
- `Response(status, body=None, link=None, error=None)`:
  - `status is None` означает сбой доставки (`Unavailable: transport`);
  - `body` — разобранный JSON;
  - `link` — сырой заголовок `Link` ответа REST.

### Продакшн: `GhTransport`

```python
from pathlib import Path
from steward.approvalfacts import v3

transport = v3.GhTransport(Path.home() / ".config/review", timeout=60)
```

- Сигнатура: `GhTransport(gh_config_dir: Path | None = None, *, timeout: float = 60)`
  (отступление О10). Внутри работает `gh api --include`, а GraphQL идёт через
  `gh api graphql --input -`.
- `gh_config_dir` выставляет `GH_CONFIG_DIR`, то есть выбирает профиль токена `gh`, как
  `ops.pr_facts` devtools. При `None` окружение вызывающего остаётся как есть. Профиль
  выбирает потребитель, библиотека его не угадывает.
- Транспорт только читает, и это заложено в построение. Не-`GET` запрос, `POST` не на
  `graphql`, GraphQL-текст с `mutation` и путь, который `gh` прочёл бы как флаг или полный
  URL, дают `ValueError` до запуска процесса.
- **`GhTransport` — для фактов акта** (`read_act_facts`, `read_open_review`). Они читают
  GraphQL, а GraphQL GitHub без токена не отвечает. Нужен аутентифицированный профиль `gh`
  с read-only токеном.
- **`GhTransport` не годится для истории политики** (`read_policy_history`,
  `read_activation`). Без токена `gh api` не выполняет запрос и печатает приглашение
  `gh auth login`. Тогда вывод `gh` не содержит строки HTTP-статуса, транспорт отдаёт
  `Response(status=None)`, и **каждый** вызов возвращает `Unavailable: transport`.

### История политики: собственный анонимный REST-транспорт

`read_policy_history` и `read_activation` читают только REST и **обязаны** работать без
credentials (спека §3.7 и §12 п. 1, спека devtools §6.2): оба репо источника доверия
публичны и читаются анонимно. Токен для этого чтения потребителю не предлагается: он
отступал бы от принятой политики devtools §6.2.

Поэтому потребитель внедряет собственную анонимную реализацию протокола `Transport`.
Контракт реализации следует из `transport.py` и `_github.py`:

- Метод один: `send(request: Request) -> Response`.
- История шлёт только `Request(method="GET", path=...)`. `path` — путь API **относительно**
  `https://api.github.com/`, без ведущего `/`. Сегменты в нём уже закодированы
  (`urllib.parse.quote`), а путь может нести строку запроса: дерево читается как
  `repos/{o}/{r}/git/trees/{sha}?recursive=1`, следующая страница списка — как путь из
  `rel="next"` заголовка `Link`. Путь передаётся как есть, без повторного кодирования.
- Любой полученный HTTP-ответ, в том числе не-2xx, возвращается как
  `Response(status=<код HTTP>, body=<разобранный JSON>, link=<сырой заголовок Link или
  None>)`. Классификацию (`http`, `malformed`, `pagination`) делает библиотека, а не
  транспорт. Тело, которое не разбирается как JSON, — `body=None` (у успешного ответа
  библиотека даст `malformed`).
- Сбой доставки (таймаут, сеть, нет ответа) — `Response(status=None, error=<текст>)`,
  библиотека даст `Unavailable: transport`. Исключений `send` бросать не должен:
  библиотека их не перехватывает, и они дойдут до вызывающего вместо `Unavailable`.
  Вызывающий обязан считать любое исключение из вызова библиотеки **«факт не
  установлен»** — так же, как `Unavailable`, никогда не разрешением.
- `link` — сырой заголовок `Link` без разбора. Конец списка библиотека определяет по
  отсутствию `rel="next"`, сама переходит по нему и принимает только URL на
  `https://api.github.com/`.

### Тесты: `ReplayTransport` и `replay_transport`

```python
transport = v3.replay_transport({"exchanges": [
    {"request": {"method": "GET", "path": "repos/o/r/pulls/7"},
     "response": {"status": 200, "body": {...}, "link": None}},
]})
got = v3.read_act_facts(transport, "o/r", 7, paths=["a.md"])
assert transport.unexpected == [] and transport.unused() == []
```

- Формат обмена совпадает с форматом файла снимка и с полем `transport` вектора. Форма
  вне этого формата даёт `ValueError`.
- Записи хранятся очередью FIFO на ключ запроса. Незаписанный запрос возвращает
  `Response(None, error="replay: unexpected request")` и попадает в `unexpected`.
  `unused()` возвращает записи, которые ещё не потреблены. Журнал всех вызовов `send` —
  поле `log`.
- **Общий порядок запросов `ReplayTransport` не проверяет.** Векторы гоняйте через
  `run_vector` (раздел «Векторы»).

## Порядок вызовов

Все функции принимают транспорт первым аргументом и `repo` в форме `owner/name`. Ошибка
вызывающего даёт `ValueError` до первого запроса: `repo` не в форме `owner/name`, путь
пустой, абсолютный, с `..` или NUL, пустой `files`, SHA не из 40 hex-символов,
неканоничный `t`.

### 1. `read_open_review`: предпроверка перед агентским мержем

```python
read_open_review(transport, repo, pr) -> ReviewFacts | Unavailable
```

Функция делает два полных прохода по ревью и таймлайну (§3.4), без доставки и путей.
Устойчивость требует полного равенства проходов, включая `headRefOid`. `pr_state`
возвращается как есть, вывод из него делает потребитель. Окончательного решения этот
результат не даёт (спека devtools §8.2).

### 2. `read_act_facts`: факты акта после мержа

```python
read_act_facts(transport, repo, pr, *, paths) -> tuple[Delivery, ReviewFacts | None] | Unavailable
```

Порядок фиксирован (§3.3):

1. Доставка — GraphQL `StewardV3Delivery` и REST `pulls/{n}`.
2. Если PR не смёржен, функция сразу возвращает `(Delivery(merged=False, ...), None)`.
3. `in_base`.
4. `paths[]`.
5. Два прохода по ревью и таймлайну.
6. Повторное чтение доставки.
7. Проверка устойчивости проходов на срезе `T = merged_at`.

Потребитель **обязан проверить ветку `waiting_delivery` раньше**, чем строить эффективные
ревью:

```python
got = v3.read_act_facts(transport, repo, pr, paths=paths)
if isinstance(got, v3.Unavailable):
    ...  # got.code ∈ v3.UNAVAILABLE_CODES
delivery, facts = got
if facts is None:
    ...  # waiting_delivery: PR не смёржен, ревью не читались
```

`ReviewFacts` берётся из второго прохода, его `head_sha` равен `delivery.head_at_merge`.
Решение контроллера M0-F: голова и база PR в каждом проходе обязаны совпасть с доставкой,
иначе `delivery_mismatch`.

### 3. `effective_reviews`: восстановление на T

```python
effective_reviews(facts, t, *, before) -> dict[str, Review] | Unavailable
```

Чистая функция без сети (§3.5).

- `t` — каноническая метка `YYYY-MM-DDTHH:MM:SSZ`. Для акта это `delivery.merged_at`.
- `before: frozenset[EventKey]` задаёт ключи событий с меткой `== t`, которые считаются
  событиями «до T». Ключ ревью — `("review", id)`, ключ отзыва — `("dismissal",
  review_id)`.
- Результат: `login → Review`, где `state` — восстановленное состояние из {`APPROVED`,
  `CHANGES_REQUESTED`, `DISMISSED`}, а `commit_id` — собственный коммит эффективного
  ревью. Логин без такого ревью в результат не входит.
- Неканоничный `t` даёт `ValueError` (О8).
- Проверки целостности (О13) функция повторяет для фактов, собранных вручную.
- Перебор допустимых порядков группы равных меток (§8.2) делает потребитель. Библиотека
  вычисляет исход для одного порядка, заданного `before`.

### 4. `read_policy_history` и `read_activation`: история источника политики

```python
read_policy_history(transport, repo, branch, *, anchor, until, files=POLICY_FILES)
    -> tuple[Activation, ...] | HistoryViolation | Unavailable
read_activation(transport, repo, merge_commit, *, files=POLICY_FILES)
    -> Activation | Unavailable
```

Обе функции читают только REST, потому что анонимный GraphQL GitHub не отвечает.

- `POLICY_FILES = ("policy/approvers.env", "policy/modes.yaml", "policy/actors.yaml")`.
- `branch` обязана быть веткой по умолчанию источника, иначе
  `Unavailable: branch_not_default` (О11).
- `until=None` означает голову ветки.
- Результат упорядочен по first-parent, старые активации первыми. Якорь всегда стоит
  первым элементом.
- `Activation.files_sha256` содержит hex без префикса. Файл, установленно отсутствующий в
  полном дереве, в словарь не входит (О3).
- Противоречие `merged_at` порядку first-parent библиотека не проверяет: это шаг решения
  devtools (devtools Task 12 п. 2).
- `read_activation` даёт факт одной активации, обычно якоря эпохи. Ветку она не
  проверяет, принадлежность ветке доказывает `read_policy_history`. Коммит без
  смёрженного PR даёт `no_pr_for_commit`.

## Типы результата и отказы

Все метки времени в результатах приведены к одной канонической форме
`YYYY-MM-DDTHH:MM:SSZ`. Метка с долями секунды или без зоны даёт `Unavailable:
bad_timestamp`.

- **Факты** — frozen dataclass-ы `Review`, `Dismissal`, `ReviewFacts`, `PathBlob`,
  `Delivery`, `Activation` (§3.1).
- **`Unavailable(reason)`** означает, что факт не установлен. `reason` имеет вид
  `"<code>"` или `"<code>: <detail>"`. Свойство `.code` возвращает код из закрытого
  словаря `UNAVAILABLE_CODES` (§3.6, О5). Конструктор с кодом вне словаря даёт
  `ValueError`. Решения принимайте по `.code`, текст `detail` не разбирайте.
  `UNAVAILABLE_CODES` = {`transport`, `http`, `graphql_errors`, `malformed`, `pagination`,
  `bad_timestamp`, `unknown_state`, `missing_commit_id`, `missing_submitted_at`,
  `dismissal_without_previous_state`, `dismissed_without_event`, `duplicate_dismissal`,
  `orphan_dismissal`, `delivery_mismatch`, `bot_login_mismatch`, `unstable`,
  `object_missing`, `path_absent_at_head`, `not_a_blob`, `chain_limit`,
  `branch_not_default`, `no_pr_for_commit`, `tree_truncated`, `actor_unresolved`}.
- **`HistoryViolation(reason)`** — установленное нарушение формы истории. Потребитель
  регистрирует его как событие периметра. Код берётся из `HISTORY_VIOLATION_CODES` =
  {`commit_without_pr`, `rebase_intermediate`, `pr_base_mismatch`, `anchor_not_reached`}.
- Недочитанный объект истории (404, ошибка, предел) — это `Unavailable`, не нарушение, и
  событие периметра для него не регистрируется.

Таблица отказов чтения — §3.3, §3.4, §3.4.1 и §3.7. Отказы транспорта: `status is None`
даёт `transport`, любой не-2xx — `http`, тело не той формы — `malformed`, непустой
GraphQL `errors` при любом статусе — `graphql_errors`.

## Записи: `steward.approvalfacts.v3.records`

Когда факт уходит в файл, его сериализуют в форму `SCHEMA.json` контракта (§4).
Потребитель, работающий в процессе, использует dataclass-ы.

```python
from steward.approvalfacts.v3.records import (
    delivery_record, policy_history_record, review_record,
)

review_record(facts, *, observed_at, t, before) -> dict
delivery_record(d, *, actor_class) -> dict
policy_history_record(repo, branch, anchor, until, got) -> dict
```

**Недоказанный вход даёт `ValueError`, а не запись с разрешающим умолчанием.** Примеры:

- `observed_at` или `t` не в каноне;
- факты, на которых `effective_reviews` возвращает `Unavailable`;
- `actor_class` вне {`human`, `agent`, `unknown`};
- `actor_class` у несмёрженного PR или без мержера;
- неустановленный мержер (§3.4.1);
- `anchor` или `until` не из 40 hex-символов.

`review_record` несёт `t` и `before` (решение M0-I): `effective[]` вычислен на T
вызывающего, а не на `observed_at`. `policy_history_record` для `Unavailable` пишет
`complete: false` с причиной.

## Вендоринг контракта и векторы

1. Вендорьте `contracts/approval-facts/v3/` пиненой копией от того же SHA steward, что и
   зависимость (`[tool.uv.sources]`, `rev = <полный SHA>`). Тег — метка, а не пин.
2. Проверяйте целостность копии (copy-integrity). `MANIFEST.json` лежит **рядом** с
   `vectors/`, не внутри него. Ключи манифеста — пути от `vectors/`. Сверка sha256
   каждого файла идёт в обе стороны: нет лишних и нет недостающих. Вместо манифеста
   годится собственный список дайджестов, снятый с пинованного SHA. Дрейф апстрима —
   отдельный вопрос подъёма пина.
3. Раскладка (О6): векторы акта лежат в корне `vectors/*.json` и несут `kind`,
   `expect.waiting_delivery`, `expect.delivery`, `expect.effective_commit`. Векторы
   `open_review/`, `history/` и `activation/` лежат в подкаталогах.
4. **Векторы обязательно гонять через `run_vector`** (MUST, решение M0-J, §12 п. 6):

   ```python
   import json
   from steward.approvalfacts import v3

   outcome = v3.run_vector(json.loads(path.read_text(encoding="utf-8")))
   assert outcome.ok, outcome.problems
   ```

   `run_vector(vector) -> VectorOutcome(problems, requests)` исключений не бросает: любое
   расхождение попадает строкой в `problems`. Функция проверяет сразу три вещи:
   - исход совпадает с `expect`, включая **точный** код `unavailable`, ветку
     `waiting_delivery` до `effective`, `delivery` и `effective_commit`; у вектора акта
     `t` обязан равняться `delivery.merged_at` (срез акта, спека §3.3 п. 7);
   - все обмены потреблены: `unexpected == []` и `unused() == []`;
   - общий порядок запросов равен порядку обменов.

   Допустим только эквивалент с обеими строгими проверками. Проверка «результат — какой-то
   `Unavailable`» вакуумна: отрицательные векторы обрезаны до потреблённого префикса, и
   мутант, пропустивший дефект, получает `transport`. Замер ревью Task 7 показал, что
   мутанты M13, M14 и M20 выживают.

## Отступления от ожидаемого интерфейса (§10)

| # | Ожидалось | В M0 |
|---|---|---|
| О1 | `PathBlob.blob_at_merge: str` | `str \| None`: `None` значит, что путь установленно удалён в merge-коммите. По заметкам Part-A п. 5 devtools `(None, sha)` даёт `Rejected(I_BYTES)` |
| О2 | `read_policy_history(..., anchor, until)` | плюс `files: Sequence[str] = POLICY_FILES`, то же у `read_activation`. Вызов devtools не меняется |
| О3 | формат `Activation.files_sha256` не задан | hex без префикса; установленно отсутствующий файл не входит в словарь |
| О4 | параметр `node_status` у гейтов | порядок «сбор → статусы → проверки», `NodeStatus = Callable[[str, Snapshot], str]`, CLI `--act-mode`, `--node-status`, `--print-snapshot-digest` (§7.2) |
| О5 | `Unavailable.reason: str` | `"<code>[: detail]"` плюс свойство `.code` и закрытый словарь |
| О6 | векторы одной формы | акт лежит в корне и несёт `kind` и дополнительные ключи `expect`, прочие векторы лежат в подкаталогах |
| О7 | `replay_transport`, `verify_vendored_vectors` | `replay_transport` поставляется, `verify_vendored_vectors` нет: copy-integrity — гарантия потребителя |
| О8 | `effective_reviews` → `Unavailable` | неканоничный `t` даёт `ValueError` (ошибка вызова) |
| О9 | форма `merged_by_login` не задана | GraphQL-форма: без `[bot]` и без `github:` |
| О10 | «`v3.GhTransport(...)` or equivalent» | `GhTransport(gh_config_dir=None, *, timeout=60)` и протокол `Transport.send(Request) -> Response` |
| О11 | `read_policy_history` по закреплённой ветке | ветка обязана быть веткой по умолчанию, иначе `Unavailable: branch_not_default` |
| О12 | — | снято в r2 |
| О13 | этап отказа по `previous_state`, `submitted_at` и `DISMISSED` без события не задан | отказ происходит на **чтении** (`read_act_facts`, `read_open_review`), `effective_reviews` повторяет проверки |
| О14 | гейты steward без нового параметра работают как прежде | `act_mode` обязателен: API даёт `TypeError`, CLI выходит кодом 2 (ломающее изменение) |
| О15 | неустановленный актор не оговорён | `actor_unresolved` на чтении для всего PR: любой автор ревью, актор отзыва, мержер (§3.4.1) |

Причины каждого отступления — в §10 спеки. О4, О14 и заметка 2 ниже относятся не к
библиотеке, а к гейтам `gate-check` (`act_mode`, `--act-mode`, `--node-status`,
`--print-snapshot-digest`). Эти гейты приходят в той же поставке M0, что и библиотека.

## Заметки §12

1. **Credentials и лимит.** Анонимное чтение источников доверия обязательно (спека
   devtools §6.2): история политики идёт через собственный анонимный REST-транспорт
   потребителя (раздел «Транспорт»). Библиотека не кэширует.
   - Кэшировать у себя по ключу SHA можно неизменяемые объекты: коммит по SHA, связь
     коммит → PR по `merge_commit_sha`, блоб по oid, дерево по SHA.
   - Свежими обязаны быть голова ветки `df-trust`/источника и журнал эпох.
   - Предел анонимного REST — 60 запросов/ч на IP. Обход истории стоит примерно 3
     запроса на звено плюс блобы активаций.
2. **`--act-mode` обязателен (R-M0-5, О14).** Без него `gate-check` выходит кодом 2, а API
   бросает `TypeError`. Мигрировать нужно **при подъёме пина**:
   - devtools S4/S8 передают `--act-mode` по результату admission: `legacy` для доставок
     под `OLD_READER`, `review` вместе с `--node-status` для `review/v1` (Task 11 вместе с
     Task 14);
   - dispatcher добавляет `--act-mode legacy` в интеграционный тест при следующем
     `producer_commit`.
   - Вместе с пином v0.2.0 приходит **gate-catalog v3**: `profiles/gate-catalog.yaml`
     `version: 3`, три новых кода `GC-ACT-*`. Кто вендорит каталог, обязан
     перевендорить его вместе с подъёмом пина: старая копия на любой находке `GC-ACT-*`
     даёт `gate-check --emit-verdicts` код 2. У dispatcher версия каталога 2 зашита в
     коде (`SUPPORTED_CATALOG_VERSION`), там нужна правка кода, а не копия байтов.
3. **Провайдер отвечает за снимок, который читает steward** (R-M0-3). Для candidate это
   candidate-байты и пины конверта, а не база. Файл `--node-status` обязан нести
   `snapshot_digest` прогона (`gate-check --print-snapshot-digest`). Отрицательный двойник
   «тот же ответ провайдера при изменённых candidate-байтах → не `approved`» — на стороне
   devtools, Task 14.
4. **`approval-facts/v2` заморожен.** Новые потребители вендорят **v3**.
5. **Векторы.**
   - Ветку `waiting_delivery` проверяйте раньше `effective` (О6).
   - `unavailable` по `previous_state`, `submitted_at`, `DISMISSED` без события и
     `actor_unresolved` приходит от `read_*` (О13, О15).
   - Коды `rebase_intermediate` и `commit_without_pr` в решении не различайте, только в
     диагностике (§3.7 п. 4).
6. **Строгий прогон векторов (M0-J).** Гонять векторы только через `run_vector` или через
   эквивалент с обеими строгими проверками (раздел «Вендоринг контракта и векторы»).
   `review_record` несёт `t` и `before` (M0-I).
7. **Ветка источника политики** обязана быть веткой по умолчанию (О11).
