"""Тесты scripts/review/collect-context.sh — курируемый контекст из base.

Центральное свойство здесь одно, и оно про полномочия, а не про удобство:
и СПИСОК файлов, и их СОДЕРЖИМОЕ определяет ветка, в которую вливают. Автор
PR не может ни подложить ревьюеру удобный файл, ни убрать неудобный, ни
переписать контекст под свой патч. Тесты ниже проверяют именно это — на
настоящем git-репозитории, где head и base заведомо разошлись.
"""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "review" / "collect-context.sh"

MANIFEST = ".github/codex/review-context.txt"

# Тот же набор интерпретаторов, что у остальных скриптов кита: /bin/sh на macOS
# — bash 3.2, в CI — dash, и расхождения между ними уже ловили раньше.
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


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", message],
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        },
    )
    return git(repo, "rev-parse", "HEAD").strip()


def write(repo: Path, rel: str, text: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Репозиторий с одним base-коммитом: манифест на два файла и сами файлы."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", ".")
    write(r, MANIFEST, "# контекст\nsrc/producer.py\ndocs/contract.md\n")
    write(r, "src/producer.py", 'def produce():\n    return "base"\n')
    write(r, "docs/contract.md", "# Контракт\n\nЧитатель обязан отвергнуть пустое.\n")
    write(r, "src/neighbour.py", "SECRET_BASE = 1\n")
    commit(r, "base")
    return r


def run(repo: Path, base: str, *extra: str, interp: str = "sh") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [interp, str(SCRIPT), "--base", base, "--manifest", MANIFEST, *extra],
        capture_output=True,
        text=True,
        cwd=repo,
    )


def attached_paths(stdout: str) -> list[str]:
    return [ln.split()[2] for ln in stdout.splitlines() if ln.startswith("--- ФАЙЛ ")]


# --- Главное свойство: рамку задаёт base, не автор патча ---------------------


@pytest.mark.parametrize("interp", INTERPRETERS)
def test_content_comes_from_base_not_worktree(repo: Path, interp: str) -> None:
    """Правка файла в рабочем дереве не доезжает до ревьюера как контекст.

    Это и есть та половина свойства, ради которой содержимое читается
    `git show`'ом: подменив файл, автор не подменит ревьюеру картину мира —
    его правку тот увидит дифом, рядом с базовой версией.
    """
    base = git(repo, "rev-parse", "HEAD").strip()
    write(repo, "src/producer.py", 'def produce():\n    return "ПОДМЕНЕНО"\n')

    res = run(repo, base, interp=interp)

    assert res.returncode == 0, res.stderr
    assert 'return "base"' in res.stdout
    assert "ПОДМЕНЕНО" not in res.stdout


@pytest.mark.parametrize("interp", INTERPRETERS)
def test_manifest_itself_is_base_owned(repo: Path, interp: str) -> None:
    """Автор PR не может ни ДОБАВИТЬ себе удобный файл, ни УБРАТЬ неудобный.

    Вторая половина свойства, и без неё первая бесполезна: читать содержимое
    из base, а список — из ветки значило бы отдать автору выбор рамки. Здесь
    ветка одновременно вписывает в манифест соседний файл и вычёркивает
    контракт; из base не берётся ни то, ни другое.
    """
    base = git(repo, "rev-parse", "HEAD").strip()
    # PR правит сам манифест: добавляет удобный файл, убирает неудобный.
    write(repo, MANIFEST, "# контекст\nsrc/producer.py\nsrc/neighbour.py\n")
    commit(repo, "PR переписывает манифест")

    res = run(repo, base, interp=interp)

    assert res.returncode == 0, res.stderr
    assert attached_paths(res.stdout) == ["src/producer.py", "docs/contract.md"]
    assert "SECRET_BASE" not in res.stdout


def test_head_commits_do_not_leak_new_files(repo: Path) -> None:
    """Даже закоммиченный в ветку файл не попадает в пакет — база не сдвинулась."""
    base = git(repo, "rev-parse", "HEAD").strip()
    write(repo, "docs/contract.md", "# Контракт\n\nПЕРЕПИСАНО В ВЕТКЕ\n")
    commit(repo, "PR правит контракт")

    res = run(repo, base)

    assert res.returncode == 0, res.stderr
    assert "ПЕРЕПИСАНО В ВЕТКЕ" not in res.stdout
    assert "Читатель обязан отвергнуть пустое." in res.stdout


# --- Отпечатки --------------------------------------------------------------


