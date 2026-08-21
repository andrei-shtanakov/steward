"""Публикация: разрушать прежнее наблюдение можно только после того, как
всё остальное доказано."""

import json
import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.approvalfacts.model import Header, RequestId, Result, scope_digest
from steward.approvalfacts.publish import (
    ConfigError,
    parse_origin,
    publish,
    remove_previous,
    resolve_bundle_target,
)

SHA = "221457933968be9e95acd51d548e080f739c794c"


def _header(scope: list[RequestId]) -> Header:
    generated_at = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    return Header(
        repository="andrei-shtanakov/steward",
        generated_at=generated_at,
        valid_until=datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC),
        policy_version=1,
        policy_digest="sha256:" + "0" * 64,
        scope=tuple(scope),
        scope_sha256=scope_digest(scope),
    )


def _git_repo(tmp_path: Path, *, origin: str | None) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    if origin is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", origin], cwd=root, capture_output=True, check=True
        )
    return root


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:andrei-shtanakov/steward.git",
        "git@github.com:andrei-shtanakov/steward",
        "https://github.com/andrei-shtanakov/steward.git",
        "https://github.com/andrei-shtanakov/steward",
        "ssh://git@github.com/andrei-shtanakov/steward.git",
    ],
)
def test_parse_origin_accepts_known_forms(url: str) -> None:
    assert parse_origin(url) == ("andrei-shtanakov", "steward")


def test_parse_origin_rejects_suffix_match() -> None:
    """`endswith` совпал бы с чужим владельцем — сравнение только по полной паре."""
    assert parse_origin("git@github.com:other-owner/steward.git") == ("other-owner", "steward")


def test_parse_origin_distinguishes_full_pair_not_suffix() -> None:
    """Прямая пин-проверка сравнения по полной паре: `other/owner-steward` не
    должен совпасть с `owner/steward`, хотя суффиксная строка совпала бы."""
    owner, repo = parse_origin("git@github.com:other/owner-steward.git")
    assert (owner, repo) != ("owner", "steward")
    assert (owner, repo) == ("other", "owner-steward")


def test_parse_origin_rejects_garbage() -> None:
    with pytest.raises(ConfigError):
        parse_origin("not-a-url")


def test_publish_writes_atomically_and_durably(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    header = _header(scope)
    path = tmp_path / ".steward" / "approval_facts.jsonl"
    publish(path, header, [Result(scope[0], "merged", SHA, "github:x", "Bot", "agent")])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["kind"] == "header"
    assert json.loads(lines[0])["complete"] is True
    assert json.loads(lines[1])["state"] == "merged"
    assert not list(path.parent.glob(".approval_facts-*.tmp")), "временный файл не убран"


def test_publish_omits_forbidden_fields_for_negative_states(tmp_path: Path) -> None:
    """Схема запрещает identity у отрицательных состояний — сериализация
    обязана их не писать, а не писать `null`."""
    scope = [RequestId("pr", 42)]
    path = tmp_path / "facts.jsonl"
    publish(path, _header(scope), [Result(scope[0], "not_merged")])
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[1])
    assert record["merge_sha"] is None
    assert "identity" not in record and "type_hint" not in record
    assert "actor_class" not in record


def test_remove_previous_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "facts.jsonl"
    remove_previous(path)  # файла нет — не ошибка
    path.write_text("x", encoding="utf-8")
    remove_previous(path)
    assert not path.exists()


def test_resolve_bundle_target_requires_matching_origin(tmp_path: Path) -> None:
    root = _git_repo(tmp_path, origin="git@github.com:someone/else.git")
    with pytest.raises(ConfigError, match="origin"):
        resolve_bundle_target("andrei-shtanakov/steward", root)


def test_resolve_bundle_target_rejects_owner_suffix_collision(tmp_path: Path) -> None:
    """Прямая пин-проверка ruling #1 на уровне `resolve_bundle_target`, не
    только `parse_origin`: origin с владельцем `not-andrei-shtanakov`
    содержит `andrei-shtanakov/steward` как ПОДСТРОКУ (и как суффикс) в
    своём URL — сравнение `endswith`/`in` по сырой строке ошибочно приняло
    бы этот origin за совпадение. Сравнение обязано идти по разобранной
    паре `(owner, repo)` целиком, а не по вхождению строки."""
    root = _git_repo(tmp_path, origin="git@github.com:not-andrei-shtanakov/steward.git")
    with pytest.raises(ConfigError, match="origin"):
        resolve_bundle_target("andrei-shtanakov/steward", root)


def test_resolve_bundle_target_requires_origin_remote(tmp_path: Path) -> None:
    """Во флоте встречаются несколько remote на репо — берётся именно origin."""
    root = _git_repo(tmp_path, origin=None)
    with pytest.raises(ConfigError, match="origin"):
        resolve_bundle_target("andrei-shtanakov/steward", root)


