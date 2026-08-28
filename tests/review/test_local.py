"""Тесты scripts/review/local.sh — диапазон, свежесть базы, пустой диф."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

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

STUB_MAJOR_FINDING = """#!/bin/sh
# Подставной ревьюер: пишет вердикт с находкой уровня major — единственный
# способ проверить, что local.sh реально пробрасывает код 1 наружу.
out=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o|--output-last-message) out="$2"; shift 2 ;;
        *) shift ;;
    esac
done
cat > /dev/null
printf '{"findings":[{"severity":"major","title":"t","file":"a.py","line":1,"scenario":"s","observed_result":"o","expected_result":"e","evidence":[{"file":"b.py","line":2,"reason":"r"}],"confidence":"high"}],"note":"stub"}' \\
    > "$out"
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
    subprocess.run(["git", "clone", "-q", str(remote), str(local)], check=True, capture_output=True)
    git(local, "config", "user.email", "t@t")
    git(local, "config", "user.name", "t")
    git(local, "remote", "set-head", "origin", "-a")
    return remote, local


def run_local(
    repo: Path,
    stub: str,
    *args: str,
    cwd: Path | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["REVIEW_CMD"] = stub
    env["REVIEW_KIT_DIR"] = str(ROOT / "scripts" / "review")
    env["REVIEW_SCHEMA"] = str(ROOT / ".github" / "codex" / "review-schema.json")
    env["REVIEW_PROMPT"] = str(ROOT / ".github" / "codex" / "review-prompt.md")
    if env_overrides:
        env.update(env_overrides)
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


def test_review_cmd_with_extra_flags_is_a_command_not_a_binary_path(tmp_path: Path) -> None:
    """`REVIEW_CMD='<путь> --extra-flag value'` — раньше `"$review_cmd" exec
    ...` искал файл, буквально названный "<путь> --extra-flag value" целиком
    (ENOENT → код 3 "ревьюер не отработал"), хотя REVIEW_CMD задумана как
    команда целиком, включая флаги, а `exec` — часть умолчания, не жёстко
    приклеенный литерал. Стаб проверяет, что реально ПОЛУЧИЛ переданный флаг
    в argv — не просто что что-то запустилось."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    stub_with_flag_check = """#!/bin/sh
saw_extra=0
out=""
while [ $# -gt 0 ]; do
    case "$1" in
        --extra-marker) saw_extra=1; shift ;;
        -o|--output-last-message) out="$2"; shift 2 ;;
        *) shift ;;
    esac
done
cat > /dev/null
if [ "$saw_extra" -ne 1 ]; then
    echo "REVIEW_CMD не донёс свой собственный флаг до argv" >&2
    exit 1
fi
printf '{"findings":[],"note":"stub"}' > "$out"
"""
    stub_path = make_stub(tmp_path, stub_with_flag_check)

    result = run_local(
        local,
        stub_path,
        env_overrides={"REVIEW_CMD": f"{stub_path} --extra-marker"},
    )
    assert result.returncode == 0, result.stderr


def test_missing_schema_is_config_error_not_reviewer_failure(tmp_path: Path) -> None:
    """Отсутствующая схема раньше доходила до `codex exec --output-schema
    <нет-файла>` и падала уже там кодом 3 — конфигурационный отказ читался
    как механический сбой ревьюера. Preflight обязан поймать это ДО вызова."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    result = run_local(
        local,
        make_stub(tmp_path, STUB_BROKEN),  # сломанный: вызов был бы виден
        env_overrides={"REVIEW_SCHEMA": str(tmp_path / "нет-такой-схемы.json")},
    )
    assert result.returncode == 2
    assert "нет файла схемы" in result.stderr


def test_missing_prompt_is_config_error(tmp_path: Path) -> None:
    """Симметрично схеме: отсутствующий промпт — тоже конфигурационный отказ,
    не сбой ревьюера. Сегодня это и так держится `build-prompt.sh`
    (пробрасывается через `set -e`), но контракт стоит проверить напрямую, а
    не полагаться на транзитивное поведение соседнего скрипта."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    result = run_local(
        local,
        make_stub(tmp_path, STUB_BROKEN),
        env_overrides={"REVIEW_PROMPT": str(tmp_path / "нет-такого-промпта.md")},
    )
    assert result.returncode == 2
    assert "нет файла инструкций" in result.stderr


def _chmod_000_actually_blocks_reads(path: Path) -> bool:
    """root (и некоторые FS) игнорирует права доступа — `chmod 000` там не
    защита. Проверяем эмпирически на реальном файле теста, а не полагаемся
    на допущение про пользователя/платформу (см. тот же приём в
    test_build_prompt.py — не дублирую импортом, чтобы файлы тестов
    оставались независимыми друг от друга)."""
    path.chmod(0o000)
    try:
        path.read_text(encoding="utf-8")
        return False
    except PermissionError:
        return True
    finally:
        path.chmod(0o644)


def test_unreadable_schema_is_config_error_not_reviewer_failure(tmp_path: Path) -> None:
    """`-f` проверяет существование, не читаемость: нечитаемая схема раньше
    доходила до `codex exec --output-schema <нечитаемый-файл>`, тот
    отказывал, и получался код 3 "ревьюер не отработал" вместо
    контрактного 2 — тот же класс, что закрыт в build-prompt.sh (находка
    21), но остававшийся открытым здесь для схемы (находка 24)."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    schema_copy = tmp_path / "copy-schema.json"
    schema_copy.write_text('{"type": "object"}', encoding="utf-8")
    if not _chmod_000_actually_blocks_reads(schema_copy):
        pytest.skip("chmod 000 не блокирует чтение под текущим пользователем/FS")

    schema_copy.chmod(0o000)
    try:
        result = run_local(
            local,
            make_stub(tmp_path, STUB_BROKEN),
            env_overrides={"REVIEW_SCHEMA": str(schema_copy)},
        )
    finally:
        schema_copy.chmod(0o644)
    assert result.returncode == 2
    assert "файл схемы нечитаем" in result.stderr