def test_digest_matches_git_show_of_base(repo: Path) -> None:
    """Отпечаток равен sha256 блоба, то есть его можно перепроверить руками.

    Заявленный, но непроверяемый отпечаток не доказывает ничего; ровно поэтому
    содержимое идёт побайтово, без нормализации через `$(...)`.
    """
    base = git(repo, "rev-parse", "HEAD").strip()
    res = run(repo, base)
    assert res.returncode == 0, res.stderr

    blob = subprocess.run(
        ["git", "-C", str(repo), "show", f"{base}:src/producer.py"],
        capture_output=True,
        check=True,
    ).stdout
    expected = hashlib.sha256(blob).hexdigest()

    assert f"--- ФАЙЛ src/producer.py sha256:{expected} ---" in res.stdout


def test_content_is_byte_exact(repo: Path) -> None:
    """Хвостовые переводы строк сохраняются — иначе отпечаток недоказуем."""
    write(repo, "src/producer.py", "x = 1\n\n\n")
    base = commit(repo, "файл с хвостовыми переводами строк")

    res = run(repo, base)

    assert res.returncode == 0, res.stderr
    blob = subprocess.run(
        ["git", "-C", str(repo), "show", f"{base}:src/producer.py"],
        capture_output=True,
        check=True,
    ).stdout
    assert hashlib.sha256(blob).hexdigest() in res.stdout
    assert "x = 1\n\n\n" in res.stdout


# --- «Не настроено» отделено от «сломано» -----------------------------------


@pytest.mark.parametrize("interp", INTERPRETERS)
def test_absent_manifest_is_not_configured(repo: Path, interp: str) -> None:
    """Нет манифеста в base — код 3, отдельный от отказа.

    Без этого различия PR, который САМ вводит манифест, не смог бы позеленеть:
    в его base манифеста ещё нет. Фича блокировала бы собственное внедрение.
    """
    (repo / MANIFEST).unlink()
    base = commit(repo, "манифеста нет")

    res = run(repo, base, interp=interp)

    assert res.returncode == 3, res.stdout + res.stderr
    assert "не настроен" in res.stderr


def test_unresolvable_base_is_a_refusal(repo: Path) -> None:
    """Неразрешимый base — поломка вызывающего (2), не «не настроено» (3).

    `git show` падает одинаково на отсутствующем пути и на неизвестной ревизии;
    смешать их значило бы объявить контекст ненастроенным всякий раз, когда
    вызывающий передал мусор вместо SHA.
    """
    res = run(repo, "0" * 40)

    assert res.returncode == 2, res.stdout + res.stderr
    assert "не разрешается" in res.stderr


def test_listed_but_missing_file_is_a_refusal(repo: Path) -> None:
    """Манифест есть, а файла нет — отказ, а не тихо усохший пакет."""
    write(repo, MANIFEST, "src/producer.py\nsrc/gone.py\n")
    base = commit(repo, "манифест ссылается на несуществующее")

    res = run(repo, base)

    assert res.returncode == 2, res.stdout
    assert "src/gone.py" in res.stderr


def test_manifest_without_files_is_a_refusal(repo: Path) -> None:
    """Манифест из одних комментариев — пустой пакет, и это отказ.

    Молча пустой пакет вернул бы ревьюера в режим «вижу только диф», но уже
    незаметно: вердикт выглядел бы вынесенным с контекстом.
    """
    write(repo, MANIFEST, "# только комментарии\n\n")
    base = commit(repo, "пустой манифест")

    res = run(repo, base)

    assert res.returncode == 2, res.stdout
    assert "пуст" in res.stderr


# --- Границы пакета ---------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["/etc/passwd", "../outside.py", "src/*.py", "src/prod?cer.py", "src/[ab].py"],
)
def test_path_shapes_refused(repo: Path, bad: str) -> None:
    """Абсолютные пути, обход вверх и glob'ы отвергаются, а не разворачиваются.

    Glob важен отдельно от безопасности: шаблон означал бы, что новый файл
    попадает в пакет молча, без ревью правки манифеста.
    """
    write(repo, MANIFEST, f"{bad}\n")
    base = commit(repo, "плохой путь")

    res = run(repo, base)

    assert res.returncode == 2, res.stdout
    assert "недопустимый путь" in res.stderr


