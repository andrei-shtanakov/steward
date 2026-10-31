"""Release-продюсер `approval-facts/v3` (спека steward §6). Пока неактивен.

`steward approval-facts` и `gate-check --stage release` переключаются на v3 одним
изменением (Task 11); до него CLI выпускает v2, а гейт читает v2.

Разрешение элемента scope: `merge_sha` → PR — `associatedPullRequests` (строка E7, как в
v2, но через `Transport`), затем **та же** `read_delivery` библиотеки (§3.3 пп. 1, 3), что
и у `read_act_facts`. Граница v2 сохраняется: определённый ответ — запись, «не смогли
спросить» — `MechanicalFailure`, файл не публикуется. `actor_unresolved` мержера — не
сбой, а терминальное `actor_unavailable` с доставкой без мержера (гейт отвергает его
строкой 9 таблицы §8.3 v2). Отсутствующий PR (`pr`) — сбой, а не `not_found`: в v3 нет
терпимости v2 к `NOT_FOUND` (§3.2); `not_found` даёт только `object: null` строки E7.

`resolve_release`/`materialize_release` свободны от политики (`actor_class=None`);
классификация — отдельный чистый шаг `classify_release` по `classify_actor`, как в v2.
"""

from collections.abc import Sequence
from dataclasses import replace

from steward.approvalfacts.model import ObservationState, RequestId
from steward.approvalfacts.producer import MechanicalFailure
from steward.approvalfacts.v3._github import _Fail, graphql_pages
from steward.approvalfacts.v3.delivery import read_delivery
from steward.approvalfacts.v3.model import Delivery
from steward.approvalfacts.v3.passes import _get, _str, split_repo
from steward.approvalfacts.v3.queries import Q_COMMIT_PULLS
from steward.approvalfacts.v3.records import delivery_record
from steward.approvalfacts.v3.release_reader import SCHEMA_VERSION_V3, ReleaseResult
from steward.approvalfacts.v3.transport import Transport
from steward.gatecheck.approval import ApprovalPolicy, classify_actor

__all__ = [
    "SCHEMA_VERSION_V3",
    "ReleaseResult",
    "classify_release",
    "materialize_release",
    "release_record",
    "resolve_release",
]

_COMMIT_PULLS = "StewardV3CommitPulls"
_CONNECTION = ("repository", "object", "associatedPullRequests")


def materialize_release(t: Transport, repo: str, scope: Sequence[RequestId]) -> list[ReleaseResult]:
    """Разрешить весь scope по порядку; любой `MechanicalFailure` прерывает батч.

    `repo` не `owner/name` — `ValueError` до первого запроса.
    """
    owner, name = split_repo(repo)
    return [resolve_release(t, owner, name, request) for request in scope]


def resolve_release(t: Transport, owner: str, name: str, request: RequestId) -> ReleaseResult:
    """Разрешить элемент scope в терминальное наблюдение с доставкой (спека §6).

    `pr`: `merged` / `actor_unavailable` / `not_merged`. `merge_sha`: `merged` /
    `actor_unavailable` / `not_found` (`object: null`) / `no_matching_pr` (ни у одного PR
    коммита `mergeCommit` не этот SHA). Любой иной `Unavailable` — `MechanicalFailure`.
    """
    what = f"{request.kind} {request.value}"
    try:
        if request.kind == "pr":
            return _from_pr(t, owner, name, request, int(request.value))
        return _from_sha(t, owner, name, request, str(request.value))
    except _Fail as fail:
        raise MechanicalFailure(f"{what}: {fail.unavailable().reason}") from fail


def classify_release(
    results: Sequence[ReleaseResult], policy: ApprovalPolicy
) -> list[ReleaseResult]:
    """`actor_class` мержера `merged`-записей по `classify_actor`; прочие — как есть."""
    return [_classified(r, policy) if r.state == "merged" else r for r in results]


def release_record(r: ReleaseResult) -> dict[str, object]:
    """Запись `result` файла release-гейта (схема v3, `$defs.result`).

    Матрица полей §6: `merged` — доставка с мержером и классом; `actor_unavailable` —
    доставка без мержера; `not_merged`/`not_found`/`no_matching_pr` — `delivery: null`;
    `paths` — `[]`. Нарушение — `ValueError` (ошибка вызывающего), а не запись.
    """
    _check_matrix(r)
    delivery = (
        None if r.delivery is None else delivery_record(r.delivery, actor_class=r.actor_class)
    )
    return {
        "kind": "result",
        "request": r.request.as_dict(),
        "state": r.state,
        "delivery": delivery,
    }


