"""История источника политики и активация (спека steward §3.7; строки H1–H6 §3.8).

Только REST: читатель источника доверия работает и без credentials. Порядок обменов
фиксирован: H1 (репо), H2 (голова ветки, если `until` не задан), H3 по first-parent от
головы к якорю, H4 по звеньям от якоря к голове, H5 (дерево) по звеньям от якоря к голове,
H6 (блобы) по активациям. Нарушение формы истории — `HistoryViolation`; любой недочитанный
объект — `Unavailable` (`_Fail` превращается в него на границе публичной функции). Поля
ответов — через типизированный доступ `passes.py` и `_github.require_sha`; REST-пути — только
через `_github.rest_path`.
"""

import base64
import binascii
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

from steward.approvalfacts.v3._github import (
    _Fail,
    rest_array_pages,
    rest_object,
    require_sha,
    rest_path,
)
from steward.approvalfacts.v3.delivery import check_paths
from steward.approvalfacts.v3.model import Activation, HistoryViolation, Unavailable, violation
from steward.approvalfacts.v3.passes import _get, _opt_str, _stamp, _str, split_repo
from steward.approvalfacts.v3.transport import Transport

#: Файлы политики источника доверия (те же пути, что у devtools `act_policy.POLICY_FILES`).
POLICY_FILES: tuple[str, ...] = (
    "policy/approvers.env",
    "policy/modes.yaml",
    "policy/actors.yaml",
)
#: Предел цепочки first-parent (звеньев); превышение — `chain_limit`.
MAX_CHAIN = 1000

_HEX40 = re.compile(r"[0-9a-f]{40}", re.ASCII)


class _Violation(Exception):
    """Нарушение формы истории: превращается в `HistoryViolation` на границе."""

    def __init__(self, code: str, detail: str) -> None:
        self.violation = violation(code, detail)  # код вне словаря — ValueError
        super().__init__(self.violation.reason)


@dataclass(frozen=True)
class _Commit:
    """Звено H3: SHA, первый родитель (None — корень), дерево."""

    sha: str
    first_parent: str | None
    tree: str


@dataclass(frozen=True)
class _Merge:
    """Смёрженный PR, чей `merge_commit_sha` — данный коммит."""

    number: int
    merged_at: str
    base_ref: str


@dataclass(frozen=True)
class _Pull:
    """PR из H4; `merged_at`/`merge_commit` — None, если GitHub отдал `null`."""

    number: int
    merge_commit: str | None
    merged_at: str | None
    base_ref: str


def read_policy_history(
    transport: Transport,
    repo: str,
    branch: str,
    *,
    anchor: str,
    until: str | None,
    files: Sequence[str] = POLICY_FILES,
) -> tuple[Activation, ...] | HistoryViolation | Unavailable:
    """Активации политики ветки `branch` от якоря `anchor` до `until` (спека §3.7 пп. 1–7).

    `until = None` — голова ветки. Результат — активации в порядке first-parent, старые
    первыми; якорь — всегда первая. Нарушение формы истории (звено не PR-мерж в ветку, якорь
    не встречен до корня) — `HistoryViolation`; ветка не по умолчанию, недочитанный объект,
    предел цепочки — `Unavailable`. Пустой или недопустимый `files`, `anchor`/`until` не
    40-hex, `repo` не `owner/name` — `ValueError` до первого запроса.

    `until` — из журнала эпох или голова ветки: принадлежность `until` ветке не проверяется,
    и коммит вне ветки (например, из сети форков) дал бы `anchor_not_reached`.
    """
    owner, name = split_repo(repo)
    checked = _check_files(files)
    _check_sha(anchor, "anchor")
    if until is not None:
        _check_sha(until, "until")
    try:
        return _history(transport, owner, name, branch, anchor, until, checked)
    except _Fail as fail:
        return fail.unavailable()
    except _Violation as broken:
        return broken.violation


def read_activation(
    transport: Transport, repo: str, merge_commit: str, *, files: Sequence[str] = POLICY_FILES
) -> Activation | Unavailable:
    """Факт одной активации (якорь эпохи): H3, H4, H5, H6 (спека §3.7).

    Нет смёрженного PR с `merge_commit_sha == merge_commit` — `no_pr_for_commit`; ветка не
    проверяется (принадлежность якоря ветке доказывает `read_policy_history`). Ошибки
    вызывающего — `ValueError`, как у `read_policy_history`.
    """
    owner, name = split_repo(repo)
    checked = _check_files(files)
    _check_sha(merge_commit, "merge_commit")
    try:
        commit = _read_commit(transport, owner, name, merge_commit)
        pull = _merged_pull(_read_pulls(transport, owner, name, merge_commit), merge_commit)
        if pull is None:
            raise _Fail("no_pr_for_commit", merge_commit)
        blobs = _read_tree(transport, owner, name, commit.tree, checked)
        return _activation(transport, owner, name, commit, pull, blobs)
    except _Fail as fail:
        return fail.unavailable()


