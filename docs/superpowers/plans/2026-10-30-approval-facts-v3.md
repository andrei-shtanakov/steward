# `approval-facts/v3` (M0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** выпустить для devtools/discovery общую библиотеку чтения фактов акта approve
(`steward.approvalfacts.v3`), контракт `approval-facts/v3` с векторами сбора §5.6, запрет
`human ∩ agent`, провайдер статуса узла и миграцию release-гейта на запись `delivery` —
релизом с тегом и ответом в steward#211.

**Architecture:** пакет `src/steward/approvalfacts/v3/` — чистая модель и нормализация
(`model.py`, `effective.py`) отделены от чтения GitHub (`transport.py`, `_github.py`,
`passes.py`, `delivery.py`, `act.py`, `history.py`); сеть — только через внедрённый
`Transport`, векторы гоняются через `ReplayTransport`. Режим акта (`act_mode`) гейтам передаёт
вызывающий; отказ установления одобрения — находки `GC-ACT-*`, статус узла не переписывается,
провайдер отвечает за снимок, который читает steward. Release-гейт читает v3-файл, чьи записи строит та же
функция чтения доставки, что и библиотека.

**Tech Stack:** Python ≥3.12, uv, pyyaml, typer, jsonschema, pytest, ruff (line 100),
pyrefly; `gh` CLI — только в `GhTransport`.

**Spec:** `docs/superpowers/specs/2026-10-30-approval-facts-v3-design.md` (сторона steward) и
сквозная спека devtools `devtools/docs/superpowers/specs/2026-10-12-approval-act-review-design.md`
rev 17 (§5, §5.5, §5.6, §6.1, §8.2, §9.3) — исполнитель читает обе; нормативные значения
ниже скопированы из них.

## Global Constraints

- Только uv: `uv sync`, `uv run pytest`, `uv run ruff format .`, `uv run ruff check . --fix`,
  `uv run pyrefly check` после каждой задачи. Длина строки — **100**.
- **Каждый коммит оставляет репо зелёным** — весь сьют, ruff, pyrefly и оба CI-вызова
  `gate-check` (R-M0-8). Красная стадия TDD — только локально, никогда не коммит. Миграция
  интерфейса и всех его вызывающих (тесты, CI, доки, скрипты) — **один** коммит. Шаг
  «Commit» каждой задачи выполняется только после шага полного прогона.
- **Формы ответов GitHub — только из живых снимков** `tests/fixtures/approvalfacts_v3/live/`
  и таблицы §3.8 спеки (R-M0-7). Фикстуры и векторы выводятся из снимков именованными
  преобразованиями (спека §5); ни одного рукописного тела ответа, кроме узла
  `ReviewDismissedEvent` (помечен `unverified_shape`). Ключ `ReplayTransport` включает текст
  запроса. На каждую строку §3.8 — положительный двойник.
- Тесты **никогда не ходят в сеть**: всё чтение — через `Transport`; `GhTransport` в тестах
  проверяется подменой `subprocess.run`.
- Импорт `steward.approvalfacts.v3` не делает ввода-вывода.
- `Unavailable` — никогда не «пустой список»: неполная пагинация, HTTP 401/403/404/5xx,
  таймаут, **GraphQL `errors` при любом HTTP-статусе (в т.ч. `200` с частичными `data`)**,
  отсутствие обязательных полей, неизвестный `state`, нет `commit_id`, несогласованные формы
  логина бота → `Unavailable` (спека devtools §5.5).
- Предел страниц — **50**; предел цепочки first-parent — **1000** звеньев.
- Канон меток — `YYYY-MM-DDTHH:MM:SSZ`; `+00:00` нормализуется в `Z`; доли секунды и метки без
  зоны — `bad_timestamp`.
- `effective_reviews`: событие с меткой `== t` — «до T» тогда и только тогда, когда его ключ
  в `before`; ключи `("review", review_id)`, `("dismissal", review_id)`.
- Эффективные состояния — только {`APPROVED`, `CHANGES_REQUESTED`, `DISMISSED`}; `COMMENTED`
  и `PENDING` эффективного состояния не меняют.
- `POLICY_FILES = ("policy/approvers.env", "policy/modes.yaml", "policy/actors.yaml")`;
  `files_sha256` — hex без префикса; отсутствующий файл — без ключа.
- История: многокоммитный rebase-merge → `HistoryViolation: rebase_intermediate`;
  однокоммитный — `Activation` (предел, не нарушение); дата коммита не используется.
- HTTP 404 — **никогда** не «установленное отсутствие»: отсутствие файла устанавливается
  только полным (`truncated = false`) успешно прочитанным деревом коммита.
- Отзыв с `previous_state = null`, ревью не в `PENDING` без `submitted_at`, ревью в
  `DISMISSED` без события отзыва — отказ **чтения** (`dismissal_without_previous_state`,
  `missing_submitted_at`, `dismissed_without_event`), безусловный.
- steward режим узла **не определяет**: режим — **обязательный** вход `act_mode` от
  вызывающего (R-M0-1, R-M0-5): API без него — `TypeError`, CLI — exit 2. Ни одного вызова
  гейтов без `act_mode` в репо после Task 9 (CI, доки, тесты мигрированы явно на `legacy`).
- Любой неустановленный актор PR (автор любого ревью, актор любого отзыва, мержер: `null`,
  пустой/отсутствующий `login`, `ghost`, тип вне {`User`, `Bot`}) → `actor_unresolved` для
  всего PR, на чтении (R-M0-6, спека steward §3.4.1).
- Отсутствующий или недоказанный вход не становится разрешением (спека steward §13).
  «Не одобрен» — error-находка `GC-ACT-*`, `meta.status` не переписывается, ни одна
  проверка не выключается (R-M0-2). Провайдер отвечает за снимок, который читает steward
  (`Snapshot`, `snapshot_digest`; R-M0-3). Итоги — таблица сочетаний спеки steward §7.2.4.
- Где факт GitHub не проверен живьём, исход fail-closed при обеих гипотезах, и факт стоит в
  чек-листе Task 13 (спека steward §8).
- Каталог `contracts/approval-facts/v2/` не меняется ни байтом.
- `profiles/approval-policy.yaml` не меняется (authority-root).
- Ни один файл репо не ссылается на dev-only каталог координации воркспейса (правило
  CLAUDE.md, раздел «dev-only»).
- Коммиты — в стиле репо (`feat(approval-facts): …`, проза по-русски), в конце
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Отзыв после мержа, появившийся между проходами** — `read_act_facts` обязан вернуть
   устойчивый результат с `APPROVED` на T, а не `unstable` (Task 5,
   `test_post_merge_dismissal_between_passes_is_stable`).
2. **Метка `+00:00` в REST и `Z` в GraphQL у одного `merged_at`** — доставка совпадает, не
   `delivery_mismatch` (Task 5, `test_rest_offset_and_graphql_z_agree`).
3. **`review_id` разной длины при равном `submitted_at`** (`"99"` и `"100"`) — порядок
   численный, не строковый (Task 3, `test_tie_breaks_numerically`).
4. **Путь с двоеточием или пробелом** в `paths` — выражение `object(expression:)` уходит
   переменной и не ломает запрос (Task 5, `test_path_with_colon_goes_as_variable`).
5. **Провайдер, бросающий исключение** — `GC-ACT-NOT-APPROVED` с причиной
   `unavailable:provider_error`, exit 1, гейт не падает трейсбеком (Task 9,
   `test_provider_invalid_answer_and_exception`).

---

## File Structure

**Библиотека (`src/steward/approvalfacts/v3/`):**
- `__init__.py` — публичный API (реэкспорт), без ввода-вывода.
- `model.py` — dataclass-ы, словари причин, `canon_ts()`.
- `transport.py` — `Request`, `Response`, `Transport`, `GhTransport`, `ReplayTransport`,
  `replay_transport()`.
- `_github.py` — `graphql()`, `rest_get()`, обход страниц; внутреннее исключение `_Fail(code,
  detail)`, превращаемое в `Unavailable` на границе публичных функций.
- `effective.py` — `effective_reviews()`.
- `passes.py` — один проход по ревью/таймлайну, проекция на T, `read_open_review()`.
- `delivery.py` — `read_delivery()` (общая с release-продюсером), `read_paths()`.
- `act.py` — `read_act_facts()`.
- `history.py` — `read_policy_history()`, `read_activation()`, `POLICY_FILES`.
- `records.py` — сериализация в записи контракта (`review_record`, `delivery_record`,
  `policy_history_record`).
- `release.py` — продюсер файла release-гейта v3; `release_reader.py` — читатель.

**Контракт:** `contracts/approval-facts/v3/{SCHEMA.json, README.md, vectors/**}`.

**Изменяемые:** `src/steward/gatecheck/approval.py` (пересечение, гейт на v3),
`src/steward/gatecheck/checks.py` (`collect_bundle`, `apply_node_status`),
`src/steward/gatecheck/cli.py` (`--node-status`, v3), `src/steward/riskclassify/cli.py`
(`approval-facts` → v3), `scripts/collect_approval_facts.py`, `CLAUDE.md`, `TODO.md`,
`pyproject.toml` (версия).

**Тесты:** `tests/approvalfacts/v3/` (по файлу на модуль + `test_vectors.py`),
`tests/gatecheck/test_node_status.py`, `tests/gatecheck/test_approval_classify.py`,
`tests/gatecheck/test_approval_check.py`, `tests/approvalfacts/v3/test_release*.py`.

---

### Task 1: Модель v3 и каркас пакета

**Files:**
- Create: `src/steward/approvalfacts/v3/__init__.py`, `src/steward/approvalfacts/v3/model.py`
- Test: `tests/approvalfacts/v3/__init__.py`, `tests/approvalfacts/v3/test_model.py`

**Interfaces:**
- Produces: `EventKey`, `Review`, `Dismissal`, `ReviewFacts`, `PathBlob`, `Delivery`,
  `Unavailable`, `Activation`, `HistoryViolation` — поля ровно по спеке steward §3.1;
  `UNAVAILABLE_CODES: frozenset[str]`, `HISTORY_VIOLATION_CODES: frozenset[str]` (§3.6);
  `Unavailable.code` / `HistoryViolation.code` (часть `reason` до первого `":"`);
  `unavailable(code: str, detail: str = "") -> Unavailable`;
  `violation(code: str, detail: str) -> HistoryViolation`;
  `canon_ts(raw: object) -> str` (бросает `BadTimestamp(ValueError)`);
  `REVIEW_STATES = frozenset({"PENDING", "COMMENTED", "APPROVED", "CHANGES_REQUESTED",
  "DISMISSED"})`; `EFFECTIVE_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED",
  "DISMISSED"})`.

- [ ] **Step 1: Write the failing tests**

