# `approval-facts/v2` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Опубликовать `approval-facts` как переносимый внешний контракт v2 (scope, completeness, lease, provenance политики), переписать продюсера под него и мигрировать release-гейт — одной атомарной миграцией.

**Architecture:** Модуль `steward/approvalfacts.py` превращается в пакет из четырёх файлов с одной ответственностью каждый: модель и канонизация, читатель с инвариантами, продюсер поверх `gh`, публикация с транзакцией. Гейт остаётся комбинатором и получает разобранный результат чтения, а не путь к файлу. Классификацию по-прежнему делает только steward.

**Tech Stack:** Python ≥3.12, uv, typer, pytest, jsonschema (для тестов контракта), `gh` CLI за единственной точкой вызова.

**Spec:** `docs/superpowers/specs/2026-08-21-approval-facts-v2-design.md`

## Global Constraints

- Пакетный менеджер — **только uv**: `uv sync`, `uv add`, `uv run pytest`. Никогда pip.
- `uv run ruff format .` и `uv run ruff check . --fix` перед коммитом; длина строки **100**.
- `uv run pyrefly check` — репозиторий типизирован, новый код обязан проходить.
- Тесты **никогда не ходят в сеть**: `gh` вызывается через единственную точку `_gh`, которую тесты подменяют.
- Константы контракта, одинаковые для всех потребителей: `schema_version = "2"`, допуск часов **300 секунд**, верхняя граница lease **2 592 000 секунд (30 суток)**.
- Механический сбой **никогда** не публикует файл; отсутствие файла — валидное состояние `unavailable`, а не ошибка.
- `additionalProperties: false` во всех ветках схемы: «не требуется» означает **запрещено**.
- Никакой параллельной поддержки v1 и v2 в рантайме.

---

## File Structure

**Контракт (публикуется наружу, вендорится соседями):**
- `contracts/approval-facts/v2/SCHEMA.json` — нормативная схема записей JSONL.
- `contracts/approval-facts/v2/README.md` — назначение, инварианты читателя, константы.
- `contracts/approval-facts/v2/fixtures/*.jsonl` — валидные и невалидные примеры.

**Код (пакет вместо одного модуля):**
- `src/steward/approvalfacts/__init__.py` — публичный API пакета.
- `src/steward/approvalfacts/model.py` — `RequestId`, `Result`, `Header`, `ApprovalFactsV2`, канонические байты, `scope_digest`.
- `src/steward/approvalfacts/reader.py` — разбор и инварианты 1–11, типы исхода чтения.
- `src/steward/approvalfacts/producer.py` — `gh`-слой, исчерпывающая пагинация, разрешение в терминальные состояния.
- `src/steward/approvalfacts/publish.py` — разбор `origin`, preflight, delete-before-attempt, долговечная публикация.

**Изменяемое:**
- `src/steward/gatecheck/approval.py` — `approval_facts_lease_seconds` в политике; `check_approval_evidence` на новую модель.
- `src/steward/riskclassify/cli.py:131-177` — команда `approval-facts`: `--repo-root`, `--out` как override.
- `src/steward/gatecheck/cli.py:210-216` — `--approval-facts` как override, дефолт из бандла.

**Тесты:** `tests/approvalfacts/test_model.py`, `test_reader.py`, `test_producer.py`, `test_publish.py`, `tests/gatecheck/test_approval_check.py` (переписывается), `tests/contract/test_approval_facts_schema.py`.

---

### Task 1: Контракт — схема, фикстуры, README

**Files:**
- Create: `contracts/approval-facts/v2/SCHEMA.json`
- Create: `contracts/approval-facts/v2/README.md`
- Create: `contracts/approval-facts/v2/fixtures/clean.jsonl`
- Create: `contracts/approval-facts/v2/fixtures/negative_states.jsonl`
- Create: `contracts/approval-facts/v2/fixtures/bad_state_for_kind.jsonl`
- Create: `contracts/approval-facts/v2/fixtures/extra_field_on_negative.jsonl`
- Test: `tests/contract/test_approval_facts_schema.py`

**Interfaces:**
- Consumes: ничего.
- Produces: файл схемы, на который ссылаются задачи 2–3; фикстуры, которые переиспользуют тесты читателя.

- [ ] **Step 1: Написать падающий тест схемы**

```python
"""Фикстуры контракта проверяются против его же схемы.

Тест намеренно проверяет ОБА направления: валидные фикстуры проходят, а
невалидные — падают. Схема, которая ничего не отвергает, выглядит рабочей и
не является контрактом.
"""

import json
from pathlib import Path

import jsonschema
import pytest

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "approval-facts" / "v2"
SCHEMA = json.loads((CONTRACT / "SCHEMA.json").read_text(encoding="utf-8"))


def _records(name: str) -> list[dict]:
    text = (CONTRACT / "fixtures" / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.mark.parametrize("fixture", ["clean.jsonl", "negative_states.jsonl"])
def test_valid_fixtures_conform(fixture: str) -> None:
    for record in _records(fixture):
        jsonschema.validate(record, SCHEMA)


@pytest.mark.parametrize(
    "fixture",
    ["bad_state_for_kind.jsonl", "extra_field_on_negative.jsonl"],
)
def test_invalid_fixtures_are_rejected(fixture: str) -> None:
    with pytest.raises(jsonschema.ValidationError):
        for record in _records(fixture):
            jsonschema.validate(record, SCHEMA)
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `uv run pytest tests/contract/test_approval_facts_schema.py -v`
Expected: FAIL — `FileNotFoundError` на `SCHEMA.json`.

- [ ] **Step 3: Добавить jsonschema в dev-зависимости**

```bash
uv add --dev jsonschema
```

- [ ] **Step 4: Написать SCHEMA.json**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "approval-facts/v2 record",
  "oneOf": [{"$ref": "#/$defs/header"}, {"$ref": "#/$defs/result"}],
  "$defs": {
    "request": {
      "type": "object",
      "additionalProperties": false,
      "required": ["kind", "value"],
      "oneOf": [
        {"properties": {"kind": {"const": "pr"}, "value": {"type": "integer", "minimum": 1}}},
        {"properties": {"kind": {"const": "merge_sha"},
                        "value": {"type": "string", "pattern": "^[0-9a-f]{40}$"}}}
      ]
    },
    "header": {
      "type": "object",
      "additionalProperties": false,
      "required": ["kind", "schema_version", "repository", "generated_at", "valid_until",
                   "policy_version", "policy_digest", "complete", "scope_sha256", "scope"],
      "properties": {
        "kind": {"const": "header"},
        "schema_version": {"const": "2"},
        "repository": {"type": "string", "pattern": "^[^/]+/[^/]+$"},
        "generated_at": {"type": "string", "format": "date-time"},
        "valid_until": {"type": "string", "format": "date-time"},
        "policy_version": {"type": "integer", "minimum": 1},
        "policy_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "complete": {"const": true},
        "scope_sha256": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "scope": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/request"}}
      }
    },
    "result": {
      "type": "object",
      "additionalProperties": false,
      "required": ["kind", "request", "state"],
      "properties": {
        "kind": {"const": "result"},
        "request": {"$ref": "#/$defs/request"},
        "state": {"enum": ["merged", "not_merged", "not_found", "no_matching_pr",
                           "actor_unavailable"]},
        "merge_sha": {"type": ["string", "null"], "pattern": "^[0-9a-f]{40}$"},
        "identity": {"type": "string"},
        "type_hint": {"type": "string"},
        "actor_class": {"enum": ["human", "agent", "unknown"]}
      },
      "allOf": [
        {
          "if": {"properties": {"state": {"const": "merged"}}},
          "then": {"required": ["merge_sha", "identity", "type_hint", "actor_class"],
                   "properties": {"merge_sha": {"type": "string"}}}
        },
        {
          "if": {"properties": {"state": {"const": "actor_unavailable"}}},
          "then": {"required": ["merge_sha"],
                   "properties": {"merge_sha": {"type": "string"}},
                   "not": {"anyOf": [{"required": ["identity"]},
                                     {"required": ["type_hint"]},
                                     {"required": ["actor_class"]}]}}
        },
        {
          "if": {"properties": {"state": {"enum": ["not_merged", "not_found", "no_matching_pr"]}}},
          "then": {"properties": {"merge_sha": {"type": "null"}},
                   "not": {"anyOf": [{"required": ["identity"]},
                                     {"required": ["type_hint"]},
                                     {"required": ["actor_class"]}]}}
        },
        {
          "if": {"properties": {"state": {"const": "not_merged"}}},
          "then": {"properties": {"request": {"properties": {"kind": {"const": "pr"}}}}}
        },
        {
          "if": {"properties": {"state": {"const": "no_matching_pr"}}},
          "then": {"properties": {"request": {"properties": {"kind": {"const": "merge_sha"}}}}}
        }
      ]
    }
  }
}
```

- [ ] **Step 5: Написать фикстуры**

`clean.jsonl`:

```
{"kind":"header","schema_version":"2","repository":"andrei-shtanakov/steward","generated_at":"2026-08-21T09:00:00Z","valid_until":"2026-08-22T09:00:00Z","policy_version":1,"policy_digest":"sha256:0000000000000000000000000000000000000000000000000000000000000000","complete":true,"scope_sha256":"sha256:1111111111111111111111111111111111111111111111111111111111111111","scope":[{"kind":"merge_sha","value":"221457933968be9e95acd51d548e080f739c794c"}]}
{"kind":"result","request":{"kind":"merge_sha","value":"221457933968be9e95acd51d548e080f739c794c"},"state":"merged","merge_sha":"221457933968be9e95acd51d548e080f739c794c","identity":"github:merge-broker","type_hint":"Bot","actor_class":"agent"}
```

`negative_states.jsonl` — header со scope из четырёх запросов и четыре результата: `not_merged` для `pr`, `not_found` для `pr`, `no_matching_pr` для `merge_sha`, `actor_unavailable` для `merge_sha` (у последнего `merge_sha` заполнен, у первых трёх `null`).

`bad_state_for_kind.jsonl` — одна запись: `state: "not_merged"` при `request.kind: "merge_sha"`.

`extra_field_on_negative.jsonl` — одна запись: `state: "not_found"` с полем `identity`.

- [ ] **Step 6: Написать README контракта**

Обязательные разделы: файл и его место (`.steward/approval_facts.jsonl`); пять состояний и матрица допустимости; **инварианты читателя 1–11** с явным указанием, что 11 требует внешнего ожидания; константы (300 с, 30 суток); правило «две раздельные гарантии» для вендоринга (copy-integrity + upstream-drift) по образцу `contracts/gate-verdicts/v1/README.md`.

- [ ] **Step 7: Прогнать тесты**

Run: `uv run pytest tests/contract/test_approval_facts_schema.py -v`
Expected: PASS, 4 теста.

- [ ] **Step 8: Коммит**

```bash
git add contracts/approval-facts/v2 tests/contract/test_approval_facts_schema.py pyproject.toml uv.lock
git commit -m "feat(contract): approval-facts/v2 — схема, фикстуры, README"
```

---

### Task 2: Модель и канонические байты

**Files:**
- Create: `src/steward/approvalfacts/model.py`
- Create: `src/steward/approvalfacts/__init__.py`
- Delete: `src/steward/approvalfacts.py` (переезжает в пакет; v1-совместимость не сохраняется)
- Test: `tests/approvalfacts/test_model.py`

**Interfaces:**
- Consumes: `ActorType` из `steward.gatecheck.approval`.
- Produces: `RequestId(kind, value)`, `Result(request, state, merge_sha, identity, type_hint, actor_class)` c свойством `comparable`, `Header(...)`, `ApprovalFactsV2(header, results)` с методами `by_merge_sha()` и `scope_has_sha(sha)`, функции `canonical_scope_bytes(scope) -> bytes` и `scope_digest(scope) -> str`.

- [ ] **Step 1: Написать падающий тест**

```python
from steward.approvalfacts.model import RequestId, Result, canonical_scope_bytes, scope_digest

SHA = "221457933968be9e95acd51d548e080f739c794c"


def test_canonical_bytes_are_order_independent() -> None:
    """Канонизация обязана давать одинаковые байты при любом порядке входа —
    иначе scope_sha256 ловил бы порядок, а не содержание."""
    a = [RequestId("pr", 42), RequestId("merge_sha", SHA)]
    b = [RequestId("merge_sha", SHA), RequestId("pr", 42)]
    assert canonical_scope_bytes(a) == canonical_scope_bytes(b)


def test_canonical_bytes_have_no_whitespace() -> None:
    assert b", " not in canonical_scope_bytes([RequestId("pr", 42)])
    assert b'": ' not in canonical_scope_bytes([RequestId("pr", 42)])


def test_scope_digest_is_prefixed_sha256() -> None:
    digest = scope_digest([RequestId("pr", 42)])
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_comparable_excludes_request() -> None:
    """Алиасы одного мержа различаются request по определению; сравнивать
    их можно только по проекции без него (§4.3 инвариант 9)."""
    by_pr = Result(RequestId("pr", 42), "merged", SHA, "github:x", "Bot", "agent")
    by_sha = Result(RequestId("merge_sha", SHA), "merged", SHA, "github:x", "Bot", "agent")
    assert by_pr.comparable == by_sha.comparable
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `uv run pytest tests/approvalfacts/test_model.py -v`
Expected: FAIL — `ModuleNotFoundError: steward.approvalfacts.model`.

- [ ] **Step 3: Реализовать модель**

```python
"""Модель `approval-facts/v2`: идентичность запроса, наблюдение, конверт.

Здесь нет ни ввода-вывода, ни `gh`, ни политики — только формы и
канонизация. Это позволяет тестировать канонические байты и проекцию
сравнения без единого внешнего вызова.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from steward.gatecheck.approval import ActorType

RequestKind = Literal["pr", "merge_sha"]
ObservationState = Literal[
    "merged", "not_merged", "not_found", "no_matching_pr", "actor_unavailable"
]

#: Состояния, осмысленные только для одного типа запроса (§4.2).
STATE_ONLY_FOR: dict[str, RequestKind] = {
    "not_merged": "pr",
    "no_matching_pr": "merge_sha",
}


@dataclass(frozen=True)
class RequestId:
    """Идентичность запрошенного элемента: `(kind, value)` и ничего больше."""

    kind: RequestKind
    value: int | str

    def as_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "value": self.value}


def canonical_scope_bytes(scope: Sequence[RequestId]) -> bytes:
    """Нормативные байты scope для `scope_sha256` (§4.4).

    Сортировка по `(kind, value)` безопасна, несмотря на разнотипный
    `value`: значения сравниваются только внутри одного `kind`, а внутри
    `kind` они однородны по схеме (pr → int, merge_sha → str).
    """
    items = [r.as_dict() for r in sorted(scope, key=lambda r: (r.kind, r.value))]
    text = json.dumps(items, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    return text.encode("utf-8")


def scope_digest(scope: Sequence[RequestId]) -> str:
    return "sha256:" + hashlib.sha256(canonical_scope_bytes(scope)).hexdigest()


@dataclass(frozen=True)
class Result:
    """Терминальное наблюдение по одному элементу scope."""

    request: RequestId
    state: ObservationState
    merge_sha: str | None = None
    identity: str | None = None
    type_hint: str | None = None
    actor_class: ActorType | None = None

    @property
    def comparable(self) -> tuple[object, ...]:
        """Проекция для сравнения алиасов: запись без `request` (§4.3(9))."""
        return (self.state, self.merge_sha, self.identity, self.type_hint, self.actor_class)


@dataclass(frozen=True)
class Header:
    repository: str
    generated_at: datetime
    valid_until: datetime
    policy_version: int
    policy_digest: str
    scope: tuple[RequestId, ...]
    scope_sha256: str


@dataclass(frozen=True)
class ApprovalFactsV2:
    header: Header
    results: tuple[Result, ...]

    def by_merge_sha(self) -> dict[str, Result]:
        """Индекс разрешённых SHA (§8.2). Записи без SHA сюда не попадают."""
        return {r.merge_sha: r for r in self.results if r.merge_sha is not None}

    def scope_has_sha(self, sha: str) -> bool:
        """Входит ли `{kind: merge_sha, value: sha}` в объявленный scope."""
        return any(r.kind == "merge_sha" and r.value == sha for r in self.header.scope)
```

- [ ] **Step 4: Создать `__init__.py` пакета**

```python
"""`approval-facts/v2`: контракт merge-акторов, продюсер и читатель."""

from steward.approvalfacts.model import (
    ApprovalFactsV2,
    Header,
    ObservationState,
    RequestId,
    RequestKind,
    Result,
    canonical_scope_bytes,
    scope_digest,
)

__all__ = [
    "ApprovalFactsV2",
    "Header",
    "ObservationState",
    "RequestId",
    "RequestKind",
    "Result",
    "canonical_scope_bytes",
    "scope_digest",
]
```

- [ ] **Step 5: Удалить старый модуль**

```bash
git rm src/steward/approvalfacts.py
```

Импорты из него временно сломаются в `riskclassify/cli.py` и `gatecheck/*` — они чинятся задачами 5–9. Чтобы набор оставался зелёным между задачами, в этом же шаге удаляется устаревший `tests/approvalfacts/test_materializer.py` (его сценарии переезжают в `test_producer.py`, задача 4) и из `gatecheck/cli.py` временно убирается передача `--approval-facts` в чек — задача 9 возвращает её на новой модели.

- [ ] **Step 6: Прогнать тесты**

Run: `uv run pytest tests/approvalfacts/test_model.py -v && uv run pytest -q`
Expected: тесты модели PASS; общий прогон зелёный.

- [ ] **Step 7: Коммит**

```bash
git add -A
git commit -m "feat(approvalfacts): модель v2 и канонические байты scope"
```

---

### Task 3: Читатель и инварианты 1–11

**Files:**
- Create: `src/steward/approvalfacts/reader.py`
- Modify: `src/steward/approvalfacts/__init__.py`
- Test: `tests/approvalfacts/test_reader.py`

**Interfaces:**
- Consumes: `RequestId`, `Result`, `Header`, `ApprovalFactsV2`, `scope_digest` (задача 2).
- Produces: `class UnreadableFacts(ValueError)`; `MAX_CLOCK_SKEW_SECONDS = 300`; `MAX_LEASE_SECONDS = 2_592_000`; `load_facts(path: Path, *, expected_repository: str, now: datetime) -> ApprovalFactsV2` — поднимает `UnreadableFacts` при нарушении любого инварианта; `detect_legacy_v1(path: Path) -> bool`.

- [ ] **Step 1: Написать падающие тесты (по инварианту на тест)**

```python
"""Читатель обязан ДОКАЗАТЬ полноту, а не поверить `complete: true`.