def _history(
    t: Transport,
    owner: str,
    name: str,
    branch: str,
    anchor: str,
    until: str | None,
    files: tuple[str, ...],
) -> tuple[Activation, ...]:
    repository = rest_object(t, rest_path("repos/{owner}/{name}", owner=owner, name=name))
    default = _str(repository, "default_branch", "H1 repository", nonempty=True)
    if default != branch:
        raise _Fail("branch_not_default", f"default branch {default!r}, not {branch!r}")
    head = until if until is not None else _read_head(t, owner, name, branch)
    chain = _read_chain(t, owner, name, head, anchor)
    pulls = [_link_pull(t, owner, name, branch, commit.sha) for commit in chain]
    trees = [_read_tree(t, owner, name, commit.tree, files) for commit in chain]
    activations: list[Activation] = []
    previous: dict[str, str] | None = None
    for commit, pull, blobs in zip(chain, pulls, trees, strict=True):
        if blobs != previous:  # сравнение с родителем, не с прошлой активацией (§3.7 п. 6)
            activations.append(_activation(t, owner, name, commit, pull, blobs))
        previous = blobs
    return tuple(activations)


def _read_head(t: Transport, owner: str, name: str, branch: str) -> str:
    """Строка H2: `commit.sha` головы ветки."""
    path = rest_path(
        "repos/{owner}/{name}/branches/{branch}", owner=owner, name=name, branch=branch
    )
    commit = _get(rest_object(t, path), "commit")
    return require_sha(_str(commit, "sha", f"{path} commit"), f"{path} commit.sha")


def _read_chain(t: Transport, owner: str, name: str, head: str, anchor: str) -> list[_Commit]:
    """Строка H3: first-parent от `head` до `anchor`; звенья от якоря к голове.

    Корень без якоря — `anchor_not_reached` (цепочка прочитана полностью); больше
    `MAX_CHAIN` звеньев — `chain_limit`.
    """
    chain: list[_Commit] = []
    sha = head
    while True:
        if len(chain) == MAX_CHAIN:
            raise _Fail("chain_limit", f"anchor {anchor} not within {MAX_CHAIN} links of {head}")
        commit = _read_commit(t, owner, name, sha)
        chain.append(commit)
        if commit.sha == anchor:
            return chain[::-1]
        if commit.first_parent is None:
            raise _Violation(
                "anchor_not_reached", f"root {commit.sha} reached from {head} without {anchor}"
            )
        sha = commit.first_parent


def _read_commit(t: Transport, owner: str, name: str, sha: str) -> _Commit:
    """Строка H3: `sha` (тот же, что запрошен), `parents` — 0–2 объекта, `commit.tree.sha`."""
    path = rest_path("repos/{owner}/{name}/commits/{sha}", owner=owner, name=name, sha=sha)
    body = rest_object(t, path)
    if require_sha(_str(body, "sha", path), f"{path} sha") != sha:
        raise _Fail("malformed", f"{path}: answer is for another commit")
    parents = body.get("parents")
    if not isinstance(parents, list) or len(parents) > 2:
        raise _Fail("malformed", f"{path}: parents is not a list of at most two")
    shas = [require_sha(_str(p, "sha", f"{path} parent"), f"{path} parent") for p in parents]
    tree = _get(_get(body, "commit"), "tree")
    return _Commit(
        sha=sha,
        first_parent=shas[0] if shas else None,
        tree=require_sha(_str(tree, "sha", f"{path} commit.tree"), f"{path} commit.tree.sha"),
    )


def _read_pulls(t: Transport, owner: str, name: str, sha: str) -> list[_Pull]:
    """Строка H4: все PR, ассоциированные с коммитом (все страницы по P1)."""
    path = rest_path(
        "repos/{owner}/{name}/commits/{sha}/pulls?per_page=100", owner=owner, name=name, sha=sha
    )
    return [_pull(item, f"{path} pull") for item in rest_array_pages(t, path)]


def _pull(item: dict, where: str) -> _Pull:
    number = item.get("number")
    if type(number) is not int:
        raise _Fail("malformed", f"{where}: number {number!r} is not an integer")
    merge_commit = _opt_str(item, "merge_commit_sha", where)
    merged_at = _opt_str(item, "merged_at", where)
    return _Pull(
        number=number,
        merge_commit=None if merge_commit is None else require_sha(merge_commit, where),
        merged_at=None if merged_at is None else _stamp(merged_at),
        base_ref=_str(_get(item, "base"), "ref", f"{where} base", nonempty=True),
    )


def _merged_pull(pulls: list[_Pull], sha: str) -> _Merge | None:
    """Единственный смёрженный PR с `merge_commit_sha == sha`; несколько — `malformed`.

    PR с `merge_commit_sha == sha`, но `merged_at = null` — не мерж (§13).
    """
    matches = [
        _Merge(p.number, p.merged_at, p.base_ref)
        for p in pulls
        if p.merged_at is not None and p.merge_commit == sha
    ]
    if len(matches) > 1:
        raise _Fail("malformed", f"{sha}: merge commit of PRs {[m.number for m in matches]}")
    return matches[0] if matches else None


