"""Доставка PR (спека steward §3.3 пп. 1, 3, 4; строки E1, E2, E5, E6 §3.8).

Одна реализация чтения доставки на оба потребителя: `read_act_facts` и release-продюсер
(спека §6, «та же `_read_delivery`»). Отказ — `_Fail`; `Unavailable` из него делает
публичная функция на своей границе. Каждое поле ответа, ставшее полем `Delivery`/`PathBlob`,
проходит типизированный доступ `passes.py` (`_str`, `_stamp`, `_resolved`, `_get`) и
`_github.require_sha` — сырые значения JSON в модель не попадают. REST-пути — только через
`_github.rest_path` (каждый сегмент закодирован, спека §3.2 «Пути REST»).
"""

from collections.abc import Sequence
from dataclasses import dataclass, fields

from steward.approvalfacts.v3._github import _Fail, graphql, require_sha, rest_object, rest_path
from steward.approvalfacts.v3.model import Delivery, PathBlob
from steward.approvalfacts.v3.passes import _actor, _get, _resolved, _stamp, _str
from steward.approvalfacts.v3.queries import Q_DELIVERY, q_blobs
from steward.approvalfacts.v3.transport import Transport

_PR_STATES = frozenset({"OPEN", "CLOSED", "MERGED"})
#: Строка E5: `status` compare базы с merge-коммитом → достижимость merge-коммита из базы.
_IN_BASE = {"identical": True, "behind": True, "ahead": False, "diverged": False}
_GRAPHQL = "GraphQL pull request"
_REST = "REST pull request"

#: §3.3 п. 2: доставка несмёрженного PR — поля мержа не читаются.
NOT_MERGED = Delivery(
    merged=False,
    merge_commit=None,
    merged_at=None,
    merged_by_login=None,
    merged_by_type_hint=None,
    head_at_merge=None,
    in_base=False,
    paths=(),
)


@dataclass(frozen=True)
class Merge:
    """Поля мержа смёрженного PR, согласованные между E2 и E1 (§3.3 п. 1).

    Логин мержера — GraphQL-форма (у бота без `[bot]`); `merged_at` — канон.
    """

    merge_commit: str
    merged_at: str
    merged_by_login: str
    merged_by_type_hint: str
    head: str
    base_ref: str


def read_delivery(t: Transport, owner: str, name: str, pr: int) -> Delivery:
    """Доставка PR без путей (§3.3 пп. 1–3): E2, E1, затем E5 — только у смёрженного.

    Несмёрженный — `NOT_MERGED`; отказ — `_Fail`. Её же вызывает release-продюсер.
    """
    merge = read_merge(t, owner, name, pr)
    if merge is None:
        return NOT_MERGED
    return delivery_of(merge, read_in_base(t, owner, name, merge), ())


def delivery_of(merge: Merge, in_base: bool, paths: tuple[PathBlob, ...]) -> Delivery:
    """`Delivery` смёрженного PR из согласованных полей мержа, `in_base` и путей."""
    return Delivery(
        merged=True,
        merge_commit=merge.merge_commit,
        merged_at=merge.merged_at,
        merged_by_login=merge.merged_by_login,
        merged_by_type_hint=merge.merged_by_type_hint,
        head_at_merge=merge.head,
        in_base=in_base,
        paths=paths,
    )


