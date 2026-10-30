"""Чтение GitHub по строкам G0, R0, P1 таблицы §3.8 спеки (внутренний слой).

Отказ — исключение `_Fail(code, detail)`; публичные функции превращают его в
`Unavailable` на своей границе. Форму тела задаёт вызывающий по строке своего endpoint:
`rest_object` (E1, E5, H1–H3, H5, H6) или `rest_array_pages` (H4); общего правила «тело
обязано быть объектом» нет.
"""

import re
from collections.abc import Mapping, Sequence

from steward.approvalfacts.v3.model import Unavailable, unavailable
from steward.approvalfacts.v3.transport import Request, Response, Transport

#: Санитарный предел обхода страниц (как `_MAX_PAGES` v2); превышение — `pagination`.
MAX_PAGES = 50

_LINK_PART = re.compile(r'<([^<>\s]+)>; rel="([^"\s]+)"')


class _Fail(Exception):
    """Факт недоступен: `code` из `UNAVAILABLE_CODES`, `detail` — диагностика."""

    def __init__(self, code: str, detail: str = "") -> None:
        self._unavailable = unavailable(code, detail)  # код вне словаря — ValueError
        super().__init__(self._unavailable.reason)
        self.code = code
        self.detail = detail

    def unavailable(self) -> Unavailable:
        """`Unavailable` с той же причиной — для границы публичной функции."""
        return self._unavailable


def _send(t: Transport, request: Request) -> tuple[int, Response]:
    """Отправить запрос; сбой транспорта (`status is None`) — `transport`."""
    response = t.send(request)
    if response.status is None:
        raise _Fail("transport", response.error or "")
    return response.status, response


def _ok(status: int) -> bool:
    return 200 <= status < 300


def graphql(t: Transport, op: str, query: str, variables: Mapping[str, object]) -> dict:
    """Строка G0: один GraphQL-запрос; возвращает объект `data`.

    Непустой `errors` при любом HTTP-статусе (в т.ч. `200` с частичными `data`) —
    `graphql_errors`; иначе не-2xx — `http`; конверт или `data` не объект — `malformed`.
    """
    status, response = _send(t, Request("POST", "graphql", op=op, variables=variables, query=query))
    body = response.body
    if isinstance(body, dict) and body.get("errors"):
        raise _Fail("graphql_errors", f"{op}: {_messages(body['errors'])}")
    if not _ok(status):
        raise _Fail("http", f"{status} graphql")
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise _Fail("malformed", f"{op}: GraphQL envelope without a data object")
    return data


def _messages(errors: object) -> str:
    if not isinstance(errors, list):
        return repr(errors)[:200]
    return "; ".join(str(e.get("message")) if isinstance(e, dict) else repr(e) for e in errors)[
        :200
    ]


def graphql_pages(
    t: Transport,
    op: str,
    query: str,
    variables: Mapping[str, object],
    *,
    connection: Sequence[str],
) -> list[dict]:
    """Все страницы GraphQL-соединения `connection` (путь ключей от `data`); объекты `data`.

    Первая страница — `after: null`, следующие — `endCursor` предыдущей. `hasNextPage` без
    строкового `endCursor` и больше `MAX_PAGES` страниц — `pagination`; соединение или
    `pageInfo` не той формы — `malformed`. `null` на пути к соединению — допустимая форма
    ряда строк §3.8 (E7 `object: null`): страница возвращается как есть, разбор — у
    вызывающего по его строке.
    """
    pages: list[dict] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        data = graphql(t, op, query, {**variables, "after": cursor})
        pages.append(data)
        cursor = _next_cursor(data, connection, op)
        if cursor is None:
            return pages
    raise _Fail("pagination", f"{op}: more than {MAX_PAGES} pages")


def _next_cursor(data: dict, connection: Sequence[str], op: str) -> str | None:
    node: object = data
    for step in connection:
        if node is None:
            return None
        if not isinstance(node, dict):
            raise _Fail("malformed", f"{op}: {step!r} is not under an object")
        node = node.get(step)
    if node is None:
        return None
    page = node.get("pageInfo") if isinstance(node, dict) else None
    if not isinstance(page, dict) or not isinstance(page.get("hasNextPage"), bool):
        raise _Fail("malformed", f"{op}: connection without pageInfo.hasNextPage")
    if not page["hasNextPage"]:
        return None
    cursor = page.get("endCursor")
    if not isinstance(cursor, str) or not cursor:
        raise _Fail("pagination", f"{op}: hasNextPage without endCursor")
    return cursor


def _rest(t: Transport, path: str) -> Response:
    """Строка R0: `GET path`; не-2xx — `http: <status> <path>`."""
    status, response = _send(t, Request("GET", path))
    if not _ok(status):
        raise _Fail("http", f"{status} {path}")
    return response


def _shape_detail(path: str, response: Response, want: str) -> str:
    if response.error:
        return f"{path}: {response.error}"
    return f"{path}: body is not {want}"


def rest_object(t: Transport, path: str) -> dict:
    """REST-endpoint, чья строка §3.8 требует объект (E1, E5, H1–H3, H5, H6)."""
    response = _rest(t, path)
    if not isinstance(response.body, dict):
        raise _Fail("malformed", _shape_detail(path, response, "an object"))
    return response.body


def rest_array_pages(t: Transport, path: str) -> list[dict]:
    """REST-список (строки H4, P1): все страницы по `Link: rel="next"`, элементы — объекты.

    Конец — отсутствие `Link` или `rel="next"` в разобранном заголовке; лишний запрос не
    делается. Заголовок вне грамматики P1 — `malformed`; больше `MAX_PAGES` — `pagination`.
    """
    items: list[dict] = []
    page_path = path
    for _ in range(MAX_PAGES):
        response = _rest(t, page_path)
        body = response.body
        if not isinstance(body, list) or not all(isinstance(e, dict) for e in body):
            raise _Fail("malformed", _shape_detail(page_path, response, "an array of objects"))
        items.extend(body)
        next_path = None if response.link is None else parse_link(response.link).get("next")
        if next_path is None:
            return items
        page_path = next_path
    raise _Fail("pagination", f"{path}: more than {MAX_PAGES} pages")


def parse_link(header: str) -> dict[str, str]:
    """Строка P1: `<url>; rel="x"` через `", "` → `{rel: url}`.

    Иная грамматика, пустой заголовок или повтор `rel` — `malformed` (не «конец списка»).
    """
    rels: dict[str, str] = {}
    for part in header.split(", "):
        match = _LINK_PART.fullmatch(part)
        if match is None or match.group(2) in rels:
            raise _Fail("malformed", f"Link header outside P1 grammar: {header[:200]!r}")
        rels[match.group(2)] = match.group(1)
    return rels