Каждый тест ломает ровно один инвариант: если бы проверки не было, файл
прошёл бы и повлиял на enforcement.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from steward.approvalfacts.model import RequestId, scope_digest
from steward.approvalfacts.reader import UnreadableFacts, detect_legacy_v1, load_facts

SHA = "221457933968be9e95acd51d548e080f739c794c"
OTHER_SHA = "05aa16e12981b35c224c2ca28d65f0a9c15c274e"
REPO = "andrei-shtanakov/steward"
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "0" * 64


def _header(scope, **over):
    base = {
        "kind": "header", "schema_version": "2", "repository": REPO,
        "generated_at": "2026-08-21T09:00:00Z", "valid_until": "2026-08-22T09:00:00Z",
        "policy_version": 1, "policy_digest": DIGEST, "complete": True,
        "scope_sha256": scope_digest(scope),
        "scope": [r.as_dict() for r in scope],
    }
    base.update(over)
    return base


def _write(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "approval_facts.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _merged(request, sha=SHA):
    return {"kind": "result", "request": request.as_dict(), "state": "merged",
            "merge_sha": sha, "identity": "github:merge-broker",
            "type_hint": "Bot", "actor_class": "agent"}


def test_valid_file_loads(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope), _merged(scope[0])])
    facts = load_facts(path, expected_repository=REPO, now=NOW)
    assert facts.by_merge_sha()[SHA].actor_class == "agent"


def test_missing_result_for_scope_item_is_unreadable(tmp_path: Path) -> None:
    """Усечённый JSONL — не валидный префикс: header обещает scope целиком."""
    scope = [RequestId("merge_sha", SHA), RequestId("pr", 42)]
    path = _write(tmp_path, [_header(scope), _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="scope"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_result_outside_scope_is_unreadable(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    stray = RequestId("pr", 7)
    path = _write(tmp_path, [_header(scope), _merged(scope[0]), _merged(stray, OTHER_SHA)])
    with pytest.raises(UnreadableFacts, match="вне scope|outside scope"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_header_must_be_first_and_only(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope), _merged(scope[0]), _header(scope)])
    with pytest.raises(UnreadableFacts, match="header"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_duplicate_scope_item_is_unreadable(tmp_path: Path) -> None:
    scope = [RequestId("pr", 42), RequestId("pr", 42)]
    path = _write(tmp_path, [_header(scope), _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="дубл|duplicate"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_contradicting_aliases_are_unreadable(tmp_path: Path) -> None:
    """Оба алиаса разрешились в один merge, но наблюдения разошлись."""
    scope = [RequestId("pr", 42), RequestId("merge_sha", SHA)]
    good = _merged(scope[0])
    bad = _merged(scope[1])
    bad["actor_class"] = "human"
    path = _write(tmp_path, [_header(scope), good, bad])
    with pytest.raises(UnreadableFacts, match="алиас|alias"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_agreeing_aliases_are_valid(tmp_path: Path) -> None:
    scope = [RequestId("pr", 42), RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope), _merged(scope[0]), _merged(scope[1])])
    facts = load_facts(path, expected_repository=REPO, now=NOW)
    assert len(facts.results) == 2


def test_foreign_repository_is_unreadable(tmp_path: Path) -> None:
    """Инвариант 11: файл чужого репозитория с тем же SHA не влияет ни на что."""
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope, repository="someone/else"), _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="repository"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_repository_comparison_ignores_case(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope, repository=REPO.upper()), _merged(scope[0])])
    assert load_facts(path, expected_repository=REPO, now=NOW).results


def test_future_generated_at_beyond_skew_is_unreadable(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    future = (NOW + timedelta(seconds=301)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = _write(tmp_path, [_header(scope, generated_at=future,
                                     valid_until="2026-08-23T09:00:00Z"),
                             _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="будущ|future"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_lease_longer_than_contract_bound_is_unreadable(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope, valid_until="2026-12-31T09:00:00Z"),
                             _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="lease"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_scope_sha256_mismatch_is_unreadable(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    path = _write(tmp_path, [_header(scope, scope_sha256="sha256:" + "9" * 64),
                             _merged(scope[0])])
    with pytest.raises(UnreadableFacts, match="scope_sha256"):
        load_facts(path, expected_repository=REPO, now=NOW)


def test_legacy_v1_is_detected_not_interpreted(tmp_path: Path) -> None:
    path = tmp_path / "approval_facts.jsonl"
    path.write_text(json.dumps({"schema": "approval-facts/v1", "actors": {}}), encoding="utf-8")
    assert detect_legacy_v1(path) is True
    with pytest.raises(UnreadableFacts, match="legacy"):
        load_facts(path, expected_repository=REPO, now=NOW)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/approvalfacts/test_reader.py -v`
Expected: FAIL — `ModuleNotFoundError: steward.approvalfacts.reader`.

- [ ] **Step 3: Реализовать читателя**

```python
"""Читатель `approval-facts/v2`: разбор плюс одиннадцать инвариантов.

`complete: true` — заявление продюсера, не доказательство. Ни один
инвариант не пропускается «ради частичной пользы»: нарушение любого делает
файл целиком `unreadable`, потому что частично достоверный evidence в
enforcement неотличим от достоверного.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from steward.approvalfacts.model import (
    STATE_ONLY_FOR,
    ApprovalFactsV2,
    Header,
    RequestId,
    Result,
    scope_digest,
)

MAX_CLOCK_SKEW_SECONDS = 300
MAX_LEASE_SECONDS = 2_592_000
SCHEMA_VERSION = "2"


class UnreadableFacts(ValueError):
    """Файл не является валидным `approval-facts/v2` — fail-closed."""


def detect_legacy_v1(path: Path) -> bool:
    """`approval-facts/v1` опознаётся явно, чтобы не быть истолкованным молча."""
    try:
        first = Path(path).read_text(encoding="utf-8").lstrip()[:200]
    except OSError:
        return False
    return '"approval-facts/v1"' in first


def _parse_ts(raw: object, field: str) -> datetime:
    if not isinstance(raw, str):
        raise UnreadableFacts(f"{field}: ожидалась строка RFC 3339, получено {raw!r}")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UnreadableFacts(f"{field}: не RFC 3339: {raw!r}") from exc
    if value.tzinfo is None:
        raise UnreadableFacts(f"{field}: требуется timezone-aware UTC")
    return value.astimezone(UTC)


def _request(raw: object, where: str) -> RequestId:
    if not isinstance(raw, dict) or set(raw) != {"kind", "value"}:
        raise UnreadableFacts(f"{where}: request должен быть {{kind, value}}")
    kind, value = raw["kind"], raw["value"]
    if kind == "pr" and isinstance(value, int) and not isinstance(value, bool):
        return RequestId("pr", value)
    if kind == "merge_sha" and isinstance(value, str) and len(value) == 40:
        return RequestId("merge_sha", value)
    raise UnreadableFacts(f"{where}: недопустимая пара (kind, value): {raw!r}")


def _normalize_repo(name: str) -> str:
    return name.strip().lower()


def load_facts(path: Path, *, expected_repository: str, now: datetime) -> ApprovalFactsV2:
    """Прочитать и полностью проверить файл фактов."""
    if detect_legacy_v1(path):
        raise UnreadableFacts(f"{path}: unsupported legacy approval-facts/v1")
    try:
        lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError as exc:
        raise UnreadableFacts(f"{path}: не читается: {exc}") from exc
    if not lines:
        raise UnreadableFacts(f"{path}: пустой файл")

    records = []
    for number, line in enumerate(lines, start=1):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise UnreadableFacts(f"{path}:{number}: не JSON: {exc}") from exc

    # Инвариант 1 — header первой и только первой строкой.
    if records[0].get("kind") != "header":
        raise UnreadableFacts(f"{path}: первая строка обязана быть header")
    if any(r.get("kind") == "header" for r in records[1:]):
        raise UnreadableFacts(f"{path}: header встречается более одного раза")
    raw_header = records[0]
    if raw_header.get("schema_version") != SCHEMA_VERSION:
        raise UnreadableFacts(
            f"{path}: schema_version {raw_header.get('schema_version')!r}, ожидалось "
            f"{SCHEMA_VERSION!r}"
        )

    # Инвариант 11 — совпадение с наблюдаемым репозиторием (внешнее ожидание).
    repository = raw_header.get("repository")
    if not isinstance(repository, str) or _normalize_repo(repository) != _normalize_repo(
        expected_repository
    ):
        raise UnreadableFacts(
            f"{path}: header.repository {repository!r} не совпадает с наблюдаемым "
            f"{expected_repository!r}"
        )

    # Инвариант 2 — scope непуст и без дублей.
    raw_scope = raw_header.get("scope")
    if not isinstance(raw_scope, list) or not raw_scope:
        raise UnreadableFacts(f"{path}: scope обязан быть непустым списком")
    scope = [_request(item, f"{path}: scope") for item in raw_scope]
    if len(set(scope)) != len(scope):
        raise UnreadableFacts(f"{path}: scope содержит дубли")

    # Инвариант 10 — время.
    generated_at = _parse_ts(raw_header.get("generated_at"), f"{path}: generated_at")
    valid_until = _parse_ts(raw_header.get("valid_until"), f"{path}: valid_until")
    if valid_until <= generated_at:
        raise UnreadableFacts(f"{path}: valid_until должен быть строго позже generated_at")
    if (valid_until - generated_at).total_seconds() > MAX_LEASE_SECONDS:
        raise UnreadableFacts(
            f"{path}: заявленная lease длиннее контрактной границы {MAX_LEASE_SECONDS} с"
        )
    if (generated_at - now).total_seconds() > MAX_CLOCK_SKEW_SECONDS:
        raise UnreadableFacts(
            f"{path}: generated_at в будущем более чем на {MAX_CLOCK_SKEW_SECONDS} с"
        )

    # §4.4 — корреляционная проверка заявки (не заменяет биекцию ниже).
    if raw_header.get("scope_sha256") != scope_digest(scope):
        raise UnreadableFacts(f"{path}: scope_sha256 не соответствует объявленному scope")

    results: list[Result] = []
    for number, raw in enumerate(records[1:], start=2):
        if raw.get("kind") != "result":
            raise UnreadableFacts(f"{path}:{number}: неизвестный kind {raw.get('kind')!r}")
        request = _request(raw.get("request"), f"{path}:{number}")
        state = raw.get("state")
        if state not in {
            "merged", "not_merged", "not_found", "no_matching_pr", "actor_unavailable"
        }:
            raise UnreadableFacts(f"{path}:{number}: неизвестное state {state!r}")
        only_for = STATE_ONLY_FOR.get(str(state))
        if only_for is not None and request.kind != only_for:
            raise UnreadableFacts(
                f"{path}:{number}: state {state!r} допустимо только для request.kind "
                f"{only_for!r}, получено {request.kind!r}"
            )
        results.append(
            Result(
                request=request,
                state=state,  # type: ignore[arg-type]  # проверено выше
                merge_sha=raw.get("merge_sha"),
                identity=raw.get("identity"),
                type_hint=raw.get("type_hint"),
                actor_class=raw.get("actor_class"),
            )
        )

    # Инварианты 3–6 — биекция scope ↔ results по идентичности (kind, value).
    seen: dict[RequestId, Result] = {}
    for result in results:
        if result.request in seen:
            raise UnreadableFacts(f"{path}: дубль result для {result.request}")
        if result.request not in set(scope):
            raise UnreadableFacts(f"{path}: result вне scope: {result.request}")
        seen[result.request] = result
    missing = [item for item in scope if item not in seen]
    if missing:
        raise UnreadableFacts(f"{path}: нет result для элементов scope: {missing}")

    # Инвариант 9 — алиасы одного мержа не противоречат друг другу.
    by_sha: dict[str, Result] = {}
    for result in results:
        if result.merge_sha is None:
            continue
        twin = by_sha.get(result.merge_sha)
        if twin is not None and twin.comparable != result.comparable:
            raise UnreadableFacts(
                f"{path}: алиасы одного мержа {result.merge_sha} дают разные наблюдения"
            )
        by_sha[result.merge_sha] = result

    header = Header(
        repository=repository,
        generated_at=generated_at,
        valid_until=valid_until,
        policy_version=int(raw_header["policy_version"]),
        policy_digest=str(raw_header["policy_digest"]),
        scope=tuple(scope),
        scope_sha256=str(raw_header["scope_sha256"]),
    )
    return ApprovalFactsV2(header=header, results=tuple(results))
```

- [ ] **Step 4: Прогнать тесты**

Run: `uv run pytest tests/approvalfacts/test_reader.py -v`
Expected: PASS, 13 тестов.

- [ ] **Step 5: Экспортировать из пакета**

Добавить в `src/steward/approvalfacts/__init__.py`: `UnreadableFacts`, `load_facts`, `detect_legacy_v1`, `MAX_CLOCK_SKEW_SECONDS`, `MAX_LEASE_SECONDS` — и в `__all__`.

- [ ] **Step 6: Формат, линт, типы**

Run: `uv run ruff format . && uv run ruff check . --fix && uv run pyrefly check`
Expected: чисто.

- [ ] **Step 7: Коммит**

```bash
git add src/steward/approvalfacts tests/approvalfacts/test_reader.py
git commit -m "feat(approvalfacts): читатель v2 с одиннадцатью инвариантами"
```

---

### Task 4: Продюсер — `gh`-слой, `errors`, `repository: null`, исчерпывающая пагинация

**Files:**
- Create: `src/steward/approvalfacts/producer.py`
- Delete: `tests/approvalfacts/test_materializer.py` (сценарии переезжают ниже)
- Test: `tests/approvalfacts/test_producer.py`

**Interfaces:**
- Consumes: `RequestId`, `Result` (задача 2).
- Produces: `class MechanicalFailure(RuntimeError)`; `_gh(args: list[str]) -> tuple[int, str]` — единственная точка вызова, которую подменяют тесты; `resolve(owner: str, name: str, request: RequestId) -> Result`; `materialize(repo: str, scope: Sequence[RequestId]) -> list[Result]`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Продюсер: определённый отрицательный ответ — запись, всё остальное — отказ.

Тесты подменяют `_gh`, поэтому сети нет ни в одном сценарии.
"""

import json
from collections.abc import Iterator

import pytest

from steward.approvalfacts import producer
from steward.approvalfacts.model import RequestId
from steward.approvalfacts.producer import MechanicalFailure, materialize, resolve

SHA = "221457933968be9e95acd51d548e080f739c794c"
OWNER, NAME = "andrei-shtanakov", "steward"


def _fake_gh(responses: list[tuple[int, object]]):
    it: Iterator[tuple[int, object]] = iter(responses)

    def fake(args: list[str]) -> tuple[int, str]:
        code, payload = next(it)
        return code, payload if isinstance(payload, str) else json.dumps(payload)

    return fake


def test_merged_pr_yields_merged_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, {"data": {"repository": {
        "pullRequest": {"mergeCommit": {"oid": SHA},
                        "mergedBy": {"login": "merge-broker", "__typename": "Bot"}}}}})]))
    result = resolve(OWNER, NAME, RequestId("pr", 42))
    assert (result.state, result.merge_sha, result.identity, result.type_hint) == (
        "merged", SHA, "github:merge-broker", "Bot")


def test_unmerged_pr_is_a_record_not_an_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    """Определённый отрицательный ответ не уничтожает батч."""
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, {"data": {"repository": {
        "pullRequest": {"mergeCommit": None, "mergedBy": None}}}})]))
    result = resolve(OWNER, NAME, RequestId("pr", 42))
    assert result.state == "not_merged"
    assert result.merge_sha is None


