# Review-kit harness layer (claude|codex) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** свежевендоренный review-kit проводит ревью claude-ревьюером по одному env-переключателю (`REVIEW_HARNESS=claude`), включая pre-push хук, без внешних переходников; умолчание и отпечаток codex не меняются.

**Architecture:** новый член кита `scripts/review/harness-claude` (POSIX sh, `100755`) говорит codex-диалектом снаружи и `claude -p` внутри, конверт разбирает `jq`. `local.sh` резолвит `REVIEW_CMD` → `REVIEW_MODEL` → `REVIEW_HARNESS` в строку `review_cmd` (она же компонента отпечатка), подмешивает `$kit_dir` в `PATH` только префиксом у вызова ревьюера и печатает результат резолва по `--print-review-cmd`. `checksum.sh` получает адаптер переходным членом `?path`.

**Tech Stack:** POSIX sh (`/bin/sh` macOS = bash 3.2, CI = dash), `jq`, `git`; тесты — pytest в `tests/review/` (подставные `claude`/`git` в `PATH`).

**Spec:** `docs/superpowers/specs/2026-09-14-review-kit-harness-layer-design.md` (читать вместе с базовой `docs/superpowers/specs/2026-08-21-codex-review-kit-design.md` §5, §7, §9).

## Global Constraints

- Кит — только POSIX shell и стандартные утилиты; **ни одного файла, требующего Python** (базовая спека §5). Разбор JSON — `jq`.
- Умолчание `codex`; строка `codex exec` в отпечатке **побайтно** не меняется (D1).
- Кит читает только `REVIEW_HARNESS` / `REVIEW_MODEL` / `REVIEW_CMD` из окружения процесса; никакого файлового или git-конфига (D2).
- Непустой `REVIEW_CMD` побеждает целиком; `REVIEW_CMD=""` — как unset (D5). `REVIEW_MODEL=""` и `REVIEW_HARNESS=""` — отказ кодом 2 (D6, §4 п. 5).
- `PATH` расширяется **только** префиксом у вызова ревьюера: `PATH="$kit_dir:$PATH" $review_cmd …` (D7). Никакого `export PATH`.
- Литералы `codex-terminal-review`, `codex-terminal-review-verdict/v1`, `codex-terminal-review-fingerprint-v1` не трогать (D4).
- Коды: 0 чисто / 1 находки / 2 конфигурация / 3 ревьюер не отработал (базовая спека §7).
- Каждый тест кита гоняется в `sh`; там, где есть параметризация `INTERPRETERS`, — и в `dash`.
- Соседние репо (devtools) не редактировать: handoff — issue с лейблом `inbox`.
- Перед каждым коммитом: `uv run ruff format . && uv run ruff check .`; перед PR — `uv run pytest -q`, `uv run pyrefly check`.
- Ветка: `feat/review-kit-harness-layer` от `master`. Коммиты — с `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## Карта файлов

| Файл | Роль |
|---|---|
| `scripts/review/harness-claude` (create, `chmod +x`) | адаптер: codex-диалект → `claude -p`, разбор конверта `jq`, атомарная запись вердикта |
| `scripts/review/local.sh` (modify: строки 26–32, 50–54, 56–92, ~99, ~644) | резолв харнесса, `--print-review-cmd`, префикс `PATH` у вызова |
| `scripts/review/checksum.sh` (modify: строка 81) | переходный член `?scripts/review/harness-claude` |
| `tests/review/test_harness_claude.py` (create) | контракт адаптера с подставным `claude` |
| `tests/review/test_local.py` (modify: добавить хелпер + тесты в конец) | резолв, отпечаток, `--print-review-cmd`, D7, e2e |
| `tests/review/test_checksum.py` (modify: в конец) | переходный член в инвентаре по умолчанию, режим файла |
| `README.md` (modify: после блока команд в «Локальное ревью перед пушем») | env-переменные, `--print-review-cmd`, оговорка про окружение хука |
| `docs/superpowers/specs/2026-08-21-codex-review-kit-design.md` (modify: §5 список, §7 `local.sh`) | состав и интерфейсы |
| `TODO.md` (modify: пункт `review-kit-harness-layer`) | закрытие + handoff |

---

### Task 1: Адаптер `harness-claude` — аргументы и префлайты

**Files:**
- Create: `scripts/review/harness-claude`
- Test: `tests/review/test_harness_claude.py`

**Interfaces:**
- Produces: исполняемый `scripts/review/harness-claude` с argv `--model <m> --sandbox read-only --output-schema <путь> --output-last-message <путь> -`, промпт со stdin; коды 0/2/3 (спека §5). Task 2–4 зовут его по `PATH` голым именем `harness-claude`.

- [ ] **Step 1: Написать стенд и первые падающие тесты (аргументы, префлайты)**

```python
"""Тесты scripts/review/harness-claude — адаптер claude CLI под codex-диалект кита.

Свойство одно: адаптер говорит codex-диалектом снаружи (ровно те флаги, что
шлёт local.sh) и claude-диалектом внутри, а вердиктом становится ТОЛЬКО
structured_output успешного ответа. Любой другой исход — не-0, который кит
превращает в код 3; молчаливый approve при сломанном ревьюере невозможен по
построению. `claude` подставной: записывает argv и промпт, отдаёт конверт.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "scripts" / "review" / "harness-claude"

INTERPRETERS = [
    "sh",
    pytest.param(
        "dash",
        marks=pytest.mark.skipif(
            shutil.which("dash") is None,
            reason="dash не найден в PATH — покрытие уже, чем в CI",
        ),
    ),
]

CLAUDE_STUB = """#!/bin/sh
# Подставной claude: argv — в $CLAUDE_STUB_ARGV (по слову на строку), промпт
# (stdin) — в $CLAUDE_STUB_PROMPT, ответ — содержимое $CLAUDE_STUB_ENVELOPE;
# $CLAUDE_STUB_EXIT — завершиться этим кодом вместо ответа.
printf '%s\\n' "$@" > "$CLAUDE_STUB_ARGV"
cat > "$CLAUDE_STUB_PROMPT"
if [ -n "${CLAUDE_STUB_EXIT:-}" ]; then
    echo "claude stub: падаю по просьбе" >&2
    exit "$CLAUDE_STUB_EXIT"
