# Review-eval harness — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** измеримый eval ревьюера — `review-eval` гоняет текущий кит steward по корпусу исторических PR в изолированных worktree, сопоставляет находки с gold-дефектами детерминированным матчером и публикует метрики с знаменателями, стоимостью и повторениями.

**Architecture:** три env-расширения вендоримого кита (`REVIEW_VERDICT_OUT`, `REVIEW_USAGE_OUT`, `REVIEW_EFFORT`) дают sidecar-артефакты и effort без изменения поведения по умолчанию; пакет `src/steward/review_eval/` (corpus → cache → runner → threshold → matcher → metrics → report/cli) читает корпус YAML, ставит detached worktree из bare-кэша на `head_sha`, зовёт боевой `local.sh`, собирает `verdict.json`/`usage.json`/wall-clock и считает метрики только по `adjudicated`-кейсам.

**Tech Stack:** POSIX sh (кит; macOS `/bin/sh` = bash 3.2, CI = dash), `jq`; Python ≥ 3.12, `typer`, `pyyaml`, stdlib (`subprocess`, `hashlib`, `json`, `random` для bootstrap); pytest с подставными `local.sh`/`claude`/`git`.

**Spec:** `docs/superpowers/specs/2026-09-14-review-eval-harness-design.md` (D1–D13, §4–§15); читать вместе с `2026-09-14-review-kit-harness-layer-design.md` (резолв `REVIEW_*` в `local.sh`) и `2026-08-21-codex-review-kit-design.md` §7 (коды выхода).

## Global Constraints

