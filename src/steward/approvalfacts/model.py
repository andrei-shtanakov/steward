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

#: Версия схемы, которую этот пакет понимает (README, «Константы контракта»).
SCHEMA_VERSION = "2"

#: Допуск часов на будущее для `generated_at` (контракт, инвариант 10).
MAX_CLOCK_SKEW_SECONDS = 300

#: Верхняя граница заявленной длительности `valid_until - generated_at`
#: (контракт, инвариант 10) — 30 суток.
MAX_LEASE_SECONDS = 2_592_000


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
