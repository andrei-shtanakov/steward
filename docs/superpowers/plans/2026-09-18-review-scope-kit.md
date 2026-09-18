# Область ревью внутри кита (срез B) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** прозаические байты не доходят до модели ни одним из трёх каналов
(`review-pr.sh`, `local.sh`, `pre-push`), а из смешанного дифа модели уходит
только кодовая часть.

**Architecture:** Фильтр живёт в ките (`scripts/review/local.sh`) и читает
пиненое правило `scripts/review/prose-paths.env` — байт-в-байт вендор-копию SSOT
из devtools. Отфильтровали всё → **код выхода 5**, отличный от «диапазон пуст»
(0). Репозиторий может вернуть себе прозу через `.github/codex/review-scope.env`,
читаемый из base; оператор — разово через `--include-prose`.

**Tech Stack:** POSIX `sh` (`set -eu`, без bash-измов), `git`, `pytest` со
стабами ревьюера (`tests/review/test_local.py`, `test_pre_push_hook.py`,
`test_checksum.py`).

**Spec:** `docs/superpowers/specs/2026-09-18-review-scope-kit-design.md`

## Global Constraints

- POSIX `sh`, файлы под `set -eu`: никаких `[[`, массивов, `pipefail`.
- **Fail-closed в сторону ревью:** правило отсутствует / нечитаемо / не
  разбирается → фильтр не применяется, модель получает полный диф.
- **Fail-closed в сторону отказа:** repo-конфиг с неизвестным значением или
  битый → код 2.
- Код 0 сохраняет прежний смысл («диапазон пуст»); код 5 — новый исход.
- Отпечаток **не меняет** состав и версию: `codex-terminal-review-fingerprint-v1`.
- Пути в `git diff` передаются с магией `:(literal)` — иначе путь с
  глоб-метасимволом стал бы pathspec-шаблоном.
- Правило и repo-конфиг читаются из **base**, не из дерева PR.
- Коммиты завершаются трейлером
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Ветка → PR; прямые коммиты в `master` запрещены. Тесты: `uv run pytest tests/review -q`.

---

## Task 1: Переходный член инвентаря (steward, PR-1)

Смена состава кита не должна дедлочиться о base-чекер: PR-1 меняет только
инвентарь, PR-2 приносит файл.

**Files:**
- Modify: `scripts/review/checksum.sh:100`
- Test: `tests/review/test_checksum.py`

**Interfaces:**
- Produces: инвентарь принимает `scripts/review/prose-paths.env` как переходный
  член (`?path`) — отсутствие файла не краснит, присутствие проверяется по PIN.

- [ ] **Step 1: Написать падающий тест**

```python
def test_prose_paths_is_a_transitional_member(tmp_path: Path) -> None:
    """Переходный член: файла нет — чисто; файл есть и разошёлся с PIN —
    красный."""
    repo = make_kit_repo(tmp_path)  # хелпер файла tests/review/test_checksum.py
    (repo / "scripts" / "review" / "prose-paths.env").unlink(missing_ok=True)
    assert run_checksum(repo).returncode == 0
    (repo / "scripts" / "review" / "prose-paths.env").write_text("PROSE=*.md\n")
    res = run_checksum(repo)
    assert res.returncode != 0
    assert "prose-paths.env" in res.stdout + res.stderr
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `uv run pytest tests/review/test_checksum.py -k prose -q`
Expected: FAIL — член неизвестен инвентарю, посторонний файл игнорируется.

- [ ] **Step 3: Добавить член в инвентарь**

`scripts/review/checksum.sh:100`, в конец строки `required_kit_default`:

```
 ?scripts/review/prose-paths.env
```

Рядом, в комментарии про переходные члены (`:95`), дописать:

```sh
# `scripts/review/prose-paths.env` вошёл переходным членом релиза 2026-09
# (срез B области ревью): PR-1 — этот инвентарь, PR-2 — файл и строка PIN.
# Обязательным становится следующим релизом кита.
```

- [ ] **Step 4: Прогнать тесты**

Run: `uv run pytest tests/review -q`
Expected: PASS.

- [ ] **Step 5: Коммит и PR**

```bash
git switch -c chore/review-scope-inventory
git add scripts/review/checksum.sh tests/review/test_checksum.py
git commit -m "$(cat <<'EOF'
chore(review-kit): prose-paths.env переходным членом инвентаря

