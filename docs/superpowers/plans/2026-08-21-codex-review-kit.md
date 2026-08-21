# codex-review kit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Вынести механику ревью (сборка промпта, порог, рендер) из YAML в переносимые shell-скрипты, перевести на них CI и дать локальный прогон с opt-in `pre-push` хуком.

**Architecture:** Три POSIX-скрипта в `scripts/review/`. Два общих (`build-prompt.sh`, `apply-threshold.sh`) вызываются и workflow'ом, и локальным прогоном — это делает вопрос и порог структурно одинаковыми. Третий (`local.sh`) существует только локально: считает диапазон, зовёт `codex exec` и два общих скрипта. Публикация результата остаётся у вызывающего: CI шлёт в комментарий PR, локальный прогон — в терминал.

**Tech Stack:** POSIX `sh`, `jq`, `git`, `codex` CLI (локально), `gh` (только в CI). Тесты — `pytest` через `subprocess`.

**Spec:** `docs/superpowers/specs/2026-08-21-codex-review-kit-design.md`

## Global Constraints

- **Только POSIX `sh`.** Шебанг `#!/bin/sh`, `set -eu`. **`pipefail` запрещён** — его нет в POSIX; где важен код выхода конвейера, использовать временные файлы, а не пайп.
- **Ни одного файла кита, требующего Python.** Кит обязан работать в репо на Rust и Elixir.
- **Коды выхода едины по репо:** `0` чисто, `1` предметный отказ, `2` ошибка конфигурации, `3` механический сбой.
- **Диапазон — `merge-base <base> <head>` и `diff <mb>..<head>`.** Ни `master`, ни `HEAD` не зашиты нигде; оба конца параметры.
- **Диф никогда не попадает в argv и не подставляется в команду** — только через файл или stdin.
- **Локальный прогон не производит governance evidence.** Хук ничего не публикует: ни комментариев, ни статусов, ни артефактов.
- **CI читает из `base` PR** промпт, схему и **оба общих скрипта**. `local.sh` в этот список не входит — CI его не вызывает.
- Русский в сообщениях и документации, английский в именах файлов и коде.
- Строка ≤ 100 символов в Python-файлах (`pyproject.toml` репо).

## File Structure

| Файл | Ответственность |
|---|---|
| `scripts/review/build-prompt.sh` | Склейка инструкций ревьюера с дифом. Больше ничего. |
| `scripts/review/apply-threshold.sh` | Валидация вердикта, рендер, порог, код выхода. **Не публикует.** |
| `scripts/review/local.sh` | Разрешение базы и головы, свежесть базы, диапазон, вызов `codex exec`, склейка первых двух. |
| `.github/hooks/pre-push` | Контракт по ref'ам плюс вызов `local.sh`. Предметной логики нет. |
| `scripts/review/install-hook.sh` | Установка хука в `.git/hooks/`. |
| `tests/review/test_apply_threshold.py` | Тесты порога и рендера. Не вендорятся. |
| `tests/review/test_build_prompt.py` | Тесты склейки. Не вендорятся. |
| `tests/review/test_local.py` | Тесты диапазона, свежести, пустого дифа. Не вендорятся. |
| `tests/review/test_pre_push_hook.py` | Тесты контракта по ref'ам. Не вендорятся. |

`checksum.sh` в этот план **не входит** — спека §11 относит его к шагу 5 и первому потребителю.

---

### Task 1: `apply-threshold.sh` — порог, рендер, код выхода

Это чистое извлечение из workflow: тот же `jq`, те же строки. Байтовое совпадение markdown-вывода с сегодняшним — предмет приёмки Task 3, поэтому текст менять нельзя даже «к лучшему».

**Files:**
- Create: `scripts/review/apply-threshold.sh`
- Test: `tests/review/test_apply_threshold.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `apply-threshold.sh --verdict <file> [--format markdown|text]`; stdout — отрендеренная сводка; exit `0` находок ниже порога, `1` есть `blocker`/`major`, `2` вердикт негоден или аргументы негодны.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Тесты scripts/review/apply-threshold.sh — порог, рендер, коды выхода."""

import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review" / "apply-threshold.sh"


def run(verdict_path: Path, fmt: str = "markdown") -> subprocess.CompletedProcess[str]:
    """Запустить скрипт как настоящий sh, а не импортом функции."""
    return subprocess.run(
        ["sh", str(SCRIPT), "--verdict", str(verdict_path), "--format", fmt],
        capture_output=True,
        text=True,
    )


def write(tmp_path: Path, payload: object) -> Path:
    target = tmp_path / "verdict.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_no_findings_is_green(tmp_path: Path) -> None:
    result = run(write(tmp_path, {"findings": [], "note": "смотрел диф"}))
    assert result.returncode == 0
    assert "Находок нет." in result.stdout
    assert "смотрел диф" in result.stdout


def test_major_reddens(tmp_path: Path) -> None:
    payload = {
        "findings": [
            {"severity": "major", "file": "a.py", "summary": "s", "failure": "f"}
        ]
    }
    assert run(write(tmp_path, payload)).returncode == 1


def test_blocker_reddens(tmp_path: Path) -> None:
    payload = {
        "findings": [
            {"severity": "blocker", "file": "a.py", "summary": "s", "failure": "f"}
        ]
    }
    assert run(write(tmp_path, payload)).returncode == 1


def test_minor_alone_stays_green(tmp_path: Path) -> None:
    """Порог — blocker и major. minor виден в выводе, но не красит."""
    payload = {
        "findings": [
            {"severity": "minor", "file": "a.py", "summary": "s", "failure": "f"}
        ]
    }
    result = run(write(tmp_path, payload))
    assert result.returncode == 0
    assert "minor" in result.stdout


def test_findings_not_an_array_is_config_error(tmp_path: Path) -> None:
    assert run(write(tmp_path, {"findings": "нет"})).returncode == 2


def test_missing_file_is_config_error(tmp_path: Path) -> None:
    result = run(tmp_path / "нет-такого.json")
    assert result.returncode == 2


def test_unknown_format_is_config_error(tmp_path: Path) -> None:
    assert run(write(tmp_path, {"findings": []}), fmt="хтмл").returncode == 2


def test_pipe_and_newline_in_cells_do_not_break_the_table(tmp_path: Path) -> None:
    """Текст вердикта пишет модель: `|` или перевод строки рвали бы таблицу молча."""
    payload = {
        "findings": [
            {
                "severity": "major",
                "file": "a.py",
                "summary": "две|трубы|внутри",
                "failure": "первая строка\nвторая строка",
            }
        ]
    }
    result = run(write(tmp_path, payload))
    rows = [ln for ln in result.stdout.splitlines() if ln.startswith("| major")]
    assert len(rows) == 1, "находка обязана остаться одной строкой таблицы"
    assert rows[0].count("|") == 5, "внутренние трубы обязаны быть экранированы"


def test_text_format_has_no_markdown_table(tmp_path: Path) -> None:
    payload = {
        "findings": [
            {"severity": "major", "file": "a.py", "summary": "s", "failure": "f"}
        ]
    }
    result = run(write(tmp_path, payload), fmt="text")
    assert "|---|" not in result.stdout
    assert "major" in result.stdout
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `uv run pytest tests/review/test_apply_threshold.py -q`
Expected: FAIL — файла `scripts/review/apply-threshold.sh` нет, `sh` возвращает 127.

- [ ] **Step 3: Написать скрипт**

```sh
#!/bin/sh
# Порог серьёзности и рендер вердикта. Публикацией НЕ занимается: stdout уходит
# в `gh pr comment` из CI и в терминал из локального прогона. Разрез проходит
# ровно здесь, иначе в скрипт пришлось бы тащить `gh` и права на запись.
#
# `pipefail` не используется — его нет в POSIX sh.
set -eu