```python
def test_canon_ts_accepts_z_and_offset():
    assert canon_ts("2026-10-12T10:00:00Z") == "2026-10-12T10:00:00Z"
    assert canon_ts("2026-10-12T10:00:00+00:00") == "2026-10-12T10:00:00Z"

@pytest.mark.parametrize("raw", ["2026-10-12T10:00:00.5Z", "2026-10-12T10:00:00",
                                 "2026-10-12T12:00:00+02:00", 1700000000, None, ""])
def test_canon_ts_rejects(raw):
    with pytest.raises(BadTimestamp):
        canon_ts(raw)

def test_unavailable_code_and_detail():
    u = unavailable("http", "403 repos/o/r/pulls/7")
    assert u.reason == "http: 403 repos/o/r/pulls/7" and u.code == "http"
    assert unavailable("unstable").reason == "unstable"

def test_unknown_code_is_rejected():
    with pytest.raises(ValueError):
        unavailable("whatever")
    with pytest.raises(ValueError):
        violation("unavailable", "x")

def test_codes_are_exactly_the_spec_vocabulary():
    assert UNAVAILABLE_CODES == {
        "transport", "http", "graphql_errors", "malformed", "pagination", "bad_timestamp",
        "unknown_state", "missing_commit_id", "missing_submitted_at",
        "dismissal_without_previous_state", "dismissed_without_event", "duplicate_dismissal",
        "orphan_dismissal", "delivery_mismatch", "bot_login_mismatch", "unstable",
        "object_missing", "path_absent_at_head", "not_a_blob", "chain_limit",
        "branch_not_default", "no_pr_for_commit", "tree_truncated", "actor_unresolved"}
    assert HISTORY_VIOLATION_CODES == {
        "commit_without_pr", "rebase_intermediate", "pr_base_mismatch", "anchor_not_reached"}

def test_public_names_exported():
    from steward.approvalfacts import v3
    for name in ("Review", "Dismissal", "ReviewFacts", "PathBlob", "Delivery", "Unavailable",
                 "Activation", "HistoryViolation", "EventKey"):
        assert hasattr(v3, name)

def test_dataclasses_are_frozen():
    r = Review("1", "alice", "User", "APPROVED", "a" * 40, "2026-10-12T09:00:00Z")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.state = "DISMISSED"  # type: ignore[misc]
```

- [ ] **Step 2: Run** `uv run pytest tests/approvalfacts/v3/test_model.py -v` → FAIL
  (`ModuleNotFoundError: steward.approvalfacts.v3`).
- [ ] **Step 3: Implement** `model.py` и `__init__.py` (реэкспорт; по мере задач список
  `__all__` растёт). `Unavailable`/`HistoryViolation` проверяют код в `__post_init__`.
- [ ] **Step 4: Run** → PASS; `uv run pytest tests/approvalfacts/test_import_cycle.py -v` → PASS;
  ruff, pyrefly.
- [ ] **Step 5: Commit** `feat(approval-facts): модель v3 — типы фактов, словари причин, канон меток`

### Task 2: Транспорт, канонические запросы и чтение GitHub по таблице §3.8

**Files:**
- Create: `src/steward/approvalfacts/v3/queries.py`, `src/steward/approvalfacts/v3/transport.py`,
  `src/steward/approvalfacts/v3/_github.py`, `tests/approvalfacts/v3/_live.py` (загрузчик
  снимков: `live(name) -> dict` — одиночный обмен или `{"exchanges": [...]}`;
  `request_from(ex) -> Request`; `key(r: Request) -> tuple` и `key_of(ex) -> tuple` —
  `(method, path, op, variables, query)` для сравнения журнала `ReplayTransport.log` с
  порядком снимка; `LIVE_CAPTURES` — имена всех файлов снимков, кроме `MANIFEST.json`)
- Modify: `scripts/capture_approval_facts_v3_live.py` — константы запросов заменяются импортом
  из `queries.py` (тот же коммит; скрипт и библиотека не могут разойтись)
- Test: `tests/approvalfacts/v3/test_transport.py`, `tests/approvalfacts/v3/test_github.py`,
  `tests/approvalfacts/v3/test_queries.py`

**Interfaces:**
- Produces (`queries.py`): `Q_DELIVERY`, `Q_REVIEWS`, `Q_TIMELINE`, `Q_COMMIT_PULLS: str`,
  `q_blobs(n: int) -> str` — **побайтово** тексты §3.8 спеки.
- Produces (`transport.py`): `ReplayTransport.log: list[Request]` — журнал всех `send` в
  порядке вызова (для проверки общего порядка запросов, не только FIFO на ключ);
  `Request(method, path, op=None, variables=None, query=None)` —
  `query` **входит** в равенство и хеш (R-M0-7); `Response(status: int | None, body: object =
  None, link: str | None = None, error: str | None = None)`; `Transport` protocol;
  `GhTransport(gh_config_dir: Path | None = None, *, timeout: float = 60)`; `ReplayTransport`
  (`send`, `unused()`, `unexpected`); `replay_transport(spec)` — формат обмена = формат файла
  снимка (`{"request": {method, path, op?, variables?, query?}, "response": {status, link?,
  body?, error?}}`).
- Produces (`_github.py`): `_Fail(code, detail)`; `graphql(t, op, query, variables) -> dict`
  (строка G0); `rest_object(t, path) -> dict`, `rest_array_pages(t, path) -> list[dict]`
  (строки R0, P1 — форма задаётся вызывающим по строке §3.8, общего «тело обязано быть
  объектом» нет); `graphql_pages(t, op, query, variables, *, connection) -> list[dict]`;
  `parse_link(header: str) -> dict[str, str]` (бросает `_Fail("malformed")`); `MAX_PAGES = 50`.

- [ ] **Step 1: Write the failing tests** (локально; красная стадия не коммитится — R-M0-8)

```python
def _exchanges(name: str) -> list[dict]:
    """Снимок — либо одиночный обмен, либо последовательность `{"exchanges": [...]}`."""
    data = live(name)
    return data["exchanges"] if "exchanges" in data else [data]

def _blob_paths(req: dict) -> int:
    return sum(1 for k in req["variables"] if re.fullmatch(r"m\d+", k))

@pytest.mark.parametrize("name", LIVE_CAPTURES)          # все файлы снимков, кроме MANIFEST
def test_library_query_text_equals_live_capture(name):
    for ex in _exchanges(name):
        req = ex["request"]
        if req["method"] != "POST":
            continue
        expected = {"StewardV3Delivery": Q_DELIVERY, "StewardV3Reviews": Q_REVIEWS,
                    "StewardV3Timeline": Q_TIMELINE, "StewardV3CommitPulls": Q_COMMIT_PULLS,
                    "StewardV3Blobs": q_blobs(_blob_paths(req))}[req["op"]]
        assert req["query"] == expected, (name, req["op"])

def test_capture_script_imports_library_queries():
    src = (ROOT / "scripts/capture_approval_facts_v3_live.py").read_text()
    assert "from steward.approvalfacts.v3.queries import" in src and "Q_DELIVERY = (" not in src

def test_replay_key_includes_query_text():
    ex = _exchanges("pr636_act_sequence")[0]                # первый обмен: StewardV3Delivery
    assert ex["request"]["op"] == "StewardV3Delivery"
    t = replay_transport({"exchanges": [ex]})
    req = request_from(ex)
    other = dataclasses.replace(req, query=req.query.replace(" baseRefName", ""))
    assert t.send(other).status is None and t.unexpected == [other]
    assert t.send(req).status == 200 and t.unused() == []

def test_replay_fifo_per_key(): ...                 # два прохода одного запроса — разные ответы

def test_g0_graphql_errors_with_200_and_partial_data():
    ex = live("neg_e2_delivery_absent_pr")                  # одиночный обмен
    req = request_from(ex)
    with pytest.raises(_Fail) as exc:
        graphql(replay_transport({"exchanges": [ex]}), req.op, req.query, req.variables)
    assert exc.value.code == "graphql_errors"

def test_r0_non_2xx_is_http():
    # живой снимок: несуществующее дерево → 422
    ex = live("h5_tree_absent_422")
    with pytest.raises(_Fail) as exc:
        rest_object(replay_transport({"exchanges": [ex]}), ex["request"]["path"])
    assert exc.value.code == "http" and "422" in exc.value.detail

def test_h4_array_body_is_accepted_positive_twin():
    ex = live("h4_commit_pulls_merge_commit")
    got = rest_array_pages(replay_transport({"exchanges": [ex]}), ex["request"]["path"])
    assert got[0]["number"] == 657

def test_object_endpoint_rejects_array_and_vice_versa():
    # форма — по строке §3.8: объект на месте массива и наоборот → malformed

def test_p1_link_from_live_capture_parses():
    rels = parse_link(live("p1_rest_list_with_link_next")["response"]["link"])
    assert set(rels) == {"next", "last"}

def test_p1_no_next_is_end_without_extra_request():
    # одна страница H4 без Link → один запрос; unused() == [] и unexpected == []
def test_p1_unparseable_link_is_malformed(): ...      # 'garbage' → _Fail("malformed")
def test_has_next_without_cursor_is_pagination(): ...
def test_page_limit_is_pagination(): ...              # 51-й запрос не отправлен
def test_gh_transport_parses_include_output(monkeypatch):
    # stdout = "HTTP/2.0 200 OK\nLink: <…>; rel=\"next\"\n\n{…}" → status 200, link, body
def test_gh_response_without_status_line_is_transport(monkeypatch): ...
def test_gh_transport_timeout_is_transport_error(monkeypatch): ...
def test_gh_transport_builds_argv_and_env(monkeypatch): ...  # GH_CONFIG_DIR, --input -
```

- [ ] **Step 2: Run** `uv run pytest tests/approvalfacts/v3 -v` → FAIL (локально).
- [ ] **Step 3: Implement**; перенести тексты запросов из скрипта захвата в `queries.py`, скрипт
  импортирует их; `uv run python scripts/capture_approval_facts_v3_live.py --out <tmp>` не
  запускается в тестах (сеть), но его импорт проверяется `test_capture_script_imports_library_queries`.
- [ ] **Step 4: Run** `uv run pytest -q` (весь сьют), `uv run ruff format --check .`,
  `uv run ruff check .`, `uv run pyrefly check` → всё зелёное.
- [ ] **Step 5: Commit** (только зелёное состояние) `feat(approval-facts): транспорт v3, канонические запросы §3.8, разбор Link`

### Task 3: `effective_reviews` — восстановление на T

**Files:**
- Create: `src/steward/approvalfacts/v3/effective.py`
- Test: `tests/approvalfacts/v3/test_effective.py`

**Interfaces:**
- Consumes: Task 1 (`Review`, `Dismissal`, `ReviewFacts`, `unavailable`, `canon_ts`).
- Produces: `effective_reviews(facts: ReviewFacts, t: str, *, before: frozenset[EventKey])
  -> dict[str, Review] | Unavailable`.

Алгоритм — спека steward §3.5 (шаги 0–2). Хелпер для тестов: `_facts(reviews=…,
dismissals=…)` с `T = "2026-10-12T10:00:00Z"`.

- [ ] **Step 1: Write the failing tests** (ожидания — словарь `login → state`)