fi
cat "$CLAUDE_STUB_ENVELOPE"
"""

VERDICT_OK = {"findings": [], "note": "stub"}


def envelope(structured: object, *, subtype: str = "success", is_error: bool = False) -> str:
    return json.dumps(
        {"type": "result", "subtype": subtype, "is_error": is_error, "structured_output": structured}
    )


class Stand:
    """Каталог с подставным claude + настоящим jq в PATH, схема и пути вывода."""

    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        stub = self.bin / "claude"
        stub.write_text(CLAUDE_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.schema = tmp_path / "schema.json"
        self.schema.write_text('{"type":"object"}\n', encoding="utf-8")
        self.out = tmp_path / "out"
        self.out.mkdir()
        self.verdict = self.out / "verdict.json"
        self.argv = tmp_path / "argv.txt"
        self.prompt = tmp_path / "prompt.txt"
        self.env_file = tmp_path / "envelope.json"

    def run(
        self,
        *args: str,
        envelope_text: str = envelope(VERDICT_OK),
        interp: str = "sh",
        stdin: str = "ПРОМПТ\n",
        path: str | None = None,
        claude_exit: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.env_file.write_text(envelope_text, encoding="utf-8")
        env = dict(os.environ)
        env["PATH"] = path if path is not None else f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        env["CLAUDE_STUB_ARGV"] = str(self.argv)
        env["CLAUDE_STUB_PROMPT"] = str(self.prompt)
        env["CLAUDE_STUB_ENVELOPE"] = str(self.env_file)
        if claude_exit is not None:
            env["CLAUDE_STUB_EXIT"] = claude_exit
        return subprocess.run(
            [interp, str(ADAPTER), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
        )

    def codex_args(self, *extra: str) -> list[str]:
        return [
            "--sandbox", "read-only",
            "--output-schema", str(self.schema),
            "--output-last-message", str(self.verdict),
            *extra,
            "-",
        ]


@pytest.mark.parametrize("bad", [["--frobnicate"], ["--model"], ["--output-schema"]])
def test_unknown_or_bare_flag_is_config_error(tmp_path: Path, bad: list[str]) -> None:
    """Неизвестный флаг и голый флаг без значения — код 2, не молчаливое
    игнорирование: кит нового поколения мог добавить семантику, которую адаптер
    не понимает, и «продолжить как понял» дало бы ревью не на тех условиях."""
    s = Stand(tmp_path)
    res = s.run(*bad)
    assert res.returncode == 2, res.stderr
    assert not s.verdict.exists()


@pytest.mark.parametrize(
    "args, reason",
    [
        (["--sandbox", "workspace-write", "--output-schema", "S", "--output-last-message", "V", "-"], "read-only"),
        (["--sandbox", "read-only", "--output-last-message", "V", "-"], "--output-schema"),
        (["--sandbox", "read-only", "--output-schema", "S", "-"], "--output-last-message"),
        (["--sandbox", "read-only", "--output-schema", "S", "--output-last-message", "V"], "stdin"),
    ],
)
def test_contract_violations_are_config_errors(tmp_path: Path, args: list[str], reason: str) -> None:
    s = Stand(tmp_path)
    args = [str(s.schema) if a == "S" else str(s.verdict) if a == "V" else a for a in args]
    res = s.run(*args)
    assert res.returncode == 2, res.stderr
    assert reason in res.stderr


def test_unreadable_schema_is_config_error(tmp_path: Path) -> None:
    """codex_args() всегда подставляет читаемую схему — здесь путь задан явно."""
    s = Stand(tmp_path)
    res = s.run(
        "--sandbox", "read-only",
        "--output-schema", str(tmp_path / "нет.json"),
        "--output-last-message", str(s.verdict),
        "-",
    )
    assert res.returncode == 2, res.stderr
    assert "схема" in res.stderr


def test_missing_claude_in_path_is_config_error(tmp_path: Path) -> None:
    """Нет бинаря — конфигурация (код 2), не «ревьюер не отработал»."""
    s = Stand(tmp_path)
    jq_dir = Path(shutil.which("jq") or "").parent
    res = s.run(*s.codex_args(), path=str(jq_dir))
    assert res.returncode == 2, res.stderr
    assert "claude" in res.stderr


def test_missing_jq_in_path_is_config_error_not_127(tmp_path: Path) -> None:
    """Без префлайта отсутствие jq выродилось бы в 127 → код 3 кита, вопреки
    заявленному контракту «конфигурация → 2» (поправка владельца к спеке)."""
    s = Stand(tmp_path)
    res = s.run(*s.codex_args(), path=str(s.bin))  # только подставной claude, без jq
    assert res.returncode == 2, res.stderr
    assert "jq" in res.stderr
```

- [ ] **Step 2: Прогнать — убедиться, что падает потому, что адаптера нет**

Run: `uv run pytest tests/review/test_harness_claude.py -q`
Expected: все FAIL; в stderr `sh: …/harness-claude: No such file or directory` (код 127 / 2 от sh), не ошибки самого стенда.

- [ ] **Step 3: Написать адаптер целиком (аргументы, префлайты, вызов, разбор, атомарная запись)**

Создать `scripts/review/harness-claude`:

```sh
#!/bin/sh
# harness-claude — адаптер claude CLI под codex-диалект review-kit.
#
# Зачем: local.sh зовёт ревьюера строкой `$review_cmd --sandbox read-only
# --output-schema <s> --output-last-message <v> - < prompt.txt` — диалект
# `codex exec`. REVIEW_CMD — задуманная точка подмены, но флаги вокруг неё
# codex-специфичны, поэтому нужна не другая строка, а программа, которая
# говорит на codex-диалекте снаружи и на claude-диалекте внутри. Файл — член
# кита (спека 2026-09-14, D3): переходник devtools покрывал только
# review-pr.sh, а хук и ручной local.sh оставались на codex.
#
# Единственный член кита, который запускается по PATH голым именем, а не
# `sh "$kit_dir/…"` (D7): строка в отпечатке обязана быть тем, что реально
# запускается, и одновременно машинно-независимой. Отсюда — без расширения
# .sh и с битом исполнения; потерянный бит ловит префлайт local.sh.
#
# Контракт выхода — как у codex exec для кита: не-0 → кит печатает «ревьюер
# не отработал» и выходит кодом 3; пустой вердикт кит ловит сам. Молчаливый
# approve при сломанном ревьюере невозможен по построению. Коды: 0 —
# вердикт записан; 2 — аргументы/префлайт; 3 — claude не отработал или
# ответ негоден. Для кита 2 и 3 равнозначны (любой не-0 → его код 3), но
# различие бесплатно и полезно при ручном вызове.
#
# Литерал протокола `codex-terminal-review` в маркерах ревью НЕ
# переименовывается: это имя протокола, не бинаря (D4).
#
# Read-only не «на слово CLI»: --restricted снимает Bash/REPL/WebFetch и
# игнорирует пользовательские settings, --tools оставляет ровно чтение
# (Read/Glob/Grep), --permission-prompts none автоматически отказывает всему
# остальному, --strict-mcp-config отрезает MCP оператора,
# --no-session-persistence не сорит сессиями в целевом репо.
set -eu

fail() {
    _code="$1"; shift
    echo "harness-claude: $*" >&2
    exit "$_code"
}

model="claude-opus-5"
sandbox=""
schema=""
verdict=""
stdin_marker=0
while [ $# -gt 0 ]; do
    case "$1" in
        --model)
            [ $# -ge 2 ] || fail 2 "--model требует значение"
            model="$2"; shift 2 ;;
        --sandbox)
            [ $# -ge 2 ] || fail 2 "--sandbox требует значение"
            sandbox="$2"; shift 2 ;;
        --output-schema)
            [ $# -ge 2 ] || fail 2 "--output-schema требует путь"
            schema="$2"; shift 2 ;;
        --output-last-message)
            [ $# -ge 2 ] || fail 2 "--output-last-message требует путь"
            verdict="$2"; shift 2 ;;
        -) stdin_marker=1; shift ;;
        # Неизвестный флаг — отказ, не молчаливое игнорирование: кит нового
        # поколения мог добавить семантику, которую адаптер не понимает, и
        # «продолжить как понял» дало бы ревью не на тех условиях.
        *) fail 2 "неизвестный аргумент: $1" ;;
    esac
done
[ "$sandbox" = "read-only" ] \
    || fail 2 "поддержан только --sandbox read-only (получено: '${sandbox}')"
[ -n "$schema" ] || fail 2 "--output-schema обязателен"
[ -r "$schema" ] || fail 2 "схема нечитаема: $schema"
[ -n "$verdict" ] || fail 2 "--output-last-message обязателен"
[ "$stdin_marker" -eq 1 ] || fail 2 "ожидается '-' (промпт со stdin)"

# Префлайты — по образцу jq/sha256sum-префлайтов кита: отсутствие бинаря —
# конфигурация (код 2), не «ревьюер не отработал». Без префлайта jq его
# отсутствие выродилось бы в 127 → код 3 кита, вопреки контракту.
command -v claude >/dev/null 2>&1 || fail 2 "claude не найден в PATH"
command -v jq >/dev/null 2>&1 \
    || fail 2 "jq не найден в PATH — нужен для разбора ответа claude"

# Временный файл вердикта — В КАТАЛОГЕ целевого файла: `mv` через границу
# файловой системы перестаёт быть rename, и атомарность теряется. Конверт
# claude — тоже в файл, не в переменную: `$(...)` съедает хвостовые переводы
# строк, а разбирать удобнее файл. Оба убираются через trap на любом исходе;
# после успешного `mv` временного файла уже нет, и rm -f безвреден.
verdict_dir=$(dirname "$verdict")
tmp=$(mktemp "$verdict_dir/.verdict.XXXXXX") \
    || fail 2 "не удалось создать временный файл в $verdict_dir"
envelope=$(mktemp) || fail 2 "не удалось создать временный файл для ответа"
trap 'rm -f "$tmp" "$envelope"' EXIT

# Промпт идёт со stdin (как и у codex) — диф не попадает в argv.
# stderr claude проходит насквозь в reviewer.err кита.
set +e
claude -p --model "$model" \
    --json-schema "$(cat "$schema")" \
    --output-format json \
    --restricted --strict-mcp-config --no-session-persistence \
    --permission-prompts none \
    --tools Read Glob Grep > "$envelope"
claude_code=$?
set -e
[ "$claude_code" -eq 0 ] || fail 3 "claude завершился кодом $claude_code"

# Разбор конверта: только успешный ответ с непустым объектом
# structured_output становится вердиктом. `jq -e` даёт не-0 и на false/null
# фильтра, и на битом JSON (код 2 парсера) — оба случая один класс.
if ! jq -e '
        type == "object"
        and (.is_error | not)
        and .subtype == "success"
        and (.structured_output | type == "object")
        and (.structured_output | length > 0)
    ' "$envelope" >/dev/null 2>&1; then
    fail 3 "ответ claude без валидного structured_output"
fi
# Каноническая сериализация — `jq -c`: байты определяет jq, а не контракт;
# читатели сравнивают JSON-семантически.
jq -c '.structured_output' "$envelope" > "$tmp" \
    || fail 3 "не удалось извлечь structured_output"
mv "$tmp" "$verdict"
```

Затем: `chmod +x scripts/review/harness-claude`.

- [ ] **Step 4: Прогнать тесты Task 1 — зелёные**

Run: `uv run pytest tests/review/test_harness_claude.py -q`
Expected: PASS все.

- [ ] **Step 5: Коммит**

```bash
uv run ruff format . && uv run ruff check .
git add scripts/review/harness-claude tests/review/test_harness_claude.py
git commit -m "feat(review-kit): адаптер harness-claude — аргументы, префлайты claude/jq

Член кита по спеке 2026-09-14 (D3): codex-диалект снаружи, claude -p
внутри. Неизвестный флаг, --sandbox != read-only, отсутствие схемы,
вердикта, '-', claude или jq в PATH — код 2 с причиной.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Адаптер — вызов claude, разбор конверта, атомарная запись

**Files:**
- Modify: `scripts/review/harness-claude` (уже написан целиком в Task 1 — здесь только доказывается тестами; если тест красный, чинить адаптер, не тест)
- Test: `tests/review/test_harness_claude.py`

**Interfaces:**
- Consumes: `Stand`, `envelope()`, `VERDICT_OK` из Task 1.
- Produces: доказанный контракт: вердикт = `jq -c .structured_output`; на любой негодный ответ — не-0, файла вердикта и `.verdict.*` в его каталоге нет.

- [ ] **Step 1: Дописать тесты боевого пути и негодных ответов**

Добавить в конец `tests/review/test_harness_claude.py`:

```python
# --- Боевой путь: argv claude, промпт, вердикт ---------------------------------


@pytest.mark.parametrize("interp", INTERPRETERS)
def test_success_writes_structured_output_and_uses_readonly_claude_flags(
    tmp_path: Path, interp: str
) -> None:
    """Вердикт JSON-семантически равен structured_output; claude получил ровно
    read-only набор флагов и промпт со stdin (диф не в argv)."""
    s = Stand(tmp_path)
    res = s.run(*s.codex_args("--model", "claude-opus-5"), interp=interp, stdin="ДИФ-В-STDIN\n")

    assert res.returncode == 0, res.stderr
    assert json.loads(s.verdict.read_text(encoding="utf-8")) == VERDICT_OK
    argv = s.argv.read_text(encoding="utf-8").splitlines()
    assert argv[:3] == ["-p", "--model", "claude-opus-5"]
    for flag in (
        "--json-schema", "--output-format", "json", "--restricted",
        "--strict-mcp-config", "--no-session-persistence", "--permission-prompts", "none",
        "--tools", "Read", "Glob", "Grep",
    ):
        assert flag in argv, flag
    assert "ДИФ-В-STDIN" not in " ".join(argv)
    assert s.prompt.read_text(encoding="utf-8") == "ДИФ-В-STDIN\n"
    # Схема передаётся СОДЕРЖИМЫМ файла; точная форма пробелов — не контракт.
    assert json.loads(argv[argv.index("--json-schema") + 1]) == {"type": "object"}


def test_default_model_is_opus_5(tmp_path: Path) -> None:
    s = Stand(tmp_path)
    res = s.run(*s.codex_args())
    assert res.returncode == 0, res.stderr
    argv = s.argv.read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("--model") + 1] == "claude-opus-5"


def test_verdict_is_canonical_jq_compact(tmp_path: Path) -> None:
    """Каноническая сериализация — `jq -c`: без пробелов, одной строкой."""
    s = Stand(tmp_path)
    res = s.run(*s.codex_args(), envelope_text=envelope({"findings": [], "note": "x y"}))
    assert res.returncode == 0, res.stderr
    text = s.verdict.read_text(encoding="utf-8")
    assert text == '{"findings":[],"note":"x y"}\n'


# --- Негодные ответы: не-0 и никакого вердикта ---------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        envelope(VERDICT_OK, is_error=True),
        envelope(VERDICT_OK, subtype="error_max_turns"),
        envelope(None),
        envelope({}),
        envelope("строка вместо объекта"),
        '{"type":"result","subtype":"success","is_error":false}',
        "это не JSON {",
        "",
    ],
)
@pytest.mark.parametrize("interp", INTERPRETERS)
def test_bad_envelope_is_failure_without_verdict(tmp_path: Path, bad: str, interp: str) -> None:
    s = Stand(tmp_path)
    res = s.run(*s.codex_args(), envelope_text=bad, interp=interp)
    assert res.returncode == 3, res.stderr
    assert "structured_output" in res.stderr
    assert not s.verdict.exists()
    assert list(s.out.glob(".verdict.*")) == [], "осиротевший временный файл"


def test_claude_nonzero_exit_is_failure_with_stderr_passthrough(tmp_path: Path) -> None:
    s = Stand(tmp_path)
    res = s.run(*s.codex_args(), claude_exit="7")
    assert res.returncode == 3, res.stderr
    assert "кодом 7" in res.stderr
    assert "падаю по просьбе" in res.stderr
    assert not s.verdict.exists()
    assert list(s.out.glob(".verdict.*")) == []


def test_temp_file_lives_next_to_verdict(tmp_path: Path) -> None:
    """Временный файл — в каталоге целевого: только тогда mv — rename, а не
    копирование через границу ФС. Подставной claude «зависает» ровно настолько,
    чтобы увидеть .verdict.* рядом с целевым путём."""
    s = Stand(tmp_path)
    probe = tmp_path / "probe.txt"
    stub = s.bin / "claude"
    stub.write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        f'ls -a "{s.out}" > "{probe}"\n'
        f'cat "{s.env_file}"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    res = s.run(*s.codex_args())
    assert res.returncode == 0, res.stderr
    assert ".verdict." in probe.read_text(encoding="utf-8")
    assert list(s.out.glob(".verdict.*")) == []
```

- [ ] **Step 2: Прогнать — все новые тесты должны быть зелёными сразу (адаптер уже полный); если какой-то красный — это дефект адаптера**

Run: `uv run pytest tests/review/test_harness_claude.py -q`
Expected: PASS. Если `test_temp_file_lives_next_to_verdict` красный — проверить, что `mktemp` получает шаблон `"$verdict_dir/.verdict.XXXXXX"`, а не умолчание `$TMPDIR`.

- [ ] **Step 3: Тест режима файла в дереве git**

Добавить в конец `tests/review/test_harness_claude.py`:

```python
def test_adapter_is_executable_in_git_tree() -> None:
    """Бит исполнения зафиксирован в ДЕРЕВЕ (100755), не в чекауте: адаптер
    запускается по PATH голым именем (D7), и потерянный при вендоринге бит
    ловит префлайт local.sh, а не copy-integrity (checksum сверяет байты)."""
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-s", "scripts/review/harness-claude"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out.startswith("100755 "), out
```

Run: `uv run pytest tests/review/test_harness_claude.py::test_adapter_is_executable_in_git_tree -q`
Expected: PASS (файл добавлен с `chmod +x` до `git add` в Task 1; если FAIL — `git update-index --chmod=+x scripts/review/harness-claude`).

- [ ] **Step 4: Коммит**

```bash
uv run ruff format . && uv run ruff check .
git add tests/review/test_harness_claude.py scripts/review/harness-claude
git commit -m "test(review-kit): harness-claude — боевой путь, негодные конверты, атомарность, режим 100755

Вердикт = jq -c .structured_output (JSON-семантическое равенство в тестах);
is_error / не-success / пустой или не-объект structured_output / битый JSON /
не-0 claude → код 3 без вердикта и без осиротевшего .verdict.*; временный
файл — в каталоге целевого.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `local.sh` — резолв харнесса и префикс `PATH` у вызова

**Files:**
- Modify: `scripts/review/local.sh:26-32` (блок `review_cmd`), `scripts/review/local.sh:644` (вызов ревьюера)
- Test: `tests/review/test_local.py`

**Interfaces:**
- Consumes: `scripts/review/harness-claude` (Task 1).
- Produces: переменная `review_cmd` в `local.sh` по таблице спеки §6; коды 2 по §4; вызов `PATH="$kit_dir:$PATH" $review_cmd …`. Task 4 добавляет `--print-review-cmd` поверх этого резолва.

- [ ] **Step 1: Хелпер и падающие тесты резолва через `--fingerprint-only`**

Добавить в конец `tests/review/test_local.py`:

```python
# --- Харнесс ревьюера: REVIEW_CMD > REVIEW_MODEL > REVIEW_HARNESS (спека 2026-09-14)


def make_repo_with_diff(tmp_path: Path) -> Path:
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    return local


def run_local_env(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    kit_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Как run_local, но БЕЗ REVIEW_CMD: проверяется резолв харнесса, а
    REVIEW_CMD его выключает целиком. Переменные харнесса приходят только
    через `env`; всё, чего в `env` нет, из окружения теста вычищается."""
    base = {
        k: v for k, v in os.environ.items()
        if k not in ("REVIEW_CMD", "REVIEW_HARNESS", "REVIEW_MODEL")
    }
    base["REVIEW_KIT_DIR"] = str(kit_dir or ROOT / "scripts" / "review")
    base["REVIEW_SCHEMA"] = str(ROOT / ".github" / "codex" / "review-schema.json")
    base["REVIEW_PROMPT"] = str(ROOT / ".github" / "codex" / "review-prompt.md")
    if env:
        base.update(env)
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        cwd=str(cwd or repo),
        capture_output=True,
        text=True,
        env=base,
    )