usage() {
    echo "usage: apply-threshold.sh --verdict <file> [--format markdown|text]" >&2
}

verdict=""
format="markdown"

while [ $# -gt 0 ]; do
    case "$1" in
        --verdict) verdict="${2:-}"; shift 2 ;;
        --format)  format="${2:-}"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

[ -n "$verdict" ] || { usage; exit 2; }
[ -f "$verdict" ] || { echo "вердикт не найден: $verdict" >&2; exit 2; }

case "$format" in
    markdown|text) ;;
    *) echo "неизвестный --format: $format" >&2; exit 2 ;;
esac

# Невалидный вердикт — отказ, а не «замечаний нет». Отдельный код (2), чтобы
# вызывающий отличал негодный вердикт от находок выше порога.
jq -e '.findings | type == "array"' "$verdict" >/dev/null 2>&1 \
    || { echo "вердикт нечитаем: .findings не массив" >&2; exit 2; }

total=$(jq '.findings | length' "$verdict")
blocking=$(jq '[.findings[]
    | select(.severity == "blocker" or .severity == "major")] | length' "$verdict")

if [ "$format" = markdown ]; then
    echo "## Ревью Codex — независимый чек"
else
    echo "Ревью Codex — независимый чек"
fi
echo
jq -r '.note // ""' "$verdict"
echo

if [ "$total" = 0 ]; then
    echo "Находок нет."
elif [ "$format" = markdown ]; then
    echo "| уровень | файл | находка | сценарий отказа |"
    echo "|---|---|---|---|"
    # Текст вердикта пишет модель: перевод строки или `|` внутри поля разорвал
    # бы таблицу молча. Схлопываем и экранируем.
    jq -r '
        def cell: (. // "") | gsub("\\r?\\n"; " ") | gsub("\\|"; "\\|");
        .findings[]
        | "| \(.severity|cell) | `\(.file|cell)` | \(.summary|cell) | \(.failure|cell) |"
    ' "$verdict"
else
    jq -r '
        def cell: (. // "") | gsub("\\r?\\n"; " ");
        .findings[]
        | "[\(.severity|cell)] \(.file|cell)\n    \(.summary|cell)\n    сценарий: \(.failure|cell)\n"
    ' "$verdict"
fi

echo
echo "_Порог: красным делают \`blocker\` и \`major\`. Это чек, не аппрув —"
echo "и не замена ревью человека._"

[ "$blocking" -eq 0 ] || exit 1
```

- [ ] **Step 4: Сделать исполняемым и прогнать**

```bash
chmod +x scripts/review/apply-threshold.sh
uv run pytest tests/review/test_apply_threshold.py -q
```
Expected: PASS, 8 тестов.

- [ ] **Step 5: Мутационная проверка каждого стража**

Мутировать по **номерам строк**, не по подстроке: подстрока с неверным отступом не применяется и даёт ложный результат. После каждой мутации **сверить длину и содержимое** мутанта, прежде чем верить коду выхода.

```bash
cp scripts/review/apply-threshold.sh /tmp/orig.sh
# 1. Порог всегда зелёный
ln=$(grep -n '^\[ "\$blocking" -eq 0 \]' scripts/review/apply-threshold.sh | cut -d: -f1)
sed "${ln}s/.*/true/" /tmp/orig.sh > scripts/review/apply-threshold.sh
sed -n "${ln}p" scripts/review/apply-threshold.sh   # обязано напечатать `true`
uv run pytest tests/review/test_apply_threshold.py -q   # обязано покраснеть
# 2. Убрать проверку валидности вердикта
cp /tmp/orig.sh scripts/review/apply-threshold.sh
ln=$(grep -n "jq -e '.findings | type" scripts/review/apply-threshold.sh | cut -d: -f1)
sed "${ln},$((ln+1))d" /tmp/orig.sh > scripts/review/apply-threshold.sh
uv run pytest tests/review/test_apply_threshold.py -q   # обязано покраснеть
# 3. Убрать экранирование труб
cp /tmp/orig.sh scripts/review/apply-threshold.sh
sed 's/ | gsub("\\\\|"; "\\\\|")//' /tmp/orig.sh > scripts/review/apply-threshold.sh
uv run pytest tests/review/test_apply_threshold.py -q   # обязано покраснеть
cp /tmp/orig.sh scripts/review/apply-threshold.sh
```

Если какая-то мутация оставила набор зелёным — соответствующий тест вакуумен, чинить тест, а не мутацию.

- [ ] **Step 6: Коммит**

```bash
git add scripts/review/apply-threshold.sh tests/review/test_apply_threshold.py
git commit -m "feat(review): apply-threshold.sh — порог, рендер и код выхода отдельно от публикации"
```

---

### Task 2: `build-prompt.sh` — склейка промпта с дифом

Тоже чистое извлечение. Поведение при маркере `--- ДИФ КОНЕЦ ---` внутри дифа **сохраняется как есть** — тест его закрепляет, а не чинит: усиление разделителя меняло бы контракт промпта и в спеку не входит (см. хвосты плана).

**Files:**
- Create: `scripts/review/build-prompt.sh`
- Test: `tests/review/test_build_prompt.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `build-prompt.sh --prompt <file> --diff <file>`; stdout — готовый промпт; exit `0` успех, `2` аргументы или файлы негодны.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Тесты scripts/review/build-prompt.sh — склейка инструкций с дифом."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review" / "build-prompt.sh"


def run(prompt: Path, diff: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), "--prompt", str(prompt), "--diff", str(diff)],
        capture_output=True,
        text=True,
    )