```python
T = "2026-10-12T10:00:00Z"

def test_approved_cr_then_second_dismissed_is_dismissed():
    # §5.3 контрпример 1: APPROVED(1, 09:00) → CR(2, 09:10) → отзыв 2 (09:20, prev CR)
    assert states(eff(...)) == {"alice": "DISMISSED"}

def test_approved_dismissed_after_t_is_approved():
    # §5.3 контрпример 2: review 1 state=DISMISSED, отзыв 10:05 prev APPROVED
    assert states(eff(...)) == {"alice": "APPROVED"}

def test_pending_without_submitted_at_ignored():
    assert states(eff(APPROVED 09:00 + PENDING None)) == {"alice": "APPROVED"}

def test_submitted_without_submitted_at_unavailable():
    assert eff(COMMENTED None).code == "missing_submitted_at"

@pytest.mark.parametrize("created", ["09:30:00", "10:05:00"])
def test_dismissal_without_previous_state_unavailable_anywhere(created):
    # безусловно (спека devtools §5.3): ни положение относительно T, ни значимость
    # для исхода не важны
    assert eff(DISMISSED + отзыв created prev None).code == "dismissal_without_previous_state"

def test_missing_previous_state_of_other_login_blocks_valid_act():
    # отрицательный двойник: Alice APPROVED действительна; у Bob отзыв до T без prev
    facts = alice APPROVED 09:00 + bob DISMISSED, отзыв 09:30 prev None
    assert eff(facts).code == "dismissal_without_previous_state"
    # положительный двойник — тот же набор с prev "CHANGES_REQUESTED"
    assert states(eff(same with prev "CHANGES_REQUESTED")) == {"alice": "APPROVED",
                                                               "bob": "DISMISSED"}

def test_dismissed_without_event_unavailable():
    assert eff(DISMISSED без отзыва).code == "dismissed_without_event"

@pytest.mark.parametrize("seq,expected", [
    (["APPROVED", "CHANGES_REQUESTED"], "CHANGES_REQUESTED"),
    (["CHANGES_REQUESTED", "APPROVED"], "APPROVED"),
    (["APPROVED", "APPROVED"], "APPROVED"),
    (["APPROVED", "COMMENTED"], "APPROVED"),
])
def test_several_reviews_of_one_login(seq, expected): ...

def test_latest_review_commit_wins():
    # APPROVED на OLD (09:00), APPROVED на NEW (09:30) → commit_id NEW
    assert eff(...)["alice"].commit_id == "b" * 40

def test_review_after_t_dropped():
    assert eff(APPROVED 10:00:01) == {}

def test_equal_stamp_dismissal_respects_before():
    facts = APPROVED 09:00 + отзыв 10:00:00 prev APPROVED (review.state DISMISSED)
    assert states(effective_reviews(facts, T, before=frozenset({("dismissal", "1")}))) \
        == {"alice": "DISMISSED"}
    assert states(effective_reviews(facts, T, before=frozenset())) == {"alice": "APPROVED"}

def test_equal_stamp_approve_respects_before():
    facts = APPROVED 10:00:00
    assert states(effective_reviews(facts, T, before=frozenset({("review", "1")}))) \
        == {"alice": "APPROVED"}
    assert effective_reviews(facts, T, before=frozenset()) == {}

def test_tie_breaks_numerically():
    # CR id "99" и APPROVED id "100" в одну секунду: эффективное — "100" (APPROVED)
    assert states(eff(...)) == {"alice": "APPROVED"}

def test_commented_only_login_absent(): ...
def test_bad_t_raises():
    with pytest.raises(ValueError):
        effective_reviews(_facts(), "2026-10-12T10:00:00.1Z", before=frozenset())
```

- [ ] **Step 2: Run** `uv run pytest tests/approvalfacts/v3/test_effective.py -v` → FAIL.
- [ ] **Step 3: Implement** по шагам 0–2 спеки steward §3.5; сравнение меток — строками в
  каноне (лексикографический порядок канона совпадает с временным).
- [ ] **Step 4: Run** → PASS; ruff, pyrefly.
- [ ] **Step 5: Commit** `feat(approval-facts): effective_reviews — восстановление состояния ревью на T (§5.3)`

### Task 4: Проходы по ревью и таймлайну, `read_open_review`

**Files:**
- Create: `src/steward/approvalfacts/v3/passes.py`
- Test: `tests/approvalfacts/v3/test_passes.py`, `tests/approvalfacts/v3/_derive.py`
  (преобразования живых снимков — закрытый список спеки §5: `set_field`, `drop_field`,
  `append_node`, `retime`, `repeat_exchange`, `page_split`; общий для тестов и генератора
  векторов Task 7)

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: `@dataclass(frozen=True) class Pass: head_sha: str; base_ref: str; pr_state: str;
  reviews: tuple[Review, ...]; dismissals: tuple[Dismissal, ...]; merged_commits:
  tuple[str, ...]`; `read_pass(t: Transport, owner: str, name: str, pr: int) -> Pass` (бросает
  `_Fail`); `projection_at(p: Pass, t: str) -> frozenset[tuple]` (спека steward §3.4);
  `read_open_review(transport, repo: str, pr: int) -> ReviewFacts | Unavailable`;
  `split_repo(repo: str) -> tuple[str, str]` (ровно один `/`, иначе `ValueError`).
- Запросы — `Q_REVIEWS`, `Q_TIMELINE` (Task 2, строки E3/E4 §3.8). `_derive.py`:
  `derive(base: list[str], transforms: list[Transform]) -> dict` (обмены для
  `replay_transport`); `append_node` сверяет ключи узла с полями текста запроса и отвергает
  лишние (`ValueError`) — фикстура не может вернуть незапрошенное поле; узел
  `ReviewDismissedEvent` строится `dismissal_node(review_id, created, prev, actor_login,
  actor_type)` по тексту E4 и помечает результат `unverified_shape`.

- [ ] **Step 1: Write the failing tests**

```python
def test_reviews_user_and_bot_live_positive_twin():
    # pr657_act_sequence / probe1_review_pass без преобразований: databaseId int → "5711633458",
    # Bot-автор (copilot-pull-request-reviewer, df-probe-reviewer-ashtanakov) — установлен
def test_timeline_query_requests_actor_typename():
    assert "actor{login __typename}" in Q_TIMELINE
def test_normal_dismissal_positive_twin():
    """Действительный APPROVED Alice + отзыв ревью Bob актором User (append_node по тексту
    E4) → effective {alice: APPROVED, bob: DISMISSED}; не actor_unresolved."""
def test_dismissal_actor_without_typename_is_unresolved(): ...   # drop_field actor.__typename
def test_open_review_clean():
    got = read_open_review(replay(two identical passes, alice APPROVED on HEAD), "o/r", 7)
    assert isinstance(got, ReviewFacts) and got.head_sha == HEAD
    assert [r.state for r in got.reviews] == ["APPROVED"]

def test_open_review_head_changed_between_passes_unstable():
    assert read_open_review(pass1 head A, pass2 head B).code == "unstable"

def test_dismissal_and_new_cr_between_pages_unstable():
    # pass1: p1 alice APPROVED; pass2: alice DISMISSED + bob CR, голова та же
    assert read_open_review(...).code == "unstable"

@pytest.mark.parametrize("node_patch,code", [
    ({"state": "SUPERSEDED"}, "unknown_state"),
    ({"commit": None}, "missing_commit_id"),
    ({"databaseId": "x1"}, "malformed"),
])
def test_review_node_refusals(node_patch, code): ...

@pytest.mark.parametrize("author", [None, {}, {"login": "", "__typename": "User"},
                                    {"login": "ghost", "__typename": "User"},
                                    {"login": "Ghost", "__typename": "User"},
                                    {"login": "x", "__typename": "Mannequin"},
                                    {"login": "x"}])
def test_unresolved_reviewer_fails_whole_pr(author):
    """R-M0-6, отрицательный двойник: действительный APPROVED Alice + ревью Bob с
    неустановленным автором → весь PR actor_unresolved."""
    assert read_open_review(...).code == "actor_unresolved"

def test_unresolved_dismissal_actor_fails_whole_pr(): ...   # actor null → "actor_unresolved"
def test_pending_review_of_unresolved_author_fails(): ...   # PENDING тоже актор PR

def test_duplicate_dismissal(): ...        # → "duplicate_dismissal"
def test_orphan_dismissal(): ...           # → "orphan_dismissal"
def test_dismissal_review_null(): ...      # → "malformed"
def test_unknown_previous_state(): ...     # previousReviewState "PENDING" → "unknown_state"
def test_dismissed_without_event_is_read_refusal():
    # R-M0-4: оба прохода — ревью DISMISSED (до T) без события отзыва
    assert read_open_review(...).code == "dismissed_without_event"
def test_previous_state_null_is_read_refusal():
    # отказ чтения, безусловный (спека steward §3.4, О13): отзыв ревью Bob до T,
    # при действительном APPROVED Alice
    assert read_open_review(...).code == "dismissal_without_previous_state"
def test_submitted_at_missing_is_read_refusal():
    # COMMENTED c submittedAt null → "missing_submitted_at"; PENDING c null — допустим
    assert read_open_review(...).code == "missing_submitted_at"
def test_pending_without_submitted_at_is_read(): ...
def test_projection_ignores_events_after_t():
    p1 = Pass(... alice APPROVED, без отзыва)
    p2 = Pass(... alice DISMISSED, отзыв 10:05 prev APPROVED; carol APPROVED 10:06)
    assert projection_at(p1, T) == projection_at(p2, T)
def test_projection_sees_event_at_t():
    # отзыв в 10:00:00 появился во втором проходе → проекции различаются
def test_repo_must_have_one_slash():
    with pytest.raises(ValueError):
        read_open_review(replay_transport({"exchanges": []}), "o/r/x", 7)
```

- [ ] **Step 2: Run** `uv run pytest tests/approvalfacts/v3/test_passes.py -v` → FAIL.
- [ ] **Step 3: Implement.** `read_pass` = все страницы обоих запросов (Task 2
  `graphql_pages`); `submittedAt`/`createdAt` → `canon_ts` (`BadTimestamp` → `_Fail
  ("bad_timestamp")`). `read_open_review`: два `read_pass`, сравнение `Pass` целиком;
  `_Fail` → `unavailable(code, detail)` на границе.
- [ ] **Step 4: Run** → PASS; ruff, pyrefly.
- [ ] **Step 5: Commit** `feat(approval-facts): два полных прохода по ревью и таймлайну, read_open_review`

### Task 5: Доставка и `read_act_facts`

**Files:**
- Create: `src/steward/approvalfacts/v3/delivery.py`, `src/steward/approvalfacts/v3/act.py`
- Test: `tests/approvalfacts/v3/test_delivery.py`, `tests/approvalfacts/v3/test_act.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `read_delivery(t: Transport, owner: str, name: str, pr: int) -> Delivery`
  (без путей, `paths=()`; бросает `_Fail`) — **её же** вызывает release-продюсер (Task 10);
  `read_paths(t, owner, name, merge_commit: str, head: str, paths: Sequence[str]) ->
  tuple[PathBlob, ...]`; `read_act_facts(transport, repo: str, pr: int, *, paths:
  Sequence[str]) -> tuple[Delivery, ReviewFacts | None] | Unavailable`.
- Операции: GraphQL `StewardV3Delivery` (`owner`, `name`, `number`), `StewardV3Blobs`
  (`owner`, `name`, `merge`, `head`, `m0..mN`, `h0..hN` — выражения `"<sha>:<path>"`);
  REST `repos/{o}/{r}/pulls/{n}`, `repos/{o}/{r}/compare/{base}...{merge}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_not_merged_live_capture_is_waiting_delivery():
    """Живой снимок df-roles-probe-private #1: REST merged=false c НЕНУЛЕВЫМ тестовым
    merge_commit_sha, GraphQL mergeCommit=null → сначала merged, поля мержа не сверяются."""
    seq = live("probe1_act_sequence")
    t = replay_transport(seq)                                       # набор — один раз
    pull = next(ex for ex in seq["exchanges"] if ex["request"]["method"] == "GET")
    assert pull["response"]["body"]["merge_commit_sha"]             # тестовый SHA не null
    assert pull["response"]["body"]["merged"] is False
    d, facts = read_act_facts(t, "andrei-shtanakov/df-roles-probe-private", 1, paths=[P])
    assert facts is None and d == Delivery(False, None, None, None, None, None, False, ())
    assert t.unused() == [] and [key(r) for r in t.log] == [key_of(ex) for ex in seq["exchanges"]]
