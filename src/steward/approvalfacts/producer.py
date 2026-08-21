"""Продюсер `approval-facts/v2` поверх `gh`.

Граница проведена один раз и держится везде: **механический сбой** (не смогли
спросить) публикацию отменяет, **определённый отрицательный ответ** (спросили,
получили «нет») становится записью. Смешивать их нельзя — иначе отказ доступа
читался бы как свойство мержа.

`resolve()`/`materialize()` свободны от политики: они возвращают факты форджа
с `actor_class=None`. Классификация — отдельный чистый шаг, `classify_results()`
ниже, применяемый вызывающей стороной между материализацией и публикацией. Это
разделение проведено намеренно (контроллерский ruling к задаче 4): schema и
reader требуют `actor_class` на `state: merged`, но producer не обязан (и не
должен) знать политику, чтобы оставаться тестируемым без неё.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from steward.approvalfacts.model import RequestId, Result
from steward.gatecheck.approval import ApprovalPolicy, classify_actor

_QUERY_BY_PR = (
    "query($owner:String!,$name:String!,$number:Int!){"
    "repository(owner:$owner,name:$name){pullRequest(number:$number){"
    "mergeCommit{oid} mergedBy{login __typename}}}}"
)

_QUERY_BY_SHA = (
    "query($owner:String!,$name:String!,$sha:GitObjectID!,$after:String){"
    "repository(owner:$owner,name:$name){object(oid:$sha){"
    "... on Commit{associatedPullRequests(first:100,after:$after){"
    "nodes{mergeCommit{oid} mergedBy{login __typename}}"
    "pageInfo{hasNextPage endCursor}}}}}}"
)

#: Санитарный предел обхода. Превышение — механический сбой, НЕ отрицательный
#: факт: любой потолок, выданный за «совпадения нет», воспроизводит ровно ту
#: ложь, ради устранения которой обход и делается исчерпывающим.
_MAX_PAGES = 50


class MechanicalFailure(RuntimeError):
    """Спросить не удалось: транспорт, auth, GraphQL errors, неполный обход."""


def _gh(args: list[str]) -> tuple[int, str]:
    """Единственная точка вызова `gh` — тесты подменяют её целиком."""
    try:
        proc = subprocess.run(  # noqa: S603 S607 — фиксированный argv
            ["gh", *args], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout if proc.returncode == 0 else proc.stderr).strip()


def _graphql(query: str, variables: dict[str, Any], *, what: str) -> dict[str, Any]:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            continue
        args += ["-F", f"{key}={value}"] if isinstance(value, int) else ["-f", f"{key}={value}"]
    code, out = _gh(args)
    if code != 0:
        raise MechanicalFailure(f"{what}: gh завершился с кодом {code}: {out}")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise MechanicalFailure(f"{what}: ответ не JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise MechanicalFailure(f"{what}: ответ не объект")
    if payload.get("errors"):
        raise MechanicalFailure(f"{what}: GraphQL errors: {payload['errors']}")
    return payload


def _repository(payload: dict[str, Any], what: str) -> dict[str, Any]:
    """`repository: null` — недоступность, а не отсутствие.

    Раньше здесь стояло `repository or {}`, из-за чего отказ доступа
    докладывался как авторитетное «not found».
    """
    data = payload.get("data")
    if not isinstance(data, dict) or "repository" not in data:
        raise MechanicalFailure(f"{what}: в ответе нет data.repository")
    repository = data["repository"]
    if repository is None:
        raise MechanicalFailure(
            f"{what}: repository = null — недоступность (auth/visibility), не отсутствие"
        )
    if not isinstance(repository, dict):
        raise MechanicalFailure(f"{what}: repository не объект")
    return repository


def _actor(node: dict[str, Any]) -> tuple[str, str] | None:
    merged_by = node.get("mergedBy")
    if not isinstance(merged_by, dict):
        return None
    login, hint = merged_by.get("login"), merged_by.get("__typename")
    if not isinstance(login, str) or not isinstance(hint, str):
        return None
    return f"github:{login}", hint


def _resolve_pr(owner: str, name: str, request: RequestId) -> Result:
    what = f"PR #{request.value}"
    payload = _graphql(
        _QUERY_BY_PR, {"owner": owner, "name": name, "number": int(request.value)}, what=what
    )
    pull_request = _repository(payload, what).get("pullRequest")
    if pull_request is None:
        return Result(request, "not_found")
    if not isinstance(pull_request, dict):
        raise MechanicalFailure(f"{what}: pullRequest не объект")
    merge_commit = pull_request.get("mergeCommit")
    if merge_commit is None:
        return Result(request, "not_merged")
    sha = (merge_commit or {}).get("oid")
    if not isinstance(sha, str):
        raise MechanicalFailure(f"{what}: mergeCommit без oid")
    actor = _actor(pull_request)
    if actor is None:
        return Result(request, "actor_unavailable", merge_sha=sha)
    identity, hint = actor
    return Result(request, "merged", merge_sha=sha, identity=identity, type_hint=hint)


def _resolve_sha(owner: str, name: str, request: RequestId) -> Result:
    what = f"merge-sha {request.value}"
    cursor: str | None = None
    for _page in range(_MAX_PAGES):
        payload = _graphql(
            _QUERY_BY_SHA,
            {"owner": owner, "name": name, "sha": str(request.value), "after": cursor},
            what=what,
        )
        commit = _repository(payload, what).get("object")
        if commit is None:
            return Result(request, "not_found")
        if not isinstance(commit, dict):
            raise MechanicalFailure(f"{what}: object не объект")
        associated = commit.get("associatedPullRequests") or {}
        for node in associated.get("nodes") or []:
            if (node.get("mergeCommit") or {}).get("oid") != request.value:
                continue
            actor = _actor(node)
            if actor is None:
                return Result(request, "actor_unavailable", merge_sha=str(request.value))
            identity, hint = actor
            return Result(
                request, "merged", merge_sha=str(request.value), identity=identity, type_hint=hint
            )
        page_info = associated.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return Result(request, "no_matching_pr")
        cursor = page_info.get("endCursor")
        if not isinstance(cursor, str):
            raise MechanicalFailure(f"{what}: hasNextPage без endCursor — обход не завершить")
    raise MechanicalFailure(f"{what}: обход не завершён за {_MAX_PAGES} страниц")


def resolve(owner: str, name: str, request: RequestId) -> Result:
    """Разрешить один элемент scope в терминальное наблюдение.

    Возвращённый `Result` свободен от политики: `actor_class` всегда `None`
    здесь, даже для `state: merged`. Классификация — отдельный шаг,
    `classify_results()`.
    """
    return (
        _resolve_pr(owner, name, request)
        if request.kind == "pr"
        else _resolve_sha(owner, name, request)
    )


def materialize(repo: str, scope: Sequence[RequestId]) -> list[Result]:
    """Разрешить весь scope. Любой механический сбой прерывает батч."""
    owner, sep, name = repo.partition("/")
    if not sep or not owner or not name:
        raise ValueError(f"repo must be 'owner/name', got {repo!r}")
    return [resolve(owner, name, request) for request in scope]


def classify_results(results: Sequence[Result], policy: ApprovalPolicy) -> list[Result]:
    """Проставить `actor_class` на `merged`-записях по существующему классификатору.

    Чистая функция без побочных эффектов и без сети: единственный источник
    семантики классификации — `steward.gatecheck.approval.classify_actor`.
    Эта функция его не переопределяет и не дублирует — она лишь применяет
    его к каждой `merged`-записи и возвращает новый объект. Записи в любом
    другом state возвращаются как есть: только `merged` требует (и допускает)
    `actor_class` по матрице полей контракта.
    """
    classified: list[Result] = []
    for result in results:
        if result.state != "merged":
            classified.append(result)
            continue
        actor_class = classify_actor(result.identity, result.type_hint, policy)
        classified.append(replace(result, actor_class=actor_class))
    return classified