def make(tmp_path: Path, prompt_text: str, diff_text: str) -> tuple[Path, Path]:
    prompt = tmp_path / "prompt.md"
    diff = tmp_path / "diff.patch"
    prompt.write_text(prompt_text, encoding="utf-8")
    diff.write_text(diff_text, encoding="utf-8")
    return prompt, diff


def test_prompt_precedes_diff_between_markers(tmp_path: Path) -> None:
    prompt, diff = make(tmp_path, "ИНСТРУКЦИИ", "ДИФ-ТЕЛО")
    out = run(prompt, diff).stdout
    assert out.index("ИНСТРУКЦИИ") < out.index("--- ДИФ НАЧАЛО ---")
    assert out.index("--- ДИФ НАЧАЛО ---") < out.index("ДИФ-ТЕЛО")
    assert out.index("ДИФ-ТЕЛО") < out.index("--- ДИФ КОНЕЦ ---")


def test_shell_metacharacters_in_diff_pass_through_verbatim(tmp_path: Path) -> None:
    """Диф идёт через файл, а не через argv: подстановки не исполняются."""
    payload = "$(touch /tmp/pwned) `id` ${HOME} && rm -rf / ; echo x"
    prompt, diff = make(tmp_path, "И", payload)
    result = run(prompt, diff)
    assert result.returncode == 0
    assert payload in result.stdout


def test_end_marker_inside_diff_is_passed_through(tmp_path: Path) -> None:
    """Закрепляет СЕГОДНЯШНЕЕ поведение, а не желаемое.

    Диф, содержащий строку конца, попадает в промпт как есть. Усиление
    разделителя — отдельное решение владельца, см. хвосты плана.
    """
    prompt, diff = make(tmp_path, "И", "before\n--- ДИФ КОНЕЦ ---\nafter")
    out = run(prompt, diff).stdout
    assert out.count("--- ДИФ КОНЕЦ ---") == 2


