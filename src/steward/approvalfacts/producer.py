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


def _gh(args: list[str]) -> tuple[int, str, str]:
    """Единственная точка вызова `gh` — тесты подменяют её целиком.

    Возвращает **оба** потока, а не один на выбор по коду выхода. Прежняя
    версия отдавала stdout только при нулевом коде, а иначе stderr — и тем
    самым выбрасывала тело ответа раньше, чем кто-либо мог в него заглянуть.
    Для `gh api graphql` это фатально: у резолверных полей вроде
    `pullRequest(number:)` отсутствие объекта приходит как валидный JSON с
    `pullRequest: null` **и** непустым top-level `errors`, из-за которого `gh`
    завершается кодом 1. Тело есть, но старая сигнатура его не пропускала.
    """
    try:
        proc = subprocess.run(  # noqa: S603 S607 — фиксированный argv
            ["gh", *args], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _graphql(query: str, variables: dict[str, Any], *, what: str) -> dict[str, Any]:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            continue
        args += ["-F", f"{key}={value}"] if isinstance(value, int) else ["-f", f"{key}={value}"]
    code, out, err = _gh(args)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        # Тела нет или оно не JSON — вот теперь код выхода единственное, что у
        # нас есть, и он решает. Диагностику берём из stderr: там текст `gh`.
        detail = err or str(exc)
        if code != 0:
            raise MechanicalFailure(f"{what}: gh завершился с кодом {code}: {detail}") from exc
        raise MechanicalFailure(f"{what}: ответ не JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise MechanicalFailure(f"{what}: ответ не объект")
    errors = payload.get("errors")
    if errors and not _only_absence(errors, payload):
        raise MechanicalFailure(f"{what}: GraphQL errors: {errors}")
    if code != 0 and not errors:
        # JSON без ошибок, но `gh` недоволен — истолковать нечего.
        raise MechanicalFailure(f"{what}: gh завершился с кодом {code}: {err or out}")
    return payload


def _only_absence(errors: object, payload: dict[str, Any]) -> bool:
    """Все ли GraphQL-ошибки — «такого объекта нет», при живом `data`?

    Разделение, которого не было: GraphQL кладёт в один ответ и частичные
    данные, и ошибки нерезолвнутых полей. `NOT_FOUND` у резолверного поля —
    авторитетный отрицательный ответ (§4.2/§9.1 обещают `not_found` законным
    терминальным состоянием для `request.kind: pr`), а вот auth, rate limit и
    прочее — сбой инструмента. Сваливать их в одно значило докладывать
    недоступность как отсутствие, то есть ровно тот класс, ради которого
    `_repository` уже отказывается читать `repository: null` как «нет репо».

    Требуется И то, И другое: только тип `NOT_FOUND` у **каждой** ошибки, и
    присутствующий `data`. Смесь типов или `data: null` читать нечем.
    """
    if not isinstance(errors, list) or not errors:
        return False
    if payload.get("data") is None:
        return False
    return all(isinstance(error, dict) and error.get("type") == "NOT_FOUND" for error in errors)


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


def _require_key(mapping: dict[str, Any], key: str, what: str, *, where: str) -> Any:
    """Различить «ключа нет» (искажённый/неполный ответ) от «ключ есть, значение
    любое» — что означает конкретное присутствующее значение, решает вызывающий
    код.

    Раньше здесь (и в `_repository()` до правки уровнем выше) стояло
    `.get(key)` / `.get(key) or {}` — из-за чего отсутствующий ключ и явный
    `null` читались одинаково. Для ``pullRequest``/``object`` это превращало
    искажённый ответ (ключ пропущен) в доказанное «не найдено»; для
    ``associatedPullRequests``/``nodes``/``pageInfo`` внутри `object` — в
    ложный `no_matching_pr` (`{"object": {}}` без вложенных полей проходил
    как «страница пройдена целиком, совпадений нет»). Отсутствие ключа —
    механический сбой в обоих случаях; значение ключа (включая ``None``)
    решает вызывающий.
    """
    if key not in mapping:
        raise MechanicalFailure(f"{what}: ответ не содержит {where}")
    return mapping[key]


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
    pull_request = _require_key(
        _repository(payload, what), "pullRequest", what, where="repository.pullRequest"
    )
    if pull_request is None:
        return Result(request, "not_found")
    if not isinstance(pull_request, dict):
        raise MechanicalFailure(f"{what}: pullRequest не объект")
    merge_commit = _require_key(pull_request, "mergeCommit", what, where="pullRequest.mergeCommit")
    if merge_commit is None:
        return Result(request, "not_merged")
    if not isinstance(merge_commit, dict):
        raise MechanicalFailure(f"{what}: mergeCommit не объект")
    sha = merge_commit.get("oid")
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
        commit = _require_key(_repository(payload, what), "object", what, where="repository.object")
        if commit is None:
            return Result(request, "not_found")
        if not isinstance(commit, dict):
            raise MechanicalFailure(f"{what}: object не объект")
        associated = _require_key(
            commit, "associatedPullRequests", what, where="object.associatedPullRequests"
        )
        if not isinstance(associated, dict):
            raise MechanicalFailure(f"{what}: associatedPullRequests не объект")
        nodes = _require_key(associated, "nodes", what, where="associatedPullRequests.nodes")
        if not isinstance(nodes, list):
            raise MechanicalFailure(f"{what}: associatedPullRequests.nodes не список")
        for node in nodes:
            if not isinstance(node, dict):
                raise MechanicalFailure(f"{what}: associatedPullRequests.nodes содержит не-объект")
            node_merge_commit = _require_key(
                node, "mergeCommit", what, where="associatedPullRequests.nodes[].mergeCommit"
            )
            if node_merge_commit is not None and not isinstance(node_merge_commit, dict):
                raise MechanicalFailure(
                    f"{what}: associatedPullRequests.nodes[].mergeCommit не объект"
                )
            if (node_merge_commit or {}).get("oid") != request.value:
                continue
            actor = _actor(node)
            if actor is None:
                return Result(request, "actor_unavailable", merge_sha=str(request.value))
            identity, hint = actor
            return Result(
                request, "merged", merge_sha=str(request.value), identity=identity, type_hint=hint
            )
        page_info = _require_key(
            associated, "pageInfo", what, where="associatedPullRequests.pageInfo"
        )
        if not isinstance(page_info, dict):
            raise MechanicalFailure(f"{what}: associatedPullRequests.pageInfo не объект")
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
    # Ровно один `/`, а не «хотя бы один»: `.partition("/")` брал только
    # первый слэш, так что `owner/repo/extra` тоже проходил бы валидацию
    # (owner="owner", name="repo/extra") — тот же класс дефекта, что и в
    # CLI-шаге 1 (Codex gate round 4 на PR #86, blocker там же).
    repo_parts = repo.split("/")
    if len(repo_parts) != 2 or not repo_parts[0] or not repo_parts[1]:
        raise ValueError(f"repo must be 'owner/name', got {repo!r}")
    owner, name = repo_parts
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
