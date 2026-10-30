"""Транспорт `approval-facts/v3` (спека §3.2): единственная граница с сетью.

`GhTransport` — продакшн через `gh api --include`; `ReplayTransport` — записанные обмены
живых снимков для векторов. Классификация ответов (§3.2 «Отказы транспорта») — в
`_github.py`; транспорт только доставляет статус, тело и `Link`.
"""

import json
import os
import subprocess
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

_UNEXPECTED = "replay: unexpected request"


@dataclass(frozen=True, eq=False)
class Request:
    """Запрос к GitHub: REST (`GET`, путь) или GraphQL (`POST`, `graphql`, op, текст).

    Равенство и хеш — по `(method, path, op, variables, query)`; переменные сравниваются
    как канонический JSON (порядок ключей не важен), текст запроса входит в ключ (R-M0-7).
    """

    method: str
    path: str
    op: str | None = None
    variables: Mapping[str, object] | None = None
    query: str | None = None

    def _key(self) -> tuple[str, str, str | None, str | None, str | None]:
        variables = None
        if self.variables is not None:
            variables = json.dumps(dict(self.variables), sort_keys=True)
        return (self.method, self.path, self.op, variables, self.query)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Request) and self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())


@dataclass(frozen=True)
class Response:
    """Ответ: `status is None` — сбой транспорта; `body` — разобранный JSON; `link` — сырой
    заголовок `Link` REST."""

    status: int | None
    body: object = None
    link: str | None = None
    error: str | None = None


class Transport(Protocol):
    """Единственный способ библиотеки обратиться к GitHub."""

    def send(self, request: Request) -> Response:
        """Отправить запрос; сбой доставки — `Response(status=None, error=...)`."""
        ...


class GhTransport:
    """Продакшн-транспорт: `gh api --include`, только `GET` и GraphQL-query.

    `gh_config_dir` выставляет `GH_CONFIG_DIR` (профиль выбирает потребитель); `None` —
    окружение вызывающего без изменений.
    """

    def __init__(self, gh_config_dir: Path | None = None, *, timeout: float = 60) -> None:
        self.gh_config_dir = gh_config_dir
        self.timeout = timeout

    def send(self, request: Request) -> Response:
        """Выполнить запрос через `gh`; не-read-only запрос — `ValueError` до процесса."""
        argv, stdin = _argv(request)
        env = dict(os.environ)
        if self.gh_config_dir is not None:
            env["GH_CONFIG_DIR"] = str(self.gh_config_dir)
        try:
            proc = subprocess.run(  # noqa: S603 — фиксированный argv, read-only gh api
                argv,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Response(None, error=f"gh did not run: {exc}")
        return _parse_include(proc.stdout, proc.stderr)


def _argv(request: Request) -> tuple[list[str], str | None]:
    """`gh api` argv и stdin; транспорт read-only по построению."""
    if request.method == "GET":
        return ["gh", "api", "--include", request.path], None
    if request.method != "POST" or request.path != "graphql":
        raise ValueError(f"read-only transport: {request.method} {request.path}")
    if request.query is None or "mutation" in request.query:
        raise ValueError(f"read-only transport: GraphQL text of {request.op!r} is not a query")
    payload = {
        "query": request.query,
        "variables": dict(request.variables or {}),
        "operationName": request.op,
    }
    return ["gh", "api", "--include", "graphql", "--input", "-"], json.dumps(payload)


def _parse_include(stdout: str, stderr: str) -> Response:
    """Статус, `Link` и тело из вывода `gh api --include`.

    Код выхода `gh` не решает: на не-2xx и на GraphQL `errors` он ненулевой, а тело есть.
    Нет строки статуса — сбой транспорта; тело не JSON — `body=None` с ошибкой.
    """
    head, sep, body = stdout.replace("\r\n", "\n").partition("\n\n")
    lines = head.split("\n")
    parts = lines[0].split()
    if not sep or len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        return Response(None, error=f"gh output without HTTP status line: {stderr.strip()[:200]}")
    link = next(
        (ln.split(":", 1)[1].strip() for ln in lines[1:] if ln.lower().startswith("link:")), None
    )
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        return Response(int(parts[1]), link=link, error=f"body is not JSON: {exc}")
    return Response(int(parts[1]), body=parsed, link=link)


@dataclass
class ReplayTransport:
    """Записанные обмены: очередь FIFO на ключ запроса, журнал всех `send`.

    Незаписанный запрос — `Response(None, error="replay: unexpected request")` и запись в
    `unexpected`; `unused()` — ещё не потреблённые записи. Вместе они позволяют тесту
    требовать точного потребления.
    """

    queues: dict[Request, deque[Response]] = field(default_factory=dict)
    log: list[Request] = field(default_factory=list)
    unexpected: list[Request] = field(default_factory=list)

    def send(self, request: Request) -> Response:
        """Отдать следующий записанный ответ на этот ключ."""
        self.log.append(request)
        queue = self.queues.get(request)
        if not queue:
            self.unexpected.append(request)
            return Response(None, error=_UNEXPECTED)
        return queue.popleft()

    def unused(self) -> list[Request]:
        """Записанные, но не запрошенные обмены."""
        return [req for req, queue in self.queues.items() for _ in queue]


def replay_transport(spec: Mapping[str, Any]) -> ReplayTransport:
    """`ReplayTransport` из `{"exchanges": [...]}` — формат обмена = формат файла снимка.

    Обмен — `{"request": {method, path, op?, variables?, query?}, "response": {status,
    link?, body?, error?}}`; иная форма — `ValueError`.
    """
    exchanges = spec.get("exchanges")
    if not isinstance(exchanges, Sequence) or isinstance(exchanges, str):
        raise ValueError("replay spec: 'exchanges' must be a list")
    transport = ReplayTransport()
    for ex in exchanges:
        request, response = _exchange(ex)
        transport.queues.setdefault(request, deque()).append(response)
    return transport


def _exchange(ex: Any) -> tuple[Request, Response]:
    try:
        req, resp = ex["request"], ex["response"]
        request = Request(
            method=req["method"],
            path=req["path"],
            op=req.get("op"),
            variables=req.get("variables"),
            query=req.get("query"),
        )
        response = Response(
            status=resp["status"],
            body=resp.get("body"),
            link=resp.get("link"),
            error=resp.get("error"),
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"replay spec: malformed exchange: {exc!r}") from exc
    return request, response
