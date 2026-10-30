# `approval-facts/v3` (M0) — факты акта approve, история источника политики, векторы

> **Статус: черновик для локального круга ревью, владельцем не подписан.** Ревизия r1 —
> по находкам круга 1 (1 blocker, 5 major, 1 minor): запрет fallback на frontmatter через
> историю git (§7.2), отказ чтения при отсутствующих `previous_state`/`submitted_at` (§3.4),
> 404 — никогда не «отсутствие» (§3.7 п. 6), исправленное обоснование О11, один авторитет
> stale-каскада (§7.2.3), атомарное переключение release-гейта (§6); вопросы §11 закрыты.
> Ревизия r2 — по кругу 2 (1 blocker, 2 major, 1 minor) и решениям контроллера R-M0-1…4:
> режим узла — вход от вызывающего (`act_mode`), механизм «история git → режим» удалён; «не
> одобрен» — блокирующая находка, а не подмена `status`; провайдер отвечает за тот же снимок
> (`Snapshot`, дайджест); таблица сочетаний §7.2.4; `dismissed_without_event` — отказ чтения.
> Ревизия r3 — по кругу 3 (1 blocker, 1 major, 3 minor) и решениям R-M0-5/6: `act_mode`
> обязателен (нет значения — exit 2); любой неустановленный актор PR — `actor_unresolved`
> на чтении; один интерфейс `act_statuses` → `run_checks`; проход по всей спеке на
> разрешающие умолчания (§13).
> Ревизия r4 — по кругу 4 (4 major, 1 minor) и решениям R-M0-7/8: **таблица контрактов
> endpoint §3.8**, снятая с живых read-only ответов (`tests/fixtures/approvalfacts_v3/live/`),
> каждая проверка ссылается на её строку; ключ `ReplayTransport` включает текст запроса;
> несмёрженный PR — сначала `merged`; конец REST-пагинации — отсутствие `next`; тип актора
> отзыва запрашивается; `--print-snapshot-digest` без `--act-mode`.
> Ревизия r5 — по кругу 5 (2 major, 1 minor): положительные векторы — **неизменённые**
> согласованные живые последовательности одного `read_act_facts` (#657 merge-коммит, #636
> squash, по одному реальному пути; §3.8); переснято read-only; инвентарь потребителей v2 и
> атомарное переключение коллектора — план Task 11; потребители v2 у соседей — §12 п. 4.
>
> Запрос — steward#211
> (inbox, from `devtools`, slug `approval-facts-v3`). Потребитель ждёт пункт
> `todo://steward/approval-facts-v3`.
>
> **Основание (читать вместе):** сквозная спека devtools «Акт утверждения через approve
> человека» — `devtools/docs/superpowers/specs/2026-10-12-approval-act-review-design.md`
> (rev 17, подписана владельцем; нормативные для steward разделы — §5, §5.5, §5.6, §6, §6.1,
> §8.2, §9.3, §11.2) и раздел «Интерфейс M0» плана devtools
> `devtools/docs/superpowers/plans/2026-10-12-approval-act-review.md` (пин `f3a8b72`, ссылки
> в steward#211). Этот документ — **сторона steward**: что именно steward выпускает, как
> читает GitHub и где отступает от ожидаемого интерфейса. Сквозную семантику (D1–D3, эпохи,
> admission, §8.2) он не переопределяет: её владелец — devtools.

## 1. Зачем

Сквозная спека переводит человеческий акт с «мерж PR учёткой человека» на «approve-ревью
человека на head SHA заявки» и объявляет **steward владельцем общей реализации чтения GitHub**
(спека devtools §0 п. 2, D4, §4): факты ревью, доставки и истории источника политики читает и
нормализует одна библиотека steward, решения принимают потребители (devtools — узлы бандла,
discovery — бриф). Вторая самостоятельная реализация логики ревью запрещена (§4, Q2).

Сегодня в steward есть только `approval-facts/v2` — наблюдение merge-актора по scope PR/SHA
для release-гейта. Ревью, отзывов, доставки по путям и истории источника политики в нём нет;
транспорт — подменяемая функция `_gh` над `subprocess`, а не внедряемый параметр. Векторов
сбора, которые потребитель мог бы гонять у себя, нет.

M0 даёт четыре вещи:

1. контракт `contracts/approval-facts/v3/` — записи `review`, `delivery`, `policy_history`,
   формат файла release-гейта и **векторы сбора** §5.6;
2. библиотеку `steward.approvalfacts.v3` с внедряемым транспортом;
3. две правки гейтов steward — запрет `human ∩ agent` в политике акторов и провайдер статуса
   узла `node_status`;
4. миграцию `gate-check --stage release` на запись `delivery`.

## 2. Границы

| Входит в M0 | Не входит (владелец) |
|---|---|
| Чтение и нормализация фактов форджа, отказы `Unavailable`, векторы | Решение D1–D3, привязка к заявке, полномочия (devtools, discovery) |
| Восстановление состояния ревью на T при заданном потребителем порядке равных меток | Перебор порядков группы равных меток §8.2 (devtools `Clock`) |
| Форма истории источника политики, факты активаций | Эпохи, журнал доверия, периметр, самоотчёт прав (devtools Task 9/12a) |
| `files_sha256` активации как факт | Разбор `approvers.env`/`modes.yaml`/`actors.yaml` набора (devtools `act_policy`) |
| Запрет пересечения списков в загрузчике steward и в `ApprovalPolicy` | Тот же запрет в `actors.yaml` источника (уже в devtools `act_policy`) |
| Находки `GC-ACT-*` по режиму `act_mode` и ответу провайдера на снимке steward | Admission режима и вычисление статуса узла `review/v1` (devtools `act_admission`, Task 14 `node_status`) |

Классификация актора по пину политики (`actor_class`) в записи для потребителей **не**
вычисляется библиотекой: `Delivery` несёт логин и подсказку типа, классифицирует потребитель
по набору из `activation_commit` через `classify_actor` steward (как devtools `act_policy`
делает сегодня). Единственное место, где steward сам ставит `actor_class`, — файл
release-гейта, классифицируемый локальной `profiles/approval-policy.yaml` (спека devtools §6:
локальные копии — только для release-гейта).

## 3. Библиотека `steward.approvalfacts.v3`

Пакет `src/steward/approvalfacts/v3/`. Сеть — только через внедрённый транспорт; импорт
пакета не делает ввода-вывода (devtools импортирует steward лениво, контракт `ci.yml:27`
devtools).

### 3.1. Публичные типы

Имена и поля — ровно ожидаемые потребителем (steward#211), кроме отступлений из §10.

```python
EventKey = tuple[str, str]  # ("review", "<review_id>") | ("dismissal", "<review_id>")

@dataclass(frozen=True)
class Review:
    review_id: str        # GraphQL PullRequestReview.databaseId, десятичная строка
    login: str            # author.login (GraphQL-форма: у бота без `[bot]`)
    type_hint: str        # author.__typename: "User" | "Bot" | ...
    state: str            # PENDING|COMMENTED|APPROVED|CHANGES_REQUESTED|DISMISSED
    commit_id: str        # commit.oid
    submitted_at: str | None  # канон `YYYY-MM-DDTHH:MM:SSZ`; None только у PENDING

@dataclass(frozen=True)
class Dismissal:
    review_id: str        # ReviewDismissedEvent.review.databaseId
    actor: str            # actor.login
    created_at: str       # канон `...Z`
    previous_state: str | None  # previousReviewState; None → effective_reviews: Unavailable

@dataclass(frozen=True)
class ReviewFacts:
    repository: str; pr: int; head_sha: str; base_ref: str; pr_state: str
    reviews: tuple[Review, ...]; dismissals: tuple[Dismissal, ...]

@dataclass(frozen=True)
class PathBlob:
    path: str
    blob_at_merge: str | None   # None — путь установленно отсутствует в merge-коммите (§10 О1)
    blob_at_head: str

@dataclass(frozen=True)
class Delivery:
    merged: bool; merge_commit: str | None; merged_at: str | None
    merged_by_login: str | None; merged_by_type_hint: str | None
    head_at_merge: str | None; in_base: bool; paths: tuple[PathBlob, ...]

@dataclass(frozen=True)
class Unavailable:
    reason: str           # "<code>" или "<code>: <detail>", code ∈ UNAVAILABLE_CODES (§3.6)
    @property
    def code(self) -> str: ...

@dataclass(frozen=True)
class Activation:
    pr: int; merge_commit: str; merged_at: str; files_sha256: dict[str, str]

@dataclass(frozen=True)
class HistoryViolation:
    reason: str           # "<code>: <detail>", code ∈ HISTORY_VIOLATION_CODES (§3.6)
    @property
    def code(self) -> str: ...
```

Все метки времени в возвращаемых типах — **одна каноническая форма** `YYYY-MM-DDTHH:MM:SSZ`.
Метки форджа приводятся к ней при чтении (`+00:00` → `Z`); доли секунды и метки без зоны —
`Unavailable: bad_timestamp` (devtools `Clock` отвергает ненормализованные метки — Part-A
notes п. 2, поэтому нормализация обязана произойти до потребителя).

### 3.2. Транспорт

```python
@dataclass(frozen=True)
class Request:
    method: str                       # "GET" (REST) | "POST" (GraphQL)
    path: str                         # "repos/o/r/pulls/7" | "graphql"
    op: str | None = None             # имя операции GraphQL (`operationName`)
    variables: Mapping[str, object] | None = None
    query: str | None = None          # точный текст GraphQL; входит в ключ (R-M0-7)

@dataclass(frozen=True)
class Response:
    status: int | None                # None — сбой транспорта (таймаут, нет процесса)
    body: object = None               # разобранный JSON
    link: str | None = None           # REST: сырой заголовок Link (разбор — §3.2, строка P1 §3.8)
    error: str | None = None

class Transport(Protocol):
    def send(self, request: Request) -> Response: ...
```

- `GhTransport(gh_config_dir: Path | None = None, *, timeout: float = 60)` — продакшн:
  `gh api --include` (статус и `Link` из заголовков), GraphQL — `gh api graphql --input -`
  с телом `{query, variables, operationName}`. `GH_CONFIG_DIR` выставляется из параметра
  (профиль агента `~/.config/review`, как `ops.pr_facts` devtools). Профиль выбирает
  потребитель; библиотека его не угадывает.
- `ReplayTransport` и `replay_transport(spec: Mapping) -> ReplayTransport` — для векторов:
  записанные обмены, ключ сопоставления `(method, path, op, variables, query)` — **с точным
  текстом запроса** (R-M0-7: фикстура не может вернуть поле, которого запрос не просит), очередь FIFO на ключ
  (два прохода одного запроса получают разные ответы). Незаписанный запрос → `Response(None,
  error="replay: unexpected request")`; `ReplayTransport.unexpected` и `.unused()` позволяют
  тесту требовать точного потребления — иначе вектор с ожиданием `unavailable` проходил бы
  вакуумно.

Каждый GraphQL-запрос библиотеки — именованная операция с **каноническим текстом из §3.8**
(`StewardV3Delivery`, `StewardV3Reviews`, `StewardV3Timeline`, `StewardV3Blobs`,
`StewardV3CommitPulls`); тексты живут в `steward/approvalfacts/v3/queries.py`, и тест
сверяет их с `request.query` живых снимков. Правка запроса требует нового снимка — векторы
версионируются вместе с библиотекой одним SHA.

**Отказы транспорта** (спека devtools §5.5): `status is None` → `transport`; любой не-2xx
(401/403/404/422/5xx и прочие) → `http: <status> <path>`; тело не той формы, что указана
**в строке §3.8 этого endpoint** (объект, массив или GraphQL-конверт), → `malformed` — общего
правила «тело обязано быть объектом» нет (штатный `commits/{sha}/pulls` — массив, строка H4);
**GraphQL-ответ с непустым `errors` при любом HTTP-статусе**, включая `200` с частичными
`data`, → `graphql_errors`. Терпимости к `NOT_FOUND`, как у v2 `_only_absence`, в v3 нет:
отсутствующий PR — тоже `Unavailable` (решение потребителя не нуждается в доказанном
отсутствии).

**Пагинация.** GraphQL — `pageInfo{hasNextPage endCursor}`; `hasNextPage` без строкового
`endCursor` → `pagination`. REST — заголовок `Link` (строка P1 §3.8, форма снята живьём:
`<url>; rel="next", <url>; rel="last"`): конец списка — **отсутствие `rel="next"`** (контракт
GitHub REST pagination), лишний запрос пустой страницы не делается; заголовок присутствует,
но не разбирается по грамматике P1 → `malformed`. Санитарный предел — **50 страниц** (как
`_MAX_PAGES` v2); превышение → `pagination`. Ответ `gh` без разбираемой строки статуса —
`Response(status=None)` → `transport`.

**Пути REST** (исправление по ревью безопасности Task 5). Путь REST строится одним
помощником `_github.rest_path` из шаблона: постоянные части шаблона — буквально, **каждый**
подставляемый сегмент (владелец, имя, номер PR, ветка, SHA) — `urllib.parse.quote(seg,
safe="")`. Ветка с `?`, `#`, `%`, `/`, `..` или пробелом не меняет ни endpoint, ни запрос:
она уходит одним закодированным сегментом. SHA, полученный от форджа (мерж-коммит, голова
доставки), до любого использования обязан быть `^[0-9a-f]{40}$` (ASCII), иначе
`malformed`. GraphQL-переменные экранирования не требуют — они уходят в теле запроса.

### 3.3. Чтение фактов акта

#### `read_act_facts(transport, repo, pr, *, paths) -> tuple[Delivery, ReviewFacts | None] | Unavailable`

Фиксированный порядок спеки devtools §5.5:

1. **Доставка** (`_read_delivery`, строки **E1** и **E2** §3.8): GraphQL `StewardV3Delivery`
   **и** REST `GET repos/{o}/{r}/pulls/{n}`. **Сначала `merged`:** значения обязаны совпасть
   (иначе `delivery_mismatch`); оба `False` → п. 2 (`waiting_delivery`), **остальные поля
   мержа не сверяются** — у несмёрженного PR REST несёт тестовый `merge_commit_sha`, а GraphQL
   — `mergeCommit: null` (снято живьём: `closed606_act_sequence`,
   devtools #606). Оба `True` → обязаны совпасть мерж-коммит,
   `merged_at`, голова (`headRefOid` = `head.sha` — §5.4, проверено на стенде devtools PR #1,
   #4 и живьём на devtools #657, #636), база. Расхождение → `delivery_mismatch`. Логин мержера: REST-форма бота `slug[bot]`
   при `type = "Bot"` сводится к GraphQL-форме `slug`; после сведения логины и типы обязаны
   совпасть, иначе `bot_login_mismatch`. Возвращается GraphQL-форма (`merged_by_login` без
   `[bot]` и без префикса `github:`; `merged_by_type_hint` — `__typename`). Мержер
   смёрженного PR, чья идентичность не установлена (правило §3.4.1), → `actor_unresolved`;
   `mergedAt`/`merged_at` = null у смёрженного PR → `malformed`; `state` PR вне {`OPEN`,
   `CLOSED`, `MERGED`} → `unknown_state`.
2. **Не смёржен** → сразу `(Delivery(merged=False, merge_commit=None, merged_at=None,
   merged_by_login=None, merged_by_type_hint=None, head_at_merge=None, in_base=False,
   paths=()), None)`. Ревью не читаются: спека запрещает переиспользовать предварительный
   результат ревью для допуска.
3. **`in_base`:** REST `GET repos/{o}/{r}/compare/{base_ref}...{merge_commit}` (строка **E5**) — `status` ∈
   {`identical`, `behind`} → `True`, ∈ {`ahead`, `diverged`} → `False`, иное → `malformed`.
4. **`paths[]`:** GraphQL `StewardV3Blobs` (строка **E6**) — `object(oid)` для merge-коммита и головы
   (оба обязаны разрешиться, иначе `object_missing`) и по паре алиасов на путь
   `object(expression: "<sha>:<path>") { ... on Blob { oid } }` (выражения — переменными).
   Отсутствие пути в merge-коммите — установленный факт удаления → `blob_at_merge = None`.
   Отсутствие на голове → `path_absent_at_head` (одобряемые байты обязаны существовать;
   решение о такой заявке — у потребителя, но выдумывать blob библиотека не будет). Объект не
   Blob (каталог) → `not_a_blob`. Путь пустой, абсолютный, с `..` или NUL → `ValueError`
   (ошибка вызывающего, не факт форджа).
5. **Два полных прохода** (§3.4) по ревью и таймлайну.
6. **Повторное чтение доставки** (п. 1, без `paths`): иное значение любого поля → `unstable`.
7. **Устойчивость проходов на срезе `T = merged_at`** (§3.4) → иначе `unstable`.

Возвращаемый `ReviewFacts` — из второго прохода; `head_sha` = `head_at_merge`.
**Решение контроллера (M0-F):** голова и база PR в **каждом** из двух проходов
(`headRefOid`, `baseRefName` строки E3) обязаны совпасть с `head_at_merge` и базой доставки,
иначе `delivery_mismatch`. Причина: без этой сверки подстановка `head_sha = head_at_merge`
скрыла бы расхождение третьего источника — факты ревью, прочитанные о другой голове или
базе, вернулись бы под головой доставки. Целостность фактов (`validate_review_facts`)
проверяется тоже в **обоих** проходах, не только в возвращаемом: дефект первого прохода
после T срез не видит.
Вторая гарантия, на которую опирается срез: таймлайн обязан содержать **ровно одно**
`MergedEvent`, и его `commit.oid` = `merge_commit` (иначе `delivery_mismatch`). **Нормативна
только сверка SHA.** Время события (`createdAt`) с `merged_at` не сверяется: живые снимки #657 и
#636 показывают равенство, но это наблюдение, а не документированная гарантия GitHub, и
отказ по нему превратил бы расхождение источников времени в отказ доставки; T берётся из
`merged_at` (§3.3 п. 1).

#### `read_open_review(transport, repo, pr) -> ReviewFacts | Unavailable`

Предпроверка открытого PR перед агентским мержем (devtools Task 15): те же два полных
прохода §3.4, **без** чтения доставки и путей. Устойчивость — **полное равенство** проходов,
включая `headRefOid` (среза T нет: новый отзыв или новое ревью между проходами — `unstable`).
Состояние PR не фильтруется: `pr_state` возвращается как есть, вывод — у потребителя.
Решение по этому результату не окончательное (спека devtools §8.2, «практическое следствие»).

### 3.4. Проход по ревью и таймлайну

Один проход = все страницы `StewardV3Reviews` (строка **E3** §3.8) + все страницы
`StewardV3Timeline` (строка **E4**; у отзыва запрашивается `actor{login __typename}` — тип
обязателен по §3.4.1, и запрос обязан его просить). `databaseId` приходит **целым числом
JSON** (снято живьём, `pr657_act_sequence`) и нормализуется в десятичную строку `review_id`.
`timelineItems.totalCount` не используется: живьём он **не учитывает** фильтр `itemTypes`
(devtools #637: `totalCount = 14` при единственном `MergedEvent`).

Разбор записи, отказы (спека devtools §5.5):

| Условие | Исход |
|---|---|
| `state` вне {PENDING, COMMENTED, APPROVED, CHANGES_REQUESTED, DISMISSED} | `unknown_state` |
| `commit` = null или нет `oid` | `missing_commit_id` |
| автор любого ревью или актор любого отзыва не установлен (§3.4.1) | `actor_unresolved` |
| `databaseId` не целое | `malformed` |
| `previousReviewState` вне {APPROVED, CHANGES_REQUESTED, COMMENTED, null} | `unknown_state` |
| `review` у события отзыва = null | `malformed` |
| два события отзыва одного `review_id` | `duplicate_dismissal` |
| событие отзыва для `review_id`, которого нет среди ревью | `orphan_dismissal` |
| `submittedAt` отсутствует (null) у ревью не в `PENDING` | `missing_submitted_at` |
| `previousReviewState` = null у **любого** события отзыва | `dismissal_without_previous_state` |
| ревью в `DISMISSED` без события отзыва в том же проходе | `dismissed_without_event` |

Три последние строки — отказ **чтения**, безусловный (`dismissed_without_event` — решение
контроллера R-M0-4): независимо от положения отзыва
относительно T и от того, зависит ли от него исход. Спека devtools §5.3 (строки 258–259)
формулирует правило без условия («отзыв без `previous_state` … → `unavailable`»), а условный
вариант позволил бы принять акт по второму человеку при неустановленном состоянии первого
(devtools `act_decision.py:110–117` перебирает все эффективные ревью). Так же отказ
приходит туда, где его ждёт тест потребителя (devtools Task 11: `expect.unavailable` сверяется
на результате `read`). `effective_reviews` повторяет эти проверки для фактов, собранных
вызывающим вручную (§3.5).

#### 3.4.1. Неустановленный актор (решение контроллера R-M0-6; бывш. Q8)

Класс «неполный обязательный факт допускается, если другой факт даёт положительное
решение» закрывается **на чтении целиком**. Любой актор PR — автор любого ревью (в т. ч.
PENDING), актор любого отзыва, мержер — считается **неустановленным**, если: объект `null`
или отсутствует; `login` отсутствует, не строка или пуст; `login` = `ghost` (зарезервированная
GitHub-заглушка удалённого пользователя; сравнение без учёта регистра); тип
(`__typename` / REST `type`) вне словаря {`User`, `Bot`} (например, `Mannequin`,
`Organization`, `EnterpriseUserAccount`, отсутствует). Такой актор → `Unavailable:
actor_unresolved` **для всего PR**, независимо от того, хватает ли остальных фактов для
положительного решения. Отрицательный двойник: действительный approve Alice + ревью (или
отзыв) неустановленного актора → `actor_unresolved` (вектор, §5).

Основание: спека devtools §5.5, строки 295–299 («отсутствие обязательных полей» →
`unavailable`); потребитель берёт первое подходящее ревью (`act_decision.py:110–117`) и
проверяет только мержера (`:176–179`), поэтому полагаться на `unknown`-классификацию
постороннего актора нельзя. Какой формой GitHub отдаёт удалённого пользователя — **не
проверено**; правило закрыто при обеих (`null` и `ghost`). Цена названа: PR с ревью
удалённого пользователя больше не даёт фактов — это fail-closed. Факт формы — в чек-листе
смоука (§8).

**Устойчивость на T** (`read_act_facts`). Проекция прохода на T:
- ревью с `submitted_at ≤ T` (и PENDING без метки): `(review_id, login, type_hint, commit_id,
  submitted_at, state_T)`, где `state_T` — `previous_state` отзыва с `created_at > T`, если он
  есть, иначе `state`;
- отзывы с `created_at ≤ T`: `(review_id, actor, created_at, previous_state)`.

Проходы устойчивы, если проекции равны как множества. Так спека devtools §5.5 допускает между
проходами ровно то, что срез отбрасывает (новые отзывы и ревью **после** T), и ничего больше.
Сравнение по `≤ T` (а не `< T`) консервативно: появление события **в секунду** T между
проходами — `unstable`.

### 3.5. `effective_reviews(facts, t, *, before) -> dict[str, Review] | Unavailable`

Чистая функция (сети нет, строк §3.8 не читает), нормализация §5.3 спеки devtools на момент `t`. Сравнение события с T:
`stamp < t` — до; `stamp > t` — после; `stamp == t` — до **тогда и только тогда**, когда ключ
события ∈ `before` (`("review", id)` для ревью, `("dismissal", review_id)` для отзыва).
Перебор допустимых порядков группы равных меток (§8.2) — у потребителя; библиотека вычисляет
исход для одного порядка.

0. Проверки целостности **до** любого сравнения с T и безусловно: отзыв с `previous_state
   = None` (любой) → `dismissal_without_previous_state`; ревью не в `PENDING` без
   `submitted_at` → `missing_submitted_at`; ревью в `DISMISSED` без события отзыва →
   `dismissed_without_event` (для фактов от `read_*` всё это уже исключено чтением, §3.4;
   здесь — для фактов, собранных вызывающим). PENDING без `submitted_at`
   исключается. `t` не в каноне → `ValueError` (ошибка вызывающего).
1. Ревью «после T» отбрасывается. Для оставшихся: отзыв «до T» → `DISMISSED`; отзыв «после
   T» → `previous_state`; отзыва нет → `state`.
2. По логину — последнее по `(submitted_at, int(review_id))` ревью с восстановленным
   состоянием ∈ {APPROVED, CHANGES_REQUESTED, DISMISSED}; COMMENTED и PENDING эффективного
   состояния не меняют; отозванное последнее даёт `DISMISSED`, раннее APPROVED не
   восстанавливается. Логин, у которого такого ревью нет, в результат не входит.

Возвращаемый `Review.state` — восстановленное состояние; остальные поля — как прочитаны
(`commit_id` эффективного ревью — его собственный, Review Focus 3 devtools).

### 3.6. Закрытые словари причин

`UNAVAILABLE_CODES` = {`transport`, `http`, `graphql_errors`, `malformed`, `pagination`,
`bad_timestamp`, `unknown_state`, `missing_commit_id`, `missing_submitted_at`,
`dismissal_without_previous_state`, `dismissed_without_event`, `duplicate_dismissal`,
`orphan_dismissal`, `delivery_mismatch`, `bot_login_mismatch`, `unstable`, `object_missing`,
`path_absent_at_head`, `not_a_blob`, `chain_limit`, `branch_not_default`, `no_pr_for_commit`,
`tree_truncated`, `actor_unresolved`}.

`HISTORY_VIOLATION_CODES` = {`commit_without_pr`, `rebase_intermediate`, `pr_base_mismatch`,
`anchor_not_reached`}.

Конструктор `Unavailable`/`HistoryViolation` с кодом вне словаря — `ValueError`: причину
без своего кода завести незаметно нельзя (приём `_UNAVAILABLE_MESSAGES` v2).

### 3.7. История источника политики

#### `read_policy_history(transport, repo, branch, *, anchor, until, files=POLICY_FILES) -> tuple[Activation, ...] | HistoryViolation | Unavailable`

`POLICY_FILES = ("policy/approvers.env", "policy/modes.yaml", "policy/actors.yaml")` — те же
пути, что `devtools/governance/act_policy.py:POLICY_FILES` и таблица §6 спеки devtools.
Пустой `files` или путь не в каноне (абсолютный, `..`, пустой) → `ValueError`: без файлов
«активаций нет» было бы разрешающим умолчанием. `anchor` не 40-hex → `ValueError`.

Только REST: читатель источника доверия обязан работать и без credentials (спека devtools
rev 16 §6.2: оба репо доверия публичны, содержимое читается анонимно), а GraphQL GitHub
без токена не отвечает.

**Credentials и предел частоты (закрыто по спеке devtools §6.2, бывш. Q4).** Анонимное
чтение содержимого источников доверия **обязательно** (спека devtools §6.2, строки 500–505:
«читаются без credentials»); разрешить токен для этого чтения значило бы изменить принятую
политику — M0 этого не делает. Предел анонимного REST — эксплуатационная задача потребителя
и его транспорта, а не разделения ответственности M0: библиотека не кэширует, транспорт
внедряется. Неизменяемые объекты (коммит по SHA, PR по `merge_commit_sha`, блоб по oid)
потребитель вправе кэшировать; свежим обязано быть чтение журнала эпох и головы ветки.
Полный повторный обход всей истории перед каждым решением спекой не предписан — заметка
для devtools, §12.

1. `GET repos/{o}/{r}` (строка **H1**) → `default_branch` обязан равняться `branch`, иначе
   `Unavailable: branch_not_default` (не нарушение — событие периметра не регистрируется).
   **Решение контроллера, подтверждается подписью владельца (бывш. Q3).** Обоснование
   прежней редакции («вне ветки по умолчанию endpoint отдаёт только открытые PR») неверно:
   документация GitHub (`List pull requests associated with a commit`) для коммита вне
   ветки по умолчанию заявляет merged **и** open PR. Ограничение v1 сохраняется по другой
   причине: выдача ассоциаций для коммитов вне ветки по умолчанию **живьём не проверена**,
   а при неверной гипотезе каждое звено читалось бы `commit_without_pr` — ложным
   нарушением, регистрирующим событие периметра и блокирующим установку. Ограничение
   сводит непроверенную поверхность к ветке по умолчанию; смена ветки по умолчанию
   источника даёт `Unavailable` (читатель ей не следует — спека devtools §6.1 п. 1, строки
   403–407 — и не продолжает молча). Это сужение допустимых входов Task 12/12a devtools —
   отступление О11 (§10). Снятие — после живой проверки ассоциаций вне ветки по умолчанию
   (чек-лист §8).
2. `until = None` → голова ветки `GET repos/{o}/{r}/branches/{branch}` (строка **H2**, `commit.sha`); ветка
   не разрешается заново между чтениями.
3. **Цепочка first-parent** от `until` к `anchor`: `GET repos/{o}/{r}/commits/{sha}` (строка **H3**) →
   `parents[0].sha`. Цепочка, **полностью прочитанная** до корня без встречи якоря, →
   `HistoryViolation: anchor_not_reached`; любой недочитанный объект (404, ошибка,
   предел) → `Unavailable` без регистрации. **Закрыто по спеке devtools (бывш. Q2):**
   отсутствие якоря проверяемой эпохи в её диапазоне — нарушение формы истории (§6.1 п. 4,
   строки 433–437), регистрируемое потребителем как событие периметра (§6.3, строки
   535–543); недоступность объектов диапазона — ограничение, `unavailable` (§6.4, строки
   557–562). Предел — **1000 звеньев**, превышение → `chain_limit`.
4. **Каждое звено, кроме якоря, — PR-мерж в эту ветку** (§6.1 п. 4):
   `GET repos/{o}/{r}/commits/{sha}/pulls` (строка **H4**, **массив**; все страницы по P1). Среди PR с `merged_at ≠ null`
   ровно один обязан иметь `merge_commit_sha == sha` (обратная сверка); его `base.ref ==
   branch`, иначе `pr_base_mismatch`. Нет такого, но есть смёрженный PR, содержащий коммит,
   → `rebase_intermediate`; нет ни одного → `commit_without_pr`. Больше одного совпадения →
   `malformed`. **Fail-closed при обеих гипотезах об API.** Отдаёт ли endpoint для
   промежуточного коммита многокоммитного rebase-merge его PR — живьём не проверено. Если
   отдаёт — `rebase_intermediate`; если нет — `commit_without_pr`; оба — `HistoryViolation`.
   Потребитель не обязан и не должен различать эти два кода в решении (только в
   диагностике); вектор многокоммитного rebase-merge фиксирует первый вариант, второй — его
   двойник (§5). Семантика `merge_commit_sha` для squash/rebase — документирована GitHub
   (`Get a pull request`).
5. **Якорь** — тоже PR-мерж (иначе те же нарушения) и **всегда первый элемент** результата:
   он — активация эпохи независимо от того, менял ли он файлы.
6. **Активация** — звено, у которого набор `(путь → blob oid)` файлов `files` отличается от
   набора его first-parent. Набор звена — из **полностью прочитанного дерева коммита**:
   `GET repos/{o}/{r}/git/trees/{tree_sha}?recursive=1` (строка **H5**), где `tree_sha` — `commit.tree.sha`
   из уже прочитанного коммита. Отсутствие файла **установлено** только тогда, когда ответ
   успешен, `truncated = false` и записи с этим путём (тип `blob`) в нём нет. Любой
   не-2xx, включая **404**, — `Unavailable: http` (спека devtools §5.5, строки 295–299:
   HTTP 404 — `unavailable`); `truncated = true` → `tree_truncated`; запись с этим путём
   не типа `blob` → `not_a_blob`. `files_sha256` активации —
   `sha256(байты)` hex **без префикса** (как `act_policy.parse_policy_set`) по
   `GET repos/{o}/{r}/git/blobs/{oid}` (строка **H6**, base64); отсутствующий файл в словарь **не входит**.
   Возврат к прежним байтам — новая активация (§6.1 п. 2): сравнение идёт с родителем, а не
   с прошлой активацией.
7. Порядок результата — first-parent, старые первыми. `merged_at` берётся из PR (REST),
   **дата коммита не используется** (§6.1). Противоречие `merged_at` порядку first-parent
   библиотека **не** проверяет: это шаг решения devtools (Task 12 п. 2) — факт отдаётся как
   есть.

**Пределы обнаружения (вектор фиксирует, а не скрывает).** Однокоммитный rebase-merge даёт
`merge_commit_sha`, равный коммиту ветки (документированная семантика API GitHub, живым тестом
не проверено — §8), и проходит как squash. Indirect merge с формально связанным PR не
обнаруживается. Force-push на предка с сохранённым якорем не обнаруживается (§6.1 модель
доверия, Q7 спеки devtools).

#### `read_activation(transport, repo, merge_commit, *, files=POLICY_FILES) -> Activation | Unavailable`

Факт одной активации (якорь эпохи): `commits/{sha}` (существование), `commits/{sha}/pulls` →
PR с `merge_commit_sha == merge_commit` и `merged_at ≠ null` (нет → `no_pr_for_commit`),
`files_sha256` по п. 6. Ветка не проверяется — принадлежность якоря ветке доказывает
`read_policy_history`.

### 3.8. Таблица контрактов endpoint (R-M0-7)

Единственный источник форм. Каждая строка снята **живым read-only** запросом
(`scripts/capture_approval_facts_v3_live.py`, учётка `gh` по умолчанию, только репо
`andrei-shtanakov/{devtools, approval-policy}` — **только публичные**: steward публичен, снимков
приватных репо в нём нет, публичность каждого репо доказывается живым снимком `pub_*` до
любого другого обмена с ним; только `GET` и
GraphQL-query) и лежит обезличенной в `tests/fixtures/approvalfacts_v3/live/` (столбец
«снимки»). Фикстуры и векторы **выводятся из снимков** именованными преобразованиями
(§5), а не пишутся руками. Каждая проверка §3.2–3.7 ссылается на строку; для каждой строки
есть положительный двойник — вектор, проходящий на неизменённом снимке.

**Захват — fail-closed и атомарный.** Каждый обмен обязан совпасть с объявленным ожиданием
(код выхода `gh`, строка HTTP-статуса, разбираемое JSON-тело, GraphQL `errors` только где
объявлены, полнота — `hasNextPage == false`, нет `rel="next"`, нет `truncated` — кроме
объявленной P1; отрицательные снимки `neg_e2_delivery_absent_pr` и `h5_tree_absent_422` объявлены
явно); любое расхождение прерывает прогон до записи. Файлы пишутся во временный каталог и
подменяют `live/` целиком (устаревшие снимки не выживают); `MANIFEST.json` несёт sha256
каждого снимка, тест сверяет манифест с каталогом в обе стороны и по байтам.

**Согласованные положительные наборы (r5).** Положительный вектор акта — это **неизменённая
живая последовательность** всех обменов одного вызова `read_act_facts(repo, pr,
paths=[path])` в порядке библиотеки: E2, E1 (доставка) → E5 → E6 (**ровно один** реальный
путь вектора) → E3, E4 (проход 1) → E3, E4 (проход 2) → E2, E1 (повторное чтение доставки).
Снимки `pr657_act_sequence` (merge-коммит; путь
`docs/evidence/2026-10-30-df-stage2a-acceptance.md`) и `pr636_act_sequence` (squash; путь
`TODO.md`) сняты именно так — каждый обмен отдельным живым запросом, второй проход —
настоящим вторым запросом; replay без преобразований обязан дать `Delivery` и
`ReviewFacts`. `closed606_act_sequence` — несмёрженный PR devtools #606: только E2, E1 (дальше библиотека не
читает). Трёхпутевой `neg_e6_blobs_present_absent_tree` — **отдельный отрицательный**
снимок (`path_absent_at_head` / `not_a_blob`), в положительные наборы не входит.

Канонические тексты GraphQL (пробелы — как в `queries.py`; `StewardV3Blobs` строится для N
путей алиасами `m{i}`/`h{i}`):

```graphql
query StewardV3Delivery($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){number state merged mergedAt mergeCommit{oid} mergedBy{login __typename} headRefOid baseRefName}}}
query StewardV3Reviews($owner:String!,$name:String!,$number:Int!,$after:String){repository(owner:$owner,name:$name){pullRequest(number:$number){state headRefOid baseRefName reviews(first:100,after:$after){nodes{databaseId state submittedAt commit{oid} author{login __typename}} pageInfo{hasNextPage endCursor}}}}}
query StewardV3Timeline($owner:String!,$name:String!,$number:Int!,$after:String){repository(owner:$owner,name:$name){pullRequest(number:$number){timelineItems(itemTypes:[REVIEW_DISMISSED_EVENT,MERGED_EVENT],first:100,after:$after){nodes{__typename ... on ReviewDismissedEvent{createdAt actor{login __typename} previousReviewState review{databaseId}} ... on MergedEvent{createdAt commit{oid}}} pageInfo{hasNextPage endCursor}}}}}
query StewardV3CommitPulls($owner:String!,$name:String!,$sha:GitObjectID!,$after:String){repository(owner:$owner,name:$name){object(oid:$sha){... on Commit{associatedPullRequests(first:100,after:$after){nodes{number mergeCommit{oid}} pageInfo{hasNextPage endCursor}}}}}}
query StewardV3Blobs($owner:String!,$name:String!,$merge:GitObjectID!,$head:GitObjectID!,$m0:String!,$h0:String!,…){repository(owner:$owner,name:$name){mc:object(oid:$merge){oid} hc:object(oid:$head){oid} m0:object(expression:$m0){__typename ... on Blob{oid}} h0:object(expression:$h0){__typename ... on Blob{oid}} …}}
```

| Строка | Запрос | Допустимая форма ответа (2xx) | Нормализация | Проверки (код отказа) | Снимки | Положительный двойник |
|---|---|---|---|---|---|---|
| **G0** | любой GraphQL | объект `{data, errors?}`; `data` — объект | — | непустой `errors` при любом статусе → `graphql_errors`; `data` не объект → `malformed` | `neg_e2_delivery_absent_pr` (200 + `errors[NOT_FOUND]` + `pullRequest: null`) | любой E2–E7 |
| **R0** | любой REST | форма — по строке endpoint | — | не-2xx → `http`; нет строки статуса → `transport` | `h5_tree_absent_422` (фактически **422**, не 404) | любой E1, H1–H6 |
| **P1** | REST-список (H4) | заголовок `Link` отсутствует **или** `<url>; rel="x"` через `, ` | `rel="next"` → URL следующей страницы | нет `next` → конец; заголовок есть, грамматика не сошлась → `malformed`; > 50 страниц → `pagination` | `p1_rest_list_with_link_next` (`rel="next"`, `rel="last"`); H4 без `Link` | H4 одностраничный |
| **E1** | `GET repos/{o}/{r}/pulls/{n}` | **объект**: `merged: bool`, `state: open\|closed`, `merge_commit_sha: str\|null`, `merged_at: str\|null`, `merged_by: {login, type}\|null`, `head.sha`, `base.ref` | `merged` первым; при `True`: `merged_at` → канон, `merged_by.login` `slug[bot]` → `slug` при `type = Bot` | §3.3 п. 1: `merged` ≠ E2 → `delivery_mismatch`; при `False` прочее **не читается**; при `True` — поля мержа ≠ E2 → `delivery_mismatch`, мержер → §3.4.1; `merge_commit_sha`/`head.sha` не `^[0-9a-f]{40}$` → `malformed` (§3.2 «Пути REST») | `pr657_act_sequence` (User), `pr636_act_sequence`, `closed606_act_sequence` (`merged: false`, **`merge_commit_sha` не null**, `merged_by: null`) | merge-commit и squash; несмёрженный → `waiting_delivery` |
| **E2** | `StewardV3Delivery` | G0 + `pullRequest`: `state ∈ {OPEN, CLOSED, MERGED}`, `merged: bool`, `mergedAt: str\|null`, `mergeCommit: {oid}\|null`, `mergedBy: {login, __typename}\|null`, `headRefOid`, `baseRefName` | то же | §3.3 п. 1; `state` вне словаря → `unknown_state`; `merged: true` и `mergedAt`/`mergeCommit` null → `malformed`; `mergeCommit.oid`/`headRefOid` не `^[0-9a-f]{40}$` → `malformed` | `pr657_act_sequence`, `pr636_act_sequence`, `closed606_act_sequence` | те же |
| **E3** | `StewardV3Reviews` | G0 + `pullRequest{state, headRefOid, baseRefName, reviews{nodes[], pageInfo}}`; узел: `databaseId: int`, `state`, `submittedAt: str\|null`, `commit: {oid}\|null`, `author: {login, __typename}\|null` | `databaseId` → десятичная строка; метки → канон | §3.4 таблица; §3.4.1 (автор); act: `headRefOid`/`baseRefName` любого прохода ≠ голове/базе доставки → `delivery_mismatch` (M0-F, §3.3) | `pr657_act_sequence` (`User` APPROVED, `Bot` `copilot-pull-request-reviewer` COMMENTED), `closed606_review_pass` (User APPROVED, Bot `copilot-pull-request-reviewer`), `pr603_review_pass` (**ревью на коммитах ≠ `headRefOid`**: CHANGES_REQUESTED и APPROVED на прежних пушах) | оба снимка; Bot-автор — установлен |
| **E4** | `StewardV3Timeline` | G0 + `timelineItems{nodes[], pageInfo}`; узел `MergedEvent{createdAt, commit{oid}}` или `ReviewDismissedEvent{createdAt, actor{login, __typename}\|null, previousReviewState\|null, review{databaseId}\|null}` | метки → канон | §3.3 (ровно один `MergedEvent`, `commit.oid` = мерж-коммит); §3.4 таблица; §3.4.1 (актор отзыва) | `pr657_act_sequence` (один `MergedEvent`; наблюдение: `createdAt` = `mergedAt`, не проверка), `closed606_review_pass` (пусто). **`ReviewDismissedEvent` живьём не снят** — в разрешённых репо нет ни одного отзыва (полный обход devtools, 30.10) | merged-снимок; отзыв — производный от схемы вектор, помечен «не проверено» (Task 13) |
| **E5** | `GET repos/{o}/{r}/compare/{base}...{merge}` | **объект**, `status ∈ {identical, behind, ahead, diverged}` | — | §3.3 п. 3; иной `status` → `malformed`; `base` и `merge` — закодированные сегменты пути (§3.2 «Пути REST») | `pr657_act_sequence` (`identical`), `pr636_act_sequence` (**`behind`**) | оба → `in_base = True`; `ahead`/`diverged` живьём не сняты (Task 13) |
| **E6** | `StewardV3Blobs` | G0 + `mc`/`hc`: `{oid}\|null`; `m{i}`/`h{i}`: `null` \| `{__typename: "Blob", oid}` \| `{__typename: <иное>}` | — | §3.3 п. 4: `mc`/`hc` null → `object_missing`; `m{i}` null → `blob_at_merge = None`; `h{i}` null → `path_absent_at_head`; не `Blob` → `not_a_blob` | `pr657_act_sequence`, `pr636_act_sequence` (один путь, Blob в мерже и на голове); отрицательный `neg_e6_blobs_present_absent_tree` (Blob, `null`, `{__typename: "Tree"}` без `oid`) | однопутевые наборы без преобразований |
| **E7** | `StewardV3CommitPulls` (release-продюсер) | G0 + `object`: `null` \| `{associatedPullRequests{nodes[{number, mergeCommit: {oid}\|null}], pageInfo}}` | — | §6 продюсер: PR с `mergeCommit.oid = sha` → E1/E2 | `e7_commit_pulls_graphql_merge` | merge-коммит → PR #657 |
| **H1** | `GET repos/{o}/{r}` | **объект**, `default_branch: str` | — | §3.7 п. 1 → `branch_not_default` | `h1_repo_policy` (`main`) | `main` = закреплённая ветка |
| **H2** | `GET repos/{o}/{r}/branches/{b}` | **объект**, `commit.sha` | — | §3.7 п. 2 | `h2_branch_policy` | голова `main` |
| **H3** | `GET repos/{o}/{r}/commits/{sha}` | **объект**: `sha`, `parents: [{sha}]` (0, 1 или 2 элемента), `commit.tree.sha` | first parent = `parents[0].sha` | §3.7 п. 3: `[]` без якоря → `anchor_not_reached` | `h3_commit_root_no_parents` (`[]`), `…_side_one_parent`, `…_merge_two_parents` | цепочка до якоря |
| **H4** | `GET repos/{o}/{r}/commits/{sha}/pulls?per_page=100` | **массив** PR-объектов: `number`, `merge_commit_sha`, `merged_at: str\|null`, `base.ref` | — | §3.7 п. 4: совпадение `merge_commit_sha`; иначе `rebase_intermediate` / `commit_without_pr`; `base.ref` ≠ ветка → `pr_base_mismatch` | `h4_commit_pulls_merge_commit` (#657), `…_squash` (#636), `…_direct_push` (**`[]`**: корень `approval-policy` — прямой коммит), `…_side_commit` (**боковой коммит merge-коммит-мержа ассоциирован с #657, чей `merge_commit_sha` другой** — гипотеза А §3.7 п. 4 подтверждена живьём для коммитов на ветке по умолчанию) | merge и squash |
| **H5** | `GET repos/{o}/{r}/git/trees/{tree}?recursive=1` | **объект**: `truncated: bool`, `tree: [{path, type, sha}]` | набор `{path: sha}` для `type = blob` | §3.7 п. 6: `truncated` → `tree_truncated`; не `blob` → `not_a_blob` | `h5_tree_recursive_policy` (`truncated: false`, `policy/approvers.env`), `h5_tree_absent_422` (422) | полное дерево |
| **H6** | `GET repos/{o}/{r}/git/blobs/{oid}` | **объект**: `encoding: "base64"`, `content`, `size` | `sha256(b64decode(content))` hex | `encoding` ≠ `base64` → `malformed` | `h6_blob_policy_approvers` | `files_sha256` approvers.env |

**Живые наблюдения, влияющие на других (не дефекты M0, — в ответ #211):**
- `andrei-shtanakov/approval-policy` сегодня — **один корневой прямой коммит** без PR (H3,
  H4 `[]`) и только `policy/approvers.env` (H5). Якорь эпохи — PR-мерж (§3.7 п. 5), поэтому
  bootstrap источника должен быть PR-мержем (шаги H1–H2 devtools); корень как якорь даст
  `commit_without_pr`.
- Для владельца (учётка с правом записи) `approval-policy` отдаёт `allow_rebase_merge: true`
  — предпосылка периметра §6.1 п. 2 спеки devtools сейчас не выполнена.

## 4. Контракт `contracts/approval-facts/v3/`

- `SCHEMA.json` (draft 2020-12, `additionalProperties: false` во всех ветках) с `$defs`:
  `review_record` (§5.2 спеки devtools: `repository`, `pr`, `observed_at`, `head_sha`,
  `base_ref`, `pr_state`, `reviews[]`, `dismissals[]`, `effective[]`, `complete`),
  `delivery_record` (§5.4: `merged`, `merge_commit`, `merged_at`,
  `merged_by{identity, type_hint, actor_class}`, `head_at_merge`, `in_base`, `paths[]`,
  `complete`), `policy_history_record` (§6.1 п. 6: `repository`, `branch`, `anchor`, `until`,
  `activations[]` | `violation`, `complete`), `header` и `result` файла release-гейта (§6).
  Записи — сериализация тех же фактов, что возвращает библиотека; потребители, работающие в
  процессе, используют dataclass-ы, а не файлы.
- `README.md` — назначение, словари причин §3.6, формат векторов §5, правила
  восстановления §3.5, пределы обнаружения §3.7, канон меток.
- `vectors/` — §5.
- `v2/` **заморожен** (правило steward: опубликованная версия контракта не меняется).

## 5. Векторы сбора (§5.6 спеки devtools)

Вектор — JSON-файл: вход (`repo`, `pr`/`branch`/…, `transport` — записанные обмены
`ReplayTransport`) и ожидание `expect`. Векторы гоняются в CI steward и вендорятся
потребителями пиненой копией (devtools Task 11, discovery M2).

**Происхождение (R-M0-7).** Обмены вектора **выводятся из живых снимков** §3.8
генератором: вектор несёт `"derived_from": [<имена снимков>]` и `"transforms": [...]` —
закрытый список именованных преобразований (`set_field(path, value)`, `drop_field(path)`,
`append_node(path, node)`, `retime(path, ts)`, `repeat_exchange(name)` для второго прохода,
`page_split(path, n)`). Узел, добавляемый `append_node`, обязан содержать **ровно** поля
запроса из §3.8 (генератор сверяет по тексту запроса) — фикстура не может вернуть
незапрошенное поле. Единственное исключение по форме — `ReviewDismissedEvent`: живьём не
снят (§3.8 E4), его узел строится по схеме GitHub и тексту запроса E4, вектор помечен
`"unverified_shape": "ReviewDismissedEvent"`, проверка формы — Task 13.

**Раскладка.** Векторы акта — в корне `vectors/*.json` (ровно форма, которую ждёт тест
devtools Task 11: `transport`, `repo`, `pr`, `paths`, `t`, `before`, `expect`); прочие — в
подкаталогах `vectors/open_review/`, `vectors/history/`, `vectors/activation/` (не
попадают в нерекурсивный `glob("*.json")` devtools; § 10 О6).

```json
{
  "kind": "act",
  "title": "APPROVED отозвано после T — на T эффективное APPROVED (§5.3)",
  "repo": "owner/product", "pr": 7, "paths": ["workstreams/ws-7/requirements.md"],
  "t": "2026-10-12T10:00:00Z", "before": [["review", "101"]],
  "transport": {"exchanges": [{"request": {...}, "response": {...}}]},
  "expect": {"effective": {"alice": "APPROVED"},
             "effective_commit": {"alice": "1111…"},
             "delivery": {"merged": true, "merged_by_login": "df-reviewer", "...": "..."}}
}
```

`expect` — ровно одно из: `{"unavailable": "<code>"}`; `{"waiting_delivery": true}`;
`{"effective": {...}, "effective_commit"?: {...}, "delivery"?: {...}}` (подмножество полей,
`paths` — список объектов); для `history` — `{"activations": [{pr, merge_commit, merged_at,
files_sha256}, ...]}` | `{"violation": "<code>"}` | `{"unavailable": "<code>"}`; для
`activation` — `{"activation": {...}}` | `{"unavailable": "<code>"}`; для `open_review` —
`{"effective": {...}}` (с `t`) | `{"unavailable": "<code>"}`. Тест steward дополнительно требует
`unused() == []` и пустой `unexpected` у транспорта.

**Обязательный набор** (каждая строка — файл; имена — в плане, Task 7):

| Группа §5.6 | Векторы |
|---|---|
| неполная пагинация | `hasNextPage` без `endCursor` (ревью); > 50 страниц таймлайна |
| ошибка доступа | REST 403; GraphQL 401 |
| смена головы во время чтения | open: `headRefOid` меняется между проходами → `unstable`; act: REST `head.sha` ≠ `headRefOid` → `delivery_mismatch`; act: `headRefOid`/`baseRefName` прохода ≠ доставке → `delivery_mismatch` (M0-F); act: повторное чтение доставки отличается → `unstable` |
| отзыв + новое CHANGES_REQUESTED между страницами, голова неизменна | open → `unstable`; act (оба события до T) → `unstable` |
| отзыв после второго прохода предпроверки, но до доставки | act: отзыв с `created_at < merged_at` → эффективное `DISMISSED` (решение по чтению после мержа) |
| `200` + partial `data` + `errors` с видимым APPROVED | `graphql_errors` |
| восстановление на T, оба контрпримера §5.3 | APPROVED→CHANGES_REQUESTED→отзыв второго ⇒ `DISMISSED`; APPROVED отозвано после T ⇒ `APPROVED` |
| отзыв без `previous_state` | отрицательный двойник действительного акта: отзыв **ревью другого логина** до T с `previous_state = null` при действительном APPROVED Alice ⇒ `dismissal_without_previous_state` (от `read_act_facts`, не от `effective`); то же с отзывом после T ⇒ тот же отказ |
| несколько ревью одного логина | APPROVED→CR ⇒ CR; CR→APPROVED ⇒ APPROVED; APPROVED(old)→APPROVED(new) ⇒ APPROVED с `commit_id` нового; APPROVED→COMMENTED ⇒ APPROVED |
| отзыв до и после `merged_at` | `DISMISSED` / `APPROVED`; отзыв после T, появившийся между проходами → устойчиво, `APPROVED` |
| две формы логина бота | REST `df-reviewer[bot]`/Bot + GraphQL `df-reviewer`/Bot ⇒ `merged_by_login = "df-reviewer"`; несводимые формы ⇒ `bot_login_mismatch` |
| squash и merge-коммит для `paths[]` | blob в squash-коммите; blob в merge-коммите; путь удалён в мерже ⇒ `blob_at_merge = null` |
| равные метки (§8.2, применение потребителем) | отзыв с `created_at = T`: ключ в `before` ⇒ `DISMISSED`, нет ⇒ `APPROVED`; approve с `submitted_at = T`: в `before` ⇒ `APPROVED`, нет ⇒ логина нет |
| PENDING | APPROVED + PENDING без `submitted_at` ⇒ `APPROVED`; отправленное без `submitted_at` ⇒ `missing_submitted_at` (от `read_act_facts`) |
| неустановленный актор (R-M0-6) | действительный APPROVED Alice + ревью автора `null` ⇒ `actor_unresolved`; то же с логином `ghost`; актор отзыва `null`; мержер `null`; тип `Mannequin` — все `actor_unresolved` от `read_act_facts`; open: ревью `ghost` ⇒ `actor_unresolved` |
| прочие отказы §5.5 | неизвестный `state`; нет `commit`; `DISMISSED` без события (от `read_act_facts`, R-M0-4); PR не смёржен ⇒ `waiting_delivery` — **из живого снимка с ненулевым тестовым REST `merge_commit_sha`** (`closed606_act_sequence`, без преобразований) |
| отзыв — положительный двойник типа актора | действительный APPROVED Alice + обычный отзыв ревью Bob актором-`User` (`actor{login, __typename}` заполнены) ⇒ `effective {"alice": "APPROVED", "bob": "DISMISSED"}`, не `actor_unresolved`; двойник: тот же отзыв с `actor` без `__typename` ⇒ `actor_unresolved` |
| положительные двойники строк §3.8 | `live_pr657.json` и `live_pr636.json` — **неизменённые** живые последовательности E1–E6 одного PR (merge-коммит и squash, по одному реальному пути); E3 Bot-автор (`copilot-pull-request-reviewer` в `pr657_act_sequence`, `closed606_review_pass`) — установлен; H1–H6 — история из снимков `approval-policy`/devtools |
| история (§6.1, приёмка №28) | merge-коммит + squash + нейтральное звено ⇒ активации; **многокоммитный rebase-merge ⇒ `HistoryViolation: rebase_intermediate`** и его двойник при второй гипотезе об API (у промежуточного коммита нет ассоциаций) ⇒ `HistoryViolation: commit_without_pr`; **однокоммитный rebase-merge ⇒ принят как `Activation` (предел, не нарушение)**; прямой push ⇒ `commit_without_pr`; якорь не достигнут ⇒ `anchor_not_reached`; база PR другая ⇒ `pr_base_mismatch`; возврат байтов X→Y→X ⇒ три активации; ветка не по умолчанию ⇒ `branch_not_default`; 404 звена ⇒ `http`; коммит прочитан, а 404 на его дереве ⇒ `http` (не «файлов нет»); дерево `truncated` ⇒ `tree_truncated`; файл политики установленно отсутствует в полном дереве ⇒ активация без ключа |
| активация | PR-мерж ⇒ `Activation`; коммит без PR ⇒ `no_pr_for_commit` |

Вектор однокоммитного rebase-merge **фиксирует предел**: его заголовок и `title` говорят, что
результат — не доказательство отсутствия rebase-merge (спека devtools §6.1 п. 2, rev 15).

## 6. Миграция `gate-check --stage release` на запись `delivery`

Спека devtools §9.3: release-гейт мигрирует на v3, v2 — только для истории.

**Файл.** Путь прежний — `<repo-root>/.steward/approval_facts.jsonl`; JSONL; строка 1 —
`header` (поля v2 без изменений смысла: `repository`, `generated_at`, `valid_until`,
`policy_version`, `policy_digest`, `complete`, `scope_sha256`, `scope`), но
`schema_version: "3"`; дальше по одной `result` на элемент scope:

| `state` | `delivery` |
|---|---|
| `merged` | объект `delivery_record` с `merged_by{identity, type_hint, actor_class}` |
| `actor_unavailable` | объект `delivery_record` с `merged_by: null` |
| `not_merged`, `not_found`, `no_matching_pr` | `null` |

Пять терминальных состояний v2 сохраняются (их различение — свойство scope, а не PR);
поля мержа v2 (`merge_sha`, `identity`, `type_hint`, `actor_class`) переезжают в
`delivery` (спека devtools §5.1). `paths` в файле гейта — `[]`.

**Продюсер.** `steward approval-facts` переходит на v3 целиком: разрешение `merge_sha` →
PR — `associatedPullRequests` (как v2, но через `Transport`), затем **та же**
`_read_delivery` библиотеки (§3.3 п. 1, 3) — одна реализация чтения доставки на оба
потребителя. `actor_unresolved` **мержера** продюсер записывает терминальным состоянием v2
`actor_unavailable` (`delivery.merged_by: null`), которое гейт отвергает строкой 9 таблицы
§8.3 v2 — блокирующей находкой, а не допуском; любой другой `Unavailable` доставки —
`MechanicalFailure`, файл не публикуется. Классификация — `classify_actor` по локальной `profiles/approval-policy.yaml`
(digest — в header). Параллельного v2-эмиттера нет (правило v2-плана «никакой параллельной
поддержки двух версий в рантайме»; внешних читателей v2 нет: dispatcher его не вендорил —
`dispatcher/contracts/` на 2026-10-30).

**Гейт.** `resolve_facts` распознаёт v2-файл как `legacy_v2` (как сегодня `legacy_v1`:
finding по пути по умолчанию, config error по явному `--approval-facts`) с подсказкой
перевыпустить. Таблица §8.3 v2 сохраняется построчно с заменой источника полей на
`result.delivery`; добавляется строка **`in_base = false` ⇒ finding «merge не достижим из
базы»** между строками 8 и 9 (**решение контроллера, подтверждается подписью владельца**,
бывш. Q9: ужесточение гейта включается). Правило
«классификация из файла, digest политики обязан совпасть» не меняется.

**v2 в коде.** Каталог контракта `v2/` заморожен. Модули `approvalfacts/{model,producer,
reader,publish}.py` в M0 **не удаляются**: `publish` и `model` переиспользуются v3
(транзакция публикации, `RequestId`, `scope_digest`), `reader` нужен для распознавания
`legacy_v2`. **Решение контроллера, подтверждается подписью владельца (бывш. Q5):**
удаление v2-продюсера — отдельный пункт TODO **после приёмки** M0.

**Переключение — атомарное.** Продюсер v3 и читатель v3 появляются неактивными (не
вызываются ни CLI, ни гейтом); затем **одно** изменение переключает `steward
approval-facts` на выпуск v3 и `resolve_facts` на чтение v3, с тестом связки «выпуск →
чтение → `gate-check --stage release`». Промежуточного состояния, в котором штатная команда
выпускает файл, который гейт отвергает (текущий `reader.py:273–276` принимает только `"2"`),
в истории нет.

## 7. Гейты steward

### 7.1. Запрет `human ∩ agent`

Спека devtools §6: «загрузчик отвергает пересечение `human_identities ∩ agent_identities`».
В steward — в двух местах, чтобы обойти его было нечем:

- `load_approval_policy` → `PolicyError("<path>: identities in both human_identities and
  agent_identities: [...]")` (exit 2 у гейта);
- `ApprovalPolicy.__post_init__` → `PolicyError` с тем же текстом без пути: devtools строит
  `ApprovalPolicy` в процессе (`act_policy.classify`), и пересечение, собранное вызывающим,
  не должно классифицироваться молча (`human` выигрывал бы из-за порядка проверок
  `classify_actor`).

`profiles/approval-policy.yaml` пересечения не содержит (сверено 2026-10-30); файл не
меняется (authority-root).

### 7.2. Режим акта и провайдер статуса узла `node_status`

Ожидание потребителя: «гейты steward, читающие статус узла, получают параметр
`node_status: Callable[[str], str] | None`; узел с конвертом `review/v1` без провайдера — „не
одобрен“ (fail-closed), frontmatter не читается».

**Норма (спека devtools §9.1 п. 3, строки 747–758; §10, строки 807–815):** режим узла
определяется **только** заявкой его действующего одобрения в истории форджа; admission режима
выполняется **до выбора читателя**; у узла `review/v1` отсутствующий или подменённый конверт —
«не одобрен», fallback на frontmatter запрещён.

**Решения контроллера по кругу 2 (R-M0-1…R-M0-4, второй заход одного класса — меняется
механизм, а не условие).** Механизм ревизии r1 («история git каталога `approvals/` →
режим») **удалён целиком**: отсутствие каталога не доказывает legacy-admission (продукт
мог быть переключён на `review` до первого манифеста воркстрима), а подмена `status`
выключала остальные проверки вместо отказа гейта.

#### 7.2.1. Режим — обязательный вход от вызывающего (R-M0-1, R-M0-5)

steward режим узла **не определяет** — ни по файлам, ни по истории. Режим передаёт вызывающий,
который выполнил admission по пинованной политике (devtools `act_admission`, спека devtools
§10). Режим **обязателен** везде, где steward устанавливает одобрение: `collect_bundle`,
`act_statuses` (а через `ActStatuses` — `run_checks`, `check_approval_evidence`, эмиттер) и
CLI `--act-mode {legacy,review}`. Значения:

- `legacy` — вызывающий установил, что одобрения бандла — прежний акт «мерж»: frontmatter
  авторитетен, как сегодня;
- `review` — одобрения устанавливает только провайдер (§7.2.3); frontmatter `status` для
  установления одобрения не используется.

**Нет значения — ошибка вызова** (решение контроллера R-M0-5): в API — `TypeError`
(keyword-only параметр без умолчания), в CLI — exit 2 с сообщением «--act-mode is required:
pass the admission result (legacy|review)»; проверка — в теле команды после разбора, с
единственным исключением `--print-snapshot-digest` (§7.2.3). Ни `legacy`, ни предупреждения по умолчанию:
отсутствие результата admission не превращается в разрешение. Прежние вызывающие обязаны явно
передать `legacy`: собственный CI steward мигрирует в этом же PR (план, Task 9), потребители —
при осознанном подъёме пина (их пины — SHA, §9; заметка §12).

**Детектор очевидного противоречия (fail-closed-находка, не authority).** Если
`act_mode = legacy`, а в `<spec_dir>/../approvals/` есть конверт `<node>.yaml` или манифест
`requests/*.yaml` с `act: review/v1`, либо каталог или любой его YAML не читается, →
**`GC-ACT-MODE-CONFLICT`** (error) на бандл. Отсутствие каталога ничего не доказывает и
ничего не разрешает.

#### 7.2.2. «Не одобрен» — находка, а не подмена статуса (R-M0-2); единый интерфейс

`meta.status` **не переписывается**. Порядок один — **сбор → статусы → проверки**:

```python
ActMode = Literal["legacy", "review"]

def collect_bundle(graph, spec_dir: Path, *, act_mode: ActMode
                   ) -> tuple[list[Artifact], list[Finding]]: ...
    # сбор как сегодня + детектор GC-ACT-MODE-CONFLICT

@dataclass(frozen=True)
class ActStatuses:
    act_mode: ActMode
    provider_given: bool
    answers: Mapping[str, str]        # node_id → ответ провайдера; пусто вне `review`
    snapshot: Snapshot | None
    def claimed(self, a: Artifact) -> bool: ...      # frontmatter status == approved
    def established(self, a: Artifact) -> bool: ...  # legacy: claimed; review: answers == "approved"
    def in_scope(self, a: Artifact) -> bool: ...     # claimed or established

def act_statuses(artifacts, *, act_mode: ActMode, node_status: NodeStatus | None,
                 snapshot: Snapshot | None) -> ActStatuses: ...
    # единственное место вызова провайдера; node_status при legacy или без snapshot → ValueError

def run_checks(graph, artifacts, git, assignments=None, *, statuses: ActStatuses,
               prospective: bool = False, not_required: frozenset[str] = frozenset()
               ) -> list[Finding]: ...
```

`run_checks` не принимает ни `act_mode`, ни `node_status` отдельно: режим и ответы приходят
только в `statuses` (обязательный параметр), так что вызвать проверки без результата
admission нельзя. `check_approval_evidence(..., statuses=...)` и эмиттер вердиктов получают
тот же объект.

Проверки, которые сегодня берут артефакт «в работу» по `status == approved`
(`check_upstream_approved` как downstream, `check_status_git`, `check_stale_cascade`,
`check_approval_evidence`), берут его при `statuses.in_scope(a)` — **ни одна проверка не
выключается** из-за отказа провайдера; при `legacy` это ровно сегодняшнее поведение.
Требование к upstream в `check_upstream_approved` — `statuses.established(upstream)`. Новая
проверка `check_act_status` (только `review`, вызывается из `run_checks`):

- `provider_given = False` → **`GC-ACT-PROVIDER-MISSING`** (error) на бандл — бандл, целиком
  оставшийся без установленных одобрений, не проходит молча;
- для каждого артефакта узла с `in_scope(a)` и `not established(a)` → **`GC-ACT-NOT-APPROVED`**
  (error) с причиной провайдера (`not_approved:…`, `unavailable:…`, `invalid_provider_answer`,
  `provider_error`, `absent_from_provider`; без провайдера — `unavailable:no_status_provider`).

Ответ вне форм `approved` | `not_approved:<reason>` | `unavailable:<reason>` (в т. ч. пустая
причина, не строка) → `not_approved:invalid_provider_answer`; исключение провайдера →
`unavailable:provider_error`. Эмиттер вердиктов (`verdicts/emitter.py:172`) в `review` пишет
`approved` только при `established(a)`; `claimed and not established` → `unknown`; прочие —
frontmatter. JSON-вывод `gate-check` получает ключи `act_mode` (`legacy` | `review`),
`delegated` (коды, делегированные провайдеру; пустой при `legacy`) и `snapshot`
(`{kind, root, digest}` прогона). Любая error-находка — exit 1 (как все `GC-*`). Три кода —
новые записи `profiles/gate-catalog.yaml` (`obligation: approval`, `stages: [authoring,
release]`).

#### 7.2.3. Провайдер отвечает за тот же снимок (R-M0-3)

```python
@dataclass(frozen=True)
class Snapshot:
    kind: Literal["live", "candidate", "injected"]
    root: Path              # live — корень репо; candidate — каталог кандидата; injected — бандл
    commit: str | None      # live — SHA HEAD; иначе None
    digest: str             # snapshot_digest(spec_dir)

NodeStatus = Callable[[str, Snapshot], str]
def snapshot_digest(spec_dir: Path) -> str: ...
def snapshot_for(run: Literal["live", "candidate", "injected"], spec_dir: Path,
                 repo_root: Path | None) -> Snapshot: ...   # единственное место построения
```

`snapshot_digest` — `sha256:` от строк `"<git blob id> <путь>\n"` (отсортированных) всех
файлов `<spec_dir>` и `<spec_dir>/../approvals/` **на диске** — тех байтов, которые читает
`collect_bundle`; нечитаемый файл → ошибка (exit 2), не пропуск. CLI строит снимок только
через `snapshot_for` из тех же входов, что и прогон; для candidate — candidate-каталог, а не
чекаут базы. **Ответ провайдера по базе для candidate — ошибка вызывающего** (контракт;
спека devtools Task 14 сегодня считает `direct_blobs` по базе — заметка §12).

Отключение stale-каскада допустимо **только** потому, что провайдер отвечает за байты этого
снимка и пины конверта (спека devtools §9.1, строки 739–758): в `review` `check_stale_cascade`
пропускает артефакт при `established(a)`; при `claimed and not established` каскад по
frontmatter-пинам выполняется (лишняя находка допустима, пропуск — нет). CLI объявляет
делегирование на stderr и в JSON (`delegated`).

**CLI.** `gate-check --act-mode review --node-status <file.json>` — провайдер для
вызывающих подпроцессом (devtools S4/S8). Формат: `{"snapshot_digest": "sha256:…", "nodes":
{node_id: status}}`; повторяющиеся ключи JSON, лишние ключи верхнего уровня, нестроковые
значения → exit 2. `snapshot_digest` не равен `snapshot_for(...)`.digest этого прогона → exit
2 (ответ относится к другим байтам). Узла нет в `nodes` → `absent_from_provider`.
`--node-status` без `--act-mode review` → exit 2. `gate-check --print-snapshot-digest [--candidate] <dir>`
печатает дайджест того снимка, который построил бы прогон, и выходит 0; **`--act-mode` для
неё не требуется** — обязательность режима проверяется в теле команды после разбора
аргументов (не `Option(...)` typer), и печать дайджеста — явное исключение. Связь ответа с
байтами в callable-режиме — обязанность провайдера; отрицательный двойник «тот же ответ,
изменённые candidate-байты» — devtools Task 14 (§12).

#### 7.2.4. Таблица сочетаний

Фикстура: бандл из двух узлов A → B, оба с frontmatter `status: approved`, пины верны, иных
дефектов нет; ответ провайдера одинаков для обоих узлов. «`review/v1` в дереве» — в
`approvals/` есть конверт или манифест `act: review/v1`. Каждая строка — тест плана (Task 9)
на уровне CLI с проверкой кодов находок и exit.

| # | `act_mode` | `review/v1` в дереве | провайдер | прогон | находки (error) | exit | объявления |
|---|---|---|---|---|---|---|---|
| 1 | `legacy` | да | нет | live | `GC-ACT-MODE-CONFLICT` | 1 | — |
| 2 | `legacy` | да | нет | candidate | `GC-ACT-MODE-CONFLICT` | 1 | stderr: NOT_EVALUATED |
| 3 | `legacy` | да | approved | live | — (ошибка вызова: `--node-status` без `--act-mode review`) | 2 | — |
| 4 | `legacy` | да | approved | candidate | — (ошибка вызова: `--node-status` без `--act-mode review`) | 2 | — |
| 5 | `legacy` | да | not_approved | live | — (ошибка вызова: `--node-status` без `--act-mode review`) | 2 | — |
| 6 | `legacy` | да | not_approved | candidate | — (ошибка вызова: `--node-status` без `--act-mode review`) | 2 | — |
| 7 | `legacy` | да | unavailable | live | — (ошибка вызова: `--node-status` без `--act-mode review`) | 2 | — |
| 8 | `legacy` | да | unavailable | candidate | — (ошибка вызова: `--node-status` без `--act-mode review`) | 2 | — |
| 9 | `legacy` | нет | нет | live | — | 0 | — |
| 10 | `legacy` | нет | нет | candidate | — | 0 | stderr: NOT_EVALUATED |
| 11 | `legacy` | нет | approved | live | — (ошибка вызова: `--node-status` без `--act-mode review`) | 2 | — |
| 12 | `legacy` | нет | approved | candidate | — (ошибка вызова: `--node-status` без `--act-mode review`) | 2 | — |
| 13 | `legacy` | нет | not_approved | live | — (ошибка вызова: `--node-status` без `--act-mode review`) | 2 | — |
| 14 | `legacy` | нет | not_approved | candidate | — (ошибка вызова: `--node-status` без `--act-mode review`) | 2 | — |
| 15 | `legacy` | нет | unavailable | live | — (ошибка вызова: `--node-status` без `--act-mode review`) | 2 | — |
| 16 | `legacy` | нет | unavailable | candidate | — (ошибка вызова: `--node-status` без `--act-mode review`) | 2 | — |
| 17 | `review` | да | нет | live | `GC-ACT-PROVIDER-MISSING`; `GC-ACT-NOT-APPROVED` (A, B); `GC-UPSTREAM` (B) | 1 | — |
| 18 | `review` | да | нет | candidate | `GC-ACT-PROVIDER-MISSING`; `GC-ACT-NOT-APPROVED` (A, B); `GC-UPSTREAM` (B) | 1 | stderr: NOT_EVALUATED |
| 19 | `review` | да | approved | live | — | 0 | stderr: GC-STALE* delegated (A, B) |
| 20 | `review` | да | approved | candidate | — | 0 | stderr: GC-STALE* delegated (A, B); stderr: NOT_EVALUATED |
| 21 | `review` | да | not_approved | live | `GC-ACT-NOT-APPROVED` (A, B; причина `not_approved:…`); `GC-UPSTREAM` (B) | 1 | — |
| 22 | `review` | да | not_approved | candidate | `GC-ACT-NOT-APPROVED` (A, B; причина `not_approved:…`); `GC-UPSTREAM` (B) | 1 | stderr: NOT_EVALUATED |
| 23 | `review` | да | unavailable | live | `GC-ACT-NOT-APPROVED` (A, B; причина `unavailable:…`); `GC-UPSTREAM` (B) | 1 | — |
| 24 | `review` | да | unavailable | candidate | `GC-ACT-NOT-APPROVED` (A, B; причина `unavailable:…`); `GC-UPSTREAM` (B) | 1 | stderr: NOT_EVALUATED |
| 25 | `review` | нет | нет | live | `GC-ACT-PROVIDER-MISSING`; `GC-ACT-NOT-APPROVED` (A, B); `GC-UPSTREAM` (B) | 1 | — |
| 26 | `review` | нет | нет | candidate | `GC-ACT-PROVIDER-MISSING`; `GC-ACT-NOT-APPROVED` (A, B); `GC-UPSTREAM` (B) | 1 | stderr: NOT_EVALUATED |
| 27 | `review` | нет | approved | live | — | 0 | stderr: GC-STALE* delegated (A, B) |
| 28 | `review` | нет | approved | candidate | — | 0 | stderr: GC-STALE* delegated (A, B); stderr: NOT_EVALUATED |
| 29 | `review` | нет | not_approved | live | `GC-ACT-NOT-APPROVED` (A, B; причина `not_approved:…`); `GC-UPSTREAM` (B) | 1 | — |
| 30 | `review` | нет | not_approved | candidate | `GC-ACT-NOT-APPROVED` (A, B; причина `not_approved:…`); `GC-UPSTREAM` (B) | 1 | stderr: NOT_EVALUATED |
| 31 | `review` | нет | unavailable | live | `GC-ACT-NOT-APPROVED` (A, B; причина `unavailable:…`); `GC-UPSTREAM` (B) | 1 | — |
| 32 | `review` | нет | unavailable | candidate | `GC-ACT-NOT-APPROVED` (A, B; причина `unavailable:…`); `GC-UPSTREAM` (B) | 1 | stderr: NOT_EVALUATED |
| 33 | `absent` | да | нет | live | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 34 | `absent` | да | нет | candidate | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 35 | `absent` | да | approved | live | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 36 | `absent` | да | approved | candidate | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 37 | `absent` | да | not_approved | live | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 38 | `absent` | да | not_approved | candidate | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 39 | `absent` | да | unavailable | live | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 40 | `absent` | да | unavailable | candidate | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 41 | `absent` | нет | нет | live | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 42 | `absent` | нет | нет | candidate | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 43 | `absent` | нет | approved | live | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 44 | `absent` | нет | approved | candidate | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 45 | `absent` | нет | not_approved | live | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 46 | `absent` | нет | not_approved | candidate | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 47 | `absent` | нет | unavailable | live | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |
| 48 | `absent` | нет | unavailable | candidate | — (ошибка вызова: нет `--act-mode`, R-M0-5) | 2 | — |

Отрицательные двойники вне матрицы (тоже CLI-тесты Task 9):

| # | Сценарий | Итог |
|---|---|---|
| Д1 | Блокер r1: конверт и манифест удалены, frontmatter `approved` сохранён, `--act-mode review`, провайдер → `not_approved:no_envelope` | `GC-ACT-NOT-APPROVED` (узел), `GC-UPSTREAM` (зависимый), exit 1 |
| Д2 | Тот же бандл, `--act-mode` не передан (как сегодняшние S4/S8) | **exit 2**, «--act-mode is required» (R-M0-5) |
| Д3 | Major r2: продукт в `review`, `approvals/` никогда не было, узел с frontmatter `approved` без манифеста, провайдер → `not_approved:no_manifest` | `GC-ACT-NOT-APPROVED`, exit 1 |
| Д4 | Candidate: `--act-mode legacy`, байты upstream изменены после пина | `GC-STALE`, exit 1 — `test_candidate.py::test_stale_cascade_sees_the_candidate_content` с добавленным `--act-mode legacy` |
| Д5 | Candidate, `--act-mode review`, файл провайдера с дайджестом **до** правки байтов | exit 2 (дайджест не совпал) |
| Д6 | Через **реальный CLI**: git-репо, база (HEAD) с байтами X, отдельный candidate-каталог с байтами Y ≠ X, `--candidate <cand> --act-mode review --node-status` | JSON `snapshot.kind = candidate`, `snapshot.root` = `<cand>` (не корень репо), `snapshot.digest = snapshot_digest(<cand>) ≠ snapshot_digest(<база>)`; файл с дайджестом базы → exit 2, с дайджестом кандидата → принят |
| Д7 | `review`, A `established`, B `claimed` и не `established`, пин B на A сломан | `GC-ACT-NOT-APPROVED` (B) **и** `GC-STALE` (B) |
| Д8 | `review`, A провайдер `approved`, frontmatter A `draft`, B `approved` обоими | exit 0: upstream проверяется по `established` |
| Д9 | API: `collect_bundle(graph, d)` / `act_statuses(arts)` без `act_mode` | `TypeError`; `act_statuses(..., act_mode="legacy", node_status=f, snapshot=s)` → `ValueError`; `review` с `node_status` без `snapshot` → `ValueError` |

## 8. Что проверяется векторами, а что — только живьём

Правило: где факт GitHub не проверен, дизайн **не опирается** на одну гипотезу — исход
fail-closed при обеих, а факт входит в чек-лист живого смоука (план, Task 13).

| Факт GitHub | Векторы | Если гипотеза неверна | Живое подтверждение |
|---|---|---|---|
| Форма ответов, отказы, пагинация, два прохода, срез T | да | — | — |
| `previousReviewState` присутствует у отзывов | **не снято**: в devtools нет ни одного `ReviewDismissedEvent` (полный обход 30.10; приватные стенды в публичный steward не снимаются) | `null` → `dismissal_without_previous_state` (отказ) | **не проверено**; Task 13 (отзыв на стенде), спека devtools §8 — вектор M3 |
| Полнота событий `REVIEW_DISMISSED_EVENT` в таймлайне | нет | отозванное ревью GitHub отдаёт в `DISMISSED`, поэтому пропущенное событие обнаруживается: `dismissed_without_event` (отказ) | смоук (число событий = число ревью в `DISMISSED`) |
| REST `head.sha` = GraphQL `headRefOid` у смёрженного PR | **снято** (E1/E2: devtools #657, #636) | расхождение → `delivery_mismatch` | **проверено живьём 30.10** |
| `merged_by` REST `slug[bot]`/`Bot`, GraphQL `slug`/`Bot`; формы для App и User | **User снят** (E1/E2); **App-мержер не снят** — в разрешённых репо мержей App нет | несводимые → `bot_login_mismatch` | User — проверено; App — **не проверено** (spike 12.10, спека devtools §1; Task 13) |
| Удалённый пользователь: `null` или логин-заглушка `ghost` | обе формы | любая → `actor_unresolved` (§3.4.1) | смоук, если найдётся |
| `MergedEvent.commit.oid` = `mergeCommit.oid` (нормативная проверка) | **снято** (E4, #657, #636) | иное → `delivery_mismatch` | **проверено живьём**. Наблюдение, **не проверка**: `createdAt` = `mergedAt` на обоих снимках; время не сверяется (§3.3) |
| `headRefOid`/`baseRefName` проходов (E3) = голове/базе доставки у смёрженного PR (M0-F) | **снято** (E3 = E2 на #657, #636) | расхождение → `delivery_mismatch` | **проверено живьём** (равенство на обоих снимках) |
| `compare` с веткой, закодированной `quote(safe="")` (`/` → `%2F`), разрешает ту же ветку | нет: живые базы — `master`/`main`, кодирование их не меняет | GitHub не раскодирует — 404 → `http` (отказ, не допуск) | **не проверено**; Task 13 (база с `/` в имени) |
| `compare` даёт `behind`/`identical` для достижимого merge-коммита | **снято** оба (E5: #657 `identical`, #636 `behind`) | иной статус → `malformed`; `ahead`/`diverged` → `in_base=false` (отказ) | **проверено живьём** |
| `commits/{sha}/pulls` для коммита на ветке по умолчанию отдаёт смёрженный PR | **снято** (H4: merge-коммит, squash, боковой коммит; прямой коммит → `[]`) | пусто → `commit_without_pr` | **проверено живьём** |
| `commits/{sha}/pulls` для промежуточного коммита rebase-merge | ближайший аналог снят: боковой коммит merge-коммит-мержа ассоциирован с PR, `merge_commit_sha` другой (H4) | любая из двух гипотез → `HistoryViolation` | rebase-merge как таковой — **не проверено**; стенд devtools (Task 18, №28) |
| `commits/{sha}/pulls` вне ветки по умолчанию | нет (запрещено `branch_not_default`) | — | снять ограничение О11 — только после проверки |
| `merge_commit_sha` однокоммитного rebase-merge = коммит ветки | форма | иначе → `rebase_intermediate`/`commit_without_pr` (строже) | **не проверено** (спека devtools §6.1 п. 2) |
| `git/trees/{sha}?recursive=1` полон для малого репо (`truncated=false`) | **снято** (H5) | `truncated` → `tree_truncated` | **проверено живьём**; несуществующее дерево → 422 (R0) |
| Анонимный REST: доступность endpoint-ов и остаток лимита | нет | 403/429 → `http` | смоук (анонимный транспорт) |
| Видимость PENDING-ревью чужому токену | форма | видно чужое PENDING — исключено как PENDING | смоук |

## 9. Поставка и пин

- `pyproject.toml` `version` 0.1.0 → **0.2.0**; аннотированный тег **`v0.2.0`** на
  merge-коммите PR в `master`. **Решение контроллера, подтверждается подписью владельца
  (бывш. Q6):** PR мержит владелец; тег ставит и пушит агент после мержа, отдельного
  подтверждения на тег не требуется — владелец подтверждает его подписью плана.
- devtools пинует `rev = <полный SHA>` (`[tool.uv.sources]`, как сегодня `b77ee0d…`); тег —
  метка, а не пин.
- Ответ в steward#211: SHA, тег, ссылки на контракт и векторы, отступления §10, заметка §12.
- discovery (M2) берёт ту же пиненую зависимость (Q2 спеки devtools).

## 10. Отступления от ожидаемого интерфейса

Потребитель разрешил отступления с причиной («devtools сверяет и переписывает свои задачи
11–17»).

| # | Ожидалось | В M0 | Причина |
|---|---|---|---|
| О1 | `PathBlob.blob_at_merge: str` | `str \| None` | путь, удалённый в merge-коммите, — установленный факт, не сбой; devtools Part-A notes п. 5 уже требует `(None, sha)` → `Rejected(I_BYTES)` |
| О2 | `read_policy_history(..., anchor, until)` | + `files: Sequence[str] = POLICY_FILES` (то же у `read_activation`) | библиотеке нужен перечень файлов политики; умолчание = `act_policy.POLICY_FILES`, вызов devtools не меняется |
| О3 | `Activation.files_sha256` — формат не задан | hex без префикса; файл, **установленно** отсутствующий в полном дереве, — без ключа | совпадает с `act_policy.parse_policy_set`; «нет файла» не выдумывается значением, а ошибка чтения не выдаётся за отсутствие |
| О4 | параметр `node_status: Callable[[str], str] \| None` у гейтов (`checks.py:377`) | порядок «сбор → статусы → проверки»: `collect_bundle(..., *, act_mode)`, `act_statuses(arts, *, act_mode, node_status, snapshot) -> ActStatuses`, `run_checks(..., *, statuses)`; `NodeStatus = Callable[[str, Snapshot], str]`; CLI `--act-mode` (обязателен), `--node-status` (файл с `snapshot_digest`), `--print-snapshot-digest` | режим — обязательный вход от вызывающего (R-M0-1, R-M0-5); провайдер обязан знать свой снимок (R-M0-3); «не одобрен» — находки `GC-ACT-*` (R-M0-2) — §7.2 |
| О5 | `Unavailable.reason: str` | `"<code>[: detail]"` + свойство `.code`, закрытый словарь | потребитель различает причины без разбора прозы; поле `reason` остаётся |
| О6 | векторы `*.json` одной формы | акт — в корне той же формы + `kind`, `expect.waiting_delivery`, `expect.delivery`, `expect.effective_commit`; прочие — в подкаталогах | у не-смёрженного вектора нет ревью: тест devtools должен ветвиться на `waiting_delivery` до `effective(None)`; история не помещается в форму акта |
| О7 | `replay_transport`, `verify_vendored_vectors` «если steward их поставит» | `replay_transport` — да; `verify_vendored_vectors` — нет | проверка пиненой копии — гарантия потребителя (copy-integrity), не продюсера |
| О8 | `effective_reviews` → `Unavailable` | неканоничный `t` → `ValueError` | `t` строит потребитель из `Clock`; это ошибка вызова, не факт форджа |
| О9 | форма `merged_by_login` не задана | GraphQL-форма: без `[bot]`, без `github:` | одна форма на оба транспорта; `act_policy._identity` devtools совместим |
| О10 | транспорт — «`v3.GhTransport(...)` or equivalent» | `GhTransport(gh_config_dir=None, *, timeout=60)`, протокол `Transport.send(Request) -> Response` | — |
| О11 | `read_policy_history` по закреплённой ветке | ветка обязана быть веткой по умолчанию источника, иначе `Unavailable: branch_not_default` | непроверенная выдача ассоциаций вне ветки по умолчанию давала бы ложные нарушения (§3.7 п. 1); сужает входы Task 12/12a — смена ветки по умолчанию источника = `Unavailable` до решения человека |
| О12 | — | **снято в r2** (механизм «история git → режим» удалён, R-M0-1) | — |
| О13 | отказ по отсутствующему `previous_state`/`submitted_at`, по `DISMISSED` без события — этап не задан | отказ на **чтении** (`read_act_facts`, `read_open_review`); `effective_reviews` повторяет проверки | тест devtools Task 11 ждёт `unavailable` от `read`; условный отказ допускал бы акт при неустановленном состоянии другого ревью (§3.4) |
| О14 | вызовы гейтов steward без нового параметра работают как прежде | `act_mode` обязателен: API — `TypeError`, CLI — exit 2 | R-M0-5: отсутствие результата admission не становится legacy; **ломающее** изменение для всех вызывающих (§12) |
| О15 | неустановленный актор — не оговорён | `actor_unresolved` на чтении для всего PR (любой автор ревью, актор отзыва, мержер) | R-M0-6, §3.4.1 |

## 11. Открытые вопросы владельцу

Открытых вопросов нет. Закрыты ссылками на подписанную спеку devtools: бывш. Q1 (§7.2), Q2
(§3.7 п. 3), Q4 (§3.7, credentials), Q7 (§7.2.3), Q8 (§3.4). Решения контроллера,
подтверждаемые подписью владельца: Q3 (§3.7 п. 1), Q5 и Q9 (§6), Q6 (§9).

## 12. Заметка для потребителей (войдёт в ответ steward#211)

1. **Credentials и лимит (devtools).** Анонимное чтение источников доверия обязательно (ваша
   спека §6.2); библиотека не кэширует. Неизменяемые объекты (коммит по SHA, связь коммит →
   PR по `merge_commit_sha`, блоб по oid, дерево по SHA) можно кэшировать у себя по ключу
   SHA; свежими обязаны быть голова ветки `df-trust`/источника и журнал эпох. Предел
   анонимного REST — 60 запросов/ч на IP; обход истории — ≈3 запроса на звено + блобы
   активаций.
2. **`--act-mode` обязателен (R-M0-5, О14).** Без него `gate-check` выходит кодом 2, API —
   `TypeError`. Миграция — **при подъёме пина**, не раньше (пины — SHA, подъём осознанный):
   - **devtools S4/S8** (`ops.py:2165–2174` candidate, `:2124–2132` с публикацией verdicts):
     передавать `--act-mode` по результату вашего admission (`legacy` для доставок под
     `OLD_READER`, `review` + `--node-status` для `review/v1`); задача — ваш Task 11 (подъём
     пина) вместе с Task 14 (провайдер). Без этого подъём пина ломает S4/S8 кодом 2 — это и
     есть барьер: прежнего ложного зелёного (приём `rc == 0`, `runner.py:3275–3285`) больше
     нет;
   - **dispatcher**: steward вызывается только в интеграционном тесте
     (`tests/test_governance_live_smoke.py:137–143`) — при следующем `producer_commit`
     (`scripts/install_pinned_steward.sh:18–29`) добавить `--act-mode legacy`; runtime
     dispatcher читает verdicts и не затронут;
   - собственный CI steward (`ci.yml:43, 46`) мигрирует в PR M0 (`--act-mode legacy`).
3. **Провайдер отвечает за снимок, который читает steward** (R-M0-3): для candidate — за
   candidate-байты и пины конверта, а не за базу (ваш Task 14 сейчас считает `direct_blobs`
   по `base_ref`, строки 4259–4273 плана). steward передаёт `Snapshot` (корень, дайджест);
   файл `--node-status` обязан нести `snapshot_digest` прогона (`gate-check
   --print-snapshot-digest`). **Отрицательный двойник «тот же ответ провайдера, изменённые
   candidate-байты upstream → не `approved`» — ваш, в Task 14**: steward stale-каскад для
   установленных провайдером узлов не выполняет. Провайдер — единственный авторитет
   актуальности и для legacy-узлов в режиме `review`.
4. **Потребители `approval-facts/v2` вне steward** (поиск read-only 30.10 по devtools и
   dispatcher): кода, читающего v2-файл или импортирующего `steward.approvalfacts`, нет.
   - dispatcher: `TODO.md:92–123` планирует «стадию 2 — завендорить пиненую копию
     `contracts/approval-facts/v2`»; после M0 вендорить **v3** (v2 заморожен, гейт steward его
     больше не читает — `legacy_v2`);
   - devtools: собственный модуль `governance/approval_facts.py` (не steward v2);
     комментарий в вендоренной `contracts/steward-actor-policy/v1/approval-policy.yaml:9`
     ссылается на старый `approvalfacts.py` — справочно, на поведение не влияет.
5. **Векторы:** ветка `waiting_delivery` в тесте Task 11 (О6); `unavailable` по
   `previous_state`/`submitted_at`/`DISMISSED` без события и `actor_unresolved` (О15)
   приходит от `read` (О13); коды
   `rebase_intermediate` и `commit_without_pr` в решении не различать (§3.7 п. 4).
6. **Ветка источника политики** обязана быть веткой по умолчанию (О11).

## 13. Проход на разрешающие умолчания (r3)

Правило: отсутствующий или недоказанный вход никогда не становится разрешением. Проверено по
всей спеке; найденное исправлено в этой ревизии:

| Область | Где было разрешающее умолчание | Теперь |
|---|---|---|
| Режим | `act_mode = None` ≡ legacy с предупреждением | обязателен, exit 2 / `TypeError` (§7.2.1) |
| Провайдер | `node_status` без `snapshot` не оговорён | `ValueError` / exit 2 (§7.2.2) |
| Провайдер | файл с повторяющимися ключами — последний выигрывает | exit 2 (§7.2.3) |
| Провайдер | пустая причина `not_approved:` | `invalid_provider_answer` (§7.2.2) |
| Снимок | нечитаемый файл бандла молча вне дайджеста | ошибка, exit 2 (§7.2.3) |
| Детектор | нечитаемый каталог `approvals/` | `GC-ACT-MODE-CONFLICT` (§7.2.1) |
| Акторы | автор постороннего ревью / актор отзыва `null` или `ghost` проходили, если хватало Alice | `actor_unresolved` для всего PR (§3.4.1) |
| Акторы | тип вне {User, Bot} | `actor_unresolved` (§3.4.1) |
| Мержер | `mergedBy = null` у смёрженного PR | `actor_unresolved`; в файле гейта — `actor_unavailable` → блокирующая находка (§6) |
| Доставка | `merged_at = null` у смёрженного PR, `state` PR вне словаря | `malformed` / `unknown_state` (§3.3) |
| Файлы | пустой `files` ⇒ «активаций нет» | `ValueError` (§3.7) |
| Файлы | 404 как «файла нет» (r1) | только полное дерево (§3.7 п. 6) |
| Пагинация | ошибка разбора `Link` неотличима от «конца» | конец — только отсутствие `rel="next"` в разобранном `Link` (контракт GitHub); заголовок, не прошедший грамматику P1, → `malformed`; лишнего запроса нет (§3.2, r4) |
| Транспорт | ответ `gh` без строки статуса | `transport` (§3.2) |
| Метки | — (уже fail-closed: `bad_timestamp`) | без изменений |
| История | PR с `merge_commit_sha == sha`, но `merged_at = null` | не считается мержем → нарушение формы (fail-closed) — без изменений, теперь сказано явно |

Оставлено сознательно (не разрешение): пустой `paths` у `read_act_facts` возвращает
`paths=()` — факт «путей не запрошено»; отказ «заявка без путей» — у потребителя (Review
Focus 4 devtools, `test_manifest_without_nodes_refused`).