def fingerprint(repo: Path, env: dict[str, str] | None = None, **kw: object) -> str:
    res = run_local_env(repo, "--fingerprint-only", env=env, **kw)  # type: ignore[arg-type]
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout.strip()


def test_default_fingerprint_equals_explicit_codex_exec_and_is_unchanged(tmp_path: Path) -> None:
    """D1: умолчание — побайтно `codex exec`. Доказательство через отпечаток:
    он включает review_cmd, и равенство с явным REVIEW_CMD='codex exec'
    означает, что строка умолчания не изменилась — опубликованные
    наследования остаются валидными."""
    repo = make_repo_with_diff(tmp_path)
    assert fingerprint(repo) == fingerprint(repo, {"REVIEW_CMD": "codex exec"})
    assert fingerprint(repo) == fingerprint(repo, {"REVIEW_HARNESS": "codex"})


def test_empty_review_cmd_behaves_as_unset(tmp_path: Path) -> None:
    """D5: `REVIEW_CMD=""` — как unset (`${REVIEW_CMD:-…}` до патча)."""
    repo = make_repo_with_diff(tmp_path)
    assert fingerprint(repo, {"REVIEW_CMD": ""}) == fingerprint(repo)


@pytest.mark.parametrize(
    "env, equivalent_cmd",
    [
        ({"REVIEW_MODEL": "gpt-5.4"}, "codex exec -m gpt-5.4"),
        ({"REVIEW_HARNESS": "codex", "REVIEW_MODEL": "gpt-5.4"}, "codex exec -m gpt-5.4"),
        ({"REVIEW_HARNESS": "claude"}, "harness-claude --model claude-opus-5"),
        ({"REVIEW_HARNESS": "claude", "REVIEW_MODEL": "claude-sonnet-5"},
         "harness-claude --model claude-sonnet-5"),
    ],
)
def test_resolution_table_via_fingerprint(tmp_path: Path, env: dict[str, str], equivalent_cmd: str) -> None:
    """Таблица §6 спеки, построчно: отпечаток с харнесс-переменными равен
    отпечатку с эквивалентным явным REVIEW_CMD — значит review_cmd
    резолвится ровно в эту строку."""
    repo = make_repo_with_diff(tmp_path)
    assert fingerprint(repo, env) == fingerprint(repo, {"REVIEW_CMD": equivalent_cmd})
    assert fingerprint(repo, env) != fingerprint(repo)