def read_merge(t: Transport, owner: str, name: str, pr: int) -> Merge | None:
    """§3.3 п. 1: GraphQL `StewardV3Delivery` (E2), затем REST `pulls/{n}` (E1).

    Сначала `merged`: расходится — `delivery_mismatch`; `state` E2 вне словаря —
    `unknown_state`; оба `False` — `None`, прочие поля мержа не читаются (у несмёрженного
    PR REST несёт тестовый `merge_commit_sha`). Оба `True`: поле мержа отсутствует или
    `null` — `malformed`, мержер не установлен (§3.4.1) — `actor_unresolved`; мерж-коммит,
    `merged_at`, голова или база расходятся — `delivery_mismatch`; мержер после сведения
    REST-формы бота — `bot_login_mismatch`.
    """
    data = graphql(t, "StewardV3Delivery", Q_DELIVERY, {"owner": owner, "name": name, "number": pr})
    gql = _get(_get(data, "repository"), "pullRequest")
    if not isinstance(gql, dict):
        raise _Fail("malformed", "StewardV3Delivery: repository.pullRequest is not an object")
    rest = rest_object(
        t, rest_path("repos/{owner}/{name}/pulls/{pr}", owner=owner, name=name, pr=pr)
    )
    merged = _bool(gql, "merged", _GRAPHQL)
    if merged != _bool(rest, "merged", _REST):
        raise _Fail("delivery_mismatch", "merged differs between GraphQL and REST")
    state = _str(gql, "state", _GRAPHQL, code="unknown_state")
    if state not in _PR_STATES:
        raise _Fail("unknown_state", f"{_GRAPHQL}: state {state!r}")
    if not merged:
        return None
    graphql_side, rest_side = _graphql_merge(gql), _rest_merge(rest)
    differ = [
        f.name for f in fields(Merge) if getattr(graphql_side, f.name) != getattr(rest_side, f.name)
    ]
    if merge_fields := [f for f in differ if not f.startswith("merged_by_")]:
        raise _Fail("delivery_mismatch", f"GraphQL and REST differ in {', '.join(merge_fields)}")
    if differ:
        raise _Fail("bot_login_mismatch", "merger (login, type) differs between GraphQL and REST")
    return graphql_side


def read_in_base(t: Transport, owner: str, name: str, merge: Merge) -> bool:
    """§3.3 п. 3, строка E5: `compare/{base}...{merge}`; `status` вне словаря — `malformed`."""
    # Ветка с `/` уходит как `%2F` — живьём не снято (спека §3.2 «Пути REST», §8; чек-лист
    # плана Task 13, п. 8). Если GitHub её не раскодирует: 404 → `http`, т. е. отказ.
    path = rest_path(
        "repos/{owner}/{name}/compare/{base}...{merge}",
        owner=owner,
        name=name,
        base=merge.base_ref,
        merge=merge.merge_commit,
    )
    status = _str(rest_object(t, path), "status", path)
    if status not in _IN_BASE:
        raise _Fail("malformed", f"{path}: compare status {status!r}")
    return _IN_BASE[status]


def read_paths(
    t: Transport, owner: str, name: str, merge_commit: str, head: str, paths: Sequence[str]
) -> tuple[PathBlob, ...]:
    """§3.3 п. 4, строка E6: blob'ы путей в merge-коммите и на голове одним `StewardV3Blobs`.

    Выражения `"<sha>:<path>"` уходят переменными `m{i}`/`h{i}` (путь не входит в текст
    запроса). Коммит не разрешился — `object_missing`; путь отсутствует в merge-коммите —
    `blob_at_merge = None` (установлено успешным полным ответом, §10 О1); отсутствует на
    голове — `path_absent_at_head`; объект не Blob — `not_a_blob`. Пустой `paths` — `()` без
    запроса; недопустимый путь — `ValueError` (`check_paths`).
    """
    checked = check_paths(paths)
    if not checked:
        return ()
    require_sha(merge_commit, "StewardV3Blobs merge")
    require_sha(head, "StewardV3Blobs head")
    variables: dict[str, object] = {
        "owner": owner,
        "name": name,
        "merge": merge_commit,
        "head": head,
    }
    for i, path in enumerate(checked):
        variables[f"m{i}"] = f"{merge_commit}:{path}"
        variables[f"h{i}"] = f"{head}:{path}"
    data = graphql(t, "StewardV3Blobs", q_blobs(len(checked)), variables)
    repo = _get(data, "repository")
    if not isinstance(repo, dict):
        raise _Fail("malformed", "StewardV3Blobs: repository is not an object")
    _commit(repo, "mc", merge_commit)
    _commit(repo, "hc", head)
    return tuple(_path_blob(repo, i, path) for i, path in enumerate(checked))