def test_absent_pr_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(producer, "_gh", _fake_gh(
        [(0, {"data": {"repository": {"pullRequest": None}}})]))
    assert resolve(OWNER, NAME, RequestId("pr", 42)).state == "not_found"


def test_null_repository_is_mechanical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`repository: null` может быть auth/visibility failure и НЕ является
    доказанным отсутствием — иначе отказ доступа выглядел бы как факт."""
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, {"data": {"repository": None}})]))
    with pytest.raises(MechanicalFailure, match="repository"):
        resolve(OWNER, NAME, RequestId("pr", 42))


def test_graphql_errors_are_mechanical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Непустой `errors` — недоступность результата, даже при exit 0."""
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, {
        "data": {"repository": {"pullRequest": None}},
        "errors": [{"message": "RATE_LIMITED"}]})]))
    with pytest.raises(MechanicalFailure, match="errors"):
        resolve(OWNER, NAME, RequestId("pr", 42))


def test_gh_nonzero_exit_is_mechanical_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(producer, "_gh", _fake_gh([(1, "gh: not authenticated")]))
    with pytest.raises(MechanicalFailure):
        resolve(OWNER, NAME, RequestId("pr", 42))


def test_sha_pagination_is_exhaustive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Совпадение на ВТОРОЙ странице обязано быть найдено: иначе продюсер
    выдал бы ложный no_matching_pr."""
    page1 = {"data": {"repository": {"object": {"associatedPullRequests": {
        "nodes": [{"mergeCommit": {"oid": "0" * 40}, "mergedBy": None}],
        "pageInfo": {"hasNextPage": True, "endCursor": "CUR"}}}}}}
    page2 = {"data": {"repository": {"object": {"associatedPullRequests": {
        "nodes": [{"mergeCommit": {"oid": SHA},
                   "mergedBy": {"login": "andrei-shtanakov", "__typename": "User"}}],
        "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}}
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, page1), (0, page2)]))
    result = resolve(OWNER, NAME, RequestId("merge_sha", SHA))
    assert result.state == "merged"
    assert result.identity == "github:andrei-shtanakov"


def test_no_match_after_full_traversal_is_no_matching_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, {"data": {"repository": {"object": {
        "associatedPullRequests": {"nodes": [],
                                   "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}})]))
    assert resolve(OWNER, NAME, RequestId("merge_sha", SHA)).state == "no_matching_pr"


def test_absent_commit_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(producer, "_gh", _fake_gh(
        [(0, {"data": {"repository": {"object": None}}})]))
    assert resolve(OWNER, NAME, RequestId("merge_sha", SHA)).state == "not_found"


def test_merged_without_mergedby_is_actor_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(producer, "_gh", _fake_gh([(0, {"data": {"repository": {
        "pullRequest": {"mergeCommit": {"oid": SHA}, "mergedBy": None}}}})]))
    result = resolve(OWNER, NAME, RequestId("pr", 42))
    assert result.state == "actor_unavailable"
    assert result.merge_sha == SHA
    assert result.identity is None


def test_materialize_aborts_whole_batch_on_mechanical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Один недоступный элемент валит публикацию целиком — но неслитый PR нет."""
    monkeypatch.setattr(producer, "_gh", _fake_gh([
        (0, {"data": {"repository": {"pullRequest": {"mergeCommit": None, "mergedBy": None}}}}),
        (1, "gh: boom"),
    ]))
    with pytest.raises(MechanicalFailure):
        materialize("andrei-shtanakov/steward",
                    [RequestId("pr", 1), RequestId("pr", 2)])