def test_review_cmd_wins_over_harness(tmp_path: Path) -> None:
    repo = make_repo_with_diff(tmp_path)
    both = {"REVIEW_CMD": "codex exec", "REVIEW_HARNESS": "claude", "REVIEW_MODEL": "x"}
    assert fingerprint(repo, both) == fingerprint(repo)


@pytest.mark.parametrize(
    "env, reason",
    [
        ({"REVIEW_HARNESS": "gemini"}, "неизвестный харнесс"),
        ({"REVIEW_HARNESS": ""}, "неизвестный харнесс"),
        ({"REVIEW_MODEL": ""}, "REVIEW_MODEL"),
        ({"REVIEW_HARNESS": "claude", "REVIEW_MODEL": ""}, "REVIEW_MODEL"),
    ],
)
def test_broken_harness_settings_are_config_errors(tmp_path: Path, env: dict[str, str], reason: str) -> None:
    """Пустые/неизвестные значения — отказ, не умолчание: они приходят только
    от явной, но сломанной настройки, и молча уйти на codex значило бы сжечь
    ровно тот лимит, ради которого переменная выставлялась."""
    repo = make_repo_with_diff(tmp_path)
    res = run_local_env(repo, "--fingerprint-only", env=env)
    assert res.returncode == 2, res.stdout + res.stderr
    assert reason in res.stderr