def _link_pull(t: Transport, owner: str, name: str, branch: str, sha: str) -> _Merge:
    """§3.7 пп. 4–5: звено — PR-мерж в `branch`, иначе нарушение формы истории.

    Код при отсутствии своего PR-мержа: есть смёрженный PR, содержащий коммит, —
    `rebase_intermediate`; нет — `commit_without_pr` (обе гипотезы об API — нарушение).
    """
    pulls = _read_pulls(t, owner, name, sha)
    pull = _merged_pull(pulls, sha)
    if pull is not None:
        if pull.base_ref != branch:
            raise _Violation("pr_base_mismatch", f"{sha}: PR #{pull.number} into {pull.base_ref}")
        return pull
    if merged := [p.number for p in pulls if p.merged_at is not None]:
        raise _Violation("rebase_intermediate", f"{sha} is inside merged PRs {merged}")
    raise _Violation("commit_without_pr", f"{sha} is not a merge of any merged PR")


def _read_tree(
    t: Transport, owner: str, name: str, tree: str, files: tuple[str, ...]
) -> dict[str, str]:
    """Строка H5: набор `{path: blob oid}` файлов `files` из полного дерева.

    Отсутствие пути установлено только полным (`truncated = false`) успешным ответом на
    запрошенное дерево (`sha` ответа = `tree`); `truncated` — `tree_truncated`; не bool,
    дубль записи пути — `malformed`; запись пути не `blob` — `not_a_blob`.
    """
    path = rest_path(
        "repos/{owner}/{name}/git/trees/{tree}?recursive=1", owner=owner, name=name, tree=tree
    )
    body = rest_object(t, path)
    if require_sha(_str(body, "sha", path), f"{path} sha") != tree:
        raise _Fail("malformed", f"{path}: answer is for another tree")
    truncated = body.get("truncated")
    if not isinstance(truncated, bool):
        raise _Fail("malformed", f"{path}: truncated {truncated!r} is not a boolean")
    if truncated:
        raise _Fail("tree_truncated", path)
    entries = body.get("tree")
    if not isinstance(entries, list):
        raise _Fail("malformed", f"{path}: tree is not a list")
    found: dict[str, str] = {}
    for entry in entries:
        entry_path = _str(entry, "path", f"{path} entry")
        if entry_path not in files:
            continue
        if entry_path in found:
            raise _Fail("malformed", f"{path}: duplicate entry {entry_path}")
        if _str(entry, "type", f"{path} {entry_path}") != "blob":
            raise _Fail("not_a_blob", entry_path)
        found[entry_path] = require_sha(_str(entry, "sha", f"{path} {entry_path}"), entry_path)
    return found


def _activation(
    t: Transport, owner: str, name: str, commit: _Commit, pull: _Merge, blobs: dict[str, str]
) -> Activation:
    hashes = {path: _blob_sha256(t, owner, name, oid) for path, oid in blobs.items()}
    return Activation(pull.number, commit.sha, pull.merged_at, hashes)


def _blob_sha256(t: Transport, owner: str, name: str, oid: str) -> str:
    """Строка H6: `sha256(b64decode(content))` hex.

    `encoding` не `base64`, содержимое не base64 или его git-oid не `oid` — `malformed`.
    """
    path = rest_path("repos/{owner}/{name}/git/blobs/{oid}", owner=owner, name=name, oid=oid)
    body = rest_object(t, path)
    encoding = _str(body, "encoding", path)
    if encoding != "base64":
        raise _Fail("malformed", f"{path}: encoding {encoding!r}")
    try:
        # GitHub переносит base64 по 60 символов; прочее вне алфавита — не base64.
        raw = base64.b64decode(_str(body, "content", path).replace("\n", ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _Fail("malformed", f"{path}: content is not base64: {exc}") from exc
    # Связь байтов с деревом: git-oid содержимого — тот блоб, что назван записью дерева.
    if hashlib.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest() != oid:
        raise _Fail("malformed", f"{path}: content does not hash to the blob oid")
    return hashlib.sha256(raw).hexdigest()


def _check_files(files: Sequence[str]) -> tuple[str, ...]:
    """`files` непуст и в каноне путей (`check_paths`); иначе `ValueError` (§3.7, §13)."""
    checked = check_paths(files)
    if not checked:
        raise ValueError("files is empty: no policy files means no evidence, not no activations")
    # Путь, который не совпадёт ни с одной записью дерева, читался бы как «файла нет».
    if bad := [f for f in checked if {"", "."} & set(f.split("/"))]:
        raise ValueError(f"files not in canonical form: {bad!r}")
    return checked


def _check_sha(value: object, what: str) -> None:
    if not isinstance(value, str) or _HEX40.fullmatch(value) is None:
        raise ValueError(f"{what} is not a 40-hex SHA: {value!r}")