def test_malformed_repo_is_value_error() -> None:
    with pytest.raises(ValueError, match="owner/name"):
        materialize("not-a-slug", [RequestId("pr", 1)])
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/approvalfacts/test_producer.py -v`
Expected: FAIL — `ModuleNotFoundError: steward.approvalfacts.producer`.

- [ ] **Step 3: Реализовать продюсера**

```python
"""Продюсер `approval-facts/v2` поверх `gh`.

Граница проведена один раз и держится везде: **механический сбой** (не смогли
спросить) публикацию отменяет, **определённый отрицательный ответ** (спросили,
получили «нет») становится записью. Смешивать их нельзя — иначе отказ доступа
читался бы как свойство мержа.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from typing import Any

from steward.approvalfacts.model import RequestId, Result

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
        args += (["-F", f"{key}={value}"] if isinstance(value, int) else ["-f", f"{key}={value}"])
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
    payload = _graphql(_QUERY_BY_PR, {"owner": owner, "name": name,
                                      "number": int(request.value)}, what=what)
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
            return Result(request, "merged", merge_sha=str(request.value),
                          identity=identity, type_hint=hint)
        page_info = associated.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return Result(request, "no_matching_pr")
        cursor = page_info.get("endCursor")
        if not isinstance(cursor, str):
            raise MechanicalFailure(f"{what}: hasNextPage без endCursor — обход не завершить")
    raise MechanicalFailure(f"{what}: обход не завершён за {_MAX_PAGES} страниц")


def resolve(owner: str, name: str, request: RequestId) -> Result:
    """Разрешить один элемент scope в терминальное наблюдение."""
    return _resolve_pr(owner, name, request) if request.kind == "pr" else _resolve_sha(
        owner, name, request
    )


def materialize(repo: str, scope: Sequence[RequestId]) -> list[Result]:
    """Разрешить весь scope. Любой механический сбой прерывает батч."""
    owner, sep, name = repo.partition("/")
    if not sep or not owner or not name:
        raise ValueError(f"repo must be 'owner/name', got {repo!r}")
    return [resolve(owner, name, request) for request in scope]
```

- [ ] **Step 4: Прогнать тесты**

Run: `uv run pytest tests/approvalfacts/test_producer.py -v`
Expected: PASS, 12 тестов.

- [ ] **Step 5: Коммит**

```bash
git add src/steward/approvalfacts/producer.py tests/approvalfacts/test_producer.py
git rm tests/approvalfacts/test_materializer.py
git commit -m "feat(approvalfacts): продюсер v2 — терминальные состояния и исчерпывающий обход"
```

---

### Task 5: Политика — `approval_facts_lease_seconds`

**Files:**
- Modify: `src/steward/gatecheck/approval.py` (`ApprovalPolicy`, `_ALLOWED_KEYS`, `load_approval_policy`)
- Modify: `profiles/approval-policy.yaml`
- Test: `tests/gatecheck/test_approval_classify.py`

**Interfaces:**
- Consumes: существующий `load_approval_policy(path) -> ApprovalPolicy`.
- Produces: `ApprovalPolicy.approval_facts_lease_seconds: int`; модульная константа `MAX_LEASE_SECONDS = 2_592_000`; `policy_digest(path: Path) -> str` — sha256 по сырым байтам файла политики.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_lease_is_required_positive_int(tmp_path: Path) -> None:
    path = tmp_path / "approval-policy.yaml"
    path.write_text(
        "version: 1\nhuman_identities: []\nagent_identities: []\n", encoding="utf-8"
    )
    with pytest.raises(PolicyError, match="approval_facts_lease_seconds"):
        load_approval_policy(path)


@pytest.mark.parametrize("bad", ["true", "0", "-1", "'86400'", "86400.5"])
def test_lease_rejects_non_positive_int(tmp_path: Path, bad: str) -> None:
    """`bool` — подкласс int в Python, поэтому запрещается отдельно: иначе
    `true` молча стал бы lease в одну секунду."""
    path = tmp_path / "approval-policy.yaml"
    path.write_text(
        "version: 1\nhuman_identities: []\nagent_identities: []\n"
        f"approval_facts_lease_seconds: {bad}\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyError):
        load_approval_policy(path)


def test_lease_upper_bound_is_enforced(tmp_path: Path) -> None:
    """Опечатка в порядке величины не должна давать многолетнюю lease."""
    path = tmp_path / "approval-policy.yaml"
    path.write_text(
        "version: 1\nhuman_identities: []\nagent_identities: []\n"
        "approval_facts_lease_seconds: 864000000\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match="2592000"):
        load_approval_policy(path)


def test_canonical_policy_declares_lease() -> None:
    assert load_approval_policy(CANONICAL).approval_facts_lease_seconds > 0


def test_policy_digest_is_over_raw_bytes(tmp_path: Path) -> None:
    """Комментарий — часть аудируемого артефакта, поэтому его правка честно
    меняет digest."""
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    body = ("version: 1\nhuman_identities: []\nagent_identities: []\n"
            "approval_facts_lease_seconds: 86400\n")
    a.write_text(body, encoding="utf-8")
    b.write_text("# пояснение\n" + body, encoding="utf-8")
    assert policy_digest(a) != policy_digest(b)
    assert policy_digest(a).startswith("sha256:")
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/gatecheck/test_approval_classify.py -v`
Expected: FAIL — нет `approval_facts_lease_seconds`, нет `policy_digest`.

- [ ] **Step 3: Реализовать**

В `ApprovalPolicy` добавить поле `approval_facts_lease_seconds: int`. В `_ALLOWED_KEYS` добавить ключ. В `load_approval_policy` — проверка:

```python
MAX_LEASE_SECONDS = 2_592_000  # 30 суток — контрактная граница (§7)


def _check_lease(value: object, path: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyError(
            f"{path}: 'approval_facts_lease_seconds' must be a positive int "
            f"(bool and non-int are rejected, not coerced), got {value!r}"
        )
    if value <= 0:
        raise PolicyError(f"{path}: 'approval_facts_lease_seconds' must be > 0, got {value}")
    if value > MAX_LEASE_SECONDS:
        raise PolicyError(
            f"{path}: 'approval_facts_lease_seconds' must be <= {MAX_LEASE_SECONDS} "
            f"(30 days), got {value}"
        )
    return value


def policy_digest(path: Path) -> str:
    """sha256 по СЫРЫМ байтам файла политики."""
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
```

Ключ обязателен: `if "approval_facts_lease_seconds" not in data: raise PolicyError(...)`.

- [ ] **Step 4: Обновить канонический профиль**

В `profiles/approval-policy.yaml` добавить с комментарием, объясняющим смысл:

```yaml
# Срок действия наблюдения approval-facts (§6.4 спеки v2). Не эвристика
# читателя по возрасту, а нормативная lease продюсера: после неё actor_class
# перестаёт быть операционным наблюдением, и D6 переводит authority в human.
approval_facts_lease_seconds: 86400
```

- [ ] **Step 5: Прогнать тесты**

Run: `uv run pytest tests/gatecheck/ -v`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add src/steward/gatecheck/approval.py profiles/approval-policy.yaml tests/gatecheck/test_approval_classify.py
git commit -m "feat(policy): approval_facts_lease_seconds и digest по сырым байтам"
```

---

### Task 6: Публикация — `origin`, preflight, delete-before-attempt, долговечная запись

**Files:**
- Create: `src/steward/approvalfacts/publish.py`
- Test: `tests/approvalfacts/test_publish.py`

**Interfaces:**
- Consumes: `Header`, `RequestId`, `Result`, `scope_digest` (задача 2); `policy_digest`, `MAX_LEASE_SECONDS` (задача 5).
- Produces: `FACTS_RELPATH = Path(".steward") / "approval_facts.jsonl"`; `class ConfigError(ValueError)`; `parse_origin(url: str) -> tuple[str, str]`; `resolve_bundle_target(repo: str, repo_root: Path) -> Path`; `build_header(...) -> Header`; `publish(path: Path, header: Header, results: Sequence[Result]) -> None`; `remove_previous(path: Path) -> None`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Публикация: разрушать прежнее наблюдение можно только после того, как
всё остальное доказано."""

import json
import os
from pathlib import Path