def _from_pr(t: Transport, owner: str, name: str, request: RequestId, pr: int) -> ReleaseResult:
    delivery = read_delivery(t, owner, name, pr, merger_required=False)
    if not delivery.merged:
        return ReleaseResult(request, "not_merged", None, None)
    return _merged(request, delivery)


def _from_sha(t: Transport, owner: str, name: str, request: RequestId, sha: str) -> ReleaseResult:
    pages = _commit_pulls(t, owner, name, sha)
    if pages is None:
        return ReleaseResult(request, "not_found", None, None)
    pr = _pull_merged_as(pages, sha)
    if pr is None:
        return ReleaseResult(request, "no_matching_pr", None, None)
    delivery = read_delivery(t, owner, name, pr, merger_required=False)
    if not delivery.merged:
        raise MechanicalFailure(f"merge_sha {sha}: PR #{pr} carries it but is not merged")
    if delivery.merge_commit != sha:
        raise MechanicalFailure(
            f"merge_sha {sha}: PR #{pr} merge commit is {delivery.merge_commit}"
        )
    return _merged(request, delivery)


def _commit_pulls(t: Transport, owner: str, name: str, sha: str) -> list[dict] | None:
    """Строка E7: все страницы `associatedPullRequests` коммита; `None` — `object: null`.

    `null` разрешён только у `object` (`null_ok_at`); у соединения и прочих шагов —
    `malformed`."""
    variables = {"owner": owner, "name": name, "sha": sha}
    pages = graphql_pages(
        t, _COMMIT_PULLS, Q_COMMIT_PULLS, variables, connection=_CONNECTION, null_ok_at="object"
    )
    if _get(_get(pages[0], "repository"), "object") is None:
        return None
    return pages


def _pull_merged_as(pages: list[dict], sha: str) -> int | None:
    """Номер первого PR, чей `mergeCommit` — `sha`; `None` — такого PR у коммита нет."""
    for page in pages:
        for node in _nodes(page):
            if _merge_oid(node) == sha:
                return _number(node)
    return None


def _nodes(page: dict) -> list[dict]:
    connection = _get(_get(_get(page, "repository"), "object"), "associatedPullRequests")
    nodes = _get(connection, "nodes")
    if not isinstance(nodes, list) or not all(isinstance(n, dict) for n in nodes):
        raise _Fail("malformed", f"{_COMMIT_PULLS}: nodes is not a list of objects")
    return nodes


def _merge_oid(node: dict) -> str | None:
    """`mergeCommit.oid` узла E7 или `None` (`mergeCommit: null`); иная форма — `malformed`."""
    if "mergeCommit" not in node:
        raise _Fail("malformed", f"{_COMMIT_PULLS}: node without mergeCommit")
    merge_commit = node["mergeCommit"]
    if merge_commit is None:
        return None
    return _str(merge_commit, "oid", f"{_COMMIT_PULLS} mergeCommit", nonempty=True)


def _number(node: dict) -> int:
    number = node.get("number")
    if type(number) is not int or number < 1:
        raise _Fail("malformed", f"{_COMMIT_PULLS}: number {number!r} is not a PR number")
    return number


def _merged(request: RequestId, delivery: Delivery) -> ReleaseResult:
    """Смёрженный PR: мержер установлен — `merged`, иначе `actor_unavailable` (§3.4.1, §6)."""
    state: ObservationState = "merged" if delivery.merged_by_login else "actor_unavailable"
    return ReleaseResult(request, state, delivery, None)


def _classified(r: ReleaseResult, policy: ApprovalPolicy) -> ReleaseResult:
    d = r.delivery
    if d is None or d.merged_by_login is None:
        raise ValueError(f"{r.request}: merged result without a merger")
    identity = f"github:{d.merged_by_login}"
    return replace(r, actor_class=classify_actor(identity, d.merged_by_type_hint, policy))


def _check_matrix(r: ReleaseResult) -> None:
    """Матрица полей §6 до сериализации; `delivery_record` проверяет мержер и класс сам."""
    d = r.delivery
    if r.state in ("not_merged", "not_found", "no_matching_pr"):
        if d is not None or r.actor_class is not None:
            raise ValueError(f"{r.request}: state {r.state!r} carries no delivery or actor_class")
        return
    if d is None or not d.merged or d.paths:
        raise ValueError(f"{r.request}: state {r.state!r} needs a merged delivery without paths")
    has_merger = d.merged_by_login is not None
    if has_merger != (r.state == "merged") or has_merger != (r.actor_class is not None):
        raise ValueError(
            f"{r.request}: state {r.state!r} needs a classified merger exactly when merged"
        )