def _kit_copy(tmp_path: Path, *, with_adapter: bool, executable: bool = True) -> Path:
    kit = tmp_path / "kit"
    kit.mkdir()
    for name in ("local.sh", "build-prompt.sh", "apply-threshold.sh", "collect-context.sh"):
        shutil.copy(ROOT / "scripts" / "review" / name, kit / name)
    if with_adapter:
        target = kit / "harness-claude"
        shutil.copy(ROOT / "scripts" / "review" / "harness-claude", target)
        target.chmod(0o755 if executable else 0o644)
    return kit


def test_claude_without_adapter_is_half_updated_kit(tmp_path: Path) -> None:
    """Перекос версий копий — штатный режим раскатки: отказ именованный (код 2),
    а не `command not found` → код 3 «ревьюер не отработал»."""
    repo = make_repo_with_diff(tmp_path)
    kit = _kit_copy(tmp_path, with_adapter=False)
    res = run_local_env(repo, "--fingerprint-only", env={"REVIEW_HARNESS": "claude"}, kit_dir=kit)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "наполовину" in res.stderr


def test_claude_with_non_executable_adapter_names_chmod(tmp_path: Path) -> None:
    repo = make_repo_with_diff(tmp_path)
    kit = _kit_copy(tmp_path, with_adapter=True, executable=False)
    res = run_local_env(repo, "--fingerprint-only", env={"REVIEW_HARNESS": "claude"}, kit_dir=kit)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "chmod +x" in res.stderr
```

- [ ] **Step 2: Прогнать — RED по ожидаемым причинам**

Run: `uv run pytest tests/review/test_local.py -k "fingerprint_equals or empty_review_cmd or resolution_table or wins_over or broken_harness or half_updated or non_executable" -q`
Expected: `test_default_fingerprint_equals_…` и `test_empty_review_cmd_…` — PASS (текущее поведение уже такое — это ratchet); остальные FAIL: таблица не совпадает (харнесс-переменные игнорируются), отказы возвращают 0 вместо 2.

- [ ] **Step 3: Заменить блок `review_cmd` в `local.sh`**

Заменить строки 26–32 (`# REVIEW_CMD — КОМАНДА, не путь к бинарю …` до `review_cmd="${REVIEW_CMD:-codex exec}"`) на:

```sh
# --- харнесс ревьюера (спека 2026-09-14) ------------------------------------
# REVIEW_CMD — КОМАНДА, не путь к бинарю: умолчание несёт `exec` внутри
# себя, а не как отдельный литерал ниже в вызове. Раньше `exec` был жёстко
# приклеен к вызову, и REVIEW_CMD='codex exec --model X' искал файл с таким
# именем целиком (ENOENT → код 3 "ревьюер не отработал"). Подменить хочется
# команду целиком, включая флаги, — не только бинарь.
#
# Приоритет: НЕПУСТОЙ REVIEW_CMD — осознанный оверрайд целиком, резолв
# харнесса не запускается (D5); REVIEW_CMD="" — как unset, так работал
# `${REVIEW_CMD:-…}` и до патча, и обещание совместимости обязано это
# сохранить. Иначе — REVIEW_HARNESS (умолчание codex: строка `codex exec`
# в отпечатке НЕ меняется, опубликованные наследования остаются валидными —
# D1) и REVIEW_MODEL. Кит читает только окружение процесса: ни файлового,
# ни git-конфига (D2) — harness.env остаётся деталью review-pr.sh.
#
# REVIEW_MODEL="" и REVIEW_HARNESS="" — ОТКАЗЫ, не умолчания: пустое
# значение приходит только от явной, но сломанной настройки (`export
# REVIEW_MODEL=` без значения), и молча уйти на codex значило бы сжечь
# ровно тот лимит, ради которого переменная выставлялась. Факт объявления
# проверяется `${REVIEW_MODEL+x}`, а не `${REVIEW_MODEL:-…}` (D6).
if [ -n "${REVIEW_CMD:-}" ]; then
    review_cmd="$REVIEW_CMD"
else
    if [ -n "${REVIEW_MODEL+x}" ] && [ -z "$REVIEW_MODEL" ]; then
        echo "REVIEW_MODEL задан пустым — уберите переменную или назовите" \
            "модель." >&2
        exit 2
    fi
    case "${REVIEW_HARNESS-codex}" in
        codex)
            review_cmd="codex exec${REVIEW_MODEL:+ -m $REVIEW_MODEL}"
            ;;
        claude)
            # Адаптер — член кита, запускаемый по PATH голым именем (D7):
            # в отпечаток идёт `harness-claude`, а не абсолютный путь,
            # иначе отпечатки разошлись бы между машинами. PATH здесь НЕ
            # меняется — $kit_dir подмешивается префиксом только к самому
            # вызову ревьюера (см. ниже), иначе выбор claude менял бы
            # разрешение git/grep/wc/хешера при подготовке дифа и отпечатка.
            # Перекос версий копий кита — штатный режим раскатки: нет
            # адаптера — именованный отказ, не `command not found` → код 3.
            # checksum.sh сверяет байты, не режим, поэтому потерянный при
            # вендоринге бит исполнения ловится здесь.
            if [ ! -f "$kit_dir/harness-claude" ]; then
                echo "REVIEW_HARNESS=claude, а $kit_dir/harness-claude нет:" \
                    "кит обновлён наполовину — ре-вендорьте кит целиком" \
                    "или уберите переменную (умолчание codex)." >&2
                exit 2
            fi
            if [ ! -x "$kit_dir/harness-claude" ]; then
                echo "адаптер без бита исполнения:" \
                    "chmod +x $kit_dir/harness-claude" >&2
                exit 2
            fi
            review_cmd="harness-claude --model ${REVIEW_MODEL:-claude-opus-5}"
            ;;
        *)
            echo "неизвестный харнесс REVIEW_HARNESS='${REVIEW_HARNESS}'" \
                "(claude|codex)." >&2
            exit 2
            ;;
    esac
fi
```

- [ ] **Step 4: Префикс `PATH` у вызова ревьюера**

В `scripts/review/local.sh` заменить (около строки 644):

```sh
if ! $review_cmd --sandbox read-only \
```

на:

```sh
# PATH расширяется ТОЛЬКО здесь и только для этой команды (D7): префикс-
# присваивание в POSIX sh действует на окружение одной команды и сочетается
# с word-splitting $review_cmd (первое слово строки — имя команды). Для
# codex префикс безвреден: $kit_dir просматривается первым, но бинаря
# `codex` в нём нет. Кит не должен когда-либо получить член с именем
# `codex` или `claude` — это превратило бы префикс в подмену ревьюера.
if ! PATH="$kit_dir:$PATH" $review_cmd --sandbox read-only \
```

- [ ] **Step 5: Прогнать тесты Task 3 и весь `test_local.py`**

Run: `uv run pytest tests/review/test_local.py -q`
Expected: PASS все (старые тесты задают `REVIEW_CMD` и не затронуты).

- [ ] **Step 6: Коммит**

```bash
uv run ruff format . && uv run ruff check .
git add scripts/review/local.sh tests/review/test_local.py
git commit -m "feat(review-kit): local.sh резолвит REVIEW_HARNESS/REVIEW_MODEL, PATH — префиксом у вызова

