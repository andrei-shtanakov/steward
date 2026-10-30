"""Загрузчик живых снимков `approval-facts/v3` (спека §3.8, R-M0-7).

Снимок — одиночный обмен `{"request", "response"}` или последовательность
`{"exchanges": [...]}`. Имена снимков берутся из `files` манифеста `MANIFEST.json`
(его байты и sha256 сверяет `tests/approvalfacts/test_capture_live.py`), а не из
листинга каталога. Преобразование записи в `Request` здесь написано независимо от
библиотеки: тест не должен проверять библиотеку её же кодом.
"""

import json
from pathlib import Path
from typing import Any

from steward.approvalfacts.v3.transport import Request

ROOT = Path(__file__).resolve().parents[3]
LIVE = ROOT / "tests" / "fixtures" / "approvalfacts_v3" / "live"

_MANIFEST = json.loads((LIVE / "MANIFEST.json").read_text(encoding="utf-8"))
LIVE_CAPTURES: tuple[str, ...] = tuple(sorted(n.removesuffix(".json") for n in _MANIFEST["files"]))


def live(name: str) -> dict[str, Any]:
    """Снимок `name` из манифеста (без суффикса `.json`)."""
    if name not in LIVE_CAPTURES:
        raise KeyError(f"no live capture {name!r} in MANIFEST.json")
    return json.loads((LIVE / f"{name}.json").read_text(encoding="utf-8"))


def exchanges(name: str) -> list[dict[str, Any]]:
    """Обмены снимка: одиночный обмен — список из одного элемента."""
    data = live(name)
    return data["exchanges"] if "exchanges" in data else [data]


def request_from(ex: dict[str, Any]) -> Request:
    """`Request` записанного обмена."""
    raw = ex["request"]
    return Request(
        method=raw["method"],
        path=raw["path"],
        op=raw.get("op"),
        variables=raw.get("variables"),
        query=raw.get("query"),
    )


def key(r: Request) -> tuple[object, ...]:
    """`(method, path, op, variables, query)`; переменные — канонический JSON."""
    variables = None if r.variables is None else json.dumps(dict(r.variables), sort_keys=True)
    return (r.method, r.path, r.op, variables, r.query)


def key_of(ex: dict[str, Any]) -> tuple[object, ...]:
    """Ключ записанного обмена — для сравнения с журналом `ReplayTransport.log`."""
    return key(request_from(ex))
