"""Фикстуры над живыми снимками `approval-facts/v3` (спека §3.8, R-M0-7; Ruling M0-C).

Снимок — одиночный обмен `{"request", "response"}` или последовательность
`{"exchanges": [...]}`. Имена снимков берутся из `files` манифеста `MANIFEST.json` (его
байты и sha256 сверяет `tests/approvalfacts/test_capture_live.py`), а не из листинга
каталога. Тест, принимающий аргумент `capture_name`, параметризуется по всем снимкам
манифеста (`pytest_generate_tests`). Хелперы — фикстуры, возвращающие функции: правило
репо — без импортов между тест-модулями (`tests/conftest.py`). Перевод записи в `Request`
написан независимо от библиотеки: тест не должен проверять библиотеку её же кодом.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from steward.approvalfacts.v3.transport import Request

ROOT = Path(__file__).resolve().parents[3]
LIVE = ROOT / "tests" / "fixtures" / "approvalfacts_v3" / "live"
_MANIFEST = json.loads((LIVE / "MANIFEST.json").read_text(encoding="utf-8"))
LIVE_CAPTURES: tuple[str, ...] = tuple(sorted(n.removesuffix(".json") for n in _MANIFEST["files"]))

Record = dict[str, Any]
Key = tuple[object, ...]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """`capture_name` — каждый снимок манифеста."""
    if "capture_name" in metafunc.fixturenames:
        metafunc.parametrize("capture_name", LIVE_CAPTURES)


def _live(name: str) -> Record:
    if name not in LIVE_CAPTURES:
        raise KeyError(f"no live capture {name!r} in MANIFEST.json")
    return json.loads((LIVE / f"{name}.json").read_text(encoding="utf-8"))


def _exchanges(name: str) -> list[Record]:
    data = _live(name)
    return data["exchanges"] if "exchanges" in data else [data]


def _request_from(ex: Record) -> Request:
    raw = ex["request"]
    return Request(
        method=raw["method"],
        path=raw["path"],
        op=raw.get("op"),
        variables=raw.get("variables"),
        query=raw.get("query"),
    )


def _key(r: Request) -> Key:
    variables = None if r.variables is None else json.dumps(dict(r.variables), sort_keys=True)
    return (r.method, r.path, r.op, variables, r.query)


@pytest.fixture
def repo_root() -> Path:
    """Корень репо (спека, скрипт захвата)."""
    return ROOT


@pytest.fixture
def live_captures() -> tuple[str, ...]:
    """Имена всех снимков манифеста."""
    return LIVE_CAPTURES


@pytest.fixture
def live() -> Callable[[str], Record]:
    """`live(name)` — снимок манифеста `name` (без `.json`)."""
    return _live


@pytest.fixture
def exchanges() -> Callable[[str], list[Record]]:
    """`exchanges(name)` — обмены снимка; одиночный обмен — список из одного."""
    return _exchanges


@pytest.fixture
def request_from() -> Callable[[Record], Request]:
    """`request_from(ex)` — `Request` записанного обмена."""
    return _request_from


@pytest.fixture
def key() -> Callable[[Request], Key]:
    """`key(r)` — `(method, path, op, variables, query)`; переменные — канонический JSON."""
    return _key


@pytest.fixture
def key_of() -> Callable[[Record], Key]:
    """`key_of(ex)` — ключ записанного обмена, для сравнения с `ReplayTransport.log`."""
    return lambda ex: _key(_request_from(ex))
