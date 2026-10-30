"""Производные обмены из живых снимков `approval-facts/v3` (спека §5, R-M0-7).

`derive(base, transforms)` склеивает обмены снимков `base` (в порядке имён, имя можно
повторить) и применяет закрытый список преобразований спеки §5. Преобразование — словарь
`{"op": <имя>, ...}`:

- `{"op": "set_field", "path": P, "value": V}` — заменить **существующее** поле; под
  `data` поле обязано быть в тексте запроса, а объект `V` — без незапрошенных ключей
  (пропуск ключа допустим: так строятся отрицательные векторы неполной записи); `V` на
  месте всего `response.body` — так же для его `data`;
- `{"op": "drop_field", "path": P}` — стереть существующий ключ объекта (под `data` —
  запрошенный);
- `{"op": "append_node", "path": P, "node": N}` — дописать узел в список; ключи узла
  (рекурсивно) обязаны совпасть с полями текста запроса обмена — незапрошенное или
  пропущенное поле — `ValueError`;
- `{"op": "retime", "path": P, "ts": S}` — заменить существующую метку времени (поле
  запроса);
- `{"op": "repeat_exchange", "name": OP}` — дописать в конец копии всех текущих обменов
  операции `OP` (второй проход);
- `{"op": "page_split", "path": P, "n": K}` — разрезать список `nodes` страницы на две:
  первые `K` узлов с `hasNextPage: true` и курсором, остальные — новой страницей сразу за
  ней (индексы следующих обменов сдвигаются на 1).

Путь `P` — `[индекс обмена, ключ или индекс, ...]` от корня обмена (`request`/`response`).
Модуль — без pytest: его же использует генератор векторов (Task 7); тесты получают его через
фикстуры `conftest.py`.
"""

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LIVE = Path(__file__).resolve().parents[2] / "fixtures" / "approvalfacts_v3" / "live"
TRANSFORMS = frozenset(
    {"set_field", "drop_field", "append_node", "retime", "repeat_exchange", "page_split"}
)
#: Тип узла, не снятый живьём (§3.8 E4): его форма — по схеме GitHub и тексту запроса E4.
UNVERIFIED_SHAPE = "ReviewDismissedEvent"

Transform = dict[str, Any]
Path_ = list[int | str]

_ARGS = re.compile(r"\([^()]*\)")
_TOKEN = re.compile(r"\.\.\.|[_A-Za-z][_0-9A-Za-z]*|[{}:]")
_DATA = ("response", "body", "data")


def derive(base: list[str], transforms: list[Transform]) -> dict[str, Any]:
    """Обмены `base` после `transforms`: `{"derived_from", "transforms", "exchanges"}`.

    Узел `ReviewDismissedEvent`, дописанный `append_node`, добавляет
    `"unverified_shape": "ReviewDismissedEvent"`. Неизвестное преобразование, путь мимо
    существующего поля, узел не по тексту запроса — `ValueError`.
    """
    exchanges = [copy.deepcopy(ex) for name in base for ex in _exchanges(name)]
    unverified = False
    for transform in transforms:
        unverified = _apply(exchanges, transform) or unverified
    out: dict[str, Any] = {
        "derived_from": list(base),
        "transforms": copy.deepcopy(transforms),
        "exchanges": exchanges,
    }
    if unverified:
        out["unverified_shape"] = UNVERIFIED_SHAPE
    return out


def dismissal_node(
    review_id: int, created: str, prev: str | None, actor_login: str, actor_type: str
) -> dict[str, Any]:
    """Узел `ReviewDismissedEvent` по тексту E4 (живьём не снят — `unverified_shape`)."""
    return {
        "__typename": UNVERIFIED_SHAPE,
        "createdAt": created,
        "actor": {"login": actor_login, "__typename": actor_type},
        "previousReviewState": prev,
        "review": {"databaseId": review_id},
    }


def _exchanges(name: str) -> list[dict[str, Any]]:
    manifest = json.loads((LIVE / "MANIFEST.json").read_text(encoding="utf-8"))
    if f"{name}.json" not in manifest["files"]:
        raise ValueError(f"no live capture {name!r} in MANIFEST.json")
    data = json.loads((LIVE / f"{name}.json").read_text(encoding="utf-8"))
    return data["exchanges"] if "exchanges" in data else [data]


def _apply(exchanges: list[dict[str, Any]], transform: Transform) -> bool:
    """Применить преобразование; `True` — дописан узел непроверенной формы."""
    op = transform.get("op")
    if op not in TRANSFORMS:
        raise ValueError(f"unknown transform {op!r}")
    if op == "repeat_exchange":
        picked = [ex for ex in exchanges if ex["request"].get("op") == transform["name"]]
        if not picked:
            raise ValueError(f"repeat_exchange: no exchange of {transform['name']!r}")
        exchanges.extend(copy.deepcopy(picked))
        return False
    path = transform["path"]
    if op == "page_split":
        _page_split(exchanges, path, transform["n"])
        return False
    parent, last = _locate(exchanges, path)
    if op == "append_node":
        node = transform["node"]
        if not isinstance(parent[last], list):
            raise ValueError(f"append_node: {path} is not a list")
        sel = _selection_at(exchanges, path)
        if sel is None:
            raise ValueError(f"append_node: {path} is not under response.body.data")
        _check(node, sel, f"{path}", exact=True)
        parent[last].append(copy.deepcopy(node))
        return node.get("__typename") == UNVERIFIED_SHAPE
    sel = _selection_at(exchanges, path)
    if op == "drop_field":
        if not isinstance(parent, dict):
            raise ValueError(f"drop_field: {path} is not an object key")
        del parent[last]
        return False
    value = transform["value" if op == "set_field" else "ts"]
    if sel is not None:
        _check(value, sel, f"{path}", exact=False)
    elif tuple(path[1:]) == _DATA[:2] and isinstance(value, dict) and "data" in value:
        root = _parse(exchanges[path[0]]["request"]["query"])
        _check(value["data"], root, f"{path}.data", exact=False)
    parent[last] = copy.deepcopy(value)
    return False