def check_paths(paths: Sequence[str]) -> tuple[str, ...]:
    """Пути заявки; пустой, абсолютный, с сегментом `..` или NUL — `ValueError` (ошибка
    вызывающего, не факт форджа). Строка вместо последовательности путей — тоже."""
    if isinstance(paths, str):
        raise ValueError(f"paths must be a sequence of paths, not a str: {paths!r}")
    checked = tuple(paths)  # итератор материализуется один раз и не теряет пути
    for path in checked:
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or "\x00" in path
            or ".." in path.split("/")
        ):
            raise ValueError(f"bad path: {path!r}")
    return checked


def _graphql_merge(pull: dict) -> Merge:
    login, kind = _actor(pull.get("mergedBy"), f"{_GRAPHQL} mergedBy")
    return Merge(
        merge_commit=_sha(pull.get("mergeCommit"), "oid", f"{_GRAPHQL} mergeCommit"),
        merged_at=_merged_at(pull.get("mergedAt"), f"{_GRAPHQL} mergedAt"),
        merged_by_login=login,
        merged_by_type_hint=kind,
        head=_sha(pull, "headRefOid", _GRAPHQL),
        base_ref=_str(pull, "baseRefName", _GRAPHQL, nonempty=True),
    )


def _rest_merge(pull: dict) -> Merge:
    """Поля мержа E1; логин бота `slug[bot]` при `type = "Bot"` сводится к `slug`."""
    merged_by = pull.get("merged_by")
    login, kind = _get(merged_by, "login"), _get(merged_by, "type")
    if kind == "Bot" and isinstance(login, str):
        login = login.removesuffix("[bot]")
    login, kind = _resolved(login, kind, f"{_REST} merged_by")
    return Merge(
        merge_commit=_sha(pull, "merge_commit_sha", _REST),
        merged_at=_merged_at(pull.get("merged_at"), f"{_REST} merged_at"),
        merged_by_login=login,
        merged_by_type_hint=kind,
        head=_sha(pull.get("head"), "sha", f"{_REST} head"),
        base_ref=_str(pull.get("base"), "ref", f"{_REST} base", nonempty=True),
    )


def _sha(obj: object, key: str, where: str) -> str:
    """SHA-поле доставки: строка `^[0-9a-f]{40}$`, иначе `malformed` (спека §3.2)."""
    return require_sha(_str(obj, key, where, nonempty=True), f"{where} {key}")


def _merged_at(raw: object, where: str) -> str:
    """Метка мержа смёрженного PR: `null` — `malformed` (§3.3 п. 1), иначе канон."""
    if raw is None:
        raise _Fail("malformed", f"{where} is null for a merged pull request")
    return _stamp(raw)


def _bool(obj: dict, key: str, where: str) -> bool:
    """Булево поле JSON; нет или не `bool` — `malformed`."""
    value = obj.get(key)
    if not isinstance(value, bool):
        raise _Fail("malformed", f"{where}: {key} {value!r} is not a boolean")
    return value


def _alias(repo: dict, alias: str) -> dict | None:
    """Объект алиаса E6 или `null`; ключа нет или не объект — `malformed`."""
    if alias not in repo:
        raise _Fail("malformed", f"StewardV3Blobs: {alias} is missing")
    value = repo[alias]
    if value is not None and not isinstance(value, dict):
        raise _Fail("malformed", f"StewardV3Blobs: {alias} is not an object or null")
    return value


def _commit(repo: dict, alias: str, sha: str) -> None:
    if _alias(repo, alias) is None:
        raise _Fail("object_missing", f"commit {sha}")


def _path_blob(repo: dict, i: int, path: str) -> PathBlob:
    at_merge = _blob(repo, f"m{i}", path)
    at_head = _blob(repo, f"h{i}", path)
    if at_head is None:
        raise _Fail("path_absent_at_head", path)
    return PathBlob(path=path, blob_at_merge=at_merge, blob_at_head=at_head)


def _blob(repo: dict, alias: str, path: str) -> str | None:
    """`oid` Blob'а алиаса; `null` — `None`; иной тип объекта — `not_a_blob`."""
    obj = _alias(repo, alias)
    if obj is None:
        return None
    where = f"StewardV3Blobs {alias} ({path})"
    if _str(obj, "__typename", where) != "Blob":
        raise _Fail("not_a_blob", f"{path}: {obj.get('__typename')}")
    return _str(obj, "oid", where, nonempty=True)