def test_merged_disagreement_is_delivery_mismatch(): ...   # set_field merged по одну сторону
def test_merged_mergecommit_live_positive_twin(): ...      # pr657_act_sequence как есть → Delivery,
                                                           # t.log == порядок снимка
                                                           # ReviewFacts, in_base (identical)
def test_merged_squash_live_positive_twin(): ...           # pr636_act_sequence как есть; in_base
                                                           # из живого `behind`

def test_clean_merge():
    d, facts = read_act_facts(replay(clean), "o/r", 7, paths=[P])
    assert d.merged and d.merge_commit == MERGE and d.head_at_merge == HEAD and d.in_base
    assert d.paths == (PathBlob(P, "b1" * 20, "b1" * 20),)
    assert facts.head_sha == HEAD

def test_rest_graphql_head_mismatch():
    assert read_act_facts(REST head.sha ≠ headRefOid).code == "delivery_mismatch"

def test_rest_offset_and_graphql_z_agree():
    # REST merged_at "…+00:00", GraphQL "…Z" — одна метка
    d, _ = read_act_facts(...); assert d.merged_at == "2026-10-12T10:00:00Z"

def test_bot_login_forms_agree():
    # REST merged_by {"login": "df-reviewer[bot]", "type": "Bot"},
    # GraphQL mergedBy {"login": "df-reviewer", "__typename": "Bot"}
    d, _ = read_act_facts(...)
    assert (d.merged_by_login, d.merged_by_type_hint) == ("df-reviewer", "Bot")

@pytest.mark.parametrize("merged_by", [None, {"login": "ghost", "__typename": "User"},
                                       {"login": "x", "__typename": "Organization"}])
def test_unresolved_merger(merged_by):
    assert read_act_facts(...).code == "actor_unresolved"
def test_merged_without_merged_at_malformed(): ...
def test_unknown_pr_state(): ...                    # state "DRAFT" → "unknown_state"
def test_bot_login_forms_disagree():
    # REST "other[bot]"/Bot vs GraphQL "df-reviewer"/Bot
    assert read_act_facts(...).code == "bot_login_mismatch"

@pytest.mark.parametrize("status,in_base", [("identical", True), ("behind", True),
                                            ("ahead", False), ("diverged", False)])
def test_in_base_from_compare(status, in_base): ...
def test_compare_unknown_status_malformed(): ...

def test_path_deleted_at_merge_is_none():
    assert read_act_facts(...)[0].paths[0].blob_at_merge is None
def test_path_absent_at_head(): ...        # → "path_absent_at_head"
def test_path_is_tree(): ...               # → "not_a_blob"
def test_merge_commit_object_missing(): ... # → "object_missing"
def test_path_with_colon_goes_as_variable():
    # путь "a: b.md": запрос несёт его в переменной m0 = "<MERGE>:a: b.md"
@pytest.mark.parametrize("bad", ["", "/abs", "../x", "a/../b", "a\x00b"])
def test_bad_path_raises(bad):
    with pytest.raises(ValueError): read_act_facts(..., paths=[bad])

def test_dismissed_without_event_refused_by_read_act_facts():
    assert read_act_facts(...).code == "dismissed_without_event"
def test_missing_previous_state_refused_by_read_act_facts():
    # вектор devtools Task 11 ждёт unavailable от read, не от effective (О13)
    assert read_act_facts(...).code == "dismissal_without_previous_state"
def test_delivery_reread_differs_unstable(): ...   # второе чтение доставки иное → "unstable"
def test_merged_event_must_match(): ...            # MergedEvent.commit ≠ MERGE → "delivery_mismatch"
def test_two_merged_events(): ...                  # → "delivery_mismatch"
def test_review_changed_before_t_between_passes_unstable(): ...
def test_post_merge_dismissal_between_passes_is_stable():
    d, facts = read_act_facts(pass1 alice APPROVED; pass2 alice DISMISSED + отзыв T+5s)
    assert states(effective_reviews(facts, d.merged_at, before=frozenset())) \
        == {"alice": "APPROVED"}
def test_dismissal_before_merge_after_preflight():
    # §5.6: отзыв после второго прохода предпроверки, но до мержа — чтение после мержа
    # видит отзыв 09:59 → эффективное DISMISSED
```

- [ ] **Step 2: Run** `uv run pytest tests/approvalfacts/v3/test_delivery.py tests/approvalfacts/v3/test_act.py -v` → FAIL.
- [ ] **Step 3: Implement** порядок спеки steward §3.3 пп. 1–7. Сведение логина: REST
  `login.removesuffix("[bot]")` только при `type == "Bot"`; сравниваются пары (логин, тип) обеих
  сторон. Устойчивость — `projection_at(pass1, merged_at) == projection_at(pass2, merged_at)`.
- [ ] **Step 4: Run** → PASS; ruff, pyrefly.
- [ ] **Step 5: Commit** `feat(approval-facts): read_act_facts — доставка первой, два прохода, срез на T (§5.5)`

### Task 6: История источника политики и активация

**Files:**
- Create: `src/steward/approvalfacts/v3/history.py`
- Test: `tests/approvalfacts/v3/test_history.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: `POLICY_FILES: tuple[str, ...]` (значение — Global Constraints);
  `MAX_CHAIN = 1000`; `read_policy_history(transport, repo: str, branch: str, *, anchor: str,
  until: str | None, files: Sequence[str] = POLICY_FILES) -> tuple[Activation, ...] |
  HistoryViolation | Unavailable`; `read_activation(transport, repo: str, merge_commit: str,
  *, files: Sequence[str] = POLICY_FILES) -> Activation | Unavailable`.
- Только REST: `repos/{o}/{r}`, `repos/{o}/{r}/branches/{branch}`,
  `repos/{o}/{r}/commits/{sha}`, `repos/{o}/{r}/commits/{sha}/pulls`,
  `repos/{o}/{r}/git/trees/{tree_sha}?recursive=1` (`tree_sha` — `commit.tree.sha` уже
  прочитанного коммита), `repos/{o}/{r}/git/blobs/{oid}`. Endpoint `contents/` не
  используется: его 404 неотличим от отсутствия доступа.