Умолчание codex, строка \`codex exec\` в отпечатке не меняется (D1);
непустой REVIEW_CMD побеждает, пустой — как unset (D5); REVIEW_MODEL=\"\"
и REVIEW_HARNESS=\"\" — код 2 (D6); claude без адаптера/без бита — код 2
именованно; \$kit_dir в PATH только у вызова ревьюера (D7). Таблица
резолва доказана равенством отпечатков с явным REVIEW_CMD.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `local.sh --print-review-cmd`

**Files:**
- Modify: `scripts/review/local.sh:50-54` (usage), `:56-92` (разбор аргументов), `:94-99` (после проверки `fp_only`/`--fetch`)
- Test: `tests/review/test_local.py`

**Interfaces:**
- Consumes: `review_cmd` из Task 3.
- Produces: `local.sh --print-review-cmd` → stdout ровно одна строка `review_cmd`, код 0; несовместим с `--fetch`/`--fingerprint-only` (код 2). Потребитель — `review-pr.sh` (handoff, Task 7).

- [ ] **Step 1: Падающие тесты**

Добавить в конец `tests/review/test_local.py`:

```python
# --- --print-review-cmd (D9): единственный источник таблицы резолва ----------


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, "codex exec"),
        ({"REVIEW_CMD": ""}, "codex exec"),
        ({"REVIEW_MODEL": "gpt-5.4"}, "codex exec -m gpt-5.4"),
        ({"REVIEW_HARNESS": "claude"}, "harness-claude --model claude-opus-5"),
        ({"REVIEW_HARNESS": "claude", "REVIEW_MODEL": "claude-sonnet-5"},
         "harness-claude --model claude-sonnet-5"),
        ({"REVIEW_CMD": "my-reviewer --flag"}, "my-reviewer --flag"),
    ],
)
def test_print_review_cmd_prints_resolved_command(tmp_path: Path, env: dict[str, str], expected: str) -> None:
    """review-pr.sh берёт reviewer_label отсюда, а не дублирует таблицу."""
    _, local = make_repo(tmp_path)  # пустой диф: команда печатается ДО работы с диапазоном
    res = run_local_env(local, "--print-review-cmd", env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout == expected + "\n"


def test_print_review_cmd_needs_no_remote(tmp_path: Path) -> None:
    """Без единого remote и без --base: резолв не трогает диапазон."""
    _, local = make_repo(tmp_path)
    git(local, "remote", "remove", "origin")
    res = run_local_env(local, "--print-review-cmd")
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout == "codex exec\n"


def test_print_review_cmd_reports_config_errors_with_code_2(tmp_path: Path) -> None:
    _, local = make_repo(tmp_path)
    res = run_local_env(local, "--print-review-cmd", env={"REVIEW_HARNESS": "gemini"})
    assert res.returncode == 2, res.stdout + res.stderr
    assert res.stdout == ""


@pytest.mark.parametrize("other", ["--fetch", "--fingerprint-only"])
def test_print_review_cmd_is_exclusive(tmp_path: Path, other: str) -> None:
    _, local = make_repo(tmp_path)
    res = run_local_env(local, "--print-review-cmd", other)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "--print-review-cmd" in res.stderr
```

- [ ] **Step 2: Прогнать — RED (`usage`, код 2, stdout пуст)**

Run: `uv run pytest tests/review/test_local.py -k print_review_cmd -q`
Expected: FAIL: код 2 от `usage` для всех, кроме `test_print_review_cmd_reports_config_errors_with_code_2` и `test_print_review_cmd_is_exclusive` (те совпадают по коду случайно — убедиться, что первые четыре красные).

- [ ] **Step 3: Реализовать флаг**

В `usage()` (строки 50–54) добавить `[--print-review-cmd]` в конец второй строки:

```sh
usage() {
    echo "usage: local.sh [--base <ref>] [--head <ref>] [--remote <name>]" \
        "[--fetch] [--format markdown|text]" \
        "[--max-diff-bytes N] [--max-diff-files N] [--fingerprint-only]" \
        "[--print-review-cmd]" >&2
}
```

В разборе аргументов, перед `--fingerprint-only) fp_only=1; shift ;;`, добавить:

```sh
        # Печать эффективной команды ревьюера (D9): единственный источник
        # таблицы резолва — кит; review-pr.sh берёт отсюда reviewer_label.
        --print-review-cmd) print_cmd=1; shift ;;
```

После блока (строки ~94–99)

```sh
fp_only="${fp_only:-0}"
```

добавить:

```sh
print_cmd="${print_cmd:-0}"
if [ "$print_cmd" -eq 1 ]; then
    if [ "$do_fetch" -eq 1 ] || [ "$fp_only" -eq 1 ]; then
        echo "--print-review-cmd несовместим с --fetch и --fingerprint-only:" \
            "он печатает команду и выходит, не трогая диапазон." >&2
        exit 2
    fi
    printf '%s\n' "$review_cmd"
    exit 0
fi
```

(Резолв `review_cmd` уже выполнен выше по файлу, до разбора аргументов — его отказы кодом 2 срабатывают и здесь.)

- [ ] **Step 4: Прогнать**

Run: `uv run pytest tests/review/test_local.py -q`
Expected: PASS все.

- [ ] **Step 5: Коммит**

```bash
uv run ruff format . && uv run ruff check .
git add scripts/review/local.sh tests/review/test_local.py
git commit -m "feat(review-kit): local.sh --print-review-cmd — эффективная команда ревьюера (D9)

Печатает review_cmd после резолва и выходит кодом 0 без remote и
диапазона; несовместим с --fetch/--fingerprint-only. review-pr.sh берёт
reviewer_label отсюда, таблица резолва существует в одном месте.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Сквозной путь `REVIEW_HARNESS=claude` и доказательство D7

**Files:**
- Test: `tests/review/test_local.py`

**Interfaces:**
- Consumes: адаптер (Task 1–2), резолв (Task 3), `CLAUDE_STUB` из `tests/review/test_harness_claude.py`.

- [ ] **Step 1: Тесты**

Добавить в конец `tests/review/test_local.py` (импорт: `from tests.review.test_harness_claude import CLAUDE_STUB, envelope` — если `tests` не пакет для импорта, продублировать `CLAUDE_STUB` и `envelope` локально с пометкой «копия из test_harness_claude.py»):

