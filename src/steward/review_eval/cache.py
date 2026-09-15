"""Bare-кэш git-объектов review-eval и detached worktree на исторический sha.

Материализация (`materialize`) — единственный сетевой шаг во всём инструменте
(D2/D3, `docs/superpowers/specs/2026-09-14-review-eval-harness-design.md` §5):
для каждого кейса гарантирует локальную доступность `base_sha`/`head_sha` в
полном bare-клоне `eval/cache/<owner>/<name>.git` (``git clone --bare``, без
``--shared``/``--reference`` — кэш не должен зависеть от ``.git`` соседнего
чекаута, который может быть перепакован или удалён). Источник — локальный
чекаут соседа (быстро, без сети); при отсутствии объекта там — ``git fetch``
из GitHub URL. `run` (§6) сети не касается никогда: `worktree` только читает
уже материализованный кэш и отказывает (`CacheError`), если объекта в нём нет.

Соседние чекауты не модифицируются никогда (полирепо-правило): клон и fetch
только читают их, worktree создаются от кэша.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from steward.review_eval.corpus import REPO_RE

__all__ = [
    "CacheError",
    "CacheUnavailable",
    "has_object",
    "materialize",
    "repo_cache_dir",
    "worktree",
]


class CacheError(Exception):
    """Bare-кэш не удалось создать/пополнить, либо worktree не удалось создать/убрать."""


class CacheUnavailable(CacheError):
    """Кэша репозитория нет вовсе — спросить у git нечего и некого.

    Отдельный тип, а не текст сообщения: «кэша нет» — законное состояние
    (метрики пересчитывают из скопированного прогона, у которого кэша по
    правилу не бывает, §7), и потребитель обязан отличать его и от «объекта в
    кэше нет» (факт о данных), и от «git отказал» (ошибка конфигурации).
    Ответ на него — не считать метрику, а не считать её нулём.
    """


def repo_cache_dir(cache_root: Path, repo: str) -> Path:
    """Путь к bare-кэшу репозитория: ``<cache_root>/<owner>/<name>.git``.

    **Две компоненты, а не склейка `owner__name`.** Алфавит `repo` допускает
    ``_`` с обеих сторон слеша, поэтому склейка не была взаимно однозначной:
    ``a__b/c`` и ``a/b__c`` давали один каталог `a__b__c.git`, то есть два
    репозитория делили кэш объектов. Диапазон одного тогда «материализуется»
    объектом другого, и прогон измерил бы чужое дерево под своим gold —
    молча, потому что объект действительно есть.

    Форма `repo` — тот же `corpus.REPO_RE`, которым её проверяет схема кейса:
    два места с разными правилами давали кейс, валидный для корпуса и негодный
    для кэша, — и отказ приходил на шаг позже своей причины. Компоненты ``.``
    и ``..`` проверяются здесь ещё раз (их запрещает и `REPO_RE`): путь строится
    из них, и цена ошибки — запись за пределами корня кэша.
    """
    if not REPO_RE.fullmatch(repo):
        raise CacheError(f"repo must look like 'org/repo' of [A-Za-z0-9_.-], got '{repo}'")
    owner, _sep, name = repo.partition("/")
    if {owner, name} & {".", ".."}:
        raise CacheError(f"repo components must not be '.' or '..', got '{repo}'")
    return cache_root / owner / f"{name}.git"


def materialize(
    cache_root: Path,
    repo: str,
    shas: Iterable[str],
    *,
    local_checkout: Path | None,
    remote_url: str,
    git: str = "git",
) -> None:
    """Гарантирует локальную доступность объектов `shas` в bare-кэше `repo`.

    Если кэша ещё нет — ``git clone --bare --no-hardlinks`` от `local_checkout`
    (если задан) или от `remote_url`. Затем для каждого sha, которого нет в
    кэше (`has_object`): ``git fetch <local_checkout> <sha>``, при неудаче —
    ``git fetch <remote_url> <sha>``. Если объект недостижим ни оттуда, ни
    оттуда — `CacheError`, называющая репозиторий и sha.

    ``--no-hardlinks`` обязателен, а не гигиеничен: клон локального пути по
    умолчанию **раскладывает хардлинки** на файлы объектов, и «полный клон»
    делил бы хранилище с чекаутом соседа — ровно та зависимость, от которой
    кэш должен быть свободен. Для сетевого источника флаг ничего не меняет.
    """
    cache = repo_cache_dir(cache_root, repo)
    if not cache.exists():
        source = str(local_checkout) if local_checkout is not None else remote_url
        cache.parent.mkdir(parents=True, exist_ok=True)
        result = _run(git, None, ["clone", "--bare", "--no-hardlinks", source, str(cache)])
        if result.returncode != 0:
            raise CacheError(f"git clone --bare {source} {cache} failed: {_stderr(result)}")

    for sha in shas:
        if has_object(cache_root, repo, sha, git=git):
            continue
        errors: list[str] = []
        fetched = False
        if local_checkout is not None:
            result = _run(git, cache, ["fetch", str(local_checkout), sha])
            if result.returncode == 0:
                fetched = True
            else:
                errors.append(f"fetch {local_checkout}: {_stderr(result)}")
        if not fetched:
            result = _run(git, cache, ["fetch", remote_url, sha])
            if result.returncode == 0:
                fetched = True
            else:
                errors.append(f"fetch {remote_url}: {_stderr(result)}")
        if not fetched or not has_object(cache_root, repo, sha, git=git):
            detail = "; ".join(errors) if errors else "object not found after fetch"
            raise CacheError(f"cannot materialize {repo}@{sha}: {detail}")


def has_object(cache_root: Path, repo: str, sha: str, *, git: str = "git") -> bool:
    """Есть ли коммит `sha` в bare-кэше `repo`. Никогда не касается сети.

    ``False`` — «объекта нет»; неработающий `git` — `CacheError` из `_run`, а
    не тот же ``False``: иначе неверный `--git` выглядел бы как непокрытый кэш.

    Отсутствие кэша целиком тоже даёт ``False``, и предикат остаётся булевым
    сознательно: обоим вызывающим этого достаточно. `materialize` вызывает его
    сразу после клонирования, где «кэша нет» невозможно, а раннер отвечает на
    ``False`` отказом, который и так называет `materialize`. Различать эти
    состояния типом нужно там, где ответ уходит в метрику
    (`CacheUnavailable` — см. `worktree` и `cli._file_lines_at`).
    """
    cache = repo_cache_dir(cache_root, repo)
    if not cache.exists():
        return False
    result = _run(git, cache, ["cat-file", "-e", f"{sha}^{{commit}}"])
    return result.returncode == 0


@contextmanager
def worktree(
    cache_root: Path,
    repo: str,
    sha: str,
    dest: Path,
    *,
    keep: bool = False,
    git: str = "git",
) -> Iterator[Path]:
    """Detached worktree на `sha`, материализованный от bare-кэша `repo` в `dest`.

    Требует, чтобы `sha` уже был в кэше (`materialize` вызывается раздельно и
    может ходить в сеть; `worktree` — никогда). Два отказа различаются типом:
    кэша репозитория нет вовсе — `CacheUnavailable` (шаги перепутаны, нужен
    `materialize`), кэш есть, а объекта в нём нет — `CacheError` (нужен именно
    этот sha). При выходе из контекста, если не `keep` —
    ``git worktree remove --force``; при `keep` worktree остаётся.

    ``git worktree prune`` здесь **не** вызывается: удачный ``remove`` снимает
    и каталог, и регистрацию, а prune — операция уровня репозитория, которая
    при `--jobs > 1` сняла бы регистрацию worktree соседнего прогона. Чистка
    остатков (после `--keep-worktrees` или обрыва) — забота того, кто каталог
    удалил: `runner._clear_scratch`.
    """
    cache = repo_cache_dir(cache_root, repo)
    if not cache.exists():
        raise CacheUnavailable(
            f"bare-кэша {cache} нет — его создаёт 'review-eval corpus materialize' (сеть)"
        )
    if not has_object(cache_root, repo, sha, git=git):
        raise CacheError(f"object {sha} is not in cache for {repo}; run materialize first")
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = _run(git, cache, ["worktree", "add", "--detach", str(dest), sha])
    if result.returncode != 0:
        raise CacheError(f"git worktree add --detach {dest} {sha} failed: {_stderr(result)}")
    try:
        yield dest
    finally:
        if not keep:
            remove_result = _run(git, cache, ["worktree", "remove", "--force", str(dest)])
            if remove_result.returncode != 0:
                raise CacheError(
                    f"git worktree remove --force {dest} failed: {_stderr(remove_result)}"
                )


def _run(git: str, cwd: Path | None, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Запустить `git` с явными `args`, опционально ``-C <cwd>``.

    Ненулевой код выхода — обычный результат (решает вызывающий), но
    **невозможность запустить** `git` — `CacheError`: `OSError` («нет такого
    файла», «отказано в доступе») означает неверный `--git`, то есть ошибку
    конфигурации, а не сбой инструмента.
    """
    command = [git]
    if cwd is not None:
        command += ["-C", str(cwd)]
    command += args
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise CacheError(f"cannot run '{git}': {error}") from error


def _stderr(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or f"exit code {result.returncode}"