- Истории для тестов — из живых снимков H1–H6 (`approval-policy`: корень без родителей,
  `commits/{sha}/pulls = []`; devtools: merge-коммит #657 с боковым коммитом, squash #636)
  через `_derive.py` (`set_field` для подмены `sha`/`parents`/`merge_commit_sha`, `append`
  записей дерева); порядок запросов фиксирован алгоритмом §3.7. Положительный двойник каждой
  строки H1–H6 — тест на неизменённом снимке.

- [ ] **Step 1: Write the failing tests**

```python
def test_merge_squash_and_neutral_chain():
    # A (anchor, PR#1) ← B (PR#2 меняет approvers.env, merge-коммит) ← C (PR#3 README,
    # squash) ← D (PR#4 меняет actors.yaml, squash) = HEAD
    got = read_policy_history(t, "o/approval-policy", "main", anchor=A, until=None)
    assert [a.pr for a in got] == [1, 2, 4]
    assert got[1].files_sha256["policy/approvers.env"] == hashlib.sha256(V2).hexdigest()
    assert got[1].merged_at == "2026-10-12T08:00:00Z"   # из PR, не из коммита

def test_multi_commit_rebase_merge_is_violation():
    # PR#2 rebase-merge из двух коммитов X1, X2: merge_commit_sha = X2;
    # гипотеза А: commits/X1/pulls → [PR#2 (merge_commit_sha X2)]
    got = read_policy_history(...)
    assert isinstance(got, HistoryViolation) and got.code == "rebase_intermediate"

def test_multi_commit_rebase_merge_violation_without_association():
    # гипотеза Б (живьём не проверено, спека steward §3.7 п. 4): commits/X1/pulls → []
    got = read_policy_history(...)
    assert isinstance(got, HistoryViolation) and got.code == "commit_without_pr"

def test_single_commit_rebase_merge_accepted_as_squash():
    """Предел обнаружения (спека devtools §6.1 п. 2, приёмка №28): не доказательство
    отсутствия rebase-merge."""
    assert [a.pr for a in read_policy_history(...)] == [1, 2]

def test_direct_push_is_violation():       # commits/X/pulls → [] → "commit_without_pr"
def test_anchor_not_reached():             # корень без якоря → "anchor_not_reached"
def test_pr_base_mismatch():               # base.ref "dev" → "pr_base_mismatch"
def test_anchor_itself_must_be_pr_merge(): # якорь без PR → "commit_without_pr"
def test_revert_to_same_bytes_is_new_activation():
    # X → Y → X: три активации, третья с теми же files_sha256, что первая
def test_branch_not_default():             # default_branch "master" ≠ "main" →
                                           # Unavailable("branch_not_default"), не violation
def test_until_explicit_not_head():        # until=C: D не читается (replay.unused() == [])
def test_missing_commit_object_is_unavailable(): # 404 на commits/{sha} → "http"
def test_chain_limit(monkeypatch):         # MAX_CHAIN=3 → "chain_limit"
def test_absent_policy_file_omitted():
    # полное дерево (truncated=false) без policy/modes.yaml до PR#3 →
    # у активации PR#2 нет ключа "policy/modes.yaml"
def test_tree_404_after_commit_read_is_unavailable():
    # commits/{sha} 200, git/trees/{tree} 404 → Unavailable("http"), не «файлов нет»
    assert read_policy_history(...).code == "http"
def test_tree_truncated_is_unavailable():
    assert read_policy_history(...).code == "tree_truncated"
def test_policy_path_not_blob():           # запись policy/actors.yaml типа tree → "not_a_blob"
def test_activation_tree_404_is_unavailable(): # read_activation: дерево 404 → "http"
def test_files_parameter_respected(): ...
@pytest.mark.parametrize("files", [(), ("/abs",), ("../x",), ("",)])
def test_bad_files_raise(files):
    with pytest.raises(ValueError): read_policy_history(..., files=files)
def test_anchor_not_hex_raises(): ...
def test_activation_ok():
    assert read_activation(t, "o/approval-policy", B) == Activation(2, B, "…Z", {...})
def test_activation_no_pr():               # → "no_pr_for_commit"
```

- [ ] **Step 2: Run** `uv run pytest tests/approvalfacts/v3/test_history.py -v` → FAIL.
- [ ] **Step 3: Implement** по спеке steward §3.7 пп. 1–7. Набор звена — записи дерева
  `{path: sha}` для `path ∈ files` и `type == "blob"`; содержимое блоба — `base64.b64decode`
  поля `content` (`encoding` обязан быть `base64`, иначе `malformed`).
- [ ] **Step 4: Run** → PASS; ruff, pyrefly.
- [ ] **Step 5: Commit** `feat(approval-facts): история источника политики и активация (§6.1)`

### Task 7: Контракт v3 и векторы сбора §5.6

**Files:**
- Create: `contracts/approval-facts/v3/SCHEMA.json`, `contracts/approval-facts/v3/README.md`,
  `contracts/approval-facts/v3/vectors/*.json`, `vectors/{open_review,history,activation}/*.json`
- Create: `src/steward/approvalfacts/v3/records.py`
- Create: `scripts/gen_approval_facts_v3_vectors.py` (генератор векторов из живых снимков через
  `_derive.py` — векторы не пишутся руками; каждый несёт `derived_from` и `transforms`;
  повторный запуск даёт те же байты)
- Test: `tests/approvalfacts/v3/test_vectors.py`, `tests/approvalfacts/v3/test_records.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: `review_record(facts: ReviewFacts, *, observed_at: str, t: str, before:
  frozenset[EventKey]) -> dict`; `delivery_record(d: Delivery, *, actor_class: str | None)
  -> dict`; `policy_history_record(repo, branch, anchor, until, got) -> dict`; файлы векторов.

Векторы (имя файла → ожидание; значения — спека steward §5):

| Файл | `expect` |
|---|---|
| `clean_approve_app_merge.json` | `effective {"alice": "APPROVED"}`, `delivery {merged: true, merged_by_login: "df-reviewer", merged_by_type_hint: "Bot", in_base: true}` |
| `pagination_reviews_no_cursor.json` | `unavailable: pagination` |
| `pagination_timeline_over_limit.json` | `unavailable: pagination` |
| `access_rest_403.json` | `unavailable: http` |
| `access_graphql_401.json` | `unavailable: http` |
| `head_rest_graphql_mismatch.json` | `unavailable: delivery_mismatch` |
| `delivery_changed_on_reread.json` | `unavailable: unstable` |
| `dismiss_and_cr_between_passes.json` | `unavailable: unstable` |
| `dismissed_after_preflight_before_merge.json` | `effective {"alice": "DISMISSED"}` |
| `graphql_200_partial_errors_approved.json` | `unavailable: graphql_errors` |
| `restore_approved_cr_dismissed.json` | `effective {"alice": "DISMISSED"}` |
| `restore_approved_dismissed_after_t.json` | `effective {"alice": "APPROVED"}` |
| `dismissal_without_previous_state.json` (отзыв ревью Bob до T без prev при действительном APPROVED Alice) / `dismissal_without_previous_state_after_t.json` | `unavailable: dismissal_without_previous_state` (оба — от `read_act_facts`) |
| `dismissal_with_previous_state_twin.json` (тот же набор, prev `CHANGES_REQUESTED`) | `effective {"alice": "APPROVED", "bob": "DISMISSED"}` |
| `login_approved_then_cr.json` / `login_cr_then_approved.json` / `login_approved_old_then_new.json` (+ `effective_commit`) / `login_approved_then_commented.json` | `CHANGES_REQUESTED` / `APPROVED` / `APPROVED` / `APPROVED` |
| `dismissal_before_merged_at.json` / `dismissal_after_merged_at.json` | `DISMISSED` / `APPROVED` |
| `post_merge_dismissal_between_passes.json` | `effective {"alice": "APPROVED"}` |
| `bot_login_two_forms.json` / `bot_login_mismatch.json` | `delivery.merged_by_login "df-reviewer"` / `unavailable: bot_login_mismatch` |
| squash и merge-коммит для `paths[]` — покрыты `live_pr636` / `live_pr657` (без преобразований); `paths_deleted_at_merge.json` (производный: `set_field` `m0 → null` в E6 `pr657_act_sequence`) | `delivery.paths` с blob-ами / `blob_at_merge: null` |
| `equal_stamp_dismissal_before.json` / `…_after.json` | `before [["dismissal","101"]]` → `DISMISSED`; `before []` → `APPROVED` |
| `equal_stamp_approve_before.json` / `…_after.json` | `APPROVED` / `effective {}` |
| `pending_without_submitted_at.json` / `submitted_without_submitted_at.json` | `APPROVED` / `unavailable: missing_submitted_at` |
| `unknown_state.json` / `missing_commit_id.json` / `dismissed_without_event.json` | соответствующий `unavailable` (все — от `read_act_facts`) |
| `actor_unresolved_other_reviewer_null.json` / `…_other_reviewer_ghost.json` / `…_dismissal_actor.json` / `…_merger.json` / `…_mannequin.json` (все — при действительном APPROVED Alice) | `unavailable: actor_unresolved` |
| `open_review/actor_unresolved_ghost.json` | `unavailable: actor_unresolved` |
| `not_merged.json` (живые `e1/probe1_act_sequence` без преобразований; REST `merge_commit_sha` ненулевой) | `waiting_delivery: true` |
| `live_pr657.json` / `live_pr636.json` — **неизменённые** живые последовательности `pr657_act_sequence` / `pr636_act_sequence` (E2, E1, E5, E6 с одним реальным путём, E3, E4 ×2, E2, E1), `t = merged_at`; `transforms: []` | `effective` и `delivery` — как в снимке (`in_base: true`: #657 `identical`, #636 `behind`) |
| `paths_three_negative.json` — `neg_e6_blobs_present_absent_tree` (отдельный отрицательный) | `unavailable: path_absent_at_head` |
| `dismissal_normal_twin.json` (Alice APPROVED + отзыв ревью Bob актором User, `unverified_shape`) / `dismissal_actor_no_typename.json` | `effective {"alice": "APPROVED", "bob": "DISMISSED"}` / `unavailable: actor_unresolved` |
| `open_review/clean.json` / `head_changed.json` / `race_between_pages.json` | `effective` / `unstable` / `unstable` |
| `history/merge_squash_neutral.json` | `activations` PR 1, 2, 4 |
| `history/rebase_multi_commit.json` / `history/rebase_multi_commit_unassociated.json` | `violation: rebase_intermediate` / `violation: commit_without_pr` |
| `history/rebase_single_commit_accepted.json` | `activations` PR 1, 2 (title: «предел, не нарушение») |
| `history/direct_push.json` / `anchor_not_reached.json` / `pr_base_mismatch.json` | соответствующий `violation` |
| `history/revert_bytes.json` | три активации |
| `history/branch_not_default.json` / `history/commit_404.json` | `unavailable: branch_not_default` / `http` |
| `history/tree_404.json` / `history/tree_truncated.json` / `history/policy_file_absent.json` | `unavailable: http` / `unavailable: tree_truncated` / активация без ключа отсутствующего файла |
| `activation/ok.json` / `activation/no_pr.json` | `activation` / `unavailable: no_pr_for_commit` |

- [ ] **Step 1: Write the failing tests**

```python
VECTORS = ROOT / "contracts/approval-facts/v3/vectors"

def _all() -> list[Path]:
    return sorted(VECTORS.rglob("*.json"))

def test_required_vectors_present():
    names = {p.relative_to(VECTORS).as_posix() for p in _all()}
    assert REQUIRED <= names   # REQUIRED — множество имён из таблицы Task 7

def test_act_vectors_have_consumer_shape():
    # форма, которую читает devtools Task 11: transport, repo, pr, t, before, expect
    for p in VECTORS.glob("*.json"):
        spec = json.loads(p.read_text())
        assert spec["kind"] == "act"
        assert {"transport", "repo", "pr", "t", "before", "expect"} <= spec.keys()

@pytest.mark.parametrize("case", _all(), ids=lambda p: p.relative_to(VECTORS).as_posix())
def test_vector(case):
    spec = json.loads(case.read_text(encoding="utf-8"))
    transport = replay_transport(spec["transport"])
    run_vector(spec, transport)          # сверка по виду kind и expect (спека steward §5)
    assert transport.unexpected == [] and transport.unused() == []

def test_positive_live_vectors_replay_unmodified():
    for name in ("live_pr657", "live_pr636"):
        spec = load_vector(name)
        assert spec["transforms"] == []
        assert spec["transport"]["exchanges"] == live(spec["derived_from"][0])["exchanges"]
        t = replay_transport(spec["transport"])
        d, facts = read_act_facts(t, spec["repo"], spec["pr"], paths=spec["paths"])
        assert d.merged and facts is not None and t.unused() == [] and t.unexpected == []
        # общий порядок вызовов библиотеки = порядок снимка (спека §3.8, «согласованные
        # наборы»): FIFO на ключ перестановку E5/E6 после проходов не поймал бы
        assert [key(r) for r in t.log] == [key_of(ex) for ex in spec["transport"]["exchanges"]]
def test_every_vector_is_derived_from_live_captures():
    for p in _all():
        spec = json.loads(p.read_text())
        assert spec["derived_from"] and all((LIVE / f"{n}.json").exists()
                                            for n in spec["derived_from"])
        assert set(t["op"] for t in spec["transforms"]) <= TRANSFORMS
def test_vector_exchanges_carry_library_query_text():
    # каждый GraphQL-обмен вектора: request.query == текст из queries.py
def test_each_contract_row_has_positive_twin():
    # для G0, R0, P1, E1–E7, H1–H6 есть вектор/тест без отказа на снимке строки (§3.8)
def test_generator_is_reproducible(tmp_path):
    # scripts/gen_approval_facts_v3_vectors.py --out tmp → байты равны закоммиченным

def test_records_validate_against_schema():
    # review_record/delivery_record/policy_history_record из clean-вектора проходят
    # jsonschema-валидацию соответствующего $defs; лишний ключ → ValidationError
```

- [ ] **Step 2: Run** `uv run pytest tests/approvalfacts/v3/test_vectors.py tests/approvalfacts/v3/test_records.py -v` → FAIL.
- [ ] **Step 3: Implement** `records.py`, `SCHEMA.json` (draft 2020-12, `additionalProperties:
  false` везде; метки — `pattern "^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$"`), генератор, затем
  `uv run python scripts/gen_approval_facts_v3_vectors.py --out contracts/approval-facts/v3/vectors`.
  `README.md` — разделы спеки steward §3.1 (поля), §3.5 (восстановление), §3.6 (словари), §3.7
  (пределы обнаружения), §5 (формат векторов), канон меток; шапка «канон живёт здесь, v2
  заморожен».
- [ ] **Step 4: Run** → PASS; `git diff --stat contracts/approval-facts/v2` пуст; ruff, pyrefly.
- [ ] **Step 5: Commit** `feat(approval-facts): контракт v3 и векторы сбора §5.6`

### Task 8: Запрет пересечения `human ∩ agent`

**Files:**
- Modify: `src/steward/gatecheck/approval.py` (`ApprovalPolicy`, `load_approval_policy`)
- Test: `tests/gatecheck/test_approval_classify.py`

**Interfaces:**
- Produces: `ApprovalPolicy.__post_init__` бросает `PolicyError("identities in both
  human_identities and agent_identities: [...]")`; `load_approval_policy` — тот же текст с
  префиксом `<path>: `.

- [ ] **Step 1: Write the failing tests**

```python
def test_policy_intersection_rejected_in_constructor():
    with pytest.raises(PolicyError, match="github:x"):
        ApprovalPolicy(1, frozenset({"github:x"}), frozenset({"github:x"}))

def test_policy_intersection_rejected_by_loader(tmp_path):
    p = write_policy(tmp_path, human=["github:x"], agent=["github:x", "github:y"])
    with pytest.raises(PolicyError, match=r"approval-policy\.yaml.*github:x"):
        load_approval_policy(p)

def test_repo_policy_has_no_intersection():
    load_approval_policy(ROOT / "profiles/approval-policy.yaml")  # не бросает

def test_gate_cli_exit_2_on_intersection(tmp_path): ...  # gate-check --stage release → exit 2
```

- [ ] **Step 2: Run** `uv run pytest tests/gatecheck/test_approval_classify.py -v` → FAIL.
- [ ] **Step 3: Implement**; `PolicyError` объявлен выше `ApprovalPolicy` — порядок в модуле
  уже такой.
- [ ] **Step 4: Run** `uv run pytest tests/gatecheck tests/approvalfacts -q` → PASS; ruff, pyrefly.
- [ ] **Step 5: Commit** `feat(gatecheck): политика акторов отвергает пересечение human/agent (§6)`

### Task 9: Обязательный режим акта, провайдер статуса узла и находки `GC-ACT-*`

**Files:**
- Modify: `src/steward/gatecheck/checks.py` (`collect_bundle`, `run_checks`,
  `check_upstream_approved`, `check_status_git`, `check_stale_cascade`; новые
  `ActStatuses`, `act_statuses`, `check_act_status`, `act_conflict`, `Snapshot`,
  `snapshot_digest`, `snapshot_for`), `src/steward/gatecheck/approval.py`
  (`check_approval_evidence(..., statuses)`), `src/steward/verdicts/emitter.py` (`_status`
  по `statuses`), `src/steward/gatecheck/cli.py` (`--act-mode` обязателен, `--node-status`,
  `--print-snapshot-digest`, JSON `act_mode`/`delegated`/`snapshot`, stderr-объявления),
  `profiles/gate-catalog.yaml` (три кода)
- Modify (миграция вызывающих steward, R-M0-5): `.github/workflows/ci.yml:43,46`
  (`--act-mode legacy`), `CLAUDE.md` (команды `gate-check`), `README.md`,
  `docs/gate-check-candidate.md` и прочие доки с командой `gate-check` (найти
  `git grep -n "gate-check "`)
- Test: `tests/gatecheck/test_act_mode.py` (матрица §7.2.4 + двойники Д1–Д9),
  `tests/gatecheck/test_snapshot.py`
- Deliberately changed tests (механическая миграция — в **том же коммите**, что и новый
  интерфейс, R-M0-8; читаемость diff — порядком файлов в описании коммита, а не отдельным
  красным коммитом): все вызовы CLI `gate-check` в `tests/` получают
  `--act-mode legacy` (через общий хелпер вызова, где он есть); все вызовы `collect_bundle(...)`
  (33 места) — `act_mode="legacy"`; все вызовы `run_checks(...)` —
  `statuses=act_statuses(arts, act_mode="legacy", node_status=None, snapshot=None)`; вызовы
  `check_approval_evidence` — `statuses=`; `tests/gatecheck/test_cli.py:167` — набор ключей
  JSON + `act_mode`, `delegated`, `snapshot`; `tests/gatecatalog/test_catalog_data.py:51` —
  `EXPECTED_ACTIVE` + три кода. Ожидания (коды находок, exit) существующих тестов **не
  меняются**: при `legacy` поведение — сегодняшнее; это проверяет полный прогон Step 4.
  `test_candidate.py::test_stale_cascade_sees_the_candidate_content` — только добавлен
  `--act-mode legacy` (= двойник Д4).

**Interfaces:**
- Produces (спека steward §7.2.2–7.2.3, единственная сигнатура): `ActMode =
  Literal["legacy", "review"]`; `collect_bundle(graph, spec_dir, *, act_mode: ActMode) ->
  tuple[list[Artifact], list[Finding]]`; `ActStatuses(act_mode, provider_given: bool,
  answers: Mapping[str, str], snapshot: Snapshot | None)` с `claimed(a)`, `established(a)`,
  `in_scope(a)`; `act_statuses(artifacts, *, act_mode: ActMode, node_status: NodeStatus |
  None, snapshot: Snapshot | None) -> ActStatuses`; `run_checks(graph, artifacts, git,
  assignments=None, *, statuses: ActStatuses, prospective: bool = False, not_required:
  frozenset[str] = frozenset()) -> list[Finding]`; `Snapshot(kind, root, commit, digest)`;
  `NodeStatus = Callable[[str, Snapshot], str]`; `snapshot_digest(spec_dir: Path) -> str`;
  `snapshot_for(run, spec_dir, repo_root) -> Snapshot`; константы `INVALID_ANSWER`,
  `PROVIDER_ERROR`, `ABSENT_FROM_PROVIDER`, `NO_PROVIDER = "unavailable:no_status_provider"`,
  `DELEGATED_RULES = ("GC-STALE", "GC-STALE-UNPINNED", "GC-STALE-KEY")`.
- Новые коды каталога: `GC-ACT-MODE-CONFLICT`, `GC-ACT-PROVIDER-MISSING`,
  `GC-ACT-NOT-APPROVED` (`obligation: approval`, `status: active`, `stages: [authoring,
  release]`); версия каталога — по его правилам (сейчас `version: 2`); потребители,
  вендорящие каталог (Maestro, dispatcher, spec-runner), — пункт TODO на ре-вендор.

- [ ] **Step 1: Migrate existing call sites** (тесты, CI, доки) на `--act-mode legacy` /
  `act_mode="legacy"` по списку Files — **в рабочем дереве, без коммита**: до Step 4 тесты
  падают (`no such option` / `unexpected keyword`), и это состояние не попадает в историю.

- [ ] **Step 2: Write the failing tests**

```python
# tests/gatecheck/test_act_mode.py — фикстура: бандл A → B (frontmatter approved, пины верны)
MATRIX = load_matrix_from_spec()   # 48 строк §7.2.4: (mode, tree, provider, run) →
                                   # (множество (rule_id, artifact), exit, объявления)

@pytest.mark.parametrize("row", MATRIX, ids=lambda r: f"#{r.n}")
def test_combination(row, tmp_path, write_roles, write_role_assignments):
    bundle = make_bundle(tmp_path, review_tree=row.tree)          # + approvals/ при "да"
    args = mode_args(row.mode) + provider_args(row.provider, bundle, row.run)  # absent → []
    res = run_gate(bundle, args, candidate=(row.run == "candidate"))
    assert res.exit_code == row.exit
    if row.exit != 2:
        assert error_codes(res) == row.findings
        for line in row.declarations:
            assert line in res.stderr

def test_d1_deleted_envelope_and_manifest_review(tmp_path): ...   # GC-ACT-NOT-APPROVED, exit 1
def test_d2_missing_act_mode_exit_2(tmp_path):
    res = run_gate(bundle, [])                                     # как сегодняшние S4/S8
    assert res.exit_code == 2 and "--act-mode is required" in res.output
def test_d3_no_approvals_ever_but_mode_review(tmp_path): ...      # GC-ACT-NOT-APPROVED, exit 1
def test_d4_candidate_legacy_changed_upstream_bytes(tmp_path): ... # GC-STALE, exit 1
def test_d5_provider_file_digest_mismatch_exit_2(tmp_path):
    digest = cli(["--print-snapshot-digest", "--candidate", str(bundle)]).stdout.strip()
    (bundle / "req.md").write_text(CHANGED)
    write_provider(path, digest, {"charter": "approved", "requirements": "approved"})
    assert run_gate(bundle, ["--act-mode", "review", "--node-status", str(path)],
                    candidate=True).exit_code == 2
def test_d6_cli_builds_candidate_snapshot_not_base(tmp_path):
    """Реальное построение Snapshot CLI: git-репо, база HEAD с байтами X, отдельный
    candidate-каталог с байтами Y ≠ X."""
    repo, base_bundle = git_repo_with_bundle(tmp_path, body=X)
    cand = copy_bundle(base_bundle, tmp_path / "cand", body=Y)
    assert snapshot_digest(cand) != snapshot_digest(base_bundle)
    write_provider(good, snapshot_digest(cand), ALL_APPROVED)
    res = run_gate(cand, ["--act-mode", "review", "--node-status", str(good),
                          "--format", "json"], candidate=True, cwd=repo)
    snap = json.loads(res.stdout)["snapshot"]
    assert (snap["kind"], Path(snap["root"]).resolve(), snap["digest"]) == \
        ("candidate", cand.resolve(), snapshot_digest(cand))
    write_provider(bad, snapshot_digest(base_bundle), ALL_APPROVED)
    assert run_gate(cand, ["--act-mode", "review", "--node-status", str(bad)],
                    candidate=True, cwd=repo).exit_code == 2
def test_d7_unestablished_node_keeps_stale_cascade(tmp_path): ... # GC-ACT-NOT-APPROVED + GC-STALE
def test_d8_upstream_judged_by_established(tmp_path): ...         # A draft+approved → exit 0
def test_d9_api_requires_act_mode():
    with pytest.raises(TypeError): collect_bundle(graph, bundle)
    with pytest.raises(TypeError): act_statuses(arts, node_status=None, snapshot=None)
    with pytest.raises(ValueError):
        act_statuses(arts, act_mode="legacy", node_status=f, snapshot=snap)
    with pytest.raises(ValueError):
        act_statuses(arts, act_mode="review", node_status=f, snapshot=None)
    with pytest.raises(TypeError): run_checks(graph, arts, git)
def test_provider_invalid_answer_and_exception(tmp_path): ...     # "yes", "not_approved:",
                                                                   # исключение → GC-ACT-NOT-APPROVED
def test_provider_file_duplicate_keys_exit_2(tmp_path): ...
def test_status_is_never_rewritten(tmp_path):
    arts, _ = collect_bundle(graph, bundle, act_mode="review")
    act_statuses(arts, act_mode="review", node_status=lambda n, s: "not_approved:x",
                 snapshot=snap)
    assert {a.meta.status for a in arts} == {"approved"}
def test_unreadable_approvals_dir_is_conflict_in_legacy(tmp_path): ...
def test_emitter_status_in_review_mode(tmp_path): ...             # claimed∧¬established → "unknown"
def test_release_evidence_runs_for_claimed_unestablished(tmp_path): ...
def test_ci_workflow_passes_act_mode():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert all("--act-mode legacy" in l for l in ci.splitlines() if "gate-check --" in l)

# tests/gatecheck/test_snapshot.py
def test_digest_covers_bundle_and_approvals(tmp_path): ...   # правка в approvals/ меняет дайджест
def test_digest_order_independent_and_stable(tmp_path): ...
def test_digest_unreadable_file_is_error(tmp_path): ...       # chmod 000 → exit 2 в CLI
def test_print_snapshot_digest_cli(tmp_path): ...            # = snapshot_for(...).digest, exit 0
def test_print_snapshot_digest_needs_no_act_mode(tmp_path):
    res = cli(["--print-snapshot-digest", "--candidate", str(bundle)])   # без --act-mode
    assert res.exit_code == 0 and res.stdout.strip().startswith("sha256:")
def test_gate_run_without_act_mode_still_exit_2(tmp_path): ...  # тот же бандл без печати → 2
```

`load_matrix_from_spec()` разбирает таблицу §7.2.4 из
`docs/superpowers/specs/2026-10-30-approval-facts-v3-design.md` (строки `| n | …`) — таблица
и тест не могут разойтись.

- [ ] **Step 3: Run** `uv run pytest tests/gatecheck/test_act_mode.py tests/gatecheck/test_snapshot.py -v` → FAIL (локально).
- [ ] **Step 4: Implement** спеку steward §7.2.1–7.2.3; предикат `statuses.in_scope` заменяет
  `status != _APPROVED` в четырёх местах (`checks.py:256/308/377`, `approval.py:505`);
  требование к upstream — `statuses.established`; `check_stale_cascade` пропускает
  `established` в `review`. CLI: `--act-mode` — typer-опция **с умолчанием `None`**;
  обязательность проверяется в теле команды после разбора: нет режима и нет
  `--print-snapshot-digest` → `_fail_config("--act-mode is required: …")` (exit 2);
  `--print-snapshot-digest` выполняется без режима (не `Option(...)` — иначе typer потребовал бы
  режим и у печати дайджеста). Снимок — только `snapshot_for`. Три кода — в
  `profiles/gate-catalog.yaml` и `EXPECTED_ACTIVE`.
- [ ] **Step 5: Run** `uv run pytest -q` → PASS (ожидания прежних тестов не менялись);
  `uv run gate-check --act-mode legacy --profile team spec/` и
  `uv run gate-check --act-mode legacy --profile team-exp workstreams/WS-005-gate-verdicts/spec/`
  → exit 0; `git grep -n "gate-check --" -- . ':!docs/superpowers'` — нет вызова без
  `--act-mode`; ruff, pyrefly.
- [ ] **Step 6: Commit** — **один** коммит: интерфейс + все мигрированные вызывающие + CI +
  доки + тесты (R-M0-8), только после зелёного Step 5:
  `feat(gatecheck): обязательный режим акта, находки GC-ACT-*, провайдер на снимке (§9.1)`

### Task 10: Продюсер и читатель v3 для release-гейта (неактивные)

Ни CLI, ни гейт в этой задаче **не переключаются** — иначе штатная команда выпускала бы
файл, который гейт (`reader.py:273–276`, только `"2"`) отвергает (спека steward §6,
«переключение — атомарное»).

**Files:**
- Create: `src/steward/approvalfacts/v3/release.py`, `src/steward/approvalfacts/v3/release_reader.py`
- Modify: `src/steward/approvalfacts/publish.py` (параметр `schema_version` и сериализатор
  записи у `publish`; поведение для v2 побайтово прежнее)
- Test: `tests/approvalfacts/v3/test_release.py`, `tests/approvalfacts/v3/test_release_reader.py`

**Interfaces:**
- Consumes: Task 5 `read_delivery`; v2 `RequestId`, `Header`, `scope_digest`,
  `build_header`, `publish` (транзакция), `MechanicalFailure`.
- Produces: `SCHEMA_VERSION_V3 = "3"`; `@dataclass(frozen=True) class ReleaseResult: request:
  RequestId; state: ObservationState; delivery: Delivery | None; actor_class: ActorType |
  None`; `resolve_release(t: Transport, owner, name, request: RequestId) -> ReleaseResult`
  (бросает `MechanicalFailure`); `classify_release(results, policy) -> list[ReleaseResult]`;
  `release_record(r: ReleaseResult) -> dict`; `materialize_release(t, repo, scope) ->
  list[ReleaseResult]`; `load_release_facts(path, *, expected_repository, now) ->
  ApprovalFactsV3` (бросает `UnreadableFacts`); `detect_legacy_v2(path) -> bool`;
  `ApprovalFactsV3(header, results)` с `by_merge_sha()` (по `delivery.merge_commit`) и
  `scope_has_sha()`.

- [ ] **Step 1: Write the failing tests**

```python
def test_pr_merged_record_carries_delivery():
    r = resolve_release(replay(...), "o", "r", RequestId("pr", 7))
    assert r.state == "merged" and r.delivery.merge_commit == MERGE
    assert release_record(classify(r))["delivery"]["merged_by"] == {
        "identity": "github:ai-prosto", "type_hint": "User", "actor_class": "agent"}
def test_merge_sha_resolves_via_associated_pr(): ...
def test_no_matching_pr_and_not_found_have_null_delivery(): ...
def test_actor_unavailable_has_delivery_with_null_merged_by(): ...
    # read_delivery → actor_unresolved мержера → state "actor_unavailable" (спека §6);
    # гейт отвергает строкой 9
def test_other_delivery_unavailable_is_mechanical_failure(): ...
def test_delivery_unavailable_is_mechanical_failure(): ...   # _Fail → MechanicalFailure
def test_published_v3_validates_schema(tmp_path): ...
def test_v2_publish_bytes_unchanged(tmp_path): ...           # golden v2-файл тот же
def test_reader_roundtrip(tmp_path):
    publish v3 → load_release_facts → by_merge_sha()[MERGE].delivery.in_base is True
def test_reader_rejects_delivery_on_negative_state(): ...
def test_reader_rejects_missing_merged_by_on_merged(): ...
def test_detect_legacy_v2(tmp_path): ...
def test_cli_and_gate_still_v2():
    # ВРЕМЕННЫЙ: переключения нет — `steward approval-facts` пишет schema_version "2", гейт
    # читает v2. Удаляется коммитом переключения Task 11 (заменяется
    # test_cli_writes_schema_version_3 и test_gate_reads_v3); test_v2_publish_bytes_unchanged
    # остаётся навсегда (низкоуровневый v2-публикатор не меняется).
def test_v2_contract_untouched(): ...                        # sha256 файлов v2/** прежние
```

- [ ] **Step 2: Run** `uv run pytest tests/approvalfacts/v3/test_release.py tests/approvalfacts/v3/test_release_reader.py -v` → FAIL.
- [ ] **Step 3: Implement**; состояния — как v2; `identity` — `f"github:{merged_by_login}"`;
  инварианты читателя v2 (`reader.py`, 11 штук) переносятся с матрицей полей спеки steward §6.
- [ ] **Step 4: Run** `uv run pytest -q` → PASS; ruff, pyrefly.
- [ ] **Step 5: Commit** `feat(approval-facts): продюсер и читатель v3 для release-гейта (неактивны)`

### Task 11: Атомарное переключение `steward approval-facts`, коллектора и release-гейта на v3

Один коммит переключает **всех** потребителей файла `.steward/approval_facts.jsonl` и
v2-кода (R-M0-8; класс «продюсер переключён без всех потребителей» — третий случай, поэтому
перечень ниже — исчерпывающий и проверяется тестом).

**Инвентарь потребителей v2** (снят 2026-10-30 по ветке, `git grep` по **всему** репо за
исключениями из `test_v2_inventory.EXCLUDED`, на импорт/вызов `steward.approvalfacts.{model,
producer,reader,publish}`, `load_facts`, `detect_legacy_v1`, `materialize`,
`classify_results`, литерал `"schema_version": "2"`, `approval-facts/v2`,
`approval_facts.jsonl`; совпадения `materialize` в `review_eval/` — посторонние, другая
функция). Исполнитель повторяет поиск в начале задачи и сверяет с таблицей; расхождение —
стоп и правка плана, а не тихое расширение.

| Файл:строки | Что | Решение в Task 11 |
|---|---|---|
| `src/steward/riskclassify/cli.py:153,156,167–169,310–334` | `approval-facts`: `materialize`, `classify_results`, `publish` v2 | **мигрирован**: `materialize_release` + `classify_release` + `publish(schema_version="3")`, транспорт — фабрика модуля `make_transport()` → `GhTransport` |
| `src/steward/gatecheck/approval.py:26,44,51,52,88,270,322,328` | `resolve_facts` на `load_facts`/`detect_legacy_v1`, тексты «v2» | **мигрирован**: `load_release_facts`, `detect_legacy_v2` + `detect_legacy_v1`, `_evaluate` по `result.delivery`, строка `in_base`, тексты «v3» |
| `src/steward/gatecheck/cli.py:184,204,426–427` | докстринг и help `--approval-facts` («v2 only») | **мигрирован** (текст «v3») |
| `src/steward/gatecheck/cli.py:19` | `FACTS_RELPATH`, `ConfigError`, `parse_origin` из `publish` | **оставлен**: общие для v2 и v3 |
| `src/steward/gatecheck/candidate.py:119` | сообщение «merge-evidence из approval-facts/v2» | **мигрирован** (текст) |
| `src/steward/gatecheck/git_facts.py:64,71,80,139` | докстринги про v2 | **мигрирован** (текст; поведение не меняется) |
| `scripts/collect_approval_facts.py:2,86,89–91,642` (`bundle_verdict`, `--check`) | импорт `load_facts`, проверка публикации v2 | **мигрирован**: `load_release_facts` (v3); v2-файл → `failed` с причиной `legacy_v2` (свежий v2 больше не «пригоден»: гейт его не читает) |
| `scripts/approval-facts-schedule.md:10,17` | текст «approval-facts/v2» | **мигрирован** |
| `tests/collector/test_collect_approval_facts.py:23,104,246,267–303` (`_bundle`, `_publishing_producer`) и все фикстуры бандла | фейковый продюсер пишет `"schema_version": "2"` | **мигрирован**: `_bundle` пишет v3 (`delivery`-записи по схеме §6); v2-бандл оставлен **одной** отрицательной фикстурой (`failed: legacy_v2`) |
| `tests/gatecheck/test_approval_check.py:23,31,161,183,198` | пишет факты v2 через `publish` | **мигрирован** на v3-файл; v2-писатель остаётся только для `test_v2_file_is_legacy_v2_finding` |
| `tests/riskclassify/test_approval_facts_cli.py:28` + подмена `producer._gh` | v2-продюсер | **мигрирован**: фабрика транспорта → `replay_transport` из живых снимков (`pr657_act_sequence`: E2, E1, E5; `e7_commit_pulls_graphql_merge`); header `"3"` |
| `tests/approvalfacts/v3/test_release.py::test_cli_and_gate_still_v2` (Task 10) | временное ожидание v2 | **удалён**, заменён `test_cli_writes_schema_version_3`, `test_gate_reads_v3` |
| `.github/codex/review-context.txt:14–15` | контекст ревью-кита: инварианты v2 | **мигрирован**: строка на `contracts/approval-facts/v3/README.md`; v2 — с пометкой «заморожен» |
| `CLAUDE.md` (Architecture, `approval-policy`/release-гейт) | описание v2 как источника гейта | **мигрирован** |
| `src/steward/approvalfacts/{__init__,model,producer,reader,publish}.py` | код v2 | **оставлен v2 намеренно** (заморожен; `model`/`publish` переиспользует v3, `reader` — для `legacy_v2`); `__init__` докстринг: «v2 заморожен; актуальный — `steward.approvalfacts.v3`». Удаление продюсера — пункт TODO после приёмки (решение контроллера, спека §6) |
| `tests/approvalfacts/{test_model,test_producer,test_reader,test_publish}.py`, `tests/contract/test_approval_facts_schema.py` | тесты v2 | **оставлены v2 намеренно** (вместе с кодом) |
| `tests/approvalfacts/test_import_cycle.py:20,30` | импорт пакета | **оставлен** + добавлен импорт `steward.approvalfacts.v3` |
| `contracts/approval-facts/v2/**` | контракт v2 | **оставлен**, заморожен (ни байта) |
| `profiles/approval-facts-scope.yaml:1,9` | комментарий «охват для регулярного сбора `approval-facts/v2`» | **мигрирован** (только комментарий; данные охвата не меняются) |
| `docs/evidence/2026-08-21-approval-facts-v2-migration/**`, `docs/evidence/2026-09-14-approval-facts-policy-refresh/**` (вкл. копию `approval_facts.jsonl`) | исторические evidence v2 | **оставлены v2 намеренно** (провенанс; evidence не переписывается) |
| соседи (devtools, dispatcher), read-only | см. спеку §12 п. 4 | **заметка потребителям**, кода-потребителя нет |

**Files:** все строки «мигрирован» выше + `TODO.md` (пункт
`@id:approval-facts-v2-producer-removal @trigger:"приёмка M0 approval-facts-v3"`).
Test: `tests/gatecheck/test_approval_check.py`, `tests/riskclassify/test_approval_facts_cli.py`,
`tests/collector/test_collect_approval_facts.py`, `tests/approvalfacts/v3/test_release_e2e.py`,
`tests/approvalfacts/v3/test_v2_inventory.py`.

**Interfaces:**
- Consumes: Task 10.
- Produces: `FactsOutcome = ApprovalFactsV3 | FactsUnavailable`; код `legacy_v2` в
  `FACTS_UNAVAILABLE_CODES`; `riskclassify.cli.make_transport() -> Transport` (точка подмены
  для тестов вместо `producer._gh`).

- [ ] **Step 1: Write the failing tests** (локально)

```python
def test_collector_real_producer_publish_check_release_gate(tmp_path, monkeypatch):
    """E2E: collect_approval_facts.run_producer → настоящий `steward approval-facts`
    (in-process, CliRunner; make_transport → replay живых pr657_act_sequence E2/E1/E5 и
    e7_commit_pulls_graphql_merge, без преобразований) → .steward/approval_facts.jsonl v3 →
    bundle_verdict == "published" и `--check` зелёный → gate-check --act-mode legacy
    --stage release --no-fs <факты: merge_provenance артефакта = merge #657> → exit 0."""
def test_collector_rejects_fresh_v2_bundle(tmp_path): ...    # failed, причина legacy_v2
def test_issue_then_read_then_release(tmp_path, monkeypatch): ...  # CLI-путь без коллектора
def test_v2_file_is_legacy_v2_finding(tmp_path): ...
def test_v2_file_explicit_is_config_error(tmp_path): ...
def test_human_merge_passes(): ...                           # строка 13
def test_agent_merge_allowed_passes(): ...                   # строка 12
def test_unknown_actor_finding(): ...                        # строка 10
def test_not_in_base_finding(): ...                          # "not reachable from base"
def test_policy_digest_mismatch_still_first(): ...
def test_cli_writes_schema_version_3(tmp_path): ...
def test_gate_reads_v3(tmp_path): ...
def test_v2_publish_bytes_unchanged(tmp_path): ...           # из Task 10 — остаётся

# tests/approvalfacts/v3/test_v2_inventory.py
INVENTORY = {...}   # таблица выше: путь → "migrated" | "kept-v2" | "deleted"
#: Область поиска — весь репо; исключения перечислены здесь и только здесь, каждое с
#: причиной, и совпадают с таблицей Task 11.
EXCLUDED = {
    "docs/superpowers/": "спеки и планы описывают миграцию, не потребляют v2",
    "tests/fixtures/approvalfacts_v3/": "живые снимки v3",
    "contracts/approval-facts/v3/": "контракт v3 ссылается на v2 как на замороженный",
    "tests/approvalfacts/v3/test_v2_inventory.py": "сам этот тест",
}
def test_no_unlisted_v2_consumer():
    hits = git_grep(V2_PATTERNS, ["."])                       # весь трекаемый репо
    paths = {h.path for h in hits if not h.path.startswith(tuple(EXCLUDED))}
    assert all(any(p == k or p.startswith(k) for k in INVENTORY) for p in paths), paths
def test_inventory_has_no_stale_rows():
    for k in INVENTORY:                                       # строка таблицы без совпадения —
        assert git_grep(V2_PATTERNS, [k]), k                  # таблица устарела
def test_migrated_files_no_longer_use_v2_reader():
    for path, verdict in INVENTORY.items():
        if verdict == "migrated":
            assert not re.search(r"\bload_facts\b|approvalfacts\.producer|\"schema_version\": \"2\"",
                                 read(path)), path
```

- [ ] **Step 2: Run** нужные тесты → FAIL (локально).
- [ ] **Step 3: Implement** по таблице. Порядок строк §8.3 v2 сохраняется; `in_base = false`
  — между 8 и 9, сообщение `"merge {sha} is not reachable from base {base_ref} (in_base:
  false)"`; `legacy_v2` — «facts source is an unsupported approval-facts/v2; reissue it with
  the `steward approval-facts` command».
- [ ] **Step 4: Run** `uv run pytest -q` → PASS; `uv run gate-check --act-mode legacy --profile
  team spec/` → exit 0; повторный `git grep` = таблица; ruff, pyrefly.
- [ ] **Step 5: Commit** — **один** коммит переключения всех «мигрирован» (R-M0-8):
  `feat(approval-facts): steward approval-facts, коллектор и release-гейт на записи delivery v3 (§9.3)`

### Task 12: Публичный API и документ потребителя

**Files:**
- Modify: `src/steward/approvalfacts/v3/__init__.py` (финальный `__all__`)
- Create: `docs/approval-facts-v3.md` (как потребитель зовёт библиотеку: транспорт, порядок
  вызовов, отступления О1–О13 спеки steward §10, заметка §12)
- Test: `tests/approvalfacts/v3/test_api.py`

**Interfaces:**
- Produces: `steward.approvalfacts.v3.__all__` == `["Activation", "Delivery", "Dismissal",
  "EventKey", "GhTransport", "HISTORY_VIOLATION_CODES", "HistoryViolation", "POLICY_FILES",
  "PathBlob", "ReplayTransport", "Request", "Response", "Review", "ReviewFacts", "Transport",
  "UNAVAILABLE_CODES", "Unavailable", "effective_reviews", "read_act_facts",
  "read_activation", "read_open_review", "read_policy_history", "replay_transport"]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_all_is_exact():
    assert sorted(v3.__all__) == sorted(EXPECTED)

def test_signatures_match_consumer_interface():
    sig = inspect.signature(v3.read_act_facts)
    assert list(sig.parameters) == ["transport", "repo", "pr", "paths"]
    assert sig.parameters["paths"].kind is inspect.Parameter.KEYWORD_ONLY
    assert list(inspect.signature(v3.effective_reviews).parameters) == ["facts", "t", "before"]
    assert list(inspect.signature(v3.read_policy_history).parameters) == \
        ["transport", "repo", "branch", "anchor", "until", "files"]
    ...  # read_open_review, read_activation

def test_import_has_no_io(monkeypatch):
    # subprocess.run и socket.socket подменены на raise; importlib.reload(v3) проходит
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; ruff, pyrefly.
- [ ] **Step 5: Commit** `docs(approval-facts): публичный API v3 и инструкция потребителя`

### Task 13: Живой смоук чтения (read-only)

**Files:**
- Create: `scripts/approval_facts_v3_smoke.py`
- Create: `docs/evidence/2026-10-30-approval-facts-v3-smoke/manifest.md`

**Interfaces:**
- Consumes: Task 12 API; профиль `~/.config/review` (`ai-prosto`, только чтение).

Сценарий — только чтения, без записей в GitHub; цели — уже существующие PR и репо. Чек-лист
— **каждая строка таблицы спеки steward §8**, где стоит «смоук»; по каждой — `verified` /
`not_verified` + причина / `contradicts` (гипотеза неверна):

Уже **снято живьём** 30.10 (`tests/fixtures/approvalfacts_v3/live/`, спека §3.8) и в смоуке
не повторяется: E1/E2 merge-коммит (User-мержер), squash, закрытый несмёрженный PR с тестовым
`merge_commit_sha`; E3 User- и Bot-авторы ревью; E4 `MergedEvent` (`createdAt = mergedAt`);
E5 `identical`; E6 Blob/`null`/Tree; E7; H1–H6 (корень без родителей, `commits/{sha}/pulls`
для merge-коммита, squash, бокового коммита и прямого коммита, полное дерево, блоб); P1.

**Не снято — смоук обязан проверить (каждая строка: `verified` / `not_verified` + причина /
`contradicts`):**

1. `ReviewDismissedEvent` (E4): форма узла, `previousReviewState`, `actor{login __typename}`,
   `review{databaseId}`; число событий = число ревью в `DISMISSED`. В разрешённых репо отзывов
   нет — смоук ставит отзыв на **стенде** (`df-roles-probe-private`, отдельное решение
   владельца на запись) либо фиксирует `not_verified`;
2. **App-мержер** (E1/E2): REST `merged_by{login: "<slug>[bot]", type: "Bot"}`, GraphQL
   `mergedBy{login: "<slug>", __typename: "Bot"}`;
3. **открытый PR** (E1/E2 `state: open`/`OPEN`, `read_open_review`): в разрешённых репо открытых
   PR на 30.10 не было;
4. `compare` со статусами `ahead`/`diverged` (E5); `identical` и `behind` сняты живьём;
5. удалённый пользователь (`null` или `ghost`) — если найдётся;
6. видимость чужого PENDING-ревью;
7. анонимный транспорт на `approval-policy`: доступность H1–H6 без токена, остаток лимита
   (`x-ratelimit-remaining`);
8. **вне смоука** (только стенд devtools, Task 18 №28): ассоциации промежуточного коммита
   **rebase-merge** (живой аналог — боковой коммит merge-мержа — снят), `merge_commit_sha`
   однокоммитного rebase-merge, ассоциации вне ветки по умолчанию.

- [ ] **Step 1:** запустить `uv run python scripts/approval_facts_v3_smoke.py --profile ~/.config/review` → JSON-отчёт.
- [ ] **Step 2:** записать в манифест: SHA steward, команды, цели, исход по каждой строке
  таблицы спеки steward §8 (`verified` / `not_verified` + причина). Неудача смоука —
  находка в манифесте и в ответе #211, а не правка ожиданий.
- [ ] **Step 3: Commit** `docs(evidence): живой смоук чтения approval-facts/v3`

### Task 14: Релиз и ответ в steward#211

**Files:**
- Modify: `pyproject.toml` (`version = "0.2.0"`), `uv.lock`, `TODO.md` (пункт
  `approval-facts-v3` → `[x]` + PR/SHA)

- [ ] **Step 1:** `uv lock`; полный прогон `uv run pytest -q`, `uv run ruff check .`,
  `uv run pyrefly check`, `uv run gate-check --act-mode legacy --profile team spec/` → всё
  зелёное.
- [ ] **Step 2:** PR по правилу репо (локальный `scripts/review/local.sh` → драфт →
  `review-pr.sh`); `profiles/approval-policy.yaml` не тронут —
  `git diff --quiet origin/master -- profiles/approval-policy.yaml` (код 0). Прочие файлы
  `profiles/` меняются законно (`gate-catalog.yaml`, Task 9). **PR мержит владелец** (решение
  контроллера, спека steward §9).
- [ ] **Step 3:** после мержа владельцем — аннотированный тег `v0.2.0` на merge-коммите и
  `git push origin v0.2.0` агентом; отдельного подтверждения на тег не требуется (владелец
  подтвердил подписью плана).
- [ ] **Step 4:** комментарий в steward#211: полный SHA, тег, пути контракта и векторов,
  таблица отступлений О1–О13, заметка для devtools (спека steward §12), ссылка на evidence
  смоука; закрыть issue (`gh issue close 211 --comment …`).
- [ ] **Step 5:** `TODO.md`: `[x]` с PR и SHA; `git commit -m "docs(todo): approval-facts-v3 выпущен (v0.2.0)"` отдельным PR или в составе релизного.