def _locate(exchanges: list[dict[str, Any]], path: Path_) -> tuple[Any, Any]:
    """Контейнер и ключ последнего шага; каждый шаг, включая последний, обязан существовать."""
    if not path:
        raise ValueError("empty path")
    node: Any = exchanges
    for step in path[:-1]:
        node = _step(node, step, path)
    _step(node, path[-1], path)
    return node, path[-1]


def _step(node: Any, step: int | str, path: Path_) -> Any:
    if isinstance(node, dict) and step in node:
        return node[step]
    if isinstance(node, list) and type(step) is int and 0 <= step < len(node):
        return node[step]
    raise ValueError(f"path {path} misses {step!r}")


def _page_split(exchanges: list[dict[str, Any]], path: Path_, n: int) -> None:
    parent, last = _locate(exchanges, path)
    nodes = parent[last]
    if last != "nodes" or not isinstance(nodes, list) or not 0 < n < len(nodes):
        raise ValueError(f"page_split: {path} cannot be split at {n}")
    index = path[0]
    second = copy.deepcopy(exchanges[index])
    cursor = f"derived-cursor-{index}-{n}"
    parent[last] = nodes[:n]
    parent["pageInfo"] = {"hasNextPage": True, "endCursor": cursor}
    tail, _ = _locate([second], [0, *path[1:]])
    tail[last] = tail[last][n:]
    second["request"]["variables"]["after"] = cursor
    exchanges.insert(index + 1, second)


@dataclass
class _Selection:
    fields: dict[str, "_Selection | None"] = field(default_factory=dict)
    fragments: dict[str, "_Selection"] = field(default_factory=dict)


def _selection_at(exchanges: list[dict[str, Any]], path: Path_) -> "_Selection | None":
    """Выборка запроса для значения по пути `[i, "response", "body", "data", ...]`.

    Путь не под `data` — `None` (проверять нечего); листовое поле — `None`; ключ, которого
    запрос не просит, или шаг вглубь листа — `ValueError`.
    """
    if tuple(path[1:4]) != _DATA:
        return None
    sel: _Selection | None = _parse(exchanges[path[0]]["request"]["query"])
    for step in path[4:]:
        if isinstance(step, int):
            continue
        if sel is None:
            raise ValueError(f"path {path}: {step!r} is below a leaf field")
        sel = _child(sel, step, path)
    return sel


def _child(sel: _Selection, key: str, path: Path_) -> "_Selection | None":
    for scope in [sel, *sel.fragments.values()]:
        if key in scope.fields:
            return scope.fields[key]
    raise ValueError(f"path {path}: query does not request {key!r}")


def _parse(query: str) -> _Selection:
    """Дерево выборки GraphQL-текста: аргументы отброшены, ключ поля — алиас, если есть."""
    text = _ARGS.sub("", query)
    tokens = _TOKEN.findall(text[text.index("{") :])
    return _block(tokens, 0)[0]


def _block(tokens: list[str], i: int) -> tuple[_Selection, int]:
    sel = _Selection()
    i += 1  # "{"
    while tokens[i] != "}":
        if tokens[i] == "...":  # ... on Type { ... }
            sel.fragments[tokens[i + 2]], i = _block(tokens, i + 3)
            continue
        key = tokens[i]
        i += 1
        if tokens[i] == ":":  # алиас:поле
            i += 2
        sub = None
        if tokens[i] == "{":
            sub, i = _block(tokens, i)
        sel.fields[key] = sub
    return sel, i + 1


def _check(value: Any, sel: _Selection, where: str, *, exact: bool) -> None:
    """Ключи объектов значения — поля выборки (с фрагментом по строковому `__typename`).

    `exact` — ровно поля запроса (узел `append_node`); иначе — без незапрошенных ключей
    (`set_field`: пропуск и не-объект на месте объекта допустимы — отрицательные векторы).
    """
    if isinstance(value, list):
        for item in value:
            _check(item, sel, where, exact=exact)
        return
    if not isinstance(value, dict):
        if exact and value is not None:
            raise ValueError(f"{where}: object expected for a selection")
        return
    allowed = dict(sel.fields)
    typename = value.get("__typename")
    fragment = sel.fragments.get(typename) if isinstance(typename, str) else None
    if fragment is not None:
        allowed.update(fragment.fields)
    keys = set(value)
    if keys - set(allowed) or (exact and keys != set(allowed)):
        raise ValueError(f"{where}: fields {sorted(keys)} != query fields {sorted(allowed)}")
    for key, sub in allowed.items():
        if sub is not None and key in value:
            _check(value[key], sub, f"{where}.{key}", exact=exact)