```python
# --- Сквозной путь claude: вердикт доезжает до apply-threshold.sh -------------

MAJOR_FINDING = {
    "findings": [{
        "kind": "defect", "severity": "major", "title": "t", "file": "a.py", "line": 1,
        "scenario": "s", "observed_result": "o", "expected_result": "e",
        "evidence": [{"file": "b.py", "line": 2, "reason": "r"}], "confidence": "high",
    }],
    "note": "stub",
}


def _claude_stand(tmp_path: Path, structured: object) -> dict[str, str]:
    """PATH с подставным claude + env для стаба; возвращает env для run_local_env."""
    bin_dir = tmp_path / "claude-bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(CLAUDE_STUB, encoding="utf-8")
    stub.chmod(0o755)
    env_file = tmp_path / "envelope.json"
    env_file.write_text(envelope(structured), encoding="utf-8")
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CLAUDE_STUB_ARGV": str(tmp_path / "argv.txt"),
        "CLAUDE_STUB_PROMPT": str(tmp_path / "prompt.txt"),
        "CLAUDE_STUB_ENVELOPE": str(env_file),
        "REVIEW_HARNESS": "claude",
    }


@pytest.mark.parametrize("structured, code", [({"findings": [], "note": "stub"}, 0), (MAJOR_FINDING, 1)])
def test_claude_harness_end_to_end_reaches_threshold(tmp_path: Path, structured: object, code: int) -> None:
    """Один env-переключатель — и вердикт claude проходит через apply-threshold.sh
    (код 0/1 по содержимому), включая прогон из подкаталога."""
    repo = make_repo_with_diff(tmp_path)
    sub = repo / "sub"
    sub.mkdir()
    env = _claude_stand(tmp_path, structured)
    res = run_local_env(repo, env=env, cwd=sub)
    assert res.returncode == code, res.stdout + res.stderr
    prompt = Path(env["CLAUDE_STUB_PROMPT"]).read_text(encoding="utf-8")
    assert "new.txt" in prompt  # диф реально дошёл до claude


def test_kit_dir_path_prefix_does_not_leak_into_preparation(tmp_path: Path) -> None:
    """D7: $kit_dir подмешан в PATH только у вызова ревьюера. Подставной `git`
    в копии кита, падающий кодом 99, НЕ должен подхватываться подготовкой
    дифа/отпечатка — при `export PATH` на весь local.sh прогон умер бы."""
    repo = make_repo_with_diff(tmp_path)
    kit = _kit_copy(tmp_path, with_adapter=True)
    fake_git = kit / "git"
    fake_git.write_text("#!/bin/sh\necho 'подставной git подхвачен' >&2\nexit 99\n", encoding="utf-8")
    fake_git.chmod(0o755)
    env = _claude_stand(tmp_path, {"findings": [], "note": "stub"})
    res = run_local_env(repo, env=env, kit_dir=kit)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "подставной git" not in res.stderr
```

- [ ] **Step 2: Прогнать — ожидается PASS (реализация уже полная); красный = дефект в Task 3**

Run: `uv run pytest tests/review/test_local.py -k "end_to_end or does_not_leak" -q`
Expected: PASS. Если `does_not_leak` красный с «подставной git подхвачен» — в `local.sh` где-то есть `export PATH`; убрать.

- [ ] **Step 3: Коммит**

```bash
uv run ruff format . && uv run ruff check .
git add tests/review/test_local.py
git commit -m "test(review-kit): сквозной путь REVIEW_HARNESS=claude и изоляция PATH (D7)

Вердикт подставного claude доезжает до apply-threshold.sh (0/1), в том
числе из подкаталога; подставной git в \$kit_dir не подхватывается
подготовкой дифа — PATH расширен только у вызова ревьюера.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `checksum.sh` — переходный член `?scripts/review/harness-claude`

**Files:**
- Modify: `scripts/review/checksum.sh:81`
- Test: `tests/review/test_checksum.py`

**Interfaces:**
- Produces: инвентарь по умолчанию содержит `?scripts/review/harness-claude`; потребитель без файла зелёный, с файлом — сверяется.

- [ ] **Step 1: Падающие тесты**

Добавить в конец `tests/review/test_checksum.py`:

```python
# --- harness-claude: переходный член инвентаря по умолчанию (спека 2026-09-14 §7)

ADAPTER = "scripts/review/harness-claude"


def test_default_inventory_has_adapter_as_optional_and_absent_is_green(tmp_path: Path) -> None:
    """Двухшаговый ре-вендор (§5 базовой спеки): в этом релизе адаптер — `?path`.
    Потребитель без файла остаётся на codex и ничего не теряет. Зелёный без
    CHECKSUM_KIT_EXTRA — член в ЗАШИТОМ инвентаре, не в env."""
    root = make_kit(tmp_path)
    assert ADAPTER not in KIT_FILES  # стенд без адаптера — ровно старый потребитель
    result = run(root, full_pin(root))
    assert result.returncode == 0, result.stderr
    assert "?" + ADAPTER in SCRIPT.read_text(encoding="utf-8")


def test_adapter_present_is_verified_like_a_mandatory_member(tmp_path: Path) -> None:
    root = make_kit(tmp_path)
    (root / ADAPTER).write_text("adapter\n", encoding="utf-8")
    pin = full_pin(root, extra=[pin_line(root, ADAPTER)])
    assert run(root, pin).returncode == 0
    (root / ADAPTER).write_text("drift\n", encoding="utf-8")
    result = run(root, pin)
    assert result.returncode == 1, result.stderr
    assert ADAPTER in result.stderr
```

- [ ] **Step 2: Прогнать — RED на `"?" + ADAPTER in SCRIPT…` и на `test_adapter_present…` (не-kit entry → код 2)**

Run: `uv run pytest tests/review/test_checksum.py -k adapter -q`
Expected: FAIL оба.

- [ ] **Step 3: Добавить член**

В `scripts/review/checksum.sh` строка 81 — дописать в конец `required_kit_default` элемент ` ?scripts/review/harness-claude`:

```sh
required_kit_default="scripts/review/build-prompt.sh scripts/review/collect-context.sh scripts/review/apply-threshold.sh scripts/review/local.sh scripts/review/checksum.sh .github/codex/review-schema.json ?scripts/review/harness-claude"
```

И над строкой — комментарий:

```sh
# `?scripts/review/harness-claude` — переходный член релиза 2026-09
# (спека харнесс-слоя §7): обязательным становится следующим релизом кита.
```

- [ ] **Step 4: Прогнать весь `test_checksum.py`**

Run: `uv run pytest tests/review/test_checksum.py -q`
Expected: PASS все (в т.ч. старые — optional-член без PIN-строки не требуется).

- [ ] **Step 5: Коммит**

```bash
uv run ruff format . && uv run ruff check .
git add scripts/review/checksum.sh tests/review/test_checksum.py
git commit -m "feat(review-kit): checksum.sh — harness-claude переходным членом инвентаря

Двухшаговый ре-вендор по §5 базовой спеки: в этом релизе \`?path\`,
обязательным — следующим. Потребитель без файла зелёный, с файлом —
сверяется как обычный член.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Документация, TODO, PR и handoff в devtools

**Files:**
- Modify: `README.md` (после блока команд в разделе «Локальное ревью перед пушем (opt-in)», строка ~88)
- Modify: `docs/superpowers/specs/2026-08-21-codex-review-kit-design.md` §5 (список файлов кита, строка ~380) и §7 (блок `local.sh`, строка ~530)
- Modify: `TODO.md` (пункт `@id:review-kit-harness-layer`)

- [ ] **Step 1: README**

После блока

````markdown
```bash
sh scripts/review/install-hook.sh     # поставить pre-push хук
sh scripts/review/local.sh            # разовый прогон без хука
sh scripts/review/local.sh --fetch    # то же, обновив базу с origin
```
````

вставить:

```markdown
### Харнесс ревьюера

Кит читает три переменные **окружения процесса** и ничего больше (ни файла,
ни git-конфига):

| Переменная | Значения | Умолчание |
|---|---|---|
| `REVIEW_HARNESS` | `codex` \| `claude` | `codex` |
| `REVIEW_MODEL` | имя модели харнесса | codex — без `-m`; claude — `claude-opus-5` |
| `REVIEW_CMD` | команда целиком (с флагами) | не задана |

Непустой `REVIEW_CMD` побеждает всё остальное; пустой — как незаданный.
`REVIEW_HARNESS=""` и `REVIEW_MODEL=""` — ошибка конфигурации (код 2), а не
умолчание: пустое значение приходит только от сломанной настройки, и молча
уйти на codex значило бы сжечь тот лимит, ради которого переменная
выставлялась. Что именно будет запущено, печатает
`sh scripts/review/local.sh --print-review-cmd`.