def test_unreadable_prompt_local_is_config_error_not_reviewer_failure(tmp_path: Path) -> None:
    """Симметрично схеме, здесь же (не только транзитивно через
    build-prompt.sh)."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    prompt_copy = tmp_path / "copy-prompt.md"
    prompt_copy.write_text("инструкции", encoding="utf-8")
    if not _chmod_000_actually_blocks_reads(prompt_copy):
        pytest.skip("chmod 000 не блокирует чтение под текущим пользователем/FS")

    prompt_copy.chmod(0o000)
    try:
        result = run_local(
            local,
            make_stub(tmp_path, STUB_BROKEN),
            env_overrides={"REVIEW_PROMPT": str(prompt_copy)},
        )
    finally:
        prompt_copy.chmod(0o644)
    assert result.returncode == 2
    assert "файл инструкций нечитаем" in result.stderr


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


def test_outside_git_repo_is_config_error(tmp_path: Path) -> None:
    """Вне git-репозитория `git rev-parse --show-toplevel` сам падает 128 —
    страж обязан перевести это в код кита (2), а не дать утечь коду вне
    объявленного §7 набора 0/1/2/3."""
    not_a_repo = tmp_path / "не-репо"
    not_a_repo.mkdir()
    result = run_local(not_a_repo, make_stub(tmp_path, STUB_OK))
    assert result.returncode == 2
    assert "128" not in result.stderr
    assert "git-репозитория" in result.stderr


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


def test_single_non_origin_remote_is_used_without_explicit_flag(tmp_path: Path) -> None:
    """`git clone -o github`, других remote'ов нет, `refs/remotes/github/HEAD`
    настроен штатно (`git remote set-head github -a`). Хук через --remote
    уже работает; документированный ручной `sh scripts/review/local.sh` не
    обязан падать на буквальном "origin" в корректно настроенном
    репозитории — единственный remote не двусмысленен, это не догадка."""
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
        ["git", "clone", "-q", "-o", "github", str(remote), str(local)],
        check=True,
        capture_output=True,
    )
    git(local, "config", "user.email", "t@t")
    git(local, "config", "user.name", "t")
    git(local, "remote", "set-head", "github", "-a")
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    result = run_local(local, make_stub(tmp_path, STUB_OK))
    assert result.returncode == 0, result.stderr
    assert "не задан refs/remotes/origin/HEAD" not in result.stderr


def test_multiple_remotes_without_origin_refuses_and_lists_them(tmp_path: Path) -> None:
    """Несколько remote'ов, `origin` среди них нет — угадывать, какой из них
    имелся в виду, нельзя (то же машинно-зависимое поведение, против
    которого написан §6). Отказ, перечисляющий найденные remote'ы, и
    подсказка про --remote."""
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
        ["git", "clone", "-q", "-o", "github", str(remote), str(local)],
        check=True,
        capture_output=True,
    )
    git(local, "config", "user.email", "t@t")
    git(local, "config", "user.name", "t")
    git(local, "remote", "set-head", "github", "-a")
    git(local, "remote", "add", "upstream", str(remote))

    result = run_local(local, make_stub(tmp_path, STUB_OK))
    assert result.returncode == 2
    assert "github" in result.stderr
    assert "upstream" in result.stderr
    assert "--remote" in result.stderr


def test_no_remote_at_all_is_fine_when_both_ends_are_explicit(tmp_path: Path) -> None:
    """Заход 12, находка 1: `--base`/`--head` заданы ПОЛНОСТЬЮ явно и не
    ссылаются на remote-tracking namespace — remote для такого прогона не
    нужен вовсе (`merge-base`, диф и ревьюер работают целиком локально).
    Раньше блок резолюции remote'а отрабатывал безусловно и валил
    репозиторий без единого remote'а отказом "нет ни одного remote", хотя
    диапазон был задан полностью."""
    local = tmp_path / "local"
    local.mkdir()
    subprocess.run(["git", "-C", str(local), "init", "-q", "-b", "master"], check=True)
    git(local, "config", "user.email", "t@t")
    git(local, "config", "user.name", "t")
    (local / "base.txt").write_text("base\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "база")
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    assert git(local, "remote") == "", "проверяем сценарий именно без единого remote'а"

    result = run_local(local, make_stub(tmp_path, STUB_OK), "--base", "HEAD~1", "--head", "HEAD")
    assert result.returncode == 0, result.stderr
    assert "remote" not in result.stderr.lower()


def test_local_branch_with_slash_in_name_needs_no_remote(tmp_path: Path) -> None:
    """Заход 13, находка 2: дыра в round-12-находке-1. `remote_needed`
    считал remote-tracking'ом любой `--base` со слешем (`*/*`) — обычнейшее
    локальное имя ветки (`release/1.0`, типовой git-flow нейминг) тоже
    содержит `/`, а remote к нему отношения не имеет. Заявленный интерфейс
    `--base <ref|sha>` ломался на типовом локальном имени: репозиторий без
    единого remote'а, `--base release/1.0` валился отказом «remote не
    настроен», хотя обе ссылки локальные."""
    local = tmp_path / "local"
    local.mkdir()
    subprocess.run(["git", "-C", str(local), "init", "-q", "-b", "master"], check=True)
    git(local, "config", "user.email", "t@t")
    git(local, "config", "user.name", "t")
    (local / "base.txt").write_text("base\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "база")
    git(local, "switch", "-qc", "release/1.0")
    (local / "hotfix.txt").write_text("хотфикс\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "хотфикс")
    git(local, "switch", "-q", "master")

    assert git(local, "remote") == "", "проверяем сценарий именно без единого remote'а"

    result = run_local(
        local, make_stub(tmp_path, STUB_OK), "--base", "release/1.0", "--head", "master"
    )
    assert result.returncode == 0, result.stderr
    assert "remote" not in result.stderr.lower()


def test_shorthand_with_configured_origin_is_still_recognized_as_remote_tracking(
    tmp_path: Path,
) -> None:
    """Контрольный к тесту выше: сужение находки 2 не должно задеть
    настоящий remote-tracking шортхенд (`origin/master` при настроенном
    `origin`) — первый сегмент здесь СОВПАДАЕТ с именем реально
    существующего remote'а, значит remote нужен, и устаревшая база обязана
    быть замечена (та же логика, что и у существующих тестов на шортхенд, но
    отдельно проверяющая именно `remote_needed`, а не `track_branch`)."""
    remote, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    (remote / "other.txt").write_text("чужое\n", encoding="utf-8")
    git(remote, "add", "-A")
    git(remote, "commit", "-qm", "чужой коммит")

    result = run_local(local, make_stub(tmp_path, STUB_OK), "--base", "origin/master")
    assert "устарел" in (result.stdout + result.stderr).lower()


def test_renamed_default_branch_on_origin_is_reported_loudly_not_as_no_connection(
    tmp_path: Path,
) -> None:
    """Ветку по умолчанию переименовали НА ORIGIN (master -> main), не трогая
    локальный клон: `refs/remotes/origin/HEAD` — локальная ссылка и сама себя
    не обновляет, `origin/master` остаётся, но такой ветки на origin больше
    нет. `git ls-remote` в этом случае отрабатывает УСПЕШНО (код 0) и
    возвращает пусто — это "определённо нет", а не "не смогли проверить",
    и текущее сообщение про отсутствие связи с origin — неверная причина."""
    remote, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    # Переименование на remote, БЕЗ участия local — ровно так это происходит
    # в бою (GitHub UI переименовывает default branch у соседей, локальный
    # клон об этом не узнаёт, пока сам не сделает fetch/prune).
    git(remote, "branch", "-m", "master", "main")

    result = run_local(local, make_stub(tmp_path, STUB_OK))
    combined = result.stdout + result.stderr
    assert "нет связи с origin" not in combined.lower(), (
        "неверная причина: ls-remote отработал успешно, дело не в связи"
    )
    assert "'master' нет на origin" in combined or "master' нет на origin" in combined
    assert "set-head" in combined


def test_renamed_default_branch_with_fetch_still_shows_the_real_cause(
    tmp_path: Path,
) -> None:
    """Взаимодействие двух наших же правок: guard `git fetch` (находка 20)
    исполнялся ДО диагностики "ветки нет" (находка 22) — `git fetch -q
    origin master`, когда `master` на origin больше нет, сам падает, и
    guard выдавал код 2 с "не удалось обновить" РАНЬШЕ, чем управление
    доходило до настоящей причины. При переименованной ветке `--fetch`
    обязан показывать ту же диагностику, что и без флага, а не "сбой
    fetch": порядок восстановлен — существование ветки проверяется ДО
    попытки её тянуть."""
    remote, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    git(remote, "branch", "-m", "master", "main")

    result = run_local(local, make_stub(tmp_path, STUB_OK), "--fetch")
    combined = result.stdout + result.stderr
    assert result.returncode == 0, result.stderr
    assert "не удалось обновить" not in combined, (
        "guard fetch'а не должен затенять диагностику про переименованную ветку"
    )
    assert "'master' нет на origin" in combined or "master' нет на origin" in combined
    assert "set-head" in combined


def test_unknown_head_ref_is_config_error_not_raw_git_code(tmp_path: Path) -> None:
    """`git rev-parse` на неизвестной ссылке сам падает 128 — страж обязан
    перевести это в код кита (2) с понятным сообщением, а не дать 128
    утечь наружу вне объявленного §7 набора 0/1/2/3."""
    _, local = make_repo(tmp_path)
    result = run_local(local, make_stub(tmp_path, STUB_OK), "--head", "totally-unknown-ref")
    assert result.returncode == 2
    assert "128" not in result.stderr
    assert "totally-unknown-ref" in result.stderr


def test_orphan_head_gives_config_error_not_findings_code(tmp_path: Path) -> None:
    """У orphan-ветки нет общего предка с базой — `git merge-base` сам падает
    кодом 1, а 1 в нашем контракте означает "есть находки уровня
    blocker/major". Диапазон не построился — это обязано быть код 2
    (конфигурационный отказ), а не инвертированный сигнал "ревью нашло
    проблемы"."""
    _, local = make_repo(tmp_path)
    git(local, "checkout", "-q", "--orphan", "orphanbranch")
    (local / "orphan.txt").write_text("orphan\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "orphan")

    result = run_local(
        local, make_stub(tmp_path, STUB_OK), "--base", "master", "--head", "orphanbranch"
    )
    assert result.returncode == 2
    assert "не удалось построить диапазон" in result.stderr


def test_range_uses_merge_base_not_raw_base_when_base_moved_ahead(tmp_path: Path) -> None:
    """База ушла вперёд после ответвления — диапазон обязан идти от точки
    расхождения (merge-base), а не от текущей головы базовой ветки: иначе в
    диф попадут чужие изменения, которых в PR нет."""
    _, local = make_repo(tmp_path)
    git(local, "switch", "-qc", "feature")
    (local / "feature.txt").write_text("фича\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "фича")
    head_sha = git(local, "rev-parse", "HEAD")

    git(local, "switch", "-q", "master")
    (local / "other.txt").write_text("постороннее\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "постороннее на master")

    expected_mb = git(local, "merge-base", "master", "feature")

    result = run_local(local, make_stub(tmp_path, STUB_OK), "--base", "master", "--head", "feature")
    assert result.returncode == 0, result.stderr
    assert f"диапазон: {expected_mb}..{head_sha}" in result.stdout


def test_fetch_updates_stale_base_and_suppresses_warning(tmp_path: Path) -> None:
    """--fetch обновляет remote-tracking: до него — предупреждение об
    устаревшей базе, после — его нет. Один прогон это не отличил бы:
    доказательство — именно пара «без флага / с флагом» на одном и том же
    состоянии репо."""
    remote, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    # Удалённый ушёл вперёд, локальный remote-tracking про это не знает
    (remote / "other.txt").write_text("чужое\n", encoding="utf-8")
    git(remote, "add", "-A")
    git(remote, "commit", "-qm", "чужой коммит")

    before = run_local(local, make_stub(tmp_path, STUB_OK))
    assert "устарел" in (before.stdout + before.stderr).lower()

    after = run_local(local, make_stub(tmp_path, STUB_OK), "--fetch")
    assert after.returncode == 0, after.stderr
    assert "устарел" not in (after.stdout + after.stderr).lower()


def test_fetch_with_fresh_base_still_works(tmp_path: Path) -> None:
    """--fetch на уже свежей базе не ломает обычный прогон."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    result = run_local(local, make_stub(tmp_path, STUB_OK), "--fetch")
    assert result.returncode == 0, result.stderr
    assert "устарел" not in (result.stdout + result.stderr).lower()


def test_fetch_against_unreachable_remote_is_config_error_not_raw_git_code(
    tmp_path: Path,
) -> None:
    """`git fetch` сам падает сырым кодом git (обычно 128) на недоступном
    remote — команда, которую README же и рекомендует. Guard обязан дать код
    кита (2), а не дать 128 утечь наружу вне объявленного §7 набора."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    git(local, "remote", "set-url", "origin", str(tmp_path / "нет-такого-remote.git"))

    result = run_local(local, make_stub(tmp_path, STUB_OK), "--fetch")
    assert result.returncode == 2
    assert "128" not in result.stderr
    assert "не удалось обновить" in result.stderr


def test_fetch_with_explicit_remote_tracking_base_updates_it(tmp_path: Path) -> None:
    """Гейт: явный `--base refs/remotes/origin/release/1.0 --fetch` (hotfix от
    отставшей ветки) раньше молча пропускал --fetch, хотя запрошенное было
    исполнимо — база ОДНОЗНАЧНО remote-tracking ref нужного remote, имя
    ветки извлекается напрямую. Пара «без флага / с флагом» на одном и том
    же отставшем состоянии, ровно как на пути умолчания."""
    remote, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    # Удалённый ушёл вперёд, локальный remote-tracking про это не знает
    (remote / "other.txt").write_text("чужое\n", encoding="utf-8")
    git(remote, "add", "-A")
    git(remote, "commit", "-qm", "чужой коммит")

    before = run_local(local, make_stub(tmp_path, STUB_OK), "--base", "refs/remotes/origin/master")
    assert "устарел" in (before.stdout + before.stderr).lower()

    after = run_local(
        local, make_stub(tmp_path, STUB_OK), "--base", "refs/remotes/origin/master", "--fetch"
    )
    assert after.returncode == 0, after.stderr
    assert "устарел" not in (after.stdout + after.stderr).lower()
    assert "--fetch игнорируется" not in after.stderr


def test_fetch_with_non_tracking_explicit_base_reports_being_ignored(tmp_path: Path) -> None:
    """Контрольный к тесту выше: `--base` на ЛОКАЛЬНУЮ ветку (не
    remote-tracking ref) не может быть обновлён `--fetch` — тянуть
    буквально нечего, "--fetch игнорируется" по-прежнему обязан
    печататься, и текст обязан называть настоящую причину (база не
    remote-tracking ref), а не просто факт явного --base."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    result = run_local(local, make_stub(tmp_path, STUB_OK), "--base", "master", "--fetch")
    assert result.returncode == 0, result.stderr
    assert "--fetch игнорируется" in result.stderr
    assert "remote-tracking" in result.stderr


def test_fetch_recognizes_shorthand_remote_tracking_base(tmp_path: Path) -> None:
    """Заход 11, находка 1: `--base origin/master` (шортхенд, самая ходовая
    форма записи remote-tracking ref'а — её пишут чаще полной) раньше НЕ
    распознавался textual-паттерном `refs/remotes/$remote/*` и читался как
    "не remote-tracking", хотя им был: --fetch молча пропускался, свежесть
    не проверялась. Тот же сценарий "устарел / --fetch", что и в тесте
    выше на полной форме, но здесь база задана шортхендом."""
    remote, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    # Удалённый ушёл вперёд, локальный remote-tracking про это не знает
    (remote / "other.txt").write_text("чужое\n", encoding="utf-8")
    git(remote, "add", "-A")
    git(remote, "commit", "-qm", "чужой коммит")

    before = run_local(local, make_stub(tmp_path, STUB_OK), "--base", "origin/master")
    assert "--fetch игнорируется" not in before.stderr
    assert "устарел" in (before.stdout + before.stderr).lower()

    after = run_local(local, make_stub(tmp_path, STUB_OK), "--base", "origin/master", "--fetch")
    assert after.returncode == 0, after.stderr
    assert "устарел" not in (after.stdout + after.stderr).lower()
    assert "--fetch игнорируется" not in after.stderr


def test_explicit_unfetched_remote_tracking_base_fails_with_code_2(tmp_path: Path) -> None:
    """Заход 11, находка 2: README рекомендует `--base
    refs/remotes/<remote>/<ветка>` для ветки, которая на remote ЕСТЬ, но
    локально ещё НЕ подтянута (свежий клон). Раньше это падало сырым
    `git rev-parse` кодом 128 — сбой инструмента предъявлялся как вердикт,
    а не как контрактный конфигурационный отказ (код 2).

    Порядок клонирования критичен для воспроизведения: `git clone` тянет
    ВСЕ ветки, присутствующие на remote НА МОМЕНТ клонирования, а не только
    ветку по умолчанию. Если создать ветку на remote до `git clone`, она
    попадёт в локальный remote-tracking автоматически, и баг не
    воспроизведётся. Поэтому здесь `local` клонируется ПЕРВЫМ (когда на
    remote есть только master), и лишь ЗАТЕМ отдельный `seed`-клон заводит
    и пушит `release/1.0` — `local` о ней ничего не знает."""
    remote, local = make_repo(tmp_path)

    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True, capture_output=True)
    git(seed, "config", "user.email", "t@t")
    git(seed, "config", "user.name", "t")
    git(seed, "checkout", "-qb", "release/1.0")
    (seed / "hotfix.txt").write_text("хотфикс\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "хотфикс")
    git(seed, "push", "-q", "origin", "release/1.0")

    # local не подтягивал release/1.0 — remote-tracking ref для неё локально
    # не существует, ровно сценарий из README.
    result = run_local(
        local,
        make_stub(tmp_path, STUB_OK),
        "--base",
        "refs/remotes/origin/release/1.0",
    )
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "ещё не подтянута" in result.stderr
    assert "git fetch origin release/1.0" in result.stderr


def test_fetch_recognizes_unfetched_shorthand_base(tmp_path: Path) -> None:
    """Заход 12, находка 2: дыра в фиксе часа назад (находка 1 захода 11).
    `--symbolic-full-name` не резолвит несуществующий локально ref, и
    прежний fallback ловил только ПОЛНУЮ форму (`refs/remotes/...`) — на
    шортхенде (`origin/release/1.0`), ещё не подтянутом локально,
    `track_branch` оставался пустым: "--fetch игнорируется" печаталось
    вместо реального fetch'а, хотя --fetch заведён ровно для этого случая.

    Тот же порядок клонирования, что и в тесте выше (round 11, находка 2):
    `local` клонируется ПЕРВЫМ, `release/1.0` заводится и пушится ПОСЛЕ, из
    отдельного `seed`-клона — иначе `git clone` уже подтянул бы её сам, и
    баг (как и тест на него) не воспроизвёлся бы."""
    remote, local = make_repo(tmp_path)

    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True, capture_output=True)
    git(seed, "config", "user.email", "t@t")
    git(seed, "config", "user.name", "t")
    git(seed, "checkout", "-qb", "release/1.0")
    (seed / "hotfix.txt").write_text("хотфикс\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "хотфикс")
    git(seed, "push", "-q", "origin", "release/1.0")

    result = run_local(
        local,
        make_stub(tmp_path, STUB_OK),
        "--base",
        "origin/release/1.0",
        "--fetch",
    )
    assert result.returncode == 0, result.stderr
    assert "--fetch игнорируется" not in result.stderr
    assert "ещё не подтянута" not in result.stderr


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


def test_default_schema_and_prompt_resolve_from_repo_root_not_cwd(tmp_path: Path) -> None:
    """README велит запускать `sh scripts/review/local.sh`, и запускать будут
    откуда попало. Умолчания REVIEW_SCHEMA/REVIEW_PROMPT обязаны резолвиться
    через `git rev-parse --show-toplevel`, а не через cwd — иначе прогон из
    подкаталога репо не находит промпт (находка 6 финального ревью)."""
    _, local = make_repo(tmp_path)
    codex_dir = local / ".github" / "codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "review-prompt.md").write_text("промпт-заглушка\n", encoding="utf-8")
    (codex_dir / "review-schema.json").write_text("{}\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "промпт и схема")

    subdir = local / "sub" / "deeper"
    subdir.mkdir(parents=True)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    env = dict(os.environ)
    env["REVIEW_CMD"] = make_stub(tmp_path, STUB_OK)
    env["REVIEW_KIT_DIR"] = str(ROOT / "scripts" / "review")
    # Намеренно НЕ задаём REVIEW_SCHEMA/REVIEW_PROMPT — проверяем умолчания.
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=str(subdir),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "нет файла инструкций" not in result.stderr


def _big_lock_body() -> str:
    return "".join(f"pin-{i} {'x' * 40}\n" for i in range(12_000))


DECLARATION = "uv.lock linguist-generated=true\n"


def test_declared_generated_is_filtered_from_subdir(tmp_path: Path) -> None:
    """Влитая декларация фильтрует generated; прогон из подкаталога её видит.

    Декларация читается git'ом из merge-base диапазона — привязки к cwd нет
    (третий заход гейта на #99 ломался на относительном доказательстве)."""
    _, local = make_repo(tmp_path)
    (local / ".gitattributes").write_text(DECLARATION, encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "декларация влита в базу диапазона")
    base_sha = git(local, "rev-parse", "HEAD")

    (local / "uv.lock").write_text(_big_lock_body(), encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "перегенерированный lock")

    subdir = local / "sub"
    subdir.mkdir()
    result = run_local(local, make_stub(tmp_path, STUB_OK), "--base", base_sha, cwd=subdir)

    assert result.returncode == 0, result.stderr
    assert "диф больше поддерживаемого" not in result.stderr


def test_same_patch_declaration_does_not_hide_code(tmp_path: Path) -> None:
    """Декларация из того же патча НЕ прячет код: действует только влитая.

    Девятый заход гейта на #99: автор, объявляющий файл generated в том же
    PR, мог бы спрятать произвольный рукописный диф от ревью. Декларация
    читается из базы диапазона (в CI — из base PR), поэтому свеже-объявлённый
    файл остаётся в дифе и честно упирается в потолок — отказ в сторону
    ревью; фильтр включится следующим PR, когда декларация будет влита."""
    _, local = make_repo(tmp_path)

    git(local, "switch", "-qc", "sneaky")
    (local / ".gitattributes").write_text(DECLARATION, encoding="utf-8")
    (local / "uv.lock").write_text(_big_lock_body(), encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "декларация и скрываемый файл одним патчем")
    head_sha = git(local, "rev-parse", "HEAD")
    git(local, "switch", "-q", "master")

    result = run_local(local, make_stub(tmp_path, STUB_OK), "--head", head_sha)

    assert result.returncode == 2, result.stderr
    assert "диф больше поддерживаемого" in result.stderr


def test_declaration_from_range_base_not_checkout(tmp_path: Path) -> None:
    """Источник декларации — база диапазона, не состояние checkout.

    Фильтр работает, даже когда в рабочей копии декларации нет вовсе:
    доказательство привязано к ревьюируемому диапазону (пятый заход гейта
    на #99 — тот же класс, привязка к живому дереву)."""
    _, local = make_repo(tmp_path)

    git(local, "switch", "-qc", "locked")
    (local / ".gitattributes").write_text(DECLARATION, encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "декларация")
    base_sha = git(local, "rev-parse", "HEAD")

    (local / "uv.lock").write_text(_big_lock_body(), encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "перегенерированный lock")
    head_sha = git(local, "rev-parse", "HEAD")

    git(local, "switch", "-q", "master")
    assert not (local / ".gitattributes").exists()

    result = run_local(
        local,
        make_stub(tmp_path, STUB_OK),
        "--base",
        base_sha,
        "--head",
        head_sha,
    )

    assert result.returncode == 0, result.stderr
    assert "диф больше поддерживаемого" not in result.stderr


def test_deleting_declared_lockfile_is_still_filtered(tmp_path: Path) -> None:
    """Удаление объявленного lock-файла — тоже generated-изменение.

    Седьмой заход гейта на #99: у эвристики «манифест-сосед» удаление
    пакета вместе с lock'ом рушило доказательство, и гигантский диф
    удаления упирался в потолок. Атрибут — паттерн дерева, а не файл:
    декларация из базы диапазона покрывает и удалённый путь."""
    _, local = make_repo(tmp_path)

    (local / ".gitattributes").write_text(DECLARATION, encoding="utf-8")
    (local / "uv.lock").write_text(_big_lock_body(), encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "декларация и lock в базе диапазона")
    base_sha = git(local, "rev-parse", "HEAD")

    git(local, "rm", "-q", "uv.lock")
    git(local, "commit", "-qm", "пакет удалён вместе с lock'ом")
    head_sha = git(local, "rev-parse", "HEAD")

    result = run_local(
        local,
        make_stub(tmp_path, STUB_OK),
        "--base",
        base_sha,
        "--head",
        head_sha,
    )

    assert result.returncode == 0, result.stderr
    assert "диф больше поддерживаемого" not in result.stderr


def test_revoking_declaration_unhides_file_in_same_pr(tmp_path: Path) -> None:
    """Снятие декларации действует СРАЗУ — в том же PR.

    Одиннадцатый заход гейта на #99: чтение только из базы прятало файл
    ровно в том PR, который снимает с него linguist-generated и правит его
    руками — PR-отзыв не мог отревьюировать то, что расклассифицирует.
    Направления асимметричны: ДОБАВЛЕНИЕ декларации действует только влитым
    (спрятать код своим же патчем нельзя — девятый заход), СНЯТИЕ действует
    сразу из ревьюируемого дерева — оно только открывает код. Итог:
    generated = объявлено и в базе, и в ревьюируемом дереве."""
    _, local = make_repo(tmp_path)

    (local / ".gitattributes").write_text(DECLARATION, encoding="utf-8")
    (local / "uv.lock").write_text("маленький\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "декларация влита")
    base_sha = git(local, "rev-parse", "HEAD")

    git(local, "rm", "-q", ".gitattributes")
    (local / "uv.lock").write_text(_big_lock_body(), encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "отзыв декларации + ручная правка lock")

    result = run_local(local, make_stub(tmp_path, STUB_OK), "--base", base_sha)

    assert result.returncode == 2, result.stderr
    assert "диф больше поддерживаемого" in result.stderr


def test_base_side_declaration_after_fork_still_filters(tmp_path: Path) -> None:
    """Декларация, влитая в base ПОСЛЕ ответвления ветки, действует локально.

    Двенадцатый заход гейта на #99: пересечение по голому head теряло
    base-side декларацию, которой нет в дереве неребейзнутой ветки, — local
    давал ложный отказ по потолку там, где CI (merge-ref) фильтрует. PR, не
    трогающий ни одного .gitattributes, ничего не отзывал: действует список
    базы; head ветирует только когда PR правит декларации."""
    _, local = make_repo(tmp_path)

    git(local, "switch", "-qc", "feature")
    (local / "uv.lock").write_text(_big_lock_body(), encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "lock на ветке, ответвлённой до декларации")
    head_sha = git(local, "rev-parse", "HEAD")

    git(local, "switch", "-q", "master")
    (local / ".gitattributes").write_text(DECLARATION, encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "декларация влита в base после ответвления")
    base_sha = git(local, "rev-parse", "HEAD")

    result = run_local(
        local,
        make_stub(tmp_path, STUB_OK),
        "--base",
        base_sha,
        "--head",
        head_sha,
    )

    assert result.returncode == 0, result.stderr
    assert "диф больше поддерживаемого" not in result.stderr


OLD_BUILD_PROMPT = """#!/bin/sh
# «Старый» build-prompt.sh: сигнатура до generated-фильтра. ВАЖНО: литерал
# флага здесь упоминать нельзя даже в комментарии — детекция в local.sh
# ищет его grep'ом по всему файлу.
set -eu
prompt=""; diff=""
while [ $# -gt 0 ]; do
    case "$1" in
        --prompt) prompt="$2"; shift 2 ;;
        --diff) diff="$2"; shift 2 ;;
        --context) shift 2 ;;
        *) echo "usage: build-prompt.sh --prompt <file> --diff <file>" >&2; exit 2 ;;
    esac
done
cat "$prompt"; cat "$diff"
"""


def test_half_updated_kit_without_generated_list_degrades(tmp_path: Path) -> None:
    """Полуобновлённый кит: новый local.sh + старый build-prompt.sh живут.

    Тринадцатый заход гейта на #99: безусловная передача --generated-list
    убивала любой непустой прогон кодом 2 (usage старого скрипта), а
    pre-push блокировал пуш. Для раскатки кита по флоту перекос версий
    вендор-копий — штатный режим, не край: флаг передаётся только по
    детекции его литерала в скрипте кита (как в CI, восьмой заход), иначе
    фильтр выключается именованно."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "обычная маленькая правка")

    kit = tmp_path / "half-kit"
    kit.mkdir()
    for name in ("local.sh", "apply-threshold.sh", "collect-context.sh"):
        src = ROOT / "scripts" / "review" / name
        if src.exists():
            shutil.copy(src, kit / name)
    (kit / "build-prompt.sh").write_text(OLD_BUILD_PROMPT, encoding="utf-8")

    result = run_local(
        local,
        make_stub(tmp_path, STUB_OK),
        env_overrides={"REVIEW_KIT_DIR": str(kit)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "generated-фильтр выключен" in result.stderr


def test_ceiling_override_on_half_updated_kit_is_named_refusal(
    tmp_path: Path,
) -> None:
    """Явный оверрайд потолка на полуобновлённом ките — именованный отказ.

    Пятнадцатый заход гейта на #99: --max-diff-* пробрасывались слепо, и
    старый build-prompt.sh умирал своим usage — обещанный путь
    восстановления после отказа по потолку не работал. Направление не как у
    --generated-list: потолок запрошен пользователем ЯВНО, молча выбросить
    его нельзя (довод пустого значения) — несовместимость называется
    отказом кодом 2 с причиной, не деградацией."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "правка")

    kit = tmp_path / "half-kit"
    kit.mkdir()
    for name in ("local.sh", "apply-threshold.sh", "collect-context.sh"):
        src = ROOT / "scripts" / "review" / name
        if src.exists():
            shutil.copy(src, kit / name)
    (kit / "build-prompt.sh").write_text(OLD_BUILD_PROMPT, encoding="utf-8")

    result = run_local(
        local,
        make_stub(tmp_path, STUB_OK),
        "--max-diff-files",
        "40",
        env_overrides={"REVIEW_KIT_DIR": str(kit)},
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "обновлён наполовину" in result.stderr
    assert "usage" not in result.stderr


@pytest.mark.parametrize("flag", ["--max-diff-bytes", "--max-diff-files"])
def test_empty_ceiling_override_is_config_error(tmp_path: Path, flag: str) -> None:
    """Пустое значение потолка — отказ, а не молчаливый откат к умолчанию.

    Раньше `--max-diff-bytes ""` принимался парсером и молча выбрасывался
    проверкой [ -n ] при пробросе — явный запрос оверрайда исчезал без
    следа (minor тринадцатого захода). Тот же довод, что у --context ""."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "правка")

    result = run_local(local, make_stub(tmp_path, STUB_OK), flag, "")

    assert result.returncode == 2, result.stderr
    assert "пустым значением" in result.stderr


def make_old_git_shim(tmp_path: Path) -> Path:
    """PATH-шим, изображающий git < 2.38: не знает `check-attr --source`,
    всё остальное делегирует настоящему git."""
    real_git = shutil.which("git")
    assert real_git is not None
    shim_dir = tmp_path / "old-git-bin"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '    case "$a" in\n'
        "        --source=*)\n"
        "            echo \"error: unknown option 'source'\" >&2\n"
        "            exit 129 ;;\n"
        "    esac\n"
        "done\n"
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim_dir


def test_old_git_without_check_attr_source_degrades_not_dies(tmp_path: Path) -> None:
    """git < 2.38 — фильтр деградирует именованно, обычный прогон живёт.

    Десятый заход гейта на #99: жёсткое требование `check-attr --source`
    убивало КАЖДЫЙ непустой локальный прогон на старом git кодом 2 — даже
    крошечный диф без единого generated-файла, а pre-push хук блокировал
    пуш. Деградация — в сторону ревью: фильтр выключается с предупреждением,
    код никогда не прячется."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "обычная маленькая правка")

    shim_dir = make_old_git_shim(tmp_path)
    result = run_local(
        local,
        make_stub(tmp_path, STUB_OK),
        env_overrides={"PATH": f"{shim_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stderr
    assert "generated-фильтр выключен" in result.stderr


def test_old_git_with_declared_lock_hits_ceiling_not_check_attr_error(
    tmp_path: Path,
) -> None:
    """Старый git + объявленный крупный lock — явный отказ по потолку.

    Худший исход деградации: generated-диф не фильтруется и честно
    упирается в потолок с рецептом, а не умирает на check-attr."""
    _, local = make_repo(tmp_path)
    (local / ".gitattributes").write_text(DECLARATION, encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "декларация")
    base_sha = git(local, "rev-parse", "HEAD")
    (local / "uv.lock").write_text(_big_lock_body(), encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "перегенерированный lock")

    shim_dir = make_old_git_shim(tmp_path)
    result = run_local(
        local,
        make_stub(tmp_path, STUB_OK),
        "--base",
        base_sha,
        env_overrides={"PATH": f"{shim_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 2, result.stderr
    assert "диф больше поддерживаемого" in result.stderr


def test_findings_above_threshold_block_exit_code(tmp_path: Path) -> None:
    """§7: `local.sh` обязан завершиться 1, когда вердикт содержит находку выше
    порога. Держится на `set -e` и позиции последней команды в скрипте — ни один
    существующий стенд этого не проверял (все писали `{"findings":[]}`), значит
    рефакторинг хвоста скрипта мог бы снять блокировку молча (находка 5
    финального ревью)."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    stub = make_stub(tmp_path, STUB_MAJOR_FINDING)
    result = run_local(local, stub)
    assert result.returncode == 1, result.stderr
    # Рендер обязан быть цел, не просто код выхода.
    assert "major" in result.stdout
    assert "a.py" in result.stdout


def test_context_path_with_space_in_work_dir(tmp_path: Path) -> None:
    """Пробел в пути рабочего каталога не ломает прогон с контекстом.

    Прежняя форма собирала аргумент в строку (`ctx_args="--context $work/…"`) и
    раскрывала её без кавычек, полагаясь на «либо пусто, либо ровно два слова».
    Это неверно ровно там, где путь приходит извне: `…/Review Temp/tmp.XYZ`
    разваливается на три слова, argv битый, и контекст молча выпадает там, где
    он и должен работать.

    Пробел загоняется подставным `mktemp` в PATH, а НЕ через `TMPDIR`: на macOS
    `mktemp -d` берёт каталог из `_CS_DARWIN_USER_TEMP_DIR` и `TMPDIR`
    игнорирует. Первая версия этого теста так и была написана — и пережила
    мутант, то есть была зелёной, не проверяя ничего. Расхождение поймано
    мутацией, а не рассуждением.
    """
    real_mktemp = shutil.which("mktemp")
    assert real_mktemp, "mktemp не найден — стенд не может подменить его осмысленно"

    remote, local = make_repo(tmp_path)
    # Remote в стенде не bare: пуш в его текущую ветку по умолчанию отвергается.
    git(remote, "config", "receive.denyCurrentBranch", "ignore")
    (local / ".github" / "codex").mkdir(parents=True)
    (local / "ctx.py").write_text("CONTEXT_MARKER = 1\n", encoding="utf-8")
    (local / ".github" / "codex" / "review-context.txt").write_text("ctx.py\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "манифест и контекст в базе")
    git(local, "push", "-q", "origin", "master")
    git(local, "remote", "set-head", "origin", "-a")

    (local / "changed.txt").write_text("правка\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "правка ветки")

    spaced = tmp_path / "Review Temp"
    spaced.mkdir()
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    shim = stub_bin / "mktemp"
    shim.write_text(
        f"#!/bin/sh\n"
        f'if [ "$1" = "-d" ]; then exec {real_mktemp} -d "{spaced}/tmp.XXXXXX"; fi\n'
        f'exec {real_mktemp} "{spaced}/tmp.XXXXXX"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)

    result = run_local(
        local,
        make_stub(tmp_path, STUB_OK),
        env_overrides={"PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "нет файла контекста" not in result.stderr
    assert "контекст: 1 файл(ов)" in result.stdout


def _kit_without_collector(tmp_path: Path) -> Path:
    """Копия кита без `collect-context.sh` — состояние «обновили наполовину»."""
    kit = tmp_path / "kit"
    kit.mkdir()
    for name in ("local.sh", "build-prompt.sh", "apply-threshold.sh"):
        shutil.copy(ROOT / "scripts" / "review" / name, kit / name)
    return kit


def test_missing_collector_falls_back_to_diff_only(tmp_path: Path) -> None:
    """Нет сборщика и нет манифеста — ревью по одному дифу, а не падение.

    CI такой случай различает явно; локальный прогон обязан вести себя так же,
    иначе половины одного кита расходятся. Без ветки `sh <нет файла>` дал бы
    `can't open` и уронил весь прогон кодом 2 там, где по документации штатное
    «контекст не настроен».
    """
    _, local = make_repo(tmp_path)
    (local / "changed.txt").write_text("правка\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "правка ветки")

    kit = _kit_without_collector(tmp_path)
    result = run_local(
        local,
        make_stub(tmp_path, STUB_OK),
        env_overrides={"REVIEW_KIT_DIR": str(kit)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "сборщика нет в ките" in result.stdout


def test_manifest_without_collector_is_a_refusal(tmp_path: Path) -> None:
    """Манифест в базе БЕЗ сборщика — отказ, а не тихий съезд на «только диф».

    Это не бутстрап: механику потеряли, данные остались. Промолчать здесь
    значило бы выдать ревью без контекста за ревью с ним.
    """
    remote, local = make_repo(tmp_path)
    git(remote, "config", "receive.denyCurrentBranch", "ignore")
    (local / ".github" / "codex").mkdir(parents=True)
    (local / "ctx.py").write_text("CONTEXT_MARKER = 1\n", encoding="utf-8")
    (local / ".github" / "codex" / "review-context.txt").write_text("ctx.py\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "манифест в базе")
    git(local, "push", "-q", "origin", "master")
    git(local, "remote", "set-head", "origin", "-a")

    (local / "changed.txt").write_text("правка\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "правка ветки")

    kit = _kit_without_collector(tmp_path)
    result = run_local(
        local,
        make_stub(tmp_path, STUB_OK),
        env_overrides={"REVIEW_KIT_DIR": str(kit)},
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "механика потеряна" in result.stderr


def test_local_forwards_diff_ceiling_overrides(tmp_path: Path) -> None:
    """`local.sh --max-diff-files/--max-diff-bytes` доезжают до build-prompt.sh.

    Гардрейл предлагает поднять потолок явно — значит поддерживаемый
    вызывающий обязан уметь его передать, иначе обещанный путь восстановления
    не существует (находка гейта на #99: интерфейс local.sh флагов не knew).
    """
    _, local = make_repo(tmp_path)
    for i in range(31):
        (local / f"f{i}.txt").write_text("x\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "широкий патч")

    # Без override — честный отказ гардрейла (код 2, не вердикт).
    refused = run_local(local, make_stub(tmp_path, STUB_OK))
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "больше поддерживаемого" in refused.stderr

    # С override — проходит.
    passed = run_local(local, make_stub(tmp_path, STUB_OK), "--max-diff-files", "40")
    assert passed.returncode == 0, passed.stdout + passed.stderr


# --- --fingerprint-only (@id:review-dedup-diff-hash, steward#126) -----------
#
# REVIEW_CMD во всех тестах отпечатка — КОНСТАНТА "false", по двум причинам
# разом: путь к tmp-стабу различается между репозиториями и ломал бы
# равенство отпечатков (REVIEW_CMD — компонент отпечатка), а вызов `false`
# уронил бы прогон кодом 3 — то есть равенство и «ревьюер не вызывается»
# доказываются одной и той же подстановкой.

FP_ENV = {"REVIEW_CMD": "false"}
HEX64 = "0123456789abcdef"


def is_hex64(line: str) -> bool:
    return len(line) == 64 and all(c in HEX64 for c in line)


def make_repo_with_feature(
    tmp_path: Path,
    name: str,
    *,
    base_files: dict[str, str] | None = None,
    feature_content: str = "новое\n",
) -> Path:
    """Репо с базой (опц. доп. файлы) и одним feature-коммитом поверх."""
    remote = tmp_path / f"{name}-remote"
    remote.mkdir()
    subprocess.run(["git", "-C", str(remote), "init", "-q", "-b", "master"], check=True)
    git(remote, "config", "user.email", "t@t")
    git(remote, "config", "user.name", "t")
    (remote / "base.txt").write_text("base\n", encoding="utf-8")
    for rel, content in (base_files or {}).items():
        path = remote / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(remote, "add", "-A")
    git(remote, "commit", "-qm", "base")

    local = tmp_path / f"{name}-local"
    subprocess.run(["git", "clone", "-q", str(remote), str(local)], check=True, capture_output=True)
    git(local, "config", "user.email", "t@t")
    git(local, "config", "user.name", "t")
    git(local, "remote", "set-head", "origin", "-a")
    (local / "new.txt").write_text(feature_content, encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")
    return local


def fingerprint(
    repo: Path,
    *args: str,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(FP_ENV)
    if env_overrides:
        env.update(env_overrides)
    return run_local(repo, "false", "--fingerprint-only", *args, env_overrides=env)


def test_fingerprint_only_prints_single_hex_line_and_is_deterministic(
    tmp_path: Path,
) -> None:
    """stdout — ровно одна строка 64 hex; повторный прогон даёт ту же."""
    local = make_repo_with_feature(tmp_path, "det")
    first = fingerprint(local)
    second = fingerprint(local)
    assert first.returncode == 0, first.stdout + first.stderr
    lines = first.stdout.splitlines()
    assert len(lines) == 1, first.stdout
    assert is_hex64(lines[0]), lines[0]
    assert first.stdout == second.stdout


def test_fingerprint_only_does_not_call_reviewer_or_threshold(tmp_path: Path) -> None:
    """REVIEW_CMD=false: вызов ревьюера дал бы код 3 — его не должно быть."""
    local = make_repo_with_feature(tmp_path, "nocall")
    result = fingerprint(local)
    assert result.returncode == 0, result.stdout + result.stderr


def test_fingerprint_only_rejects_fetch(tmp_path: Path) -> None:
    """--fetch несовместим: отпечаток обязан быть оффлайн-вычислимым."""
    local = make_repo_with_feature(tmp_path, "nofetch")
    result = fingerprint(local, "--fetch")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "--fingerprint-only" in result.stderr


def test_fingerprint_only_skips_network_freshness_check(tmp_path: Path) -> None:
    """При недоступном remote обычный прогон предупреждает, fp-режим — молчит:
    ls-remote в оффлайн-режиме не вызывается вовсе."""
    local = make_repo_with_feature(tmp_path, "offline")
    git(local, "remote", "set-url", "origin", str(tmp_path / "нет-такого"))
    noisy = run_local(local, "false", env_overrides=FP_ENV)
    assert "свежесть базы не проверена" in noisy.stderr
    quiet = fingerprint(local)
    assert quiet.returncode == 0, quiet.stdout + quiet.stderr
    assert "свежесть базы не проверена" not in quiet.stderr


def test_fingerprint_stable_across_head_sha_for_same_input(tmp_path: Path) -> None:
    """Тот же эффективный вход при другом head SHA — тот же отпечаток
    (близнец боевого повода: close/reopen, rerun)."""
    local = make_repo_with_feature(tmp_path, "amend")
    before = fingerprint(local)
    assert before.returncode == 0, before.stdout + before.stderr
    assert is_hex64(before.stdout.strip()), before.stdout
    old_sha = git(local, "rev-parse", "HEAD")
    git(local, "commit", "--amend", "-qm", "работа (другое сообщение)")
    assert git(local, "rev-parse", "HEAD") != old_sha
    after = fingerprint(local)
    assert before.stdout == after.stdout


def test_fingerprint_changes_with_diff_content(tmp_path: Path) -> None:
    a = fingerprint(make_repo_with_feature(tmp_path, "diff-a"))
    b = fingerprint(make_repo_with_feature(tmp_path, "diff-b", feature_content="иное\n"))
    assert a.stdout != b.stdout


def test_fingerprint_changes_with_base_context(tmp_path: Path) -> None:
    """Правка контекста в базе меняет отпечаток при неизменном дифе."""
    manifest = ".github/codex/review-context.txt"

    def repo(name: str, ctx: str) -> Path:
        return make_repo_with_feature(
            tmp_path, name, base_files={manifest: "ctx.md\n", "ctx.md": ctx}
        )

    a = fingerprint(repo("ctx-a", "инвариант один\n"))
    b = fingerprint(repo("ctx-b", "инвариант другой\n"))
    assert a.returncode == 0 and b.returncode == 0, a.stderr + b.stderr
    assert a.stdout != b.stdout


def test_fingerprint_changes_with_generated_filter_result(tmp_path: Path) -> None:
    """Декларация generated в базе меняет вход (файл уходит в маркер) — и отпечаток."""
    a = fingerprint(make_repo_with_feature(tmp_path, "gen-a"))
    b = fingerprint(
        make_repo_with_feature(
            tmp_path,
            "gen-b",
            base_files={".gitattributes": "new.txt linguist-generated\n"},
        )
    )
    assert a.returncode == 0 and b.returncode == 0, a.stderr + b.stderr
    assert a.stdout != b.stdout


def test_fingerprint_changes_with_prompt_schema_threshold_review_cmd(
    tmp_path: Path,
) -> None:
    """Каждая компонента входа — промпт, схема, порог, REVIEW_CMD — меняет отпечаток."""
    local = make_repo_with_feature(tmp_path, "components")
    baseline = fingerprint(local)
    assert baseline.returncode == 0, baseline.stderr

    prompt_copy = tmp_path / "prompt.md"
    prompt_copy.write_text(
        (ROOT / ".github" / "codex" / "review-prompt.md").read_text(encoding="utf-8")
        + "\nдоп. правило\n",
        encoding="utf-8",
    )
    schema_copy = tmp_path / "schema.json"
    schema_copy.write_text(
        (ROOT / ".github" / "codex" / "review-schema.json").read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    kit_copy = tmp_path / "kit"
    shutil.copytree(ROOT / "scripts" / "review", kit_copy)
    threshold = kit_copy / "apply-threshold.sh"
    threshold.write_text(
        threshold.read_text(encoding="utf-8") + "\n# порог пересмотрен\n",
        encoding="utf-8",
    )

    variants = [
        fingerprint(local, env_overrides={"REVIEW_PROMPT": str(prompt_copy)}),
        fingerprint(local, env_overrides={"REVIEW_SCHEMA": str(schema_copy)}),
        fingerprint(local, env_overrides={"REVIEW_KIT_DIR": str(kit_copy)}),
        fingerprint(local, env_overrides={"REVIEW_CMD": "false --model другой"}),
    ]
    outs = [v.stdout for v in variants]
    for v in variants:
        assert v.returncode == 0, v.stderr
    assert len({baseline.stdout, *outs}) == 5, outs


def test_fingerprint_only_empty_diff_prints_no_hex(tmp_path: Path) -> None:
    """Пустой диф — штатный исход «ревьюировать нечего», без отпечатка:
    контракт «ровно одна строка 64-hex» — только для непустого входа."""
    _, local = make_repo(tmp_path)
    result = fingerprint(local)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(is_hex64(line) for line in result.stdout.splitlines())
    assert "ревьюировать нечего" in result.stdout + result.stderr