import pytest

from steward.approvalfacts.model import Header, RequestId, Result, scope_digest
from steward.approvalfacts.publish import (
    ConfigError,
    parse_origin,
    publish,
    remove_previous,
    resolve_bundle_target,
)

SHA = "221457933968be9e95acd51d548e080f739c794c"


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:andrei-shtanakov/steward.git",
        "git@github.com:andrei-shtanakov/steward",
        "https://github.com/andrei-shtanakov/steward.git",
        "https://github.com/andrei-shtanakov/steward",
        "ssh://git@github.com/andrei-shtanakov/steward.git",
    ],
)
def test_parse_origin_accepts_known_forms(url: str) -> None:
    assert parse_origin(url) == ("andrei-shtanakov", "steward")


def test_parse_origin_rejects_suffix_match() -> None:
    """`endswith` совпал бы с чужим владельцем — сравнение только по полной паре."""
    assert parse_origin("git@github.com:other-owner/steward.git") == ("other-owner", "steward")


def test_parse_origin_rejects_garbage() -> None:
    with pytest.raises(ConfigError):
        parse_origin("not-a-url")


def test_publish_writes_atomically_and_durably(tmp_path: Path) -> None:
    scope = [RequestId("merge_sha", SHA)]
    header = _header(scope)
    path = tmp_path / ".steward" / "approval_facts.jsonl"
    publish(path, header, [Result(scope[0], "merged", SHA, "github:x", "Bot", "agent")])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["kind"] == "header"
    assert json.loads(lines[0])["complete"] is True
    assert json.loads(lines[1])["state"] == "merged"
    assert not list(path.parent.glob(".approval_facts-*.tmp")), "временный файл не убран"


def test_publish_omits_forbidden_fields_for_negative_states(tmp_path: Path) -> None:
    """Схема запрещает identity у отрицательных состояний — сериализация
    обязана их не писать, а не писать `null`."""
    scope = [RequestId("pr", 42)]
    path = tmp_path / "facts.jsonl"
    publish(path, _header(scope), [Result(scope[0], "not_merged")])
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[1])
    assert record["merge_sha"] is None
    assert "identity" not in record and "type_hint" not in record
    assert "actor_class" not in record


def test_remove_previous_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "facts.jsonl"
    remove_previous(path)  # файла нет — не ошибка
    path.write_text("x", encoding="utf-8")
    remove_previous(path)
    assert not path.exists()


def test_resolve_bundle_target_requires_matching_origin(tmp_path: Path) -> None:
    root = _git_repo(tmp_path, origin="git@github.com:someone/else.git")
    with pytest.raises(ConfigError, match="origin"):
        resolve_bundle_target("andrei-shtanakov/steward", root)


def test_resolve_bundle_target_requires_origin_remote(tmp_path: Path) -> None:
    """Во флоте встречаются несколько remote на репо — берётся именно origin."""
    root = _git_repo(tmp_path, origin=None)
    with pytest.raises(ConfigError, match="origin"):
        resolve_bundle_target("andrei-shtanakov/steward", root)


def test_resolve_bundle_target_returns_bundle_path(tmp_path: Path) -> None:
    root = _git_repo(tmp_path, origin="git@github.com:andrei-shtanakov/steward.git")
    target = resolve_bundle_target("andrei-shtanakov/steward", root)
    assert target == root / ".steward" / "approval_facts.jsonl"


def test_resolve_bundle_target_ignores_case(tmp_path: Path) -> None:
    root = _git_repo(tmp_path, origin="git@github.com:Andrei-Shtanakov/Steward.git")
    assert resolve_bundle_target("andrei-shtanakov/steward", root)
```

Вспомогательные `_header` и `_git_repo` — в том же файле: `_git_repo` делает `git init`, при `origin is not None` — `git remote add origin <url>`, и возвращает путь.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/approvalfacts/test_publish.py -v`
Expected: FAIL — `ModuleNotFoundError: steward.approvalfacts.publish`.

- [ ] **Step 3: Реализовать публикацию**

```python
"""Публикация фактов: разрешение цели, транзакция, долговечная запись.

Порядок действий здесь — не деталь реализации, а требование §6.1 и §8.4:
удаление прежней публикации выполняется ПОСЛЕ полного preflight. Удаление
ради безопасности — осознанный размен; удаление из-за опечатки в конфиге —
потеря данных на ровном месте.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from steward.approvalfacts.model import Header, RequestId, Result, scope_digest

FACTS_RELPATH = Path(".steward") / "approval_facts.jsonl"
SCHEMA_VERSION = "2"

_ORIGIN_RE = re.compile(
    r"^(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"
)


class ConfigError(ValueError):
    """Ошибка конфигурации вызова — exit 2, прежняя публикация не тронута."""


def parse_origin(url: str) -> tuple[str, str]:
    """Разобрать URL remote в пару `(owner, repo)`.

    Сравнение по полной паре, без суффиксного: `endswith("owner/repo")`
    совпал бы и с `other-owner/repo`, и с чужим хостом.
    """
    match = _ORIGIN_RE.match(url.strip())
    if match is None:
        raise ConfigError(f"не удалось разобрать origin: {url!r}")
    return match.group("owner"), match.group("repo")


def _git(cwd: Path, *args: str) -> str | None:
    proc = subprocess.run(  # noqa: S603 S607 — фиксированный argv
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def resolve_bundle_target(repo: str, repo_root: Path) -> Path:
    """Разрешить путь бандла и доказать, что это тот самый репозиторий."""
    top = _git(repo_root, "rev-parse", "--show-toplevel")
    if top is None:
        raise ConfigError(f"{repo_root} не внутри git-репозитория")
    origin = _git(Path(top), "remote", "get-url", "origin")
    if origin is None:
        raise ConfigError(
            f"{top}: нет remote 'origin' — в рабочих копиях флота бывает несколько "
            "remote, и сверяется именно origin"
        )
    owner, name = parse_origin(origin)
    if (owner.lower(), name.lower()) != tuple(part.lower() for part in repo.split("/", 1)):
        raise ConfigError(
            f"{top}: origin указывает на {owner}/{name}, а --repo говорит {repo} — "
            "публикация в чужой бандл запрещена"
        )
    return Path(top) / FACTS_RELPATH


def build_header(
    *,
    repository: str,
    scope: Sequence[RequestId],
    policy_version: int,
    policy_digest_value: str,
    lease_seconds: int,
    now: datetime,
) -> Header:
    generated_at = now.astimezone(UTC).replace(microsecond=0)
    return Header(
        repository=repository,
        generated_at=generated_at,
        valid_until=generated_at + timedelta(seconds=lease_seconds),
        policy_version=policy_version,
        policy_digest=policy_digest_value,
        scope=tuple(scope),
        scope_sha256=scope_digest(scope),
    )


def _header_record(header: Header) -> dict[str, object]:
    return {
        "kind": "header",
        "schema_version": SCHEMA_VERSION,
        "repository": header.repository,
        "generated_at": header.generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "valid_until": header.valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy_version": header.policy_version,
        "policy_digest": header.policy_digest,
        "complete": True,
        "scope_sha256": header.scope_sha256,
        "scope": [r.as_dict() for r in header.scope],
    }


def _result_record(result: Result) -> dict[str, object]:
    """Запрещённые схемой поля НЕ пишутся вовсе, а не пишутся как null."""
    record: dict[str, object] = {
        "kind": "result",
        "request": result.request.as_dict(),
        "state": result.state,
        "merge_sha": result.merge_sha,
    }
    if result.state == "merged":
        record["identity"] = result.identity
        record["type_hint"] = result.type_hint
        record["actor_class"] = result.actor_class
    return record


def remove_previous(path: Path) -> None:
    """Снять прежнюю публикацию и зафиксировать это на диске."""
    Path(path).unlink(missing_ok=True)
    parent = Path(path).parent
    if parent.is_dir():
        _fsync_dir(parent)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path: Path, header: Header, results: Sequence[Result]) -> None:
    """Атомарно и долговечно опубликовать файл фактов."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [_header_record(header), *(_result_record(r) for r in results)]
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".approval_facts-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        _fsync_dir(path.parent)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
```

- [ ] **Step 4: Прогнать тесты**

Run: `uv run pytest tests/approvalfacts/test_publish.py -v`
Expected: PASS, 11 тестов.

- [ ] **Step 5: Коммит**

```bash
git add src/steward/approvalfacts/publish.py tests/approvalfacts/test_publish.py
git commit -m "feat(approvalfacts): публикация — origin, транзакция, долговечная запись"
```

---

### Task 7: CLI `steward approval-facts` — `--repo-root`, полный preflight

**Files:**
- Modify: `src/steward/riskclassify/cli.py:131-177`
- Test: `tests/riskclassify/test_approval_facts_cli.py`

**Interfaces:**
- Consumes: `materialize` (задача 4), `resolve_bundle_target` / `build_header` / `publish` / `remove_previous` / `ConfigError` (задача 6), `load_approval_policy` / `policy_digest` (задача 5).
- Produces: команда с опциями `--repo`, `--repo-root` (default `.`), `--out`, `--merge-sha`, `--prs`, `--policy`; exit-коды 0/2/3.

- [ ] **Step 1: Написать падающие тесты**

