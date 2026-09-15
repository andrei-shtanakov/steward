"""Тесты steward.review_eval.cache: bare-кэш объектов и detached worktree.

Дизайн: docs/superpowers/specs/2026-09-14-review-eval-harness-design.md §5, §6, D3.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

from steward.review_eval.cache import (
    CacheError,
    CacheUnavailable,
    has_object,
    materialize,
    repo_cache_dir,
    worktree,
)

REPO = "andrei-shtanakov/steward"
REMOTE_URL = "https://example.invalid/andrei-shtanakov/steward.git"
RANDOM_SHA = "f" * 40

_SHIM_SCRIPT = """#!/bin/sh
for arg in "$@"; do
  case "$arg" in
    http*|git@*)
      echo "network blocked (test shim)" >&2
      exit 1
      ;;
  esac
done
exec "{real_git}" "$@"
"""


def _real_git() -> str:
    result = subprocess.run(["which", "git"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _make_fixture_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Репо с двумя коммитами (разное содержимое ``a.txt``). Возвращает (path, sha1, sha2)."""
    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("first\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "first")
    sha1 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    (repo / "a.txt").write_text("second\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-a", "-m", "second")
    sha2 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return repo, sha1, sha2


def _make_network_blocking_shim(tmp_path: Path) -> str:
    """`git`, который падает на любом аргументе-URL (``http*``/``git@*``), иначе — настоящий git.

    Доказывает, что materialize из локального источника не касается сети:
    сеть используется только в fallback-fetch на `remote_url`.
    """
    shim = tmp_path / "git-shim"
    shim.write_text(_SHIM_SCRIPT.format(real_git=_real_git()), encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(shim)


# --------------------------------------------------------------------------
# repo_cache_dir
# --------------------------------------------------------------------------


def test_repo_cache_dir_shape(tmp_path: Path) -> None:
    """Две компоненты пути: владелец каталогом, имя — файлом ``<name>.git``."""
    assert repo_cache_dir(tmp_path, REPO) == tmp_path / "andrei-shtanakov" / "steward.git"


def test_repo_cache_dir_is_injective(tmp_path: Path) -> None:
    """Разные репо — разные пути кэша.

    Склейка через ``owner__name`` их не различала (`a__b/c` и `a/b__c` давали
    один каталог), то есть два репозитория делили кэш: диапазон одного
    «материализовался» бы объектом другого — измерение чужого дерева под своим
    gold. Путь из двух компонент различает и одноимённые репозитории разных
    владельцев, и разные имена у одного.
    """
    assert repo_cache_dir(tmp_path, "org-a/service") != repo_cache_dir(tmp_path, "org-b/service")
    assert repo_cache_dir(tmp_path, "org/a.b") != repo_cache_dir(tmp_path, "org/a_b")


@pytest.mark.parametrize("repo", ["../evil", "a/..", "./x", "a/.", "not-a-repo"])
def test_repo_cache_dir_rejects_malformed_repo(tmp_path: Path, repo: str) -> None:
    """Форма проверяется до склейки пути: `..` компонентой вывела бы за корень кэша."""
    with pytest.raises(CacheError, match="repo"):
        repo_cache_dir(tmp_path, repo)


def test_materialized_object_does_not_leak_between_repos(tmp_path: Path) -> None:
    """Объект, материализованный для `org-a/service`, не виден как `org-b/service`."""
    fixture, sha1, _sha2 = _make_fixture_repo(tmp_path)
    cache_root = tmp_path / "cache"

    materialize(cache_root, "org-a/service", [sha1], local_checkout=fixture, remote_url=REMOTE_URL)

    assert has_object(cache_root, "org-a/service", sha1) is True
    assert has_object(cache_root, "org-b/service", sha1) is False


# --------------------------------------------------------------------------
# materialize
# --------------------------------------------------------------------------


def test_materialize_from_local_checkout_without_network(tmp_path: Path) -> None:
    fixture, sha1, sha2 = _make_fixture_repo(tmp_path)
    cache_root = tmp_path / "cache"
    shim = _make_network_blocking_shim(tmp_path)

    materialize(
        cache_root,
        REPO,
        [sha1, sha2],
        local_checkout=fixture,
        remote_url=REMOTE_URL,
        git=shim,
    )

    assert has_object(cache_root, REPO, sha1, git=shim)
    assert has_object(cache_root, REPO, sha2, git=shim)


def test_materialize_missing_sha_with_failing_remote_raises(tmp_path: Path) -> None:
    fixture, _sha1, _sha2 = _make_fixture_repo(tmp_path)
    cache_root = tmp_path / "cache"
    shim = _make_network_blocking_shim(tmp_path)

    with pytest.raises(CacheError, match=f"{REPO}@{RANDOM_SHA}"):
        materialize(
            cache_root,
            REPO,
            [RANDOM_SHA],
            local_checkout=fixture,
            remote_url=REMOTE_URL,
            git=shim,
        )


def test_materialize_is_noop_when_cache_already_has_object(tmp_path: Path) -> None:
    fixture, sha1, _sha2 = _make_fixture_repo(tmp_path)
    cache_root = tmp_path / "cache"
    shim = _make_network_blocking_shim(tmp_path)

    materialize(cache_root, REPO, [sha1], local_checkout=fixture, remote_url=REMOTE_URL, git=shim)
    # повторный вызов не должен требовать сети и не должен падать
    materialize(cache_root, REPO, [sha1], local_checkout=fixture, remote_url=REMOTE_URL, git=shim)
    assert has_object(cache_root, REPO, sha1, git=shim)


# --------------------------------------------------------------------------
# has_object
# --------------------------------------------------------------------------


def test_has_object_false_for_random_sha(tmp_path: Path) -> None:
    fixture, sha1, _sha2 = _make_fixture_repo(tmp_path)
    cache_root = tmp_path / "cache"
    materialize(cache_root, REPO, [sha1], local_checkout=fixture, remote_url=REMOTE_URL)
    assert has_object(cache_root, REPO, RANDOM_SHA) is False


def test_has_object_false_when_cache_missing(tmp_path: Path) -> None:
    assert has_object(tmp_path / "cache", REPO, RANDOM_SHA) is False


# --------------------------------------------------------------------------
# worktree
# --------------------------------------------------------------------------


def test_worktree_isolates_historical_content(tmp_path: Path) -> None:
    fixture, sha1, sha2 = _make_fixture_repo(tmp_path)
    cache_root = tmp_path / "cache"
    materialize(cache_root, REPO, [sha1, sha2], local_checkout=fixture, remote_url=REMOTE_URL)

    dest = tmp_path / "wt" / "first"
    with worktree(cache_root, REPO, sha1, dest) as wt_path:
        assert wt_path == dest
        assert (dest / "a.txt").read_text(encoding="utf-8") == "first\n"


def test_worktree_removed_after_context_exits(tmp_path: Path) -> None:
    fixture, sha1, _sha2 = _make_fixture_repo(tmp_path)
    cache_root = tmp_path / "cache"
    materialize(cache_root, REPO, [sha1], local_checkout=fixture, remote_url=REMOTE_URL)

    dest = tmp_path / "wt" / "gone"
    with worktree(cache_root, REPO, sha1, dest):
        assert dest.exists()
    assert not dest.exists()


def test_worktree_leaves_no_registration_after_removal(tmp_path: Path) -> None:
    """`worktree remove --force` снимает и каталог, и регистрацию — prune не нужен.

    Безусловный `prune` после удачного `remove` был опасен: при `--jobs > 1` он
    мог снять регистрацию worktree соседнего прогона, чей каталог в этот момент
    существует. Тест закрепляет, что чистить после себя нечего.
    """
    fixture, sha1, _sha2 = _make_fixture_repo(tmp_path)
    cache_root = tmp_path / "cache"
    materialize(cache_root, REPO, [sha1], local_checkout=fixture, remote_url=REMOTE_URL)
    cache = repo_cache_dir(cache_root, REPO)

    dest = tmp_path / "wt" / "one"
    with worktree(cache_root, REPO, sha1, dest):
        pass

    listing = subprocess.run(
        ["git", "-C", str(cache), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert str(dest) not in listing
    assert "prunable" not in listing


def test_materialize_does_not_hardlink_objects_from_a_local_checkout(tmp_path: Path) -> None:
    """Кэш независим от соседнего чекаута: объекты копируются, не жёстко связываются.

    `git clone --bare` локального пути по умолчанию раскладывает хардлинки, и
    «полный клон» делил бы хранилище объектов с чекаутом соседа — ровно то, от
    чего кэш должен быть свободен (§5).
    """
    fixture, sha1, _sha2 = _make_fixture_repo(tmp_path)
    cache_root = tmp_path / "cache"
    materialize(cache_root, REPO, [sha1], local_checkout=fixture, remote_url=REMOTE_URL)

    objects = [
        path
        for path in (repo_cache_dir(cache_root, REPO) / "objects").rglob("*")
        if path.is_file() and path.suffix not in (".pack", ".idx")
    ]
    assert objects, "в кэше нет ни одного объекта — фикстура сломана"
    assert all(path.stat().st_nlink == 1 for path in objects), [
        (str(path), path.stat().st_nlink) for path in objects
    ]


def test_worktree_kept_when_keep_true(tmp_path: Path) -> None:
    fixture, sha1, _sha2 = _make_fixture_repo(tmp_path)
    cache_root = tmp_path / "cache"
    materialize(cache_root, REPO, [sha1], local_checkout=fixture, remote_url=REMOTE_URL)

    dest = tmp_path / "wt" / "kept"
    with worktree(cache_root, REPO, sha1, dest, keep=True):
        assert dest.exists()
    assert dest.exists()
    assert (dest / "a.txt").read_text(encoding="utf-8") == "first\n"


def test_second_worktree_same_sha_different_dest(tmp_path: Path) -> None:
    fixture, sha1, _sha2 = _make_fixture_repo(tmp_path)
    cache_root = tmp_path / "cache"
    materialize(cache_root, REPO, [sha1], local_checkout=fixture, remote_url=REMOTE_URL)

    dest_a = tmp_path / "wt" / "a"
    dest_b = tmp_path / "wt" / "b"
    with worktree(cache_root, REPO, sha1, dest_a):
        with worktree(cache_root, REPO, sha1, dest_b) as wt_b:
            assert wt_b == dest_b
            assert (dest_b / "a.txt").read_text(encoding="utf-8") == "first\n"
    assert not dest_a.exists()
    assert not dest_b.exists()


def test_worktree_missing_sha_raises(tmp_path: Path) -> None:
    """Кэш есть, объекта в нём нет — `CacheError`, но **не** `CacheUnavailable`.

    Это разные факты: «спрашивать некого» (кэша нет) и «спросили, такого
    объекта нет». Второй лечится материализацией именно этого sha, первый —
    созданием кэша, и потребитель метрик их различает (`CacheUnavailable`
    делает метрику непосчитанной, а не нулевой).
    """
    fixture, sha1, _sha2 = _make_fixture_repo(tmp_path)
    cache_root = tmp_path / "cache"
    materialize(cache_root, REPO, [sha1], local_checkout=fixture, remote_url=REMOTE_URL)

    dest = tmp_path / "wt" / "missing"
    with pytest.raises(CacheError, match=RANDOM_SHA) as excinfo:
        with worktree(cache_root, REPO, RANDOM_SHA, dest):
            pass
    assert not isinstance(excinfo.value, CacheUnavailable)
    assert not dest.exists()


def test_worktree_without_a_cache_raises_cache_unavailable(tmp_path: Path) -> None:
    """Кэша репозитория нет вовсе — `CacheUnavailable`, а не «объекта нет».

    Тип отказа — часть контракта модуля: он уже отличает «кэша нет» от «нет
    объекта» для метрик, и worktree отвечал на оба одинаково, пряча ошибку
    порядка шагов (забыли `materialize`) за видом ошибки данных.
    """
    cache_root = tmp_path / "cache"
    dest = tmp_path / "wt" / "no-cache"

    with pytest.raises(CacheUnavailable, match="materialize") as excinfo:
        with worktree(cache_root, REPO, RANDOM_SHA, dest):
            pass

    assert str(repo_cache_dir(cache_root, REPO)) in str(excinfo.value)
    assert not dest.exists()