Смена состава кита не дедлочится о base-чекер: PR-1 меняет инвентарь, PR-2
приносит файл и строку PIN. Механика переходных членов (?path) заведена для
ровно такого случая.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin HEAD && gh pr create --fill
```

---

## Task 2: Фильтр области и код выхода 5 (steward, PR-2)

**Files:**
- Create: `scripts/review/prose-paths.env` (вендор-копия SSOT devtools)
- Modify: `scripts/review/local.sh` (после `:528`), `scripts/review/PIN`
- Test: `tests/review/test_local.py`

**Interfaces:**
- Consumes: инвентарь из Task 1.
- Produces: код выхода 5 «всё отфильтровано»; флаг `--include-prose`;
  переменная окружения `REVIEW_INCLUDE_PROSE=1`.

- [ ] **Step 1: Завендорить правило**

Скопировать `../devtools/contracts/review-scope/v1/prose-paths.env` в
`scripts/review/prose-paths.env` **байт-в-байт**, дописав provenance первой
строкой шапки (единственное отличие — эта строка уже есть в SSOT как
комментарий, сверить и не дублировать):

```sh
# VENDORED: devtools @ <sha> — contracts/review-scope/v1/prose-paths.env
# SSOT там; здесь пиненая копия. Правка — только ре-вендором, не на месте.
```

Пересчитать строку PIN:

```bash
shasum -a 256 scripts/review/prose-paths.env
```

и добавить её в `scripts/review/PIN`, а в шапку PIN — строку про нового члена.

- [ ] **Step 2: Написать падающие тесты**

В `tests/review/test_local.py` (хелперы `make_repo`, `run_local`, `git` уже есть):

```python
def test_prose_only_range_exits_five_without_calling_reviewer(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "note.md").write_text("prose\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "prose only")
    stub = make_stub(tmp_path, "echo REVIEWER_WAS_CALLED >&2; exit 0")
    res = run_local(repo, stub)
    assert res.returncode == 5, res.stderr
    assert "REVIEWER_WAS_CALLED" not in res.stderr
    assert "всё отфильтровано" in res.stderr


def test_empty_range_still_exits_zero(tmp_path: Path) -> None:
    """Код 0 сохраняет прежний смысл: пуст сам диапазон, а не остаток."""
    repo, _ = make_repo(tmp_path)
    stub = make_stub(tmp_path, "exit 0")
    res = run_local(repo, stub)
    assert res.returncode == 0, res.stderr
    assert "диф пуст" in res.stderr


def test_mixed_range_sends_only_code_to_the_model(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "note.md").write_text("prose\n")
    (repo / "tool.py").write_text("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "mixed")
    dump = tmp_path / "prompt-seen.txt"
    stub = make_stub(tmp_path, f'cat > "{dump}"; exit 0')
    assert run_local(repo, stub).returncode == 0
    seen = dump.read_text()
    assert "tool.py" in seen
    assert "docs/note.md" not in seen


def test_code_only_range_diff_is_unchanged(tmp_path: Path) -> None:
    """Кодовый PR фильтр не трогает — иначе поехал бы отпечаток и
    наследование прошлых вердиктов."""
    repo, _ = make_repo(tmp_path)
    (repo / "tool.py").write_text("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "code only")
    dump = tmp_path / "prompt-seen.txt"
    stub = make_stub(tmp_path, f'cat > "{dump}"; exit 0')
    assert run_local(repo, stub).returncode == 0
    assert "tool.py" in dump.read_text()


def test_include_prose_flag_disables_the_filter(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "note.md").write_text("prose\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "prose only")
    dump = tmp_path / "prompt-seen.txt"
    stub = make_stub(tmp_path, f'cat > "{dump}"; exit 0')
    assert run_local(repo, stub, "--include-prose").returncode == 0
    assert "docs/note.md" in dump.read_text()
    assert run_local(
        repo, stub, env_overrides={"REVIEW_INCLUDE_PROSE": "1"}
    ).returncode == 0


def test_missing_rule_file_means_full_diff(tmp_path: Path) -> None:
    """Fail-closed в сторону ревью: правила нет — фильтра нет."""
    repo, _ = make_repo(tmp_path)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "note.md").write_text("prose\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "prose only")
    dump = tmp_path / "prompt-seen.txt"
    stub = make_stub(tmp_path, f'cat > "{dump}"; exit 0')
    res = run_local(
        repo, stub, env_overrides={"REVIEW_SCOPE_RULES": str(tmp_path / "nope.env")}
    )
    assert res.returncode == 0, res.stderr
    assert "docs/note.md" in dump.read_text()
    assert "правило области ревью" in res.stderr


def test_fingerprint_mode_on_filtered_range_prints_nothing_and_exits_five(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "note.md").write_text("prose\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "prose only")
    stub = make_stub(tmp_path, "exit 0")
    res = run_local(repo, stub, "--fingerprint-only")
    assert res.returncode == 5, res.stderr
    assert res.stdout.strip() == ""


def test_path_with_glob_metachar_is_matched_literally(tmp_path: Path) -> None:
    """Путь с `*` не должен толковаться как pathspec-шаблон."""
    repo, _ = make_repo(tmp_path)
    (repo / "a[1].py").write_text("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "odd name")
    dump = tmp_path / "prompt-seen.txt"
    stub = make_stub(tmp_path, f'cat > "{dump}"; exit 0')
    assert run_local(repo, stub).returncode == 0
    assert "a[1].py" in dump.read_text()
```

- [ ] **Step 3: Прогнать — убедиться, что падают**

Run: `uv run pytest tests/review/test_local.py -k "prose or filtered or glob_metachar" -q`
Expected: FAIL — фильтра нет, прозаический диапазон уходит ревьюеру.

- [ ] **Step 4: Разбор флага и правила**

К разбору аргументов `local.sh` (рядом с `--fingerprint-only`, `:239`):

```sh
        --include-prose) include_prose=1; shift ;;
```

Инициализация рядом с прочими флагами:

```sh
include_prose="${REVIEW_INCLUDE_PROSE:+1}"
include_prose="${include_prose:-0}"
scope_rules="${REVIEW_SCOPE_RULES:-$kit_dir/prose-paths.env}"
```

- [ ] **Step 5: Реализовать классификацию и фильтр**

Сразу после `git diff "$mb..$head_sha" > "$work/diff.patch"` (`:528`) и
существующей проверки пустого дифа (`:529-536`) — то есть «диапазон пуст»
остаётся кодом 0 и решается раньше:

```sh
# --- Область ревью: проза модели не показывается (спека среза B) ------------
# Фильтр стоит ПОСЛЕ проверки пустого дифа: «пуст диапазон» (0) и «пуст
# остаток после фильтра» (5) — разные факты, и обвязка обязана различать их,
# иначе прозаический PR получил бы approve, которого не выносила модель.
#
# Правило читается из ФАЙЛА КИТА, а он на авторитетном канале берётся из
# доверенного чекаута, не из дерева PR: PR не может расширить список того,
# что скрыто от его собственного ревьюера.
#
# Fail-closed в сторону ревью: нет файла, нечитаем, нет PROSE — фильтр не
# применяется, модель видит полный диф. Недоказанная проза не прячется.
prose_globs=""
code_globs=""
if [ "$include_prose" -eq 1 ]; then
    info "--include-prose: фильтр области ревью отключён на этом прогоне"
elif [ ! -r "$scope_rules" ]; then
    info "правило области ревью нечитаемо ($scope_rules) — фильтр не" \
        "применяется, ревьюер получает полный диф"
else
    prose_globs=$(sed -n 's/^[[:space:]]*PROSE=//p' "$scope_rules" | tr '\n' ' ')
    code_globs=$(sed -n 's/^[[:space:]]*CODE_OVERRIDE=//p' "$scope_rules" \
        | tr '\n' ' ')
    if [ -z "$prose_globs" ]; then
        info "правило области ревью не называет PROSE ($scope_rules) —" \
            "фильтр не применяется"
    fi
fi

# 0 — путь проза, 1 — код. CODE_OVERRIDE сильнее PROSE. `set -f` обязателен:
# неквотированный глоб иначе развернулся бы по содержимому cwd вместо того,
# чтобы остаться шаблоном для `case`; снимается перед КАЖДЫМ возвратом.
path_is_prose() {
    set -f
    for _g in $code_globs; do
        case "$1" in $_g) set +f; return 1 ;; esac
    done
    for _g in $prose_globs; do
        case "$1" in $_g) set +f; return 0 ;; esac
    done
    set +f
    return 1
}

if [ -n "$prose_globs" ]; then
    # Пути в позиционные параметры: список кодовых путей уходит в git с
    # магией `:(literal)`, иначе путь с глоб-метасимволом стал бы
    # pathspec-ШАБЛОНОМ и подобрал бы чужие файлы. --no-renames: и старый, и
    # новый путь переименования проходят классификацию.
    _old_ifs=$IFS
    IFS='
'
    set -f
    set --
    for _f in $(git diff --no-renames --name-only "$mb..$head_sha"); do
        IFS=$_old_ifs
        set +f
        path_is_prose "$_f" || set -- "$@" ":(literal)$_f"
        IFS='
'
        set -f
    done
    IFS=$_old_ifs
    set +f
    if [ "$#" -eq 0 ]; then
        info "ревьюировать нечего: всё отфильтровано как проза" \
            "(правило $scope_rules)"
        exit 5
    fi
    git diff "$mb..$head_sha" -- "$@" > "$work/diff.patch"
fi
```

- [ ] **Step 6: Прогнать тесты**

Run: `uv run pytest tests/review -q`
Expected: PASS целиком (264 прежних + новые).

- [ ] **Step 7: Объявить код 5 в контракте кита**

В шапке `local.sh`, где перечислены коды выхода, добавить строку:

```
#   5 — ревьюировать нечего: исходный диф непуст, но после фильтра области
#       не осталось ничего. НЕ то же, что 0 («пуст сам диапазон»): потребитель
#       обязан различать, иначе опубликует approve, которого не было.
```

То же — в `README` кита, если он перечисляет коды.

- [ ] **Step 8: Коммит и PR**

```bash
git switch -c feat/review-scope-filter
git add scripts/review/local.sh scripts/review/prose-paths.env scripts/review/PIN tests/review/test_local.py
git commit -m "$(cat <<'EOF'
feat(review-kit): фильтр области ревью и код выхода 5

Проза вычитается из дифа до вызова модели: прозаический диапазон даёт код 5
(«всё отфильтровано»), смешанный — уходит модели одной кодовой частью. Код 0
сохраняет прежний смысл, «пуст сам диапазон»: слить их значило бы отдать
обвязке approve, которого не выносила модель.

Правило — пиненая вендор-копия SSOT devtools, член кита. Нет файла или нет
PROSE — фильтр не применяется: недоказанная проза не прячется от ревьюера.
Пути уходят в git с магией :(literal), переименования классифицируются по
обоим путям.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin HEAD && gh pr create --fill
```

---

## Task 3: `pre-push` считает 5 успехом (steward, тот же PR-2)

**Files:**
- Modify: `.github/hooks/pre-push:151`
- Test: `tests/review/test_pre_push_hook.py`

- [ ] **Step 1: Написать падающий тест**

```python
def test_exit_five_is_a_successful_skip(tmp_path: Path) -> None:
    """Прозаический пуш не блокируется: код 5 кита — пропуск, не отказ."""
    repo = make_hook_repo(tmp_path)  # хелпер файла
    kit = repo / "scripts" / "review"
    (kit / "local.sh").write_text("#!/bin/sh\nexit 5\n")
    (kit / "local.sh").chmod(0o755)
    assert run_hook(repo).returncode == 0


def test_exit_one_still_blocks(tmp_path: Path) -> None:
    repo = make_hook_repo(tmp_path)
    kit = repo / "scripts" / "review"
    (kit / "local.sh").write_text("#!/bin/sh\nexit 1\n")
    (kit / "local.sh").chmod(0o755)
    assert run_hook(repo).returncode == 1
```

- [ ] **Step 2: Прогнать — убедиться, что первый падает**

Run: `uv run pytest tests/review/test_pre_push_hook.py -k exit_five -q`
Expected: FAIL (returncode 5, пуш заблокирован).

- [ ] **Step 3: Обработать код 5 в хуке**

Последняя строка `.github/hooks/pre-push` (`:151`) заменяется на:

```sh
# Код 5 кита — «ревьюировать нечего: всё отфильтровано» (срез B): для пуша это
# УСПЕХ, а не отказ. Прочие ненулевые коды (1 находки, 2 конфигурация,
# 3 ревьюер не отработал) по-прежнему блокируют пуш.
set +e
sh "$kit_dir/local.sh" --head "$local_sha" --remote "$remote_name"
kit_code=$?
set -e
[ "$kit_code" -ne 5 ] || exit 0
exit "$kit_code"
```

- [ ] **Step 4: Прогнать тесты**

Run: `uv run pytest tests/review -q`
Expected: PASS.

- [ ] **Step 5: Коммит в ту же ветку**

```bash
git add .github/hooks/pre-push tests/review/test_pre_push_hook.py
git commit -m "$(cat <<'EOF'
fix(pre-push): код 5 кита — успешный пропуск, не отказ

Хук зовёт local.sh последней строкой, и под set -eu код кита становится кодом
хука: с новым китом прозаическая ветка перестала бы пушиться вовсе. Ложного
approve это не даёт, но ломает доступность. 1/2/3 по-прежнему блокируют пуш.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Repo-owned `review-scope.env` (steward, тот же PR-2)

**Files:**
- Modify: `scripts/review/local.sh` (сразу после разбора правила, Task 2 Step 5)
- Test: `tests/review/test_local.py`

**Interfaces:**
- Consumes: `prose_globs`/`code_globs` из Task 2.
- Produces: `PROSE_REVIEW` ∈ {`off`,`paths`,`all`}; при `paths` —
  `PROSE_REVIEW_PATHS`; конфиг читается из base.

- [ ] **Step 1: Написать падающие тесты**

```python
def _write_scope_config(repo: Path, body: str) -> None:
    """Конфиг обязан прийти из BASE — коммитим его в базовую ветку."""
    cfg = repo / ".github" / "codex" / "review-scope.env"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "scope config")


def test_prose_review_paths_returns_named_prose_to_the_model(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    _write_scope_config(repo, "PROSE_REVIEW=paths\nPROSE_REVIEW_PATHS=authored/*\n")
    git(repo, "checkout", "-qb", "work")
    (repo / "authored").mkdir(exist_ok=True)
    (repo / "authored" / "rule.md").write_text("prose\n")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "other.md").write_text("prose\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "prose")
    dump = tmp_path / "prompt-seen.txt"
    stub = make_stub(tmp_path, f'cat > "{dump}"; exit 0')
    assert run_local(repo, stub).returncode == 0
    seen = dump.read_text()
    assert "authored/rule.md" in seen
    assert "docs/other.md" not in seen


def test_prose_review_all_disables_the_filter(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    _write_scope_config(repo, "PROSE_REVIEW=all\n")
    git(repo, "checkout", "-qb", "work")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "note.md").write_text("prose\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "prose")
    dump = tmp_path / "prompt-seen.txt"
    stub = make_stub(tmp_path, f'cat > "{dump}"; exit 0')
    assert run_local(repo, stub).returncode == 0
    assert "docs/note.md" in dump.read_text()


def test_unknown_prose_review_value_is_config_error(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    _write_scope_config(repo, "PROSE_REVIEW=maybe\n")
    git(repo, "checkout", "-qb", "work")
    (repo / "tool.py").write_text("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "code")
    stub = make_stub(tmp_path, "exit 0")
    res = run_local(repo, stub)
    assert res.returncode == 2, res.stdout
    assert "PROSE_REVIEW" in res.stderr


def test_config_from_head_does_not_apply_to_its_own_pr(tmp_path: Path) -> None:
    """Конфиг читается из base: PR не отключает собственное ревью."""
    repo, _ = make_repo(tmp_path)
    git(repo, "checkout", "-qb", "work")
    _write_scope_config(repo, "PROSE_REVIEW=all\n")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "note.md").write_text("prose\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "prose + config")
    dump = tmp_path / "prompt-seen.txt"
    stub = make_stub(tmp_path, f'cat > "{dump}"; exit 0')
    res = run_local(repo, stub)
    assert res.returncode == 0, res.stderr
    assert "docs/note.md" not in dump.read_text()
```

- [ ] **Step 2: Прогнать — убедиться, что падают**

Run: `uv run pytest tests/review/test_local.py -k prose_review -q`
Expected: FAIL — конфиг не читается.

- [ ] **Step 3: Реализовать чтение конфига из base**

Перед блоком фильтрации (Task 2 Step 5), после разбора правила:

```sh
# Repo-owned политика: репозиторий может ВЕРНУТЬ себе прозу, но не спрятать
# новое — расширение skip-набора живёт только в пиненом правиле. Читается из
# BASE (`git show "$mb:путь"`), не из дерева: иначе PR отключал бы собственное
# ревью. Отсутствие файла — штатное умолчание `off`.
scope_cfg_path=".github/codex/review-scope.env"
prose_review=off
prose_review_paths=""
if scope_cfg=$(git show "$mb:$scope_cfg_path" 2>/dev/null); then
    prose_review=$(printf '%s\n' "$scope_cfg" \
        | sed -n 's/^[[:space:]]*PROSE_REVIEW=//p' | tr -d '[:space:]')
    prose_review="${prose_review:-off}"
    prose_review_paths=$(printf '%s\n' "$scope_cfg" \
        | sed -n 's/^[[:space:]]*PROSE_REVIEW_PATHS=//p' | tr '\n' ' ')
    case "$prose_review" in
        off|all) ;;
        paths)
            [ -n "$prose_review_paths" ] || {
                echo "PROSE_REVIEW=paths без PROSE_REVIEW_PATHS в" \
                    "$scope_cfg_path — нечего возвращать ревьюеру." >&2
                exit 2
            } ;;
        *)
            # Неизвестное значение — отказ, а не умолчание: неразобранный
            # конфиг означает «мы не знаем, что репо велел ревьюить», и
            # читать это в пользу МЕНЬШЕГО ревью нельзя.
            echo "неизвестное PROSE_REVIEW='$prose_review' в" \
                "$scope_cfg_path (ожидалось off|paths|all)." >&2
            exit 2 ;;
    esac
fi
[ "$prose_review" != "all" ] || include_prose=1
```

А в `path_is_prose`, первым циклом — возврат названных путей в код:

```sh
    for _g in $prose_review_paths; do
        case "$1" in $_g) set +f; return 1 ;; esac
    done
```

(порядок: `PROSE_REVIEW_PATHS` и `CODE_OVERRIDE` оба сильнее `PROSE`; между
собой не конфликтуют — оба означают «это ревьюируем».)

- [ ] **Step 4: Прогнать тесты**

Run: `uv run pytest tests/review -q`
Expected: PASS.

- [ ] **Step 5: Документировать в README кита**

Добавить раздел про `review-scope.env` с таблицей значений и явной фразой:
«конфиг умеет только вернуть файлы ревьюеру; спрятать новое можно лишь правкой
пиненого `prose-paths.env`».

- [ ] **Step 6: Коммит в ту же ветку**

```bash
git add scripts/review/local.sh tests/review/test_local.py README.md
git commit -m "$(cat <<'EOF'
feat(review-kit): repo-owned review-scope.env — только в сторону большего ревью

Репозиторий может вернуть модели свои прозаические артефакты (PROSE_REVIEW=
paths|all), но не спрятать новые: расширение skip-набора остаётся только в
пиненом prose-paths.env. Конфиг читается из base — иначе PR отключал бы
собственное ревью. Неизвестное значение — код 2: неразобранный конфиг нельзя
читать в пользу меньшего ревью.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Обвязка знает код 5 (devtools, отдельный PR)

**Files:**
- Modify: `review-pr.sh` (ветвление `case "$kit_code"`, `:1168`; разбор флагов; `usage()`)
- Test: `tests/test_review_pr.py`

**Interfaces:**
- Consumes: код 5 кита из Task 2.
- Produces: код 5 → тот же путь scope-аттестации, что у ветки прозы среза A;
  флаг `--include-prose` обходит и раннюю классификацию, и фильтр кита.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_kit_exit_five_publishes_attestation(fleet: Fleet) -> None:
    """Кит отфильтровал всё — обвязка публикует аттестацию, а не approve."""
    _seed_files(fleet, "src/tool.py")  # обвязка считает PR кодовым
    fleet.write_kit(LOCAL_SH_STUB.replace("exit 0", "exit 5"))
    res = fleet.run("demo", "7")
    assert res.returncode == 0, res.stderr
    body = fleet.body_out.read_text()
    assert "Automated scope attestation" in body
    assert "codex-terminal-review" not in body


def test_kit_exit_five_does_not_charge_budget(fleet: Fleet) -> None:
    _seed_files(fleet, "src/tool.py")
    fleet.write_kit(LOCAL_SH_STUB.replace("exit 0", "exit 5"))
    fleet.run("demo", "7")
    ledger = fleet.tmp / "review-budget" / "andrei-shtanakov_demo-7.log"
    assert not ledger.exists()


def test_include_prose_bypasses_early_attestation(fleet: Fleet) -> None:
    _seed_files(fleet, "docs/guide.md")
    res = fleet.run("demo", "7", "--include-prose")
    assert res.returncode == 0, res.stderr
    assert _kit_calls(fleet) != []
    assert "--include-prose" in _kit_calls(fleet)[0]
    assert "Automated scope attestation" not in fleet.body_out.read_text()
```

- [ ] **Step 2: Прогнать — убедиться, что падают**

Run: `uv run pytest tests/test_review_pr.py -k "exit_five or include_prose" -v`
Expected: FAIL — код 5 попадает в `*)` и даёт `die 3`.

- [ ] **Step 3: Вынести построение аттестации в функцию**

Тело аттестации сейчас строится внутри ветки прозы. Вынести в
`publish_scope_attestation()` без изменения текста (проверяется существующими
тестами среза A), чтобы у неё стало два вызывающих: ранняя классификация и
код 5.

- [ ] **Step 4: Обработать код 5**

В `case "$kit_code"` (`:1168`), перед `2|3)`:

```sh
    # Кит отфильтровал прозу целиком (срез B). Модель не звалась, круг не
    # списывается. Обвязка могла классифицировать PR как кодовый — репо-конфиг
    # и пиненое правило кита видит именно кит, и его слово здесь последнее.
    5) publish_scope_attestation; exit 0 ;;
```

- [ ] **Step 5: Добавить `--include-prose`**

Разбор флага рядом с `--fresh`; в ранней ветке прозы — условие
`[ "$include_prose" -eq 0 ]`; в вызов кита — проброс флага; строка в `usage()`.

- [ ] **Step 6: Прогнать тесты**

Run: `uv run pytest tests/test_review_pr.py -q`
Expected: PASS.

- [ ] **Step 7: Обновить `CLAUDE.md` devtools и коммит**

К буллету про область ревью добавить: код 5 кита, `--include-prose`,
repo-конфиг `review-scope.env` (читается из base, только расширяет ревью).

```bash
git add review-pr.sh tests/test_review_pr.py CLAUDE.md
git commit -m "$(cat <<'EOF'
feat(review-scope): обвязка знает код 5 кита и флаг --include-prose

Кит среза B возвращает 5, когда после фильтра области ревьюировать нечего.
Обвязка публикует на нём scope-аттестацию тем же путём, что и ранняя
классификация, и не списывает круг: модель не звалась. Прежнее умолчание
(незнакомый код → die 3) сохранено для всех прочих значений.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Волна ре-вендора (23 репо, по PR на репо)

**Files (в каждом репо):** `scripts/review/local.sh`, `scripts/review/checksum.sh`,
`scripts/review/prose-paths.env`, `scripts/review/PIN`, `.github/hooks/pre-push`

- [ ] **Step 1: Собрать список потребителей**

```bash
cd /Users/Andrei_Shtanakov/labs/all_ai_orchestrators
ls -d */scripts/review | sed 's|/scripts/review||' | grep -v '^steward$'
```

Ожидается 23 репо.

- [ ] **Step 2: Прогнать по одному репо и убедиться, что рецепт верен**

Взять `devtools` (он же потребитель, и там уже есть тесты обвязки):

```bash
R=devtools
cd /Users/Andrei_Shtanakov/labs/all_ai_orchestrators/$R
git switch -c chore/review-kit-revendor-scope
for f in local.sh checksum.sh prose-paths.env; do
  cp ../steward/scripts/review/$f scripts/review/$f
done
cp ../steward/.github/hooks/pre-push .github/hooks/pre-push
sh scripts/review/checksum.sh   # ожидается красное: PIN ещё старый
```

Пересчитать PIN:

```bash
for f in scripts/review/*.sh scripts/review/harness-claude scripts/review/prose-paths.env .github/codex/review-schema.json; do
  shasum -a 256 "$f"
done
```

вписать строки в `scripts/review/PIN`, обновить шапку (`SOURCE: steward @ <sha>`),
затем `sh scripts/review/checksum.sh` — ожидается чисто.

- [ ] **Step 3: Проверить на этом репо живьём**

Run: `sh scripts/review/local.sh --base master --head HEAD --fingerprint-only`
Expected: на прозаической ветке — код 5, stdout пуст; на кодовой — 64-hex.

- [ ] **Step 4: Коммит и PR, дождаться мержа**

```bash
git add scripts/review .github/hooks/pre-push
git commit -m "$(cat <<'EOF'
chore(review-kit): ре-вендор — фильтр области ревью и код выхода 5

Кит @ steward <sha>. Прозаический диапазон больше не доходит до модели ни
локальным прогоном, ни хуком; смешанный уходит одной кодовой частью.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin HEAD && gh pr create --fill
```

- [ ] **Step 5: Повторить по остальным 22 репо**

Рецепт тот же. Репо без `.github/hooks/pre-push` — шаг с хуком пропустить.
Репо, где `checksum.sh` после копирования краснеет по ДРУГИМ членам, —
остановиться и сказать владельцу: это не задача волны, а протухшая копия.

- [ ] **Step 6: Сверка волны**

```bash
cd /Users/Andrei_Shtanakov/labs/all_ai_orchestrators
for r in $(ls -d */scripts/review | sed 's|/scripts/review||'); do
  printf '%-22s ' "$r"
  if [ -f "$r/scripts/review/prose-paths.env" ]; then
    shasum -a 256 "$r/scripts/review/prose-paths.env" | cut -c1-8
  else
    echo "НЕТ ФАЙЛА"
  fi
done
```

Expected: одинаковый хеш во всех репо, «НЕТ ФАЙЛА» ни одного.

---

## Самопроверка плана

**Покрытие спеки.** D1 — Task 2 Step 5; D2 — Task 2 (код 5, fp-режим) + Task 5
(маппинг) + Task 3 (хук); D3 — Task 1 + Task 2 Step 1 + Task 6; D4 — Task 4;
D5 — Task 2 Step 4 и Task 5 Step 5; D6 — проверяется тестом
`test_code_only_range_diff_is_unchanged` (кодовый диф не меняется → отпечаток
тот же); D7 — кода не требует, запрет зафиксирован в спеке. Матрица
совместимости §D2 — тестами Task 5 (новая обвязка) и существующим умолчанием
`die 3` (старая, проверено при написании спеки).

**Плейсхолдеры.** Нет: каждый шаг несёт итоговый код, тест или команду.
`<sha>` в текстах ре-вендора подставляется исполнителем из фактического коммита
steward — это единственное вычисляемое значение, и оно названо явно.

**Согласованность имён.** `include_prose`, `scope_rules`, `prose_globs`,
`code_globs`, `prose_review`, `prose_review_paths`, `path_is_prose`,
`publish_scope_attestation` — одни и те же во всех задачах; `make_repo`,
`run_local`, `make_stub`, `git` — существующие хелперы `tests/review/test_local.py`;
`fleet`, `_seed_files`, `_kit_calls`, `LOCAL_SH_STUB` — существующие хелперы
`devtools/tests/test_review_pr.py`.