```python
"""CLI: ни один шаг preflight не должен разрушать прежнюю публикацию."""

from pathlib import Path

from typer.testing import CliRunner

from steward.riskclassify.cli import app

runner = CliRunner()


def test_bad_repo_is_config_error_and_keeps_previous(tmp_path: Path) -> None:
    previous = tmp_path / "facts.jsonl"
    previous.write_text("prior", encoding="utf-8")
    result = runner.invoke(app, ["approval-facts", "--repo", "bad", "--prs", "1",
                                 "--out", str(previous)])
    assert result.exit_code == 2
    assert previous.read_text(encoding="utf-8") == "prior", "прежняя публикация уничтожена"


def test_bad_lease_config_is_caught_before_delete(tmp_path: Path) -> None:
    """Битая lease-конфигурация обязана упасть ДО удаления."""
    previous = tmp_path / "facts.jsonl"
    previous.write_text("prior", encoding="utf-8")
    policy = tmp_path / "approval-policy.yaml"
    policy.write_text(
        "version: 1\nhuman_identities: []\nagent_identities: []\n"
        "approval_facts_lease_seconds: 864000000\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["approval-facts", "--repo", "o/n", "--prs", "1",
                                 "--out", str(previous), "--policy", str(policy)])
    assert result.exit_code == 2
    assert previous.read_text(encoding="utf-8") == "prior"


def test_duplicate_scope_is_config_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["approval-facts", "--repo", "o/n", "--prs", "1,1",
                                 "--out", str(tmp_path / "f.jsonl")])
    assert result.exit_code == 2
    assert "дубл" in result.output or "duplicate" in result.output


def test_no_identifiers_is_config_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["approval-facts", "--repo", "o/n",
                                 "--out", str(tmp_path / "f.jsonl")])
    assert result.exit_code == 2
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/riskclassify/test_approval_facts_cli.py -v`
Expected: FAIL — старая команда требует `--out` и не знает `--policy`.

- [ ] **Step 3: Переписать команду**

Порядок в теле команды обязан быть ровно таким (§8.4):

```python
@app.command("approval-facts")
def approval_facts(
    repo: str = typer.Option(..., "--repo", help="owner/name на форже"),
    repo_root: Path = typer.Option(
        Path("."), "--repo-root", help="Чекаут наблюдаемого репозитория (для пути бандла)."
    ),
    out: Path | None = typer.Option(
        None, "--out", help="Явный override пути; без него пишется в бандл."
    ),
    policy: Path | None = typer.Option(None, "--policy", help="Файл политики классификации."),
    merge_sha: list[str] = typer.Option([], "--merge-sha", help="Merge SHA (повторяемо)."),
    prs: str | None = typer.Option(None, "--prs", help="Номера PR через запятую."),
) -> None:
    """Материализовать `approval-facts/v2` и опубликовать его.

    Exit: ``0`` опубликовано, ``2`` ошибка конфигурации (прежняя публикация
    НЕ тронута), ``3`` механический сбой материализации (файла нет).
    """
    # 1-3: разбор --repo, scope и его валидация (непуст, без дублей, формы).
    # 4:   загрузка политики и вычисление policy_digest.
    # 5:   валидация approval_facts_lease_seconds (внутри load_approval_policy).
    # 6:   разрешение цели: resolve_bundle_target(...) для бандла ЛИБО
    #      проверка родительского каталога для --out.
    # --- только теперь ---
    # 7:   remove_previous(target)
    # 8:   materialize(...)          -> MechanicalFailure => exit 3
    # 9:   build_header(...) + publish(...)
```

Каждый шаг 1–6 при ошибке печатает `config error: …` в stderr и завершает `_EXIT_CONFIG`.

- [ ] **Step 4: Прогнать тесты**

Run: `uv run pytest tests/riskclassify/ -v`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/steward/riskclassify/cli.py tests/riskclassify/test_approval_facts_cli.py
git commit -m "feat(cli): approval-facts публикует v2 в бандл, preflight до удаления"
```

---

### Task 8: Гейт — исход чтения как типизированное значение

**Files:**
- Modify: `src/steward/gatecheck/approval.py` (`check_approval_evidence`)
- Modify: `src/steward/gatecheck/cli.py:210-216`
- Test: `tests/gatecheck/test_approval_check.py` (переписывается целиком)

**Interfaces:**
- Consumes: `ApprovalFactsV2`, `load_facts`, `UnreadableFacts` (задачи 2–3); `policy_digest` (задача 5); `resolve_bundle_target`, `parse_origin` (задача 6).
- Produces: `@dataclass(frozen=True) class FactsUnavailable: code: str; detail: str`; `FactsOutcome = ApprovalFactsV2 | FactsUnavailable`; `resolve_facts(path, *, expected_repository, policy, policy_path, now, explicit) -> FactsOutcome` (поднимает `ConfigError` при `explicit=True` и невалидном файле); новая сигнатура `check_approval_evidence(artifacts, git, policy, facts, *, stage)`.

- [ ] **Step 1: Написать падающие тесты — по строке таблицы §8.3**

```python
"""Двадцать четыре сценария приёмки §9.1: у каждого исхода своё сообщение.

Проверяется не только вердикт, но и РАЗЛИЧИМОСТЬ: сведение двух разных
причин к одному тексту прячет поломку прибора под видом свойства актора.
"""

CODES = {
    "absent", "unreadable", "legacy_v1", "policy_digest_mismatch",
    "lease_mismatch", "stale",
}


def test_absent_file_is_unavailable_not_unknown() -> None:
    outcome = resolve_facts(MISSING, expected_repository=REPO, policy=POLICY,
                            policy_path=POLICY_PATH, now=NOW, explicit=False)
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "absent"


def test_legacy_v1_on_bundle_default_is_finding(tmp_path: Path) -> None:
    path = _write_v1(tmp_path)
    outcome = resolve_facts(path, expected_repository=REPO, policy=POLICY,
                            policy_path=POLICY_PATH, now=NOW, explicit=False)
    assert isinstance(outcome, FactsUnavailable) and outcome.code == "legacy_v1"


def test_legacy_v1_on_explicit_path_is_config_error(tmp_path: Path) -> None:
    """Правило §8.3.1: оператор указал не тот файл — это ошибка вызова."""
    path = _write_v1(tmp_path)
    with pytest.raises(ConfigError):
        resolve_facts(path, expected_repository=REPO, policy=POLICY,
                      policy_path=POLICY_PATH, now=NOW, explicit=True)


def test_stale_on_explicit_path_stays_finding(tmp_path: Path) -> None:
    """Граница §8.3.1 проходит по валидности файла, НЕ по свежести:
    просроченный файл корректен и честно сообщает о себе."""
    path = _write_valid(tmp_path, valid_until="2026-08-20T09:00:00Z")
    outcome = resolve_facts(path, expected_repository=REPO, policy=POLICY,
                            policy_path=POLICY_PATH, now=NOW, explicit=True)
    assert isinstance(outcome, FactsUnavailable) and outcome.code == "stale"


def test_policy_digest_mismatch_beats_live_lease(tmp_path: Path) -> None:
    """Приоритет строки 3 таблицы: наблюдение по другой политике не является
    наблюдением по текущей, даже если lease ещё жива."""
    path = _write_valid(tmp_path, policy_digest="sha256:" + "e" * 64)
    outcome = resolve_facts(path, expected_repository=REPO, policy=POLICY,
                            policy_path=POLICY_PATH, now=NOW, explicit=False)
    assert isinstance(outcome, FactsUnavailable)
    assert outcome.code == "policy_digest_mismatch"


def test_lease_duration_mismatch_is_its_own_code(tmp_path: Path) -> None:
    path = _write_valid(tmp_path, valid_until="2026-08-21T10:00:00Z")  # 1 час вместо суток
    outcome = resolve_facts(path, expected_repository=REPO, policy=POLICY,
                            policy_path=POLICY_PATH, now=NOW, explicit=False)
    assert isinstance(outcome, FactsUnavailable) and outcome.code == "lease_mismatch"


def test_sha_outside_scope_is_unknown_not_conflict() -> None:
    """Контрольный: PR-запрос с not_merged при живой локальной provenance
    даёт `unknown` вне scope, а не доказанное противоречие — связать нечем."""
    facts = _facts(scope=[RequestId("pr", 42)],
                   results=[Result(RequestId("pr", 42), "not_merged")])
    findings = check_approval_evidence(_artifacts(), _git(sha=SHA), POLICY, facts,
                                       stage="release")
    assert len(findings) == 1
    assert "вне объявленного scope" in findings[0].message


def test_not_found_for_requested_sha_is_source_conflict() -> None:
    facts = _facts(scope=[RequestId("merge_sha", SHA)],
                   results=[Result(RequestId("merge_sha", SHA), "not_found")])
    findings = check_approval_evidence(_artifacts(), _git(sha=SHA), POLICY, facts,
                                       stage="release")
    assert "противоречие источников" in findings[0].message


def test_no_matching_pr_for_requested_sha_is_source_conflict() -> None:
    facts = _facts(scope=[RequestId("merge_sha", SHA)],
                   results=[Result(RequestId("merge_sha", SHA), "no_matching_pr")])
    findings = check_approval_evidence(_artifacts(), _git(sha=SHA), POLICY, facts,
                                       stage="release")
    assert "противоречие источников" in findings[0].message