- Кит: только POSIX sh + стандартные утилиты, JSON через `jq`; без переменных `REVIEW_VERDICT_OUT`/`REVIEW_USAGE_OUT`/`REVIEW_EFFORT` поведение и отпечаток **побайтно прежние**; объявленная пустой переменная → код 2; пути sidecar в отпечаток не входят; effort входит в `review_cmd` и в отпечаток (D4, D13).
- `REVIEW_USAGE_OUT` пишется сразу после разбора конверта как JSON — до проверок `subtype`/`is_error`/`structured_output` (D12); отсутствующие числа — `null`, никогда 0 (D6).
- Пакет — `src/steward/review_eval/`, console-script `review-eval`; никакой зависимости `steward.gatecheck → review_eval`; данные — `eval/corpus/` (в git), `eval/runs/`, `eval/cache/` (git-ignored).
- `run` — всегда офлайн; единственная сетевая команда — `corpus materialize`; соседние чекауты только читаются.
- Каждый кейс — detached worktree на `head_sha` из bare-кэша; `REVIEW_CMD` вычищается из окружения раннера; `--jobs 1` по умолчанию.
- Матчер: mutual-best без greedy и без tie-break по порядку; веса — целочисленные кортежи; неоднозначность → adjudication; `matcher_version` + `matcher_rules_digest` в `run.json` (D10).
- «Блокирующее предсказание» = предикат `BLOCKING_DEF` из `apply-threshold.sh`; контрактный тест против настоящего скрипта; TP только на gold major/blocker (§9).
- Официальные метрики — только `annotation.status: adjudicated`; recall/false-block — только `blocking_complete: true`; `precision` скрыт при непустой очереди (`pending_adjudication`) (D8, D9).
- Тесты не вызывают модель. `uv run pytest -q`, `uv run ruff format . && uv run ruff check .`, `uv run pyrefly check` — зелёные перед каждым коммитом; строка ≤ 100.
- Коммиты с трейлером `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (буквально, без подстановки своей модели). Ветка `feat/review-eval-harness` от `master`.
- Соседние репо не редактировать; корпус-кандидаты читаются с GitHub через `gh api`.

---

## Карта файлов

| Файл | Роль |
|---|---|
| `scripts/review/local.sh` | `REVIEW_VERDICT_OUT` (после проверки вердикта, до порога), `REVIEW_EFFORT` в резолве |
| `scripts/review/harness-claude` | `--effort`, `REVIEW_USAGE_OUT` |
| `tests/review/test_local.py`, `tests/review/test_harness_claude.py` | тесты кита |
| `src/steward/review_eval/__init__.py` | пусто |
| `src/steward/review_eval/corpus.py` | модель кейса, загрузка/валидация YAML, реестр id |
| `src/steward/review_eval/cache.py` | bare-кэш: `materialize`, `has_object`, `worktree()` контекст |
| `src/steward/review_eval/threshold.py` | `is_blocking(finding)` — зеркало `BLOCKING_DEF` |
| `src/steward/review_eval/matcher.py` | рёбра, mutual-best назначение, дубликаты, очередь, версия |
| `src/steward/review_eval/runner.py` | прогон кейса × варианта × повторения, артефакты, классификация исходов |
| `src/steward/review_eval/metrics.py` | метрики, знаменатели, bootstrap, compare |
| `src/steward/review_eval/report.py` | `report.md`, `adjudication-queue.md` |
| `src/steward/review_eval/candidates.py` | черновики кейсов из ревью ai-prosto (сеть) |
| `src/steward/review_eval/cli.py` | Typer-приложение `review-eval` |
| `tests/review_eval/test_*.py` | тесты пакета |
| `eval/corpus/_ids.txt`, `eval/corpus/*.yaml` | реестр id, черновики кейсов |
| `pyproject.toml`, `.gitignore`, `README.md`, `docs/review-eval.md`, спека харнесс-слоя, `TODO.md` | регистрация, документация |

---

### Task 1: `local.sh` — `REVIEW_VERDICT_OUT`

**Files:**
- Modify: `scripts/review/local.sh` (после блока `if [ ! -s "$work/verdict.json" ]; then … fi`, перед `sh "$kit_dir/apply-threshold.sh" …`; плюс валидация пустого значения рядом с проверкой `REVIEW_MODEL`)
- Test: `tests/review/test_local.py`

**Interfaces:**
- Produces: env `REVIEW_VERDICT_OUT=<path>` → после успешного вызова ревьюера файл с байтами `$work/verdict.json` существует по этому пути (atomic), независимо от исхода `apply-threshold.sh`.

- [ ] **Step 0: Изоляция окружения тестов**

В `tests/review/test_local.py` хелпер `run_local_env` вычищает из наследуемого `os.environ` только `REVIEW_CMD`, `REVIEW_HARNESS`, `REVIEW_MODEL`. Расширить кортеж до `("REVIEW_CMD", "REVIEW_HARNESS", "REVIEW_MODEL", "REVIEW_EFFORT", "REVIEW_VERDICT_OUT", "REVIEW_USAGE_OUT")` — иначе экспортированный в shell разработчика `REVIEW_EFFORT` (переменная для этого и заводится) менял бы ожидания `--print-review-cmd`/отпечатков, а `REVIEW_VERDICT_OUT` заставлял бы тесты писать по чужому пути. Аналогично в `tests/review/test_harness_claude.py` `Stand.run`: строить env из `os.environ` без `REVIEW_USAGE_OUT`/`REVIEW_EFFORT`, добавить параметр `extra_env: dict[str, str] | None = None` и применять его поверх — тесты Task 2 передают путь sidecar через `extra_env`, не через `monkeypatch.setenv`.

- [ ] **Step 1: Падающие тесты**

Добавить в конец `tests/review/test_local.py` (хелперы `make_repo_with_diff`, `run_local_env`, `harness_fp`, `_claude_stand`, `MAJOR_FINDING` уже есть в файле):

```python
# --- REVIEW_VERDICT_OUT (спека review-eval §7) -------------------------------


def test_verdict_out_is_written_even_when_threshold_blocks(tmp_path: Path) -> None:
    """Sidecar пишется ДО apply-threshold.sh: при коде 1 (major) вердикт доступен."""
    repo = make_repo_with_diff(tmp_path)
    out = tmp_path / "artifacts" / "verdict.json"
    env = _claude_stand(tmp_path, MAJOR_FINDING)
    env["REVIEW_VERDICT_OUT"] = str(out)
    res = run_local_env(repo, env=env)
    assert res.returncode == 1, res.stdout + res.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == MAJOR_FINDING


def test_verdict_out_is_written_when_threshold_rejects_verdict(tmp_path: Path) -> None:
    """Битый по схеме вердикт: apply-threshold.sh даёт 2, но sidecar уже сохранён."""
    repo = make_repo_with_diff(tmp_path)
    out = tmp_path / "verdict.json"
    env = _claude_stand(tmp_path, {"findings": [{"severity": "major"}], "note": "x"})
    env["REVIEW_VERDICT_OUT"] = str(out)
    res = run_local_env(repo, env=env)
    assert res.returncode == 2, res.stdout + res.stderr
    assert out.exists()


def test_verdict_out_empty_is_config_error(tmp_path: Path) -> None:
    repo = make_repo_with_diff(tmp_path)
    res = run_local_env(repo, "--print-review-cmd", env={"REVIEW_VERDICT_OUT": ""})
    assert res.returncode == 2, res.stdout + res.stderr
    assert "REVIEW_VERDICT_OUT" in res.stderr


def test_verdict_out_does_not_change_fingerprint(tmp_path: Path) -> None:
    repo = make_repo_with_diff(tmp_path)
    assert harness_fp(repo) == harness_fp(repo, {"REVIEW_VERDICT_OUT": str(tmp_path / "v.json")})
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/review/test_local.py -k verdict_out -q`
Expected: первые три FAIL (файла нет / код 0 вместо 2), последний PASS (ratchet).

- [ ] **Step 3: Реализация**

В `local.sh`, в блоке резолва сразу после проверки `REVIEW_MODEL` (внутри `else` ветки — нет: проверка должна работать и при `REVIEW_CMD`; поставить её **перед** `if [ -n "${REVIEW_CMD:-}" ]`):

```sh
# Sidecar-артефакты eval (спека review-eval §7): пути задаются окружением,
# пустое значение — отказ (прецедент REVIEW_MODEL), в отпечаток не входят.
if [ -n "${REVIEW_VERDICT_OUT+x}" ] && [ -z "$REVIEW_VERDICT_OUT" ]; then
    echo "REVIEW_VERDICT_OUT задан пустым — уберите переменную или назовите путь." >&2
    exit 2
fi
```

После блока `if [ ! -s "$work/verdict.json" ]; then … exit 3; fi` и перед `sh "$kit_dir/apply-threshold.sh"`:

```sh
# Копия вердикта ДО порога (спека review-eval §7, D4): eval обязан видеть
# находки и при коде 1, и при отказе валидации порога. Атомарно: tmp в
# каталоге цели + mv; потерять запрошенный артефакт молча нельзя — код 2.
if [ -n "${REVIEW_VERDICT_OUT:-}" ]; then
    verdict_out_dir=$(dirname "$REVIEW_VERDICT_OUT")
    mkdir -p "$verdict_out_dir" || { echo "REVIEW_VERDICT_OUT: не создать каталог $verdict_out_dir" >&2; exit 2; }
    verdict_tmp=$(mktemp "$verdict_out_dir/.verdict.XXXXXX") \
        || { echo "REVIEW_VERDICT_OUT: не создать временный файл в $verdict_out_dir" >&2; exit 2; }
    if ! cp "$work/verdict.json" "$verdict_tmp" || ! mv "$verdict_tmp" "$REVIEW_VERDICT_OUT"; then
        rm -f "$verdict_tmp"
        echo "REVIEW_VERDICT_OUT: не удалось сохранить вердикт в $REVIEW_VERDICT_OUT" >&2
        exit 2
    fi
fi
```

- [ ] **Step 4: GREEN** — `uv run pytest tests/review/test_local.py -q`; `dash -n scripts/review/local.sh`.
- [ ] **Step 5: Commit** — `feat(review-kit): local.sh — REVIEW_VERDICT_OUT, sidecar вердикта до порога`.

---

### Task 2: `harness-claude` — `REVIEW_USAGE_OUT` и `--effort`

**Files:**
- Modify: `scripts/review/harness-claude` (разбор аргументов; блок `claude -p …`; после `claude_code=$?`)
- Test: `tests/review/test_harness_claude.py`

**Interfaces:**
- Produces: аргумент `--effort <e>` → `claude -p … --effort <e>`; env `REVIEW_USAGE_OUT=<path>` → JSON `review-usage/v1` (см. спека §7) сразу после разбора конверта, и при `outcome: error`.

- [ ] **Step 1: Падающие тесты** (в `tests/review/test_harness_claude.py`, стенд `Stand`, `envelope`, `VERDICT_OK` есть):

```python
# --- REVIEW_USAGE_OUT и --effort (спека review-eval §7, D12/D13) ----------------

FULL_ENVELOPE = json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "structured_output": VERDICT_OK, "duration_ms": 12345, "total_cost_usd": 0.42,
    "usage": {"input_tokens": 1000, "output_tokens": 200,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 900},
})


def _usage_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    out = tmp_path / "side" / "usage.json"
    return {"REVIEW_USAGE_OUT": str(out)}, out


def test_usage_sidecar_written_on_success(tmp_path: Path) -> None:
    s = Stand(tmp_path)
    env, out = _usage_env(tmp_path)
    res = s.run(*s.codex_args("--model", "claude-opus-5", "--effort", "high"), envelope_text=FULL_ENVELOPE, extra_env=env)
    assert res.returncode == 0, res.stderr
    u = json.loads(out.read_text(encoding="utf-8"))
    assert u["schema"] == "review-usage/v1" and u["provider"] == "claude"
    assert u["model"] == "claude-opus-5" and u["requested_effort"] == "high"
    assert u["usage"]["input_tokens"] == 1000 and u["total_cost_usd"] == 0.42
    assert u["provider_duration_ms"] == 12345 and u["outcome"] == "success"
    argv = s.argv.read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("--effort") + 1] == "high"


def test_usage_sidecar_written_on_error_envelope(tmp_path: Path) -> None:
    """Ошибочный ответ тоже стоил денег (D12): sidecar есть, outcome=error, код адаптера 3."""
    s = Stand(tmp_path)
    env, out = _usage_env(tmp_path)
    bad = json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True,
                      "total_cost_usd": 0.05, "usage": {"input_tokens": 10, "output_tokens": 1}})
    res = s.run(*s.codex_args(), envelope_text=bad, extra_env=env)
    assert res.returncode == 3
    u = json.loads(out.read_text(encoding="utf-8"))
    assert u["outcome"] == "error" and u["total_cost_usd"] == 0.05
    assert u["provider_duration_ms"] is None  # отсутствует → null, не 0


def test_usage_sidecar_on_unparseable_envelope(tmp_path: Path) -> None:
    s = Stand(tmp_path)
    env, out = _usage_env(tmp_path)
    res = s.run(*s.codex_args(), envelope_text="not json {", extra_env=env)
    assert res.returncode == 3
    u = json.loads(out.read_text(encoding="utf-8"))
    assert u["outcome"] == "error" and u["usage"] is None


def test_usage_out_empty_is_config_error(tmp_path: Path) -> None:
    s = Stand(tmp_path)
    res = s.run(*s.codex_args(), extra_env={"REVIEW_USAGE_OUT": ""})
    assert res.returncode == 2 and "REVIEW_USAGE_OUT" in res.stderr


def test_no_effort_flag_without_effort_arg(tmp_path: Path) -> None:
    s = Stand(tmp_path)
    assert s.run(*s.codex_args()).returncode == 0
    assert "--effort" not in s.argv.read_text(encoding="utf-8").splitlines()
```

(`Stand.run` после Task 1 Step 0 принимает `extra_env` и вычищает `REVIEW_USAGE_OUT`/`REVIEW_EFFORT` из наследуемого окружения.)

- [ ] **Step 2: RED** — `uv run pytest tests/review/test_harness_claude.py -k "usage or effort" -q` → все FAIL кроме `no_effort_flag` (ratchet).

- [ ] **Step 3: Реализация**

В разборе аргументов добавить `effort=""` в инициализацию и ветку:

```sh
        --effort)
            [ $# -ge 2 ] || fail 2 "--effort требует значение"
            effort="$2"; shift 2 ;;
```

После префлайтов:

```sh
# Sidecar usage (спека review-eval §7, D12): пустое значение — отказ; пишется
# сразу после того, как конверт разобран как JSON, ДО проверок subtype /
# structured_output — ошибочный ответ тоже стоил денег.
if [ -n "${REVIEW_USAGE_OUT+x}" ] && [ -z "$REVIEW_USAGE_OUT" ]; then
    fail 2 "REVIEW_USAGE_OUT задан пустым — уберите переменную или назовите путь"
fi
write_usage() {
    # $1 — outcome (success|error); конверт — $envelope (может быть не-JSON).
    [ -n "${REVIEW_USAGE_OUT:-}" ] || return 0
    usage_dir=$(dirname "$REVIEW_USAGE_OUT")
    mkdir -p "$usage_dir" || fail 2 "REVIEW_USAGE_OUT: не создать каталог $usage_dir"
    usage_tmp=$(mktemp "$usage_dir/.usage.XXXXXX") || fail 2 "REVIEW_USAGE_OUT: не создать временный файл"
    if jq -e 'type == "object"' "$envelope" >/dev/null 2>&1; then
        jq -c --arg model "$model" --arg effort "$effort" --arg outcome "$1" '{
            schema: "review-usage/v1", provider: "claude", model: $model,
            requested_effort: (if $effort == "" then null else $effort end),
            usage: (if (.usage|type) == "object" then {
                input_tokens: (.usage.input_tokens // null),
                output_tokens: (.usage.output_tokens // null),
                cache_creation_input_tokens: (.usage.cache_creation_input_tokens // null),
                cache_read_input_tokens: (.usage.cache_read_input_tokens // null)
            } else null end),
            total_cost_usd: (.total_cost_usd // null),
            provider_duration_ms: (.duration_ms // null),
            outcome: $outcome }' "$envelope" > "$usage_tmp"
    else
        jq -nc --arg model "$model" --arg effort "$effort" --arg outcome "$1" '{
            schema: "review-usage/v1", provider: "claude", model: $model,
            requested_effort: (if $effort == "" then null else $effort end),
            usage: null, total_cost_usd: null, provider_duration_ms: null,
            outcome: $outcome }' > "$usage_tmp"
    fi
    mv "$usage_tmp" "$REVIEW_USAGE_OUT" || fail 2 "REVIEW_USAGE_OUT: не сохранить $REVIEW_USAGE_OUT"
}
```

Вызов claude: добавить `${effort:+--effort "$effort"}` — в POSIX sh с кавычками внутри `${…:+…}` это работает как два слова только при непустом `effort`; чтобы не полагаться на тонкости, использовать `set --` для сборки:

```sh
set -- -p --model "$model"
[ -n "$effort" ] && set -- "$@" --effort "$effort"
set +e
claude "$@" \
    --json-schema "$(cat "$schema")" \
    --output-format json \
    --restricted --strict-mcp-config --no-session-persistence \
    --permission-prompts none \
    --tools Read Glob Grep > "$envelope"
claude_code=$?
set -e
```

Сразу после `claude_code=$?`/`set -e` и **до** `[ "$claude_code" -eq 0 ] || fail 3 …`: определить исход и записать sidecar:

```sh
if [ "$claude_code" -eq 0 ] && jq -e '
        type == "object" and (.is_error == false) and .subtype == "success"
        and (.structured_output | type == "object")
        and (.structured_output | length > 0)' "$envelope" >/dev/null 2>&1; then
    write_usage success
else
    write_usage error
fi
```

(Существующая проверка конверта ниже остаётся — она задаёт код выхода; `write_usage` не меняет коды.)

- [ ] **Step 4: GREEN** — весь `tests/review/test_harness_claude.py`; `dash -n scripts/review/harness-claude`.
- [ ] **Step 5: Commit** — `feat(review-kit): harness-claude — REVIEW_USAGE_OUT (и при ошибке) и --effort`.

---

### Task 3: `local.sh` — `REVIEW_EFFORT` в резолве и отпечатке

**Files:**
- Modify: `scripts/review/local.sh` (блок резолва)
- Test: `tests/review/test_local.py`
- Docs: README «Харнесс ревьюера» (таблица: `REVIEW_EFFORT`, `REVIEW_VERDICT_OUT`, `REVIEW_USAGE_OUT`), спека харнесс-слоя §4/§6 (строка таблицы отпечатка)

- [ ] **Step 1: Падающие тесты**

```python
# --- REVIEW_EFFORT (спека review-eval D13) -----------------------------------


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"REVIEW_EFFORT": "high"}, "codex exec -c model_reasoning_effort=high"),
        ({"REVIEW_MODEL": "gpt-5.4", "REVIEW_EFFORT": "low"},
         "codex exec -m gpt-5.4 -c model_reasoning_effort=low"),
        ({"REVIEW_HARNESS": "claude", "REVIEW_EFFORT": "high"},
         "harness-claude --model claude-opus-5 --effort high"),
        ({"REVIEW_CMD": "my-reviewer", "REVIEW_EFFORT": "high"}, "my-reviewer"),
    ],
)
def test_effort_resolution(tmp_path: Path, env: dict[str, str], expected: str) -> None:
    _, local = make_repo(tmp_path)
    res = run_local_env(local, "--print-review-cmd", env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout == expected + "\n"


def test_effort_changes_fingerprint_and_matches_explicit_cmd(tmp_path: Path) -> None:
    repo = make_repo_with_diff(tmp_path)
    with_effort = harness_fp(repo, {"REVIEW_EFFORT": "high"})
    assert with_effort != harness_fp(repo)
    assert with_effort == harness_fp(repo, {"REVIEW_CMD": "codex exec -c model_reasoning_effort=high"})


def test_effort_empty_is_config_error(tmp_path: Path) -> None:
    _, local = make_repo(tmp_path)
    res = run_local_env(local, "--print-review-cmd", env={"REVIEW_EFFORT": ""})
    assert res.returncode == 2 and "REVIEW_EFFORT" in res.stderr
```

- [ ] **Step 2: RED** — `-k effort`.
- [ ] **Step 3: Реализация** — в блоке резолва, после проверки `REVIEW_MODEL` внутри `else`:

```sh
    if [ -n "${REVIEW_EFFORT+x}" ] && [ -z "$REVIEW_EFFORT" ]; then
        echo "REVIEW_EFFORT задан пустым — уберите переменную или назовите уровень." >&2
        exit 2
    fi
```

и в ветках: `codex)` → `review_cmd="codex exec${REVIEW_MODEL:+ -m $REVIEW_MODEL}${REVIEW_EFFORT:+ -c model_reasoning_effort=$REVIEW_EFFORT}"`; `claude)` → `review_cmd="harness-claude --model ${REVIEW_MODEL:-claude-opus-5}${REVIEW_EFFORT:+ --effort $REVIEW_EFFORT}"`. Комментарий: effort — часть команды, значит отпечатка (D13); при `REVIEW_CMD` игнорируется вместе с harness/model. Значение не валидируется — только непустота.

- [ ] **Step 4: GREEN** + docs (README таблица + одна строка про sidecar-переменные; спека харнесс-слоя §6 — две строки таблицы с effort).
- [ ] **Step 5: Commit** — `feat(review-kit): REVIEW_EFFORT — reasoning-уровень в резолве и отпечатке`.

---

### Task 4: Пакет, корпус, реестр id

**Files:**
- Create: `src/steward/review_eval/__init__.py`, `src/steward/review_eval/corpus.py`, `eval/corpus/_ids.txt` (пустой с комментарием), `tests/review_eval/__init__.py`, `tests/review_eval/test_corpus.py`
- Modify: `pyproject.toml` (`review-eval = "steward.review_eval.cli:app"` — cli появится в Task 11; до неё скрипт регистрировать в Task 11), `.gitignore` (`eval/runs/`, `eval/cache/`)

**Interfaces (produces):**

```python
@dataclass(frozen=True)
class Match: files: tuple[str, ...]; line_window: int; keywords_any: tuple[str, ...]
@dataclass(frozen=True)
class Defect: id: str; severity: str; file: str; line_hint: int; scenario: str; evidence: tuple[str, ...]; match: Match
@dataclass(frozen=True)
class NonDefect: id: str; file: str; line_hint: int; scenario: str; match: Match   # без severity/evidence — их наличие в YAML non_defects → CorpusError
@dataclass(frozen=True)
class Annotation: status: str; blocking_complete: bool; adjudicated_by: str | None; adjudicated_at: str | None; source: str
@dataclass(frozen=True)
class Case: case_id: str; repo: str; pr: int; base_sha: str; head_sha: str; cls: str; local_args: tuple[str, ...]; expected_outcome: str; annotation: Annotation; defects: tuple[Defect, ...]; non_defects: tuple[NonDefect, ...]; notes: str
class CorpusError(Exception)
def load_case(path: Path) -> Case            # CorpusError на нарушении схемы
def load_corpus(dir: Path) -> list[Case]     # + уникальность case_id/defect id, реестр _ids.txt
def corpus_digest(cases: list[Case]) -> str  # sha256 канонического JSON
def is_gold(case: Case) -> bool              # annotation.status == "adjudicated"
```

Правила валидации (каждое — тест): `schema == "review-eval-case/v1"`; `case_id == f"{repo.split('/')[1]}-{pr}"`; `base_sha`/`head_sha` — 40 hex, различны; `cls ∈ {defective, clean, large}`; `large` требует `local_args` непустой **или** `expected_outcome == "guardrail_rejection"`; `expected_outcome ∈ {verdict, guardrail_rejection}`; `annotation.status ∈ {draft, adjudicated}`, `adjudicated` требует `adjudicated_by`/`adjudicated_at`; `defects[].severity ∈ {blocker, major, minor}`; `non_defects[]` без `severity`/`evidence` (присутствие — ошибка); id формата `D-<repo>-<pr>-<n>` / `NF-…`, уникальны по всему корпусу; каждый id из корпуса присутствует в `_ids.txt`, а id из `_ids.txt`, отсутствующий в корпусе, — допустим (удалённый) и **не может** появиться снова с другим содержимым (реестр хранит `id sha256(defect-json)`; повторное появление с другим хешем → `CorpusError`).

Steps: тесты (валидный кейс из фикстуры YAML; каждое правило → `CorpusError`) → RED → реализация (`yaml.safe_load`, ручная проверка полей, без pydantic) → GREEN → commit `feat(review-eval): корпус — модель кейса, валидация, реестр id`.

---

### Task 5: Bare-кэш и worktree

**Files:** `src/steward/review_eval/cache.py`, `tests/review_eval/test_cache.py`

**Interfaces:**

```python
class CacheError(Exception)
def repo_cache_dir(cache_root: Path, repo: str) -> Path          # <root>/<owner>__<name>.git
def materialize(cache_root: Path, repo: str, shas: Iterable[str], *, local_checkout: Path | None, remote_url: str, git: str = "git") -> None
def has_object(cache_root: Path, repo: str, sha: str, *, git: str = "git") -> bool
@contextmanager
def worktree(cache_root: Path, repo: str, sha: str, dest: Path, *, keep: bool = False, git: str = "git") -> Iterator[Path]
```

`materialize`: если кэша нет — `git clone --bare <local_checkout>` (без `--shared`), иначе ничего; для каждого sha без объекта — `git fetch <local_checkout> <sha>` затем, при неудаче, `git fetch <remote_url> <sha>`; неудача обоих → `CacheError`. `worktree`: `git worktree add --detach <dest> <sha>` от bare-кэша; выход из контекста — `git worktree remove --force` (если не `keep`) и `git worktree prune`. Тесты на фикстурном репо из двух коммитов: materialize из локального пути без сети (подставной `git` в PATH только для `fetch` к URL — падает, чтобы доказать, что сеть не нужна при локальном источнике); `has_object`; worktree видит исторический файл; удаление по выходу; `keep` оставляет. Commit `feat(review-eval): bare-кэш объектов и detached worktree`.

---

### Task 6: Предикат блокировки — зеркало `apply-threshold.sh`

**Files:** `src/steward/review_eval/threshold.py`, `tests/review_eval/test_threshold.py`

**Interfaces:**

```python
def is_blank(value: object) -> bool   # None или строка, пустая после удаления всех \s
def is_blocking(finding: Mapping[str, object]) -> bool
```

`is_blocking`: `severity in {"blocker","major"}` and `confidence == "high"` and not blank(`file`) and not blank(`scenario`) and not blank(`observed_result`) and any(evidence element is dict and not blank(`reason`) and not blank(`file`)). `kind` и `line` не участвуют.

Контрактный тест: таблица из ≥ 14 вердиктов (валидных по схеме, чтобы `apply-threshold.sh` дошёл до порога): базовый блокирующий; confidence medium; file пробельный; scenario пробельный; observed_result пробельный; evidence пустой список; evidence с пустым reason; evidence с пустым file; второй evidence валидный при первом пустом; `kind: file-missing` (с `line: 0`) блокирующий; severity minor; два findings один блокирующий. Для каждого: `subprocess.run(["sh", SCRIPT, "--verdict", path, "--format", "text"])` → `returncode in {0, 1}` и `(returncode == 1) == any(is_blocking(f) for f in findings)`. Commit `feat(review-eval): is_blocking — контрактное зеркало BLOCKING_DEF apply-threshold.sh`.

---

### Task 7: Матчер

**Files:** `src/steward/review_eval/matcher.py`, `tests/review_eval/test_matcher.py`

**Interfaces:**

```python
MATCHER_VERSION = 1
@dataclass(frozen=True)
class Prediction: index: int; finding: Mapping[str, object]      # index — только для отображения
@dataclass(frozen=True)
class Edge: prediction: int; defect_id: str; weight: tuple[int, int, int]   # (kw_hits, evidence_overlap, -abs(dline))
@dataclass(frozen=True)
class MatchResult:
    assigned: dict[int, str]          # prediction index → defect id (TP-кандидат)
    duplicates: dict[int, str]        # prediction → defect, уже назначенный другому
    known_fp: dict[int, str]          # prediction → non_defect id
    unlabeled: tuple[int, ...]        # без рёбер после назначения
    ambiguous: tuple[tuple[tuple[int, ...], tuple[str, ...]], ...]  # компоненты в очередь
    unmatched_defects: tuple[str, ...]
def rules_digest() -> str
def build_edges(preds: Sequence[Prediction], defects: Sequence[Defect]) -> list[Edge]
def assign(edges: Sequence[Edge]) -> tuple[dict[int, str], list[Edge]]   # mutual-best до неподвижной точки; остаток
def match(preds: Sequence[Prediction], defects: Sequence[Defect], non_defects: Sequence[NonDefect]) -> MatchResult
```

`build_edges`: файл prediction (нормализованный: `./` снят, слэши) ∈ `match.files`; `abs(line − line_hint) ≤ line_window`; ≥ 1 keyword (casefold) в `title + scenario + expected_result`; вес `(kw_hits, |evidence-файлы prediction ∩ файлы из defect.evidence|, −abs(dline))`. `assign`: цикл — для каждого prediction лучший по весу (уникальный: единственное ребро с максимальным весом), для каждого defect аналогично; пара назначается только если она лучшая для обоих; удалить **обе вершины** пары и все инцидентные им рёбра; повторять до отсутствия новых назначений. Классификация остатка — по **исходному** множеству рёбер `E0` (не по остатку после удаления): `unlabeled` — prediction без рёбер в `E0`; `duplicates` — неназначенный prediction, у которого в `E0` есть ребро к назначенному defect **и нет** рёбер к неназначенным defect (спека D10: «ребро к уже назначенному» — хотя бы одно); prediction с рёбрами и к назначенному, и к неназначенному defect — в `ambiguous` (вместе со своей компонентой остатка), не в duplicates; остаток рёбер к неназначенным вершинам группируется в компоненты связности → `ambiguous`. `known_fp`: те же правила против `non_defects`, **после** назначения по дефектам (defect приоритетнее), только для prediction, не попавших в assigned/duplicates/ambiguous. Порядок: результат вычисляется как множества; для стабильного вывода сортировать ключи.

Тесты: перефразированная находка (keywords в scenario) → assigned; другой файл → unlabeled; две находки на один defect с разным весом → 1 assigned + 1 duplicate (по `E0`, несмотря на удаление инцидентных рёбер); prediction с рёбрами к назначенному d1 и к неназначенному d2 → ambiguous, не duplicate; `duplicates ∩ unlabeled = ∅` на всех входах property-теста; равные веса на один defect → ambiguous (компонента с 2 pred, 1 defect), `assigned` пуст; цепочка A→d1 лучший для A, но d1 лучший для B, B лучший для d2 — назначаются только взаимно-лучшие (B–d1? нет: d1 лучший для B, B лучший для d2 → ни одной пары mutual → всё в ambiguous) — зафиксировать ожидаемый результат явно; инвариантность: для входов ≤ 4×4 — все перестановки `preds` и `defects` дают равные `MatchResult` (сравнение по множествам); `non_defect` → known_fp; `rules_digest()` меняется при изменении константы `LINE_TOLERANCE_DEFAULT`/весовой функции (тест сравнивает с зафиксированным значением и объясняет, что смена требует бампа `MATCHER_VERSION`). Commit `feat(review-eval): матчер — рёбра, mutual-best назначение, дубликаты, очередь`.

---

### Task 8: Раннер

**Files:** `src/steward/review_eval/runner.py`, `tests/review_eval/test_runner.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class Variant: harness: str; model: str; effort: str | None
def parse_variant(text: str) -> Variant           # "claude:claude-opus-5[:high]"; ValueError
@dataclass(frozen=True)
class KitUnderTest: kit_dir: Path; prompt: Path; schema: Path; commit: str; digests: dict[str, str]
def kit_under_test(steward_root: Path) -> KitUnderTest   # digests: prompt, schema, apply-threshold, local.sh, collect-context, harness-claude
@dataclass(frozen=True)
class RunResult:  # → result.json
    case_id: str; variant: str; repetition_id: int; exit_code: int; outcome: str   # verdict|guardrail_rejection|config_failure|mechanical_failure|invalid_verdict|unexpected_outcome
    wall_clock_s: float; verdict_path: str | None; usage_path: str | None; cost_status: str   # available|unavailable
    requested_effort: str | None; stdout_path: str; stderr_path: str
def run_case(case: Case, variant: Variant, rep: int, *, kit: KitUnderTest, cache_root: Path, out_dir: Path, env_base: Mapping[str, str], keep_worktrees: bool, git: str = "git") -> RunResult
def run_all(cases, variants, *, repetitions, out_dir, kit, cache_root, jobs=1, rerun=False, keep_worktrees=False) -> RunManifest   # пишет run.json
```

`run_case`: worktree (Task 5) → env = `env_base` без `REVIEW_CMD`/`REVIEW_*` + `REVIEW_KIT_DIR`, `REVIEW_PROMPT`, `REVIEW_SCHEMA`, `REVIEW_HARNESS`, `REVIEW_MODEL`, `REVIEW_EFFORT` (если есть), `REVIEW_VERDICT_OUT`, `REVIEW_USAGE_OUT` → `subprocess.run(["sh", kit_dir/"local.sh", "--base", base, "--head", head, "--format", "text", *local_args], cwd=worktree, capture_output=True, text=True)` с `time.monotonic()` вокруг → классификация (порядок проверок важен; «sidecar есть» = файл `REVIEW_VERDICT_OUT` существует и непуст — он пишется `local.sh` **до** порога, поэтому наличие sidecar означает «ревьюер отработал»):
   - код 0/1 **и** sidecar есть **и** проходит `review-schema.json` → `verdict`;
   - код 2 **и** в stderr подстрока `диф больше поддерживаемого` → `guardrail_rejection`;
   - код 2 **и** sidecar есть → `invalid_verdict` (вердикт получен, но отвергнут порогом: правила `apply-threshold.sh` строже схемы — например `kind: file-missing` с `line > 0`; это ошибка **модели**, не конфигурации);
   - код 0/1 **и** sidecar есть, но не проходит схему → `invalid_verdict`;
   - код 2 без sidecar → `config_failure`; код 3 → `mechanical_failure`; код 0/1 без sidecar → `mechanical_failure` (кит обещает sidecar до порога — его отсутствие при успехе значит, что ревьюер не отработал по контракту);
   - сверка с `case.expected_outcome` → `unexpected_outcome` при несовпадении (`verdict`↔`verdict`, `guardrail_rejection`↔`guardrail_rejection` — совпадения; `invalid_verdict` — всегда ожидаемым не бывает, но метрики валидности по нему считаются).
   Поле `reviewer_ran: bool` (= sidecar есть) пишется в `result.json` — знаменатель `valid_verdict_rate` (Task 9). `cost_status = available` если `usage.json` существует и `total_cost_usd` не `null`. Идемпотентность: готовый `result.json` пропускается без `rerun`. `run.json`: kit, `tools` (`claude --version`, `codex --version`, `git --version` — `unavailable` при отсутствии), variants, `corpus_digest`, `matcher_version`, `rules_digest`, `started/finished`, `jobs`, `repetitions`.

Тесты: подставной `local.sh` в временном kit_dir (записывает `REVIEW_*` в файл, пишет `verdict.json`/`usage.json` по путям из env, выходит заданным кодом; вариант со stderr «диф больше поддерживаемого» и кодом 2) + фикстурный репо → проверки: env без `REVIEW_CMD`; артефакты на местах; классификация каждого исхода, включая «код 2 + sidecar» → `invalid_verdict` и «код 2 без sidecar» → `config_failure`, «код 0 без sidecar» → `mechanical_failure`; `wall_clock_s > 0`; `cost_status unavailable` без usage; `requested_effort` записан; повторный `run_all` пропускает готовые; объект вне кэша → `CacheError`→ код 2 без сети (подставной `git`, падающий на `fetch`); worktree на историческом SHA (стаб читает файл, существующий только в первом коммите). Commit `feat(review-eval): раннер — изоляция, sidecar, исходы, run.json`.

---

### Task 9: Метрики и bootstrap

**Files:** `src/steward/review_eval/metrics.py`, `tests/review_eval/test_metrics.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class CaseEval: case: Case; result: RunResult; findings: list[Mapping]; match: MatchResult | None
def evaluate_case(case, result, verdict: Mapping | None, worktree_files: Callable[[str], int | None]) -> CaseEval   # worktree_files(path) → число строк или None (для resolvable_evidence)
def metrics_for_variant(evals: Sequence[CaseEval]) -> dict   # см. спека §9; каждое значение {"value": float|None, "numerator": int, "denominator": int} + "status": ok|pending_adjudication|no_gold
def bootstrap_ci(values_by_case: Sequence[tuple[int, int]], *, n: int = 1000, seed: int = 0) -> tuple[float, float]
def compare(metrics_a: dict, metrics_b: dict, paired: Sequence[tuple[CaseEval, CaseEval]], *, seed: int = 0) -> dict
```

Определения — по спеке §9 буквально (`valid_verdict_rate` = исходы `verdict` / прогоны с `reviewer_ran: true`, т. е. по всем прогонам, где ревьюер отдал вердикт, включая `invalid_verdict` с кодом 2); блокирующее предсказание — `is_blocking`; TP только если назначенный defect имеет severity ∈ {blocker, major}; `precision` отсутствует (не `None` — ключа нет) при непустой очереди, `status: pending_adjudication`; recall/false-block — только `blocking_complete`; `resolvable_evidence_rate` через `worktree_files`; эксплуатационные по всем adjudicated; стоимость по всем прогонам с `available`, `cost_unavailable_cases` отдельно; `cost_usd_mean_per_case` помечается `partial: true` при хотя бы одном unavailable. Тесты — рукотворные наборы с известными числами для каждой формулы; bootstrap детерминирован по seed и лежит в [0,1]. Commit `feat(review-eval): метрики с знаменателями, статусы, bootstrap, compare`.

---

### Task 10: Отчёт и очередь

**Files:** `src/steward/review_eval/report.py`, `tests/review_eval/test_report.py`

`write_report(run_dir, metrics_by_variant, evals) -> None` — `metrics.json` (канонический JSON, sort_keys) и `report.md`: таблица «метрика | вариант A | вариант B …» с `value (num/den)`, статус прогона, список кейсов с исходами; `adjudication-queue.md`: по кейсу и варианту — unlabeled предсказания (title/file/line/severity) и ambiguous-компоненты с кандидатами. Тест: снапшот-сравнение с эталоном (строгий текст), пустая очередь → строка «очередь пуста». Commit `feat(review-eval): report.md, metrics.json, adjudication-queue.md`.

---

### Task 11: CLI, кандидаты из истории, docs, корпус-черновики

**Files:** `src/steward/review_eval/cli.py`, `src/steward/review_eval/candidates.py`, `tests/review_eval/test_cli.py`, `tests/review_eval/test_candidates.py`, `pyproject.toml` (`review-eval = "steward.review_eval.cli:app"`), `docs/review-eval.md`, `eval/corpus/*.yaml` (черновики §13 спеки), `TODO.md`

- `cli.py` (Typer): `corpus validate`, `corpus candidates --repo --pr [--out]`, `corpus materialize [--cache eval/cache] [--from-local ../]`, `run …` (флаги по спеке §11), `metrics <run_dir>`, `compare <a> <b>`; коды выхода по спеке §11. Тесты через `typer.testing.CliRunner` с подставными модулями (monkeypatch `runner.run_all` и т. п.).
- `candidates.py`: `gh api repos/<repo>/pulls/<pr>/reviews` (через `subprocess`, тестируется с подставным `gh`), парсинг тел ai-prosto по формату `apply-threshold.sh` (`### [severity] title — `file:line``, строки «Сценарий/Наблюдаемое/Evidence/confidence»), маркер `head=`; `git log` PR-коммитов после ревью (через кэш) → `candidate_status`; вывод черновика YAML с `annotation.status: draft`, `source: history-proxy`, id `D-<repo>-<pr>-<n>`, `match` из file/line/keywords заголовка (3–5 самых длинных слов). Тест: фикстурное тело ревью → ожидаемый YAML.
- `docs/review-eval.md`: как размечать кейс (поля, что значит `blocking_complete`), как материализовать, запускать, читать отчёт, вести очередь, делать P3-сравнение; предупреждение про память при `--jobs > 1`.
- `eval/corpus/`: черновики через `corpus candidates` для steward#155, #157, #159 и трёх чистых (#152, #156, #161) — `draft`, разметка — владельцу.
- `TODO.md`: пункт `review-kit-eval-harness` остаётся `[ ]` с пометкой «код влит PR #…, закрывается живым прогоном на gold ≥ 10»; `review-kit-verdict-corpus` — переоценка по спеке §14.
- Полный гейт, `sh scripts/review/local.sh`, PR.

---

## Self-review (при написании)

- Покрытие спеки: D4 (T1, T2), D12/D6 (T2), D13 (T2, T3), корпус §5 (T4, T11), кэш/офлайн §5–§6 (T5, T8), предикат §9 (T6), матчер §8/D10 (T7), раннер §6 (T8), метрики §9/D8/D9/D11 (T9), артефакты §10 (T8, T10), CLI §11 (T11), тесты §12 (по задачам), корпус §13 и docs §14 (T11).
- Плейсхолдеры: код кита в T1–T3 полный; для T4–T11 даны сигнатуры и правила — исполнитель пишет тела по спеке (прозы достаточно для однозначной реализации; спорные места — веса матчера и классификация исходов — зафиксированы формулами).
- Согласованность имён: `Case/Defect/Match/Annotation` (T4) используются в T7–T9; `RunResult`, `Variant`, `KitUnderTest` (T8) — в T9–T11; `MatchResult` (T7) — в T9–T10; `is_blocking` (T6) — в T9.
