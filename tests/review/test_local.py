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
printf '{"findings":[{"severity":"major","file":"a.py","summary":"s","failure":"f"}],"note":"stub"}' \\
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


def test_fetch_with_explicit_base_reports_being_ignored(tmp_path: Path) -> None:
    """При явном --base `--fetch` не резолвит default_branch и ничего не
    обновляет — молчание здесь было бы той же несопоставимостью диапазонов,
    ради которой написан §6.1. Флаг обязан сказать о себе явно."""
    _, local = make_repo(tmp_path)
    (local / "new.txt").write_text("новое\n", encoding="utf-8")
    git(local, "add", "-A")
    git(local, "commit", "-qm", "работа")

    result = run_local(
        local, make_stub(tmp_path, STUB_OK), "--base", "refs/remotes/origin/master", "--fetch"
    )
    assert result.returncode == 0, result.stderr
    assert "--fetch игнорируется" in result.stderr


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