def test_resolve_bundle_target_ignores_other_remotes(tmp_path: Path) -> None:
    """Несколько remote на одно репо (как у atp-platform во флоте) — сверяется
    именно origin, а не первый попавшийся или совпадающий по имени repo."""
    root = _git_repo(tmp_path, origin="git@github.com:andrei-shtanakov/steward.git")
    subprocess.run(
        ["git", "remote", "add", "mirror", "https://gitlab.example.com/mirror/steward.git"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    target = resolve_bundle_target("andrei-shtanakov/steward", root)
    assert target == root / ".steward" / "approval_facts.jsonl"


def test_resolve_bundle_target_returns_bundle_path(tmp_path: Path) -> None:
    root = _git_repo(tmp_path, origin="git@github.com:andrei-shtanakov/steward.git")
    target = resolve_bundle_target("andrei-shtanakov/steward", root)
    assert target == root / ".steward" / "approval_facts.jsonl"


def test_resolve_bundle_target_ignores_case(tmp_path: Path) -> None:
    root = _git_repo(tmp_path, origin="git@github.com:Andrei-Shtanakov/Steward.git")
    assert resolve_bundle_target("andrei-shtanakov/steward", root)


def test_publish_calls_fsync_on_file_and_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Долговечность отделена от атомарности: `os.replace` даёт вторую, но
    без `fsync` файла и директории переименование может не пережить сбой.

    Мы не можем наблюдать долговечность из теста напрямую — но можем
    зафиксировать, что код зовёт настоящий `os.fsync` на дескрипторах,
    которые в момент вызова указывают ровно на итоговый файл данных и на
    его родительскую директорию (сравнение по `(st_dev, st_ino)` после
    публикации), а не на мок, прошедший бы при любой реализации: два
    fsync на два случайных временных fd тоже дали бы `len(...) >= 2`, но
    не доказывали бы, что засинкан именно нужный файл и именно нужный
    каталог."""
    scope = [RequestId("pr", 1)]
    path = tmp_path / ".steward" / "approval_facts.jsonl"
    seen: list[tuple[int, int]] = []  # (st_dev, st_ino) в момент fsync
    real_fsync = os.fsync

    def spy_fsync(fd: int) -> None:
        st = os.fstat(fd)  # поднимет OSError на невалидном/закрытом fd
        seen.append((st.st_dev, st.st_ino))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    publish(path, _header(scope), [Result(scope[0], "not_merged")])

    final_file = os.stat(path)
    final_dir = os.stat(path.parent)
    assert (final_file.st_dev, final_file.st_ino) in seen, "файл данных не засинкан"
    assert (final_dir.st_dev, final_dir.st_ino) in seen, "родительская директория не засинкана"


def test_publish_removes_new_file_when_directory_fsync_fails_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex gate round 3 on PR #86: `os.replace()` can succeed and only THEN
    the follow-up directory fsync (`_fsync_dir`, confirming the rename's
    directory-entry durability across a crash) can fail — e.g. a filesystem
    that errors on directory fsync. Before this fix, the `except` block only
    removed the temp file; the already-replaced `path` stayed on disk even
    though `publish()` raised, contradicting every caller's "an exception
    means no file was published" contract (the CLI's exit-3 mechanical-
    failure path is documented as leaving the source absent).

    `os.fsync` is monkeypatched to fail only for a DIRECTORY fd (checked via
    `stat.S_ISDIR`) — the data-file fsync inside the `with os.fdopen(...)`
    block still succeeds normally, so the failure is pinned to exactly the
    post-`os.replace()` step this fix addresses, not to writing the content
    itself.
    """
    scope = [RequestId("pr", 1)]
    path = tmp_path / ".steward" / "approval_facts.jsonl"
    real_fsync = os.fsync

    def failing_dir_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("simulated directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_dir_fsync)

    with pytest.raises(OSError):
        publish(path, _header(scope), [Result(scope[0], "not_merged")])

    assert not path.exists(), "publish() raised — the just-replaced file must not remain"
    assert not list(path.parent.glob(".approval_facts-*.tmp")), "временный файл тоже не остаётся"


def test_ordering_previous_publication_survives_late_preflight_failure(tmp_path: Path) -> None:
    """Ordering-гарантия: прежняя публикация обязана пережить отказ на
    ПОЗДНЕМ шаге preflight (несовпадение origin — последний содержательный
    шаг resolve_bundle_target), а не только на раннем (отсутствие git-репо).
    Если бы удаление происходило раньше конца preflight, эта прежняя
    публикация была бы уже стёрта к моменту ConfigError."""
    root = _git_repo(tmp_path, origin="git@github.com:someone/else.git")
    bundle_dir = root / ".steward"
    bundle_dir.mkdir()
    bundle_path = bundle_dir / "approval_facts.jsonl"
    bundle_path.write_text('{"kind": "header"}\n', encoding="utf-8")

    with pytest.raises(ConfigError):
        resolve_bundle_target("andrei-shtanakov/steward", root)

    assert bundle_path.read_text(encoding="utf-8") == '{"kind": "header"}\n'


def test_ordering_previous_publication_survives_early_preflight_failure(tmp_path: Path) -> None:
    """То же самое, но для самого раннего шага (repo_root вне git-репозитория)
    — обе крайности preflight обязаны быть безопасны для прежней публикации."""
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    bundle_dir = outside / ".steward"
    bundle_dir.mkdir()
    bundle_path = bundle_dir / "approval_facts.jsonl"
    bundle_path.write_text("stale", encoding="utf-8")

    with pytest.raises(ConfigError):
        resolve_bundle_target("andrei-shtanakov/steward", outside)

    assert bundle_path.read_text(encoding="utf-8") == "stale"