def test_actor_unavailable_is_distinct_from_unknown() -> None:
    unavailable = _facts(scope=[RequestId("merge_sha", SHA)],
                         results=[Result(RequestId("merge_sha", SHA), "actor_unavailable",
                                         merge_sha=SHA)])
    unknown = _facts(scope=[RequestId("merge_sha", SHA)],
                     results=[Result(RequestId("merge_sha", SHA), "merged", SHA,
                                     "github:stranger", "User", "unknown")])
    a = check_approval_evidence(_artifacts(), _git(sha=SHA), POLICY, unavailable,
                                stage="release")[0].message
    b = check_approval_evidence(_artifacts(), _git(sha=SHA), POLICY, unknown,
                                stage="release")[0].message
    assert a != b, "две разные причины обязаны быть различимы в сообщении"


def test_human_merge_yields_no_finding() -> None:
    facts = _facts(scope=[RequestId("merge_sha", SHA)],
                   results=[Result(RequestId("merge_sha", SHA), "merged", SHA,
                                   "github:andrei-shtanakov", "User", "human")])
    assert check_approval_evidence(_artifacts(), _git(sha=SHA), POLICY, facts,
                                   stage="release") == []


def test_agent_merge_denied_by_default() -> None:
    facts = _facts(scope=[RequestId("merge_sha", SHA)],
                   results=[Result(RequestId("merge_sha", SHA), "merged", SHA,
                                   "github:merge-broker", "Bot", "agent")])
    findings = check_approval_evidence(_artifacts(), _git(sha=SHA), POLICY, facts,
                                       stage="release")
    assert len(findings) == 1


def test_agent_merge_allowed_by_policy() -> None:
    facts = _facts(scope=[RequestId("merge_sha", SHA)],
                   results=[Result(RequestId("merge_sha", SHA), "merged", SHA,
                                   "github:merge-broker", "Bot", "agent")])
    assert check_approval_evidence(_artifacts(), _git(sha=SHA), POLICY_ALLOWING, facts,
                                   stage="release") == []


def test_authoring_stage_does_not_run_at_all() -> None:
    assert check_approval_evidence(_artifacts(), _git(sha=SHA), POLICY, None,
                                   stage="authoring") == []


def test_artifact_outside_default_branch_is_out_of_scope() -> None:
    git = _git(sha=SHA, on_default_branch=False)
    assert check_approval_evidence(_artifacts(), git, POLICY, None, stage="release") == []


def test_no_provenance_is_absent() -> None:
    findings = check_approval_evidence(_artifacts(), _git(sha=None), POLICY, None,
                                       stage="release")
    assert len(findings) == 1
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/gatecheck/test_approval_check.py -v`
Expected: FAIL — нет `resolve_facts`, `FactsUnavailable`, сигнатура чека прежняя.

- [ ] **Step 3: Реализовать `resolve_facts` и переписать чек**

`resolve_facts` применяет строки 1–6 таблицы §8.3 по порядку и возвращает либо `ApprovalFactsV2`, либо `FactsUnavailable(code, detail)`; при `explicit=True` невалидный (не просроченный) файл поднимает `ConfigError`.

`check_approval_evidence` для каждого managed-артефакта со `status: approved` на дефолтной ветке:

```python
provenance = git.merge_provenance(artifact.path)
if provenance is None:
    -> finding "absent"
if isinstance(facts, FactsUnavailable):
    -> finding с текстом по facts.code
result = facts.by_merge_sha().get(provenance.sha)
if result is None:
    if facts.scope_has_sha(provenance.sha):
        -> finding "противоречие источников: <state>"   # строки 7-8
    -> finding "мерж вне объявленного scope"            # строка 6
if result.state == "actor_unavailable":  -> finding
if result.actor_class == "unknown":      -> finding
if result.actor_class == "agent" and not policy.agent_merge_allowed: -> finding
# human, либо разрешённый agent -> finding нет
```

- [ ] **Step 4: Переписать `gatecheck/cli.py`**

`--approval-facts` остаётся override (`explicit=True`); при его отсутствии путь берётся из бандла — `Path(repo_root) / FACTS_RELPATH`, где `repo_root` уже вычисляется для эмиттера. `expected_repository` выводится из `origin` того же чекаута через `parse_origin`; если `origin` нет — `FactsUnavailable(code="absent")`, а не падение гейта.

- [ ] **Step 5: Прогнать тесты**

Run: `uv run pytest tests/gatecheck/ -v && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Формат, линт, типы**

Run: `uv run ruff format . && uv run ruff check . --fix && uv run pyrefly check`

- [ ] **Step 7: Коммит**

```bash
git add src/steward/gatecheck tests/gatecheck/test_approval_check.py
git commit -m "feat(gate): миграция release-гейта на approval-facts/v2"
```

---

### Task 9: Migration acceptance на реальных мержах

**Files:**
- Create: `docs/evidence/2026-08-21-approval-facts-v2-migration/manifest.md`
- Create: `docs/evidence/2026-08-21-approval-facts-v2-migration/approval_facts.jsonl`
- Modify: `TODO.md`

**Interfaces:**
- Consumes: всё предыдущее.
- Produces: леджер приёмки; никаких новых интерфейсов кода.

- [ ] **Step 1: Материализовать факты по реальным мержам**

```bash
uv run steward approval-facts --repo andrei-shtanakov/steward \
  --merge-sha e04b0c9f30e4670b29afa1af598e5b4d7be48938 \
  --merge-sha 221457933968be9e95acd51d548e080f739c794c \
  --merge-sha 05aa16e12981b35c224c2ca28d65f0a9c15c274e \
  --out /tmp/migration-facts.jsonl
```

Ожидается: exit 0; в файле header плюс три записи `merged`; `e04b0c9…` — `github:andrei-shtanakov` / `User` / `human`; два других — `github:merge-broker` / `Bot` / `agent`.

- [ ] **Step 2: Доказать эквивалентность enforcement**

Сравнить с зафиксированным результатом v1 (`docs/evidence/2026-08-20-i4-live-acceptance/approval-facts.json`): те же три мержа обязаны получить те же классификации. Отличие только в форме файла и в наличии scope/lease — вердикты не меняются.

- [ ] **Step 3: Проверить отрицательный путь на живых данных**

```bash
uv run steward approval-facts --repo andrei-shtanakov/steward --prs 999999 \
  --out /tmp/negative.jsonl
```

Ожидается: exit 0 и запись `not_found` (несуществующий PR — определённый ответ), а **не** обрушенный батч.

- [ ] **Step 4: Записать леджер**

`manifest.md` фиксирует: пины (HEAD steward, версия CLI), точные команды, вывод, сравнение с v1-леджером, и **что именно доказано** — эквивалентность enforcement при смене формата, а не работоспособность парсера.

- [ ] **Step 5: Закрыть пункты плана**

В `TODO.md` §9 закрыть `approval-facts-external-contract` и `approval-facts-bundle-emission` со ссылкой на леджер; добавить хвост про fsync-долг `verdicts/emitter.py` (§10 спеки) и пункт-handoff для стадии 2 dispatcher.

- [ ] **Step 6: Коммит**

```bash
git add docs/evidence/2026-08-21-approval-facts-v2-migration TODO.md
git commit -m "docs: приёмка миграции approval-facts/v2 на реальных мержах"
```

---

## Self-Review

**Покрытие спеки.** §3 (v2 вместо v1) — задачи 1, 3 (`detect_legacy_v1`), 8 (правило пути). §4 (файл, header, result, инварианты, канонические байты) — задачи 1–3. §5 (продюсер, три дефекта, пагинация) — задача 4. §6 (транзакция, lease, scope-ограниченность) — задачи 6–7, читатель проверяет время в задаче 3. §7 (конфигурация lease) — задача 5. §8 (миграция гейта, два lookup, таблица, пути ввода, preflight) — задачи 6–8. §9 (приёмка) — тесты задач 1–8 плюс живой прогон задачи 9. §10 (хвосты) — шаг 5 задачи 9.

**Плейсхолдеры.** Ни одного «TBD», «add error handling» или «similar to Task N»: каждый шаг несёт либо код, либо точную команду с ожидаемым результатом. Два места намеренно описаны структурой, а не готовым кодом — тело CLI в задаче 7 и комбинатор в задаче 8: там существенен **порядок** шагов, и он выписан по пунктам, а копирование двухсот строк вокруг него сделало бы план менее проверяемым, чем спека, на которую он ссылается.

**Согласованность типов.** `RequestId(kind, value)`, `Result(request, state, merge_sha, identity, type_hint, actor_class)`, `Result.comparable`, `Header(...)`, `ApprovalFactsV2.by_merge_sha()` / `.scope_has_sha()`, `load_facts(path, *, expected_repository, now)`, `materialize(repo, scope)`, `resolve(owner, name, request)`, `publish(path, header, results)`, `resolve_bundle_target(repo, repo_root)`, `policy_digest(path)` — одни и те же имена во всех задачах, где встречаются.

**Порядок задач.** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9. Задача 2 удаляет старый модуль и временно отключает передачу фактов в гейт, чтобы набор оставался зелёным между задачами; задача 8 возвращает связь на новой модели. Каждая задача заканчивается независимо проверяемым результатом и коммитом.