def test_missing_prompt_is_config_error(tmp_path: Path) -> None:
    _, diff = make(tmp_path, "И", "Д")
    result = subprocess.run(
        ["sh", str(SCRIPT), "--prompt", str(tmp_path / "нет.md"), "--diff", str(diff)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_missing_diff_is_config_error(tmp_path: Path) -> None:
    prompt, _ = make(tmp_path, "И", "Д")
    result = subprocess.run(
        ["sh", str(SCRIPT), "--prompt", str(prompt), "--diff", str(tmp_path / "нет.patch")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `uv run pytest tests/review/test_build_prompt.py -q`
Expected: FAIL — скрипта нет, `sh` возвращает 127.

- [ ] **Step 3: Написать скрипт**

```sh
#!/bin/sh
# Склейка инструкций ревьюера с дифом. Оба приходят ФАЙЛАМИ и склеиваются
# через `cat`: диф никогда не попадает ни в аргумент командной строки, ни в
# подстановку выражений — иначе его содержимое интерпретировалось бы как часть
# команды, а диф это недоверенный текст.
set -eu

usage() {
    echo "usage: build-prompt.sh --prompt <file> --diff <file>" >&2
}

prompt=""
diff=""

while [ $# -gt 0 ]; do
    case "$1" in
        --prompt) prompt="${2:-}"; shift 2 ;;
        --diff)   diff="${2:-}"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

[ -n "$prompt" ] || { usage; exit 2; }
[ -n "$diff" ] || { usage; exit 2; }
[ -f "$prompt" ] || { echo "нет файла инструкций: $prompt" >&2; exit 2; }
[ -f "$diff" ] || { echo "нет файла дифа: $diff" >&2; exit 2; }

cat "$prompt"
printf '\n\n--- ДИФ НАЧАЛО ---\n'
cat "$diff"
printf '\n--- ДИФ КОНЕЦ ---\n'
```

- [ ] **Step 4: Сделать исполняемым и прогнать**

```bash
chmod +x scripts/review/build-prompt.sh
uv run pytest tests/review/test_build_prompt.py -q
```
Expected: PASS, 5 тестов.

- [ ] **Step 5: Мутационная проверка**

```bash
cp scripts/review/build-prompt.sh /tmp/orig-bp.sh
# Передать диф аргументом вместо файла — тест на метасимволы обязан покраснеть
ln=$(grep -n '^cat "\$diff"' scripts/review/build-prompt.sh | cut -d: -f1)
sed "${ln}s|.*|echo \$(cat \"\$diff\")|" /tmp/orig-bp.sh > scripts/review/build-prompt.sh
sed -n "${ln}p" scripts/review/build-prompt.sh    # сверить, что подставилось
uv run pytest tests/review/test_build_prompt.py -q   # обязано покраснеть
cp /tmp/orig-bp.sh scripts/review/build-prompt.sh
```

- [ ] **Step 6: Коммит**

```bash
git add scripts/review/build-prompt.sh tests/review/test_build_prompt.py
git commit -m "feat(review): build-prompt.sh — склейка инструкций с дифом через файлы"
```

---

### Task 3: Перевод workflow на общие скрипты

**Отдельный PR.** Не смешивать ни с чем: диф этого PR обязан показывать только замену инлайна на вызовы.

Три содержательных изменения, и второе легко упустить:

1. Джоб `review` зовёт `build-prompt.sh` вместо инлайн-склейки.
2. Джоб `report` **получает checkout base'а**, которого у него сегодня нет, — иначе скрипта в его рабочем каталоге не существует. Это безопасно ровно потому, что после steward#87 страж смотрит на `DOWNLOAD_OUTCOME`, а не на наличие файла: посторонний `verdict.json` из чекаута не будет принят за вердикт.
3. Проверка присутствия в base расширяется с двух файлов до четырёх.

**Files:**
- Modify: `.github/workflows/codex-review.yml`

**Interfaces:**
- Consumes: `build-prompt.sh --prompt <file> --diff <file>` (Task 2); `apply-threshold.sh --verdict <file> --format markdown` с кодами `0/1/2` (Task 1).
- Produces: ничего для последующих задач.

- [ ] **Step 1: Расширить проверку присутствия в base**

В джобе `review`, шаг «Собрать промпт», заменить список файлов цикла:

```sh
for f in .github/codex/review-prompt.md .github/codex/review-schema.json \
         scripts/review/build-prompt.sh scripts/review/apply-threshold.sh; do
    [ -f "$f" ] && continue
    echo "::error::$f нет в base ($BASE_SHA). Инструкции И ИСПОЛНЯЕМАЯ механика"\
         "ревьюера читаются из base по построению — иначе автор PR правит порог"\
         "себе. PR, вводящий их, этим чеком не проверяется; смержите его."
    exit 1
done
```

- [ ] **Step 2: Заменить инлайн-склейку вызовом**

В том же шаге заменить блок `{ cat …; printf …; } > prompt.txt` на:

```sh
sh scripts/review/build-prompt.sh \
    --prompt .github/codex/review-prompt.md \
    --diff diff.patch > prompt.txt
```

- [ ] **Step 3: Дать джобу `report` checkout base'а**

Первым шагом джоба `report`, **до** `download-artifact`:

```yaml
      # Скрипты кита читаются из base, как промпт и схема (спека §4): иначе
      # автор PR правит `apply-threshold.sh` на `exit 0` и снимает гейт.
      # Чекаут здесь безопасен только потому, что страж ниже смотрит на
      # `DOWNLOAD_OUTCOME`, а не на наличие `verdict.json` в каталоге —
      # см. steward#87.
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
        with:
          ref: ${{ github.event.pull_request.base.sha }}
          persist-credentials: false
```

- [ ] **Step 4: Заменить инлайн-порог вызовом**

В шаге «Опубликовать сводку и применить порог» заменить всё **от** строки `jq -e '.findings | type == "array"'` **до конца шага** на:

```sh
          # `set -e` снят вокруг вызова намеренно: нужен И отрендеренный вывод,
          # И код выхода. Иначе шаг умер бы на находках, не опубликовав их.
          set +e
          sh scripts/review/apply-threshold.sh \
              --verdict verdict.json --format markdown > body.md
          rc=$?
          set -e

          if [ "$rc" -eq 2 ]; then
              # Рендера нет — публиковать нечего. Причина уже в stderr скрипта.
              echo "::error::вердикт негоден — чек красный"
              exit 1
          fi

          gh pr comment "$PR" --repo "$REPO" --body-file body.md \
              || echo "::warning::комментарий не опубликован; чек всё равно красный"

          if [ "$rc" -ne 0 ]; then
              echo "::error::находки уровня blocker/major — чек красный"
              exit 1
          fi
          echo "находок выше порога нет"
```

- [ ] **Step 5: Приёмка №1 — вердикт не изменился**

Открыть PR с этой правкой, дождаться чека и сверить комментарий с комментарием предыдущего прогона на master:

```bash
gh pr comment --help >/dev/null   # sanity: gh на месте
gh pr view <N> --json comments \
  --jq '[.comments[]|select(.author.login=="github-actions")|.body]|last' > /tmp/new-body.md
```
Expected: структура и формулировки совпадают с сегодняшними — заголовок, `note`, таблица, строка про порог. Расхождение означает, что извлечение изменило текст: чинить скрипт, а не приёмку.

- [ ] **Step 6: Приёмка №2 — base-владение работает**

Это главная приёмка задачи, и без неё Step 1–4 доказаны только наполовину.

```bash
git switch -c test/threshold-tamper
# Заменить последнюю строку apply-threshold.sh на безусловный успех
ln=$(grep -n '^\[ "\$blocking" -eq 0 \]' scripts/review/apply-threshold.sh | cut -d: -f1)
sed -i.bak "${ln}s/.*/exit 0/" scripts/review/apply-threshold.sh && rm -f scripts/review/apply-threshold.sh.bak
sed -n "${ln}p" scripts/review/apply-threshold.sh   # обязано напечатать `exit 0`
# Добавить в тот же PR заведомую находку уровня major — например, вызов
# `eval "$UNTRUSTED"` в новом файле scripts/review/_tamper_probe.sh
git add -A && git commit -m "test: проверка base-владения (не мержить)"
git push -u origin test/threshold-tamper && gh pr create --fill --head test/threshold-tamper
```
Expected: чек `report` **КРАСНЫЙ**. Зелёный означает, что порог поехал из head — Step 1 или Step 3 не работает.

Ветку и PR после проверки закрыть и удалить:
```bash
gh pr close <N> --delete-branch
```

- [ ] **Step 7: Коммит**

```bash
git add .github/workflows/codex-review.yml
git commit -m "refactor(codex-review): перевести на общие скрипты кита, читать их из base

Порог и склейка промпта больше не живут инлайном в YAML. Оба скрипта
читаются из base наравне с промптом и схемой: взятый из head
apply-threshold.sh отдавал бы автору PR прямое управление вердиктом.

Джоб report получил чекаут base — сегодня у него не было никакого. Это
безопасно ровно потому, что после #87 страж смотрит на DOWNLOAD_OUTCOME,
а не на наличие verdict.json в рабочем каталоге."
```

---

### Task 4: `local.sh` — диапазон, свежесть базы, прогон

**Files:**
- Create: `scripts/review/local.sh`
- Test: `tests/review/test_local.py`

**Interfaces:**
- Consumes: `build-prompt.sh`, `apply-threshold.sh` из Task 1–2.
- Produces: `local.sh [--base <ref|sha>] [--head <ref|sha>] [--fetch] [--format markdown|text]`; exit `0/1/2`, плюс `3` — ревьюер не отработал. Команда ревьюера подменяется переменной окружения `REVIEW_CMD` (для тестов); по умолчанию `codex exec`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Тесты scripts/review/local.sh — диапазон, свежесть базы, пустой диф."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "review" / "local.sh"

STUB_OK = """#!/bin/sh
# Подставной ревьюер: пишет годный вердикт без находок туда, куда просят.
out=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o|--output-last-message) out="$2"; shift 2 ;;
        *) shift ;;
    esac
done
cat > /dev/null           # промпт приходит на stdin — проглотить
printf '{"findings":[],"note":"stub"}' > "$out"
"""

STUB_BROKEN = """#!/bin/sh
cat > /dev/null
echo "ревьюер сломался" >&2
exit 1
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def make_stub(tmp_path: Path, body: str) -> str:
    stub = tmp_path / "stub-reviewer"
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)
    return str(stub)


def make_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Создать 'удалённый' и локальный репо с настроенным origin/HEAD."""
    remote = tmp_path / "remote"
    remote.mkdir()
    subprocess.run(["git", "-C", str(remote), "init", "-q", "-b", "master"], check=True)
    git(remote, "config", "user.email", "t@t")
    git(remote, "config", "user.name", "t")
    (remote / "base.txt").write_text("base\n", encoding="utf-8")
    git(remote, "add", "-A")
    git(remote, "commit", "-qm", "base")

    local = tmp_path / "local"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(local)], check=True, capture_output=True
    )
    git(local, "config", "user.email", "t@t")
    git(local, "config", "user.name", "t")
    git(local, "remote", "set-head", "origin", "-a")
    return remote, local


def run_local(
    repo: Path, stub: str, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["REVIEW_CMD"] = stub
    env["REVIEW_KIT_DIR"] = str(ROOT / "scripts" / "review")
    env["REVIEW_SCHEMA"] = str(ROOT / ".github" / "codex" / "review-schema.json")
    env["REVIEW_PROMPT"] = str(ROOT / ".github" / "codex" / "review-prompt.md")
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        cwd=str(cwd or repo),
        capture_output=True,
        text=True,
        env=env,
    )


def test_empty_diff_is_green_without_calling_the_reviewer(tmp_path: Path) -> None:
    """Пустой диф — определённое состояние, а не «ревью прошло»."""
    _, local = make_repo(tmp_path)
    stub = make_stub(tmp_path, STUB_BROKEN)  # сломанный: вызов был бы виден
    result = run_local(local, stub)
    assert result.returncode == 0
    assert "ревьюировать нечего" in result.stdout


def test_clean_verdict_on_real_diff_is_green(tmp_path: Path) -> None:
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    result = run_local(local, make_stub(tmp_path, STUB_OK))
    assert result.returncode == 0, result.stderr


def test_broken_reviewer_is_mechanical_failure(tmp_path: Path) -> None:
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    result = run_local(local, make_stub(tmp_path, STUB_BROKEN))
    assert result.returncode == 3


def test_stale_base_is_reported_and_run_continues(tmp_path: Path) -> None:
    """Устаревшая база сдвигает диапазон молча — обязана быть названа."""
    remote, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    # Удалённый ушёл вперёд, локальный remote-tracking про это не знает
    (remote / "other.txt").write_text("чужое\n", encoding="utf-8")
    git(remote, "add", "-A")
    git(remote, "commit", "-qm", "чужой коммит")

    result = run_local(local, make_stub(tmp_path, STUB_OK))
    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert "устарел" in combined.lower() or "устаревш" in combined.lower()


def test_missing_origin_head_is_config_error(tmp_path: Path) -> None:
    """Ветку по умолчанию не угадываем — отказываем с подсказкой."""
    _, local = make_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(local), "symbolic-ref", "-d", "refs/remotes/origin/HEAD"],
        check=True,
    )
    result = run_local(local, make_stub(tmp_path, STUB_OK))
    assert result.returncode == 2
    assert "set-head" in result.stderr