Для claude нужен `claude` CLI и `jq` в `PATH`; адаптер
`scripts/review/harness-claude` — член кита (в этом релизе переходный), и
`REVIEW_HARNESS=claude` при его отсутствии — именованный отказ «кит обновлён
наполовину», а не `command not found`. Строка команды попадает в отпечаток
ревью: умолчание `codex exec` не изменилось, старые наследования вердиктов
остаются валидными; переключение харнесса или модели — другой ревьюер,
наследовать нельзя.

**Хук наследует окружение процесса `git push`.** Задайте `REVIEW_HARNESS` в
профиле shell — и `git push` из терминала пойдёт на claude. GUI/IDE-клиенты
git, не читающие профиль shell, переменной не увидят, и хук честно уйдёт на
умолчание `codex`; проверяйте из того же окружения:
`sh scripts/review/local.sh --print-review-cmd`.
```

- [ ] **Step 2: Базовая спека кита — §5 и §7**

В §5 в блоке «Вендоримый кит» после строки `scripts/review/checksum.sh          # переносимая сверка копии с PIN` добавить:

```
scripts/review/harness-claude       # адаптер claude (переходный член релиза 2026-09; 100755, по PATH)
```

и после блока — абзац:

```markdown
`harness-claude` — единственный член без расширения и с битом исполнения: он
запускается по `PATH` голым именем, чтобы строка команды в отпечатке была
машинно-независимой и при этом тем, что реально запускается (спека
харнесс-слоя 2026-09-14, D7). Зависимость `claude` — только при
`REVIEW_HARNESS=claude`.
```

В §7 блок `local.sh` дополнить строкой после `exit: как у apply-threshold.sh, плюс 3 — codex не отработал`:

```
    Ревьюер: REVIEW_CMD (непустой — целиком) > REVIEW_HARNESS=codex|claude
    (+ REVIEW_MODEL); только окружение процесса. `--print-review-cmd`
    печатает эффективную команду и выходит кодом 0. Подробно —
    2026-09-14-review-kit-harness-layer-design.md.
```

и в «Зависимости кита — `git`, `jq`, `codex`, …» заменить `codex` на `codex` **или** `claude` (по `REVIEW_HARNESS`).

- [ ] **Step 3: Полный прогон и проверки**

```bash
uv run ruff format . && uv run ruff check . && uv run pyrefly check && uv run pytest -q
sh scripts/review/local.sh --print-review-cmd     # sanity: печатает `codex exec`
REVIEW_HARNESS=claude sh scripts/review/local.sh --print-review-cmd   # `harness-claude --model claude-opus-5`
```

Expected: всё зелёное; две строки как в комментариях.

- [ ] **Step 4: TODO — закрыть пункт**

В `TODO.md` у пункта `@id:review-kit-harness-layer` сменить `- [ ]` на `- [x]` и дописать в конец тела:

```markdown
  Закрыт PR этой ветки: `scripts/review/harness-claude` (100755, по PATH),
  резолв в `local.sh` + `--print-review-cmd`, переходный член в
  `checksum.sh`, README/спека. Handoff в devtools — issue
  `review-pr-harness-env` (inbox): `review-pr.sh` переходит на
  `REVIEW_HARNESS`/`REVIEW_MODEL`, `reviewer_label` — из
  `local.sh --print-review-cmd` (feature-detect по литералу), переходник
  `scripts/harness/claude-review` удаляется. Волна ре-вендора по флоту — по
  образцу `review-kit-fp-wave`; PIN у потребителей — `checksum.sh`.
```

- [ ] **Step 5: Коммит, локальное ревью, PR по правилам репо**

```bash
git add README.md docs/superpowers/specs/2026-08-21-codex-review-kit-design.md TODO.md
git commit -m "docs(review-kit): харнесс ревьюера — README, спека §5/§7, TODO

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
sh scripts/review/local.sh                # до чистого вердикта
git push -u origin feat/review-kit-harness-layer
gh pr create --draft --title "feat(review-kit): харнесс-слой ревьюера claude|codex в самом ките (steward#147)" --body "…"
sh ../devtools/review-pr.sh steward <pr> --dry-run   # затем без --dry-run
```

Тело PR: цель из issue, решения D1–D9 одной строкой каждое, таблица отпечатка §6, что доказывают тесты (равенство отпечатков как доказательство таблицы; подставной git как доказательство D7), «переходный член — ре-вендор двухшаговый», handoff. Мерж — от ai-prosto по правилам `CLAUDE.md` (authority-root пути не тронуты).

- [ ] **Step 6: После мержа — handoff в devtools и закрытие issue**

```bash
gh issue create -R andrei-shtanakov/devtools --label inbox \
  --title "review-pr.sh: перейти на REVIEW_HARNESS/REVIEW_MODEL кита, удалить переходник claude-review" \
  --body "$(cat <<'EOF'
slug: review-pr-harness-env
from: steward#review-kit-harness-layer

Кит steward @ <sha мержа> канонизировал харнесс-слой (спека
docs/superpowers/specs/2026-09-14-review-kit-harness-layer-design.md):
`local.sh` читает REVIEW_HARNESS=codex|claude и REVIEW_MODEL из окружения,
адаптер scripts/review/harness-claude — член кита (переходный `?path`),
`local.sh --print-review-cmd` печатает эффективную команду.

Просьба (миграция односторонняя):
- `--harness/--model` и harness.env выставляют REVIEW_HARNESS/REVIEW_MODEL
  вместо сборки REVIEW_CMD; PATH-подмешивание scripts/harness снимается;
- reviewer_label — из `local.sh --print-review-cmd` с feature-detect по
  литералу (старые копии кита по флоту — прежняя ветка);
- переходник scripts/harness/claude-review удаляется
  (ваш review-harness-shim-removal).

Признак «сделано»: боевой прогон review-pr.sh на репо со свежим китом идёт
через harness-claude кита (в теле ревью — `harness-claude --model …`), и
файла scripts/harness/claude-review в devtools нет.
EOF
)"
gh issue close 147 --reason completed --comment "Выполнено в PR #<n> (master <sha>). Handoff в devtools: <url issue>."
```

---

## Self-review (выполнен при написании)

- **Покрытие спеки:** D1 (Task 3 тест `unchanged`), D2 (нет чтения файлов — Task 3), D3 (Task 1), D4 (литералы не тронуты — ни одна задача их не меняет), D5 (`empty_review_cmd`), D6 (`broken_harness_settings`), D7 (Task 3 Step 4 + Task 5), D8 (Task 7 Step 6), D9 (Task 4); §5 адаптер (Task 1–2, включая jq-префлайт, tmp в каталоге цели, `jq -c`); §6 таблица (`resolution_table`, `print_review_cmd`); §7 переходный член (Task 6); §9 тесты — все перечисленные есть; §10 README/спека/TODO (Task 7).
- **Плейсхолдеры:** `<sha мержа>`, `<pr>`, `<n>`, `<url issue>` — значения известны только во время выполнения Step 5–6 Task 7; всё остальное — конкретный код.
- **Согласованность имён:** `run_local_env`, `fingerprint`, `make_repo_with_diff`, `_kit_copy` определены в Task 3 и используются в Task 4–5 с теми же сигнатурами; `Stand`, `envelope`, `CLAUDE_STUB`, `VERDICT_OK` — Task 1, используются в Task 2 и 5; сообщения об ошибках в тестах (`наполовину`, `chmod +x`, `неизвестный харнесс`, `REVIEW_MODEL`, `structured_output`, `кодом 7`, `--print-review-cmd`) совпадают с текстами в реализации.