def test_per_file_cap(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD").strip()
    res = run(repo, base, "--max-file", "10")
    assert res.returncode == 2
    assert "больше 10 байт" in res.stderr


def test_total_cap(repo: Path) -> None:
    """Потолок суммы — про предсказуемость: пакет не должен вытеснить сам диф."""
    base = git(repo, "rev-parse", "HEAD").strip()
    res = run(repo, base, "--max-total", "40")
    assert res.returncode == 2
    assert "пакет контекста больше 40 байт" in res.stderr


def test_total_cap_counts_headers_not_just_blobs(repo: Path) -> None:
    """Потолок меряет СОБРАННЫЕ байты, а не сумму длин файлов.

    Заголовок `--- ФАЙЛ <путь> sha256:<64 hex> ---` весит около сотни байт — на
    манифесте из множества крошечных файлов именно заголовки составят почти весь
    пакет. Сумма блобов сказала бы «уложились», пока пакет вытесняет из промпта
    сам диф: та самая «неизвестность как зелёное», только у прибора.
    """
    paths = [f"tiny/f{i}.py" for i in range(8)]
    for rel in paths:
        write(repo, rel, "x\n")  # 2 байта каждый, 16 суммарно
    write(repo, MANIFEST, "\n".join(paths) + "\n")
    base = commit(repo, "много крошечных файлов")

    res = run(repo, base, "--max-total", "200")

    assert res.returncode == 2, res.stdout
    assert "собрано" in res.stderr


def test_directory_in_manifest_is_a_refusal(repo: Path) -> None:
    """Каталог в манифесте — отказ, а не «листинг дерева под видом файла».

    `git show <base>:<каталог>` выходит с нулём и печатает перечень имён. Без
    проверки типа объекта ревьюер получил бы заголовок, отпечаток и зелёный чек,
    но вместо кода модуля — оглавление.
    """
    write(repo, MANIFEST, "src\n")
    base = commit(repo, "каталог в манифесте")

    res = run(repo, base)

    assert res.returncode == 2, res.stdout
    assert "не файл, а tree" in res.stderr


def test_crlf_manifest_still_resolves_paths(repo: Path) -> None:
    """Манифест с CRLF не ломает поиск путей.

    `read -r` оставляет `\r` в конце строки, и `git show` искал бы блоб с этим
    символом в имени — отказ на существующем файле, красный чек на каждом PR,
    пока кто-то не перепишет манифест в LF.
    """
    (repo / MANIFEST).write_bytes(b"# \xd0\xba\xd0\xbe\xd0\xbc\r\nsrc/producer.py\r\n")
    base = commit(repo, "манифест с CRLF")

    res = run(repo, base)

    assert res.returncode == 0, res.stdout + res.stderr
    assert attached_paths(res.stdout) == ["src/producer.py"]


def test_binary_refused(repo: Path) -> None:
    """NUL-байт в файле — отказ: в промпте бинарь либо потеряется, либо порвёт разметку."""
    (repo / "src" / "blob.bin").write_bytes(b"\x00\x01\x02")
    write(repo, MANIFEST, "src/blob.bin\n")
    base = commit(repo, "бинарь в манифесте")

    res = run(repo, base)

    assert res.returncode == 2, res.stdout
    assert "не текстовый" in res.stderr


def test_comments_and_blank_lines_skipped(repo: Path) -> None:
    write(repo, MANIFEST, "\n# шапка\n\nsrc/producer.py\n\n# хвост\n")
    base = commit(repo, "манифест с комментариями")

    res = run(repo, base)

    assert res.returncode == 0, res.stderr
    assert attached_paths(res.stdout) == ["src/producer.py"]


@pytest.mark.parametrize("flag", ["--base", "--manifest", "--max-file", "--max-total"])
def test_bare_flag_is_a_config_error(repo: Path, flag: str) -> None:
    """Голый флаг без значения — код 2, а не платформозависимый сдвиг argv."""
    res = subprocess.run(
        ["sh", str(SCRIPT), flag],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert res.returncode == 2
    assert "usage:" in res.stderr


def test_header_does_not_collide_with_markdown_headings(repo: Path) -> None:
    """Заголовок файла отличим от markdown-заголовка ВНУТРИ приложенного файла.

    Найдено живым прогоном, не рассуждением: приложенный `contracts/…/README.md`
    сам содержит строки `### Матрица полей по state`, и при markdown'ном формате
    заголовка ревьюер прочёл бы их как границу нового файла — то есть приписал
    бы часть контракта несуществующему пути.
    """
    write(repo, "docs/contract.md", "# Контракт\n\n### Матрица полей\n\nтело\n")
    write(repo, MANIFEST, "docs/contract.md\n")
    base = commit(repo, "контракт с подзаголовками")

    res = run(repo, base)

    assert res.returncode == 0, res.stderr
    assert attached_paths(res.stdout) == ["docs/contract.md"]
    assert "### Матрица полей" in res.stdout