def test_explicit_head_reviews_that_ref_not_current_head(tmp_path: Path) -> None:
    """--head — второй конец диапазона; без него хук не смог бы дать подсказку."""
    _, local = make_repo(tmp_path)
    git(local, "switch", "-qc", "other")
    (local / "other-work.txt").write_text("другое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "другая работа")
    other_sha = git(local, "rev-parse", "HEAD")
    git(local, "switch", "-q", "master")

    result = run_local(local, make_stub(tmp_path, STUB_OK), "--head", other_sha)
    assert result.returncode == 0, result.stderr
    assert other_sha[:8] in result.stdout
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `uv run pytest tests/review/test_local.py -q`
Expected: FAIL — скрипта нет.

- [ ] **Step 3: Написать скрипт**

```sh
#!/bin/sh
# Локальный прогон ревью. НЕ производит governance evidence и ничего не
# публикует: его назначение — быстрый фильтр перед пушем, чтобы не жечь раунды
# CI. Авторитет остаётся у чека в CI.
#
# Здесь неизбежно используются промпт, схема и скрипты АВТОРА: локально base и
# head — одно рабочее дерево. В CI они читаются из base, чтобы автор патча не
# переписал инструкции своему ревьюеру; локально такого разделения нет и быть
# не может. Это цена, а не дефект — см. спеку §4.1.
set -eu

kit_dir="${REVIEW_KIT_DIR:-$(dirname "$0")}"
schema="${REVIEW_SCHEMA:-.github/codex/review-schema.json}"
prompt="${REVIEW_PROMPT:-.github/codex/review-prompt.md}"
review_cmd="${REVIEW_CMD:-codex}"

base=""
head_ref="HEAD"
do_fetch=0
format="text"

while [ $# -gt 0 ]; do
    case "$1" in
        --base)   base="${2:-}"; shift 2 ;;
        --head)   head_ref="${2:-}"; shift 2 ;;
        --format) format="${2:-}"; shift 2 ;;
        --fetch)  do_fetch=1; shift ;;
        *) echo "usage: local.sh [--base <ref>] [--head <ref>] [--fetch]" \
                "[--format markdown|text]" >&2; exit 2 ;;
    esac
done

# --- ветка по умолчанию: отказ, а не догадка -------------------------------
# Угадывание по списку завело бы машинно-зависимое поведение. Флот за такое уже
# платил: список зеркал Robin держал устаревшие имена репо и молча был слеп к
# двум активным.
default_branch=""
if [ -z "$base" ]; then
    if ! ref=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null); then
        echo "не задан refs/remotes/origin/HEAD — ветку по умолчанию не угадываем." >&2
        echo "выполните: git remote set-head origin -a   (или укажите --base)" >&2
        exit 2
    fi
    default_branch="${ref#refs/remotes/origin/}"
    base="refs/remotes/origin/$default_branch"
fi

if [ "$do_fetch" -eq 1 ] && [ -n "$default_branch" ]; then
    git fetch -q origin "$default_branch"
fi

# --- свежесть базы ---------------------------------------------------------
# Устаревшая база молча сдвигает диапазон: локальный ревьюер смотрит на диф,
# которого в PR не будет. Показать обязаны; отказывать — нет, это инструмент
# скорости.
if [ -n "$default_branch" ]; then
    if remote_line=$(git ls-remote --heads origin "$default_branch" 2>/dev/null) \
        && [ -n "$remote_line" ]; then
        remote_sha=$(printf '%s' "$remote_line" | cut -f1)
        local_sha=$(git rev-parse "$base")
        if [ "$remote_sha" != "$local_sha" ]; then
            behind=$(git rev-list --count "$base..$remote_sha" 2>/dev/null || echo "?")
            echo "ВНИМАНИЕ: локальная база устарела." >&2
            echo "  локально:  $local_sha" >&2
            echo "  на origin: $remote_sha  (отстаём на $behind коммит(ов))" >&2
            echo "  диапазон посчитан по устаревшей базе; --fetch обновит." >&2
        fi
    else
        echo "ВНИМАНИЕ: свежесть базы не проверена (нет связи с origin)." >&2
    fi
fi

# --- диапазон --------------------------------------------------------------
head_sha=$(git rev-parse "$head_ref")
mb=$(git merge-base "$base" "$head_sha")
echo "база:     $(git rev-parse --short "$base")"
echo "голова:   $(git rev-parse --short "$head_sha")"
echo "диапазон: ${mb}..${head_sha}"

work=$(mktemp -d)
# shellcheck disable=SC2064
trap "rm -rf '$work'" EXIT

git diff "$mb..$head_sha" > "$work/diff.patch"
if [ ! -s "$work/diff.patch" ]; then
    echo "ревьюировать нечего: диф пуст"
    exit 0
fi

sh "$kit_dir/build-prompt.sh" --prompt "$prompt" --diff "$work/diff.patch" \
    > "$work/prompt.txt"

# Промпт идёт на stdin, а не аргументом: диф — недоверенный текст, и в argv он
# не попадает ни здесь, ни в CI. Ревьюер в песочнице read-only: он читает, а не
# правит рабочее дерево.
if ! "$review_cmd" exec --sandbox read-only \
        --output-schema "$schema" \
        --output-last-message "$work/verdict.json" \
        - < "$work/prompt.txt" >/dev/null 2>"$work/reviewer.err"; then
    echo "ревьюер не отработал:" >&2
    cat "$work/reviewer.err" >&2
    exit 3
fi

if [ ! -s "$work/verdict.json" ]; then
    echo "ревьюер завершился успешно, но вердикта не оставил" >&2
    exit 3
fi

sh "$kit_dir/apply-threshold.sh" --verdict "$work/verdict.json" --format "$format"
```

**Замечание для реализатора:** подставной ревьюер в тестах вызывается как
`"$review_cmd" exec …`, поэтому заглушка обязана игнорировать первый аргумент
`exec`. Заглушки в тестах выше это делают (`*) shift ;;`).

- [ ] **Step 4: Сделать исполняемым и прогнать**

```bash
chmod +x scripts/review/local.sh
uv run pytest tests/review/test_local.py -q
```
Expected: PASS, 6 тестов.

- [ ] **Step 5: Мутационная проверка**

```bash
cp scripts/review/local.sh /tmp/orig-l.sh
# 1. Заменить merge-base на сырую базу — диапазон поедет
sed 's/^mb=\$(git merge-base "\$base" "\$head_sha")/mb=$(git rev-parse "$base")/' \
    /tmp/orig-l.sh > scripts/review/local.sh
grep -n '^mb=' scripts/review/local.sh   # сверить, что подставилось
uv run pytest tests/review/test_local.py -q
# 2. Убрать отказ при отсутствии origin/HEAD
cp /tmp/orig-l.sh scripts/review/local.sh
sed 's/^        exit 2$/        base="refs\/remotes\/origin\/master"/' \
    /tmp/orig-l.sh > scripts/review/local.sh
uv run pytest tests/review/test_local.py -q   # обязано покраснеть
# 3. Убрать проверку пустого дифа
cp /tmp/orig-l.sh scripts/review/local.sh
ln=$(grep -n 'if \[ ! -s "\$work\/diff.patch" \]' /tmp/orig-l.sh | cut -d: -f1)
sed "${ln},$((ln+3))d" /tmp/orig-l.sh > scripts/review/local.sh
uv run pytest tests/review/test_local.py -q   # обязано покраснеть
cp /tmp/orig-l.sh scripts/review/local.sh
```

Мутация 1 может остаться зелёной: в тестовом репо база и merge-base часто
совпадают. Если так — **добавить тест**, где база ушла вперёд после
ответвления, и убедиться, что он краснеет. Зелёный мутант здесь означает
незакреплённое требование спеки §6, а не безобидную мутацию.

- [ ] **Step 6: Коммит**

```bash
git add scripts/review/local.sh tests/review/test_local.py
git commit -m "feat(review): local.sh — диапазон по merge-base, показ устаревшей базы, прогон ревьюера"
```

---

### Task 5: `pre-push` хук и его установка

**Files:**
- Create: `.github/hooks/pre-push`
- Create: `scripts/review/install-hook.sh`
- Test: `tests/review/test_pre_push_hook.py`

**Interfaces:**
- Consumes: `local.sh` из Task 4.
- Produces: ничего для последующих задач.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Тесты .github/hooks/pre-push — контракт по ref'ам.

Хук читает stdin как настоящий git, поэтому тесты подают stdin, а не зовут
внутреннюю функцию: иначе тест закрепил бы форму разбора, а не поведение хука.
"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".github" / "hooks" / "pre-push"
ZERO = "0" * 40


def run_hook(stdin: str, head_sha: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Запустить хук с подставным local.sh, который всегда зелёный."""
    stub_kit = tmp_path / "kit"
    stub_kit.mkdir(exist_ok=True)
    stub_local = stub_kit / "local.sh"
    stub_local.write_text("#!/bin/sh\necho 'ревью выполнено'\nexit 0\n", encoding="utf-8")
    stub_local.chmod(0o755)

    env = dict(os.environ)
    env["REVIEW_KIT_DIR"] = str(stub_kit)
    env["REVIEW_HEAD_SHA"] = head_sha
    return subprocess.run(
        ["sh", str(HOOK), "origin", "git@example.com:o/r.git"],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


def test_single_branch_ref_at_head_runs_review(tmp_path: Path) -> None:
    sha = "a" * 40
    line = f"refs/heads/feature {sha} refs/heads/feature {ZERO}\n"
    result = run_hook(line, sha, tmp_path)
    assert result.returncode == 0
    assert "ревью выполнено" in result.stdout


def test_two_refs_are_unsupported(tmp_path: Path) -> None:
    """Пуш нескольких ref'ов проверил бы в лучшем случае один из них."""
    sha = "a" * 40
    stdin = (
        f"refs/heads/one {sha} refs/heads/one {ZERO}\n"
        f"refs/heads/two {'b' * 40} refs/heads/two {ZERO}\n"
    )
    result = run_hook(stdin, sha, tmp_path)
    assert result.returncode != 0
    assert "ревью выполнено" not in result.stdout
    assert "--no-verify" in result.stderr


def test_ref_whose_sha_is_not_head_is_unsupported(tmp_path: Path) -> None:
    """Иначе проверили бы одно дерево, а отправили другое."""
    line = f"refs/heads/other {'b' * 40} refs/heads/other {ZERO}\n"
    result = run_hook(line, "a" * 40, tmp_path)
    assert result.returncode != 0
    assert "--head" in result.stderr, "подсказка обязана быть исполнимой"
    assert "b" * 40 in result.stderr


def test_tag_is_unsupported(tmp_path: Path) -> None:
    sha = "a" * 40
    line = f"refs/tags/v1 {sha} refs/tags/v1 {ZERO}\n"
    result = run_hook(line, sha, tmp_path)
    assert result.returncode != 0


def test_branch_deletion_is_unsupported(tmp_path: Path) -> None:
    line = f"(delete) {ZERO} refs/heads/gone {'c' * 40}\n"
    result = run_hook(line, "a" * 40, tmp_path)
    assert result.returncode != 0


def test_empty_stdin_is_unsupported(tmp_path: Path) -> None:
    """Пустой stdin — не «нечего проверять»; форма не та, о которой договаривались."""
    result = run_hook("", "a" * 40, tmp_path)
    assert result.returncode != 0


def test_any_non_zero_from_local_blocks_the_push(tmp_path: Path) -> None:
    """Спека §8: блокирует ЛЮБОЙ неположительный исход, не только находки.

    Механический сбой ревьюера (3) обязан останавливать пуш наравне с
    находками (1) и ошибкой конфигурации (2). Иначе «инструмент не отработал»
    молча превратилось бы в «замечаний нет» — тот же класс, что чинили в #87.
    """
    sha = "a" * 40
    line = f"refs/heads/feature {sha} refs/heads/feature {ZERO}\n"
    for code in (1, 2, 3):
        stub_kit = tmp_path / f"kit-{code}"
        stub_kit.mkdir()
        stub_local = stub_kit / "local.sh"
        stub_local.write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8")
        stub_local.chmod(0o755)
        env = dict(os.environ)
        env["REVIEW_KIT_DIR"] = str(stub_kit)
        env["REVIEW_HEAD_SHA"] = sha
        result = subprocess.run(
            ["sh", str(HOOK), "origin", "git@example.com:o/r.git"],
            input=line,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0, f"код {code} от local.sh обязан блокировать пуш"
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `uv run pytest tests/review/test_pre_push_hook.py -q`
Expected: FAIL — хука нет.

- [ ] **Step 3: Написать хук**

```sh
#!/bin/sh
# pre-push: ревью того, что РЕАЛЬНО отправляется.
#
# Git передаёт сюда через stdin строки `<local ref> <local sha> <remote ref>
# <remote sha>` — фактически отправляемые ссылки, которые с HEAD совпадать не
# обязаны. Безусловное ревью HEAD означало бы проверить одно дерево, а
# отправить другое: результат выглядел бы доказательством, не будучи им.
#
# Хук НЕ является governance evidence и ничего не публикует. `--no-verify` —
# штатный осознанный обход.
set -eu

kit_dir="${REVIEW_KIT_DIR:-$(git rev-parse --show-toplevel)/scripts/review}"
head_sha="${REVIEW_HEAD_SHA:-$(git rev-parse HEAD)}"

lines=$(cat)
count=$(printf '%s' "$lines" | grep -c . || true)

unsupported() {
    echo "pre-push review: форма пуша не поддержана — $1" >&2
    echo "Проверить нужное вручную:" >&2
    echo "  sh $kit_dir/local.sh --head <sha> [--base <ref>]" >&2
    echo "Либо отправить без ревью: git push --no-verify" >&2
    exit 1
}

[ "$count" -eq 1 ] || unsupported "ожидалась ровно одна ссылка, получено $count"

local_ref=$(printf '%s' "$lines" | awk '{print $1}')
local_sha=$(printf '%s' "$lines" | awk '{print $2}')

case "$local_ref" in
    refs/heads/*) ;;
    *) unsupported "отправляется не ветка ($local_ref)" ;;
esac

case "$local_sha" in
    *[!0]*) ;;
    *) unsupported "удаление ветки" ;;
esac

if [ "$local_sha" != "$head_sha" ]; then
    unsupported "отправляется $local_sha, а HEAD сейчас $head_sha"
fi

sh "$kit_dir/local.sh"
```

- [ ] **Step 4: Прогнать**

Run: `uv run pytest tests/review/test_pre_push_hook.py -q`
Expected: PASS, 6 тестов.

- [ ] **Step 5: Мутационная проверка каждого условия контракта**

```bash
cp .github/hooks/pre-push /tmp/orig-h.sh
for probe in 'count' 'refs/heads' 'local_sha" != "$head_sha'; do
    cp /tmp/orig-h.sh .github/hooks/pre-push
    ln=$(grep -n "$probe" .github/hooks/pre-push | head -1 | cut -d: -f1)
    sed "${ln}s/.*/true/" /tmp/orig-h.sh > .github/hooks/pre-push
    sed -n "${ln}p" .github/hooks/pre-push          # сверить подстановку
    echo "--- мутирован '$probe' ---"
    uv run pytest tests/review/test_pre_push_hook.py -q || true
done
cp /tmp/orig-h.sh .github/hooks/pre-push
```
Expected: каждая из трёх мутаций краснит **свой** тест. Мутация, оставившая набор зелёным, означает вакуумный тест.

- [ ] **Step 6: Написать установщик**

```sh
#!/bin/sh
# Установка pre-push хука. Opt-in: хук не появляется сам при клонировании,
# потому что решение тратить время и токены на каждый пуш — за разработчиком.
set -eu

root=$(git rev-parse --show-toplevel)
src="$root/.github/hooks/pre-push"
dst="$(git rev-parse --git-path hooks)/pre-push"

[ -f "$src" ] || { echo "нет $src" >&2; exit 2; }

if [ -e "$dst" ]; then
    echo "уже есть: $dst" >&2
    echo "снести вручную и повторить, если хотите заменить" >&2
    exit 2
fi

cp "$src" "$dst"
chmod +x "$dst"
echo "установлено: $dst"
echo "обход: git push --no-verify"
```

- [ ] **Step 7: Проверить установщик руками**

```bash
chmod +x scripts/review/install-hook.sh
sh scripts/review/install-hook.sh          # обязано установить
sh scripts/review/install-hook.sh          # обязано отказать кодом 2
rm -f "$(git rev-parse --git-path hooks)/pre-push"
```

- [ ] **Step 8: Документировать в README репо**

Добавить в конец `README.md`:

```markdown
## Локальное ревью перед пушем (opt-in)

Быстрый фильтр, задающий те же вопросы, что чек `codex-review` в CI. Экономит
раунды CI: локальный прогон — секунды, раунд CI — минуты.

```bash
sh scripts/review/install-hook.sh     # поставить pre-push хук
sh scripts/review/local.sh            # разовый прогон без хука
sh scripts/review/local.sh --fetch    # то же, обновив базу с origin
```

Хук блокирует пуш на любом неположительном исходе. Отправить без ревью:
`git push --no-verify` — это штатное осознанное действие, а не нарушение.

**Локальный прогон не является гейтом и не производит governance evidence.** Он
идёт на машине разработчика, его ключом, и снимается флагом; проверить, что он
отработал, некому. Авторитет остаётся у чека `report` в CI. Локальный зелёный
не аргумент в PR.

Локально используются промпт, схема и скрипты из рабочего дерева — включая
незакоммиченные правки. В CI они читаются из `base`, чтобы автор патча не мог
переписать инструкции и порог своему же ревьюеру; локально такого разделения
нет по построению.
```

- [ ] **Step 9: Коммит**

```bash
git add .github/hooks/pre-push scripts/review/install-hook.sh \
        tests/review/test_pre_push_hook.py README.md
git commit -m "feat(review): opt-in pre-push хук с контрактом по ref'ам"
```

---

## Открытые хвосты плана

- `@id:review-kit-diff-marker-hardening` — диф, содержащий `--- ДИФ КОНЕЦ ---`, попадает в промпт как есть (Task 2 это закрепляет тестом). Усиление разделителя — например, детерминированный nonce от хеша дифа — закрыло бы вектор инъекции через содержимое PR, но меняет контракт промпта и в утверждённую спеку не входит. Решение владельца отдельной задачей.
- `@id:review-kit-checksum-bootstrap` — из спеки §12, к этому плану не относится.
- `@id:review-kit-fleet-producer` — из спеки §12, решается при первом потребителе.
