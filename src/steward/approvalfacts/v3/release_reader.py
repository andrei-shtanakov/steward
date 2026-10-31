"""Читатель файла release-гейта `approval-facts/v3` (спека steward §6). Пока неактивен.

Файл — `.steward/approval_facts.jsonl`, как у v2: строка 1 — `header` (поля v2,
`schema_version: "3"`), дальше по одной `result` на элемент scope. Поля мержа v2
(`merge_sha`, `identity`, `type_hint`, `actor_class`) переехали в `result.delivery`
(`delivery_record` спеки devtools §5.4). Одиннадцать инвариантов v2-читателя
(`steward.approvalfacts.reader`) переносятся построчно: header-инварианты и биекция — общим
кодом v2, матрица полей (инвариант 7) — по таблице §6:

| `state` | `delivery` |
|---|---|
| `merged` | объект, `merged: true`, `merged_by{identity, type_hint, actor_class}` |
| `actor_unavailable` | объект, `merged: true`, `merged_by: null` |
| `not_merged`, `not_found`, `no_matching_pr` | `null` |

Читатель строже `SCHEMA.json` в двух местах (как и v2-читатель, он «не менее строг», чем
схема): у `merge_sha`-запроса `delivery.merge_commit` обязан быть запрошенным SHA —
иначе `by_merge_sha()` проиндексировал бы чужой SHA; `merged_by.identity` — установленный
по §3.4.1 актор (без `ghost`, `[bot]`, `:`). Оба свойства гарантирует продюсер. `not_found`
у `pr`-запроса продюсер v3 больше не выпускает (§3.2: отсутствующий PR — сбой), но схема и
матрица v2 его допускают, и читатель — тоже: ужесточать только здесь значило бы разойтись
со схемой.

Здесь же — модель файла (`ReleaseResult`, `ApprovalFactsV3`): гейт импортирует только этот
модуль, а он не импортирует ни политику (`steward.gatecheck`), ни продюсеры — иначе
`gatecheck.approval` → читатель → продюсер → `gatecheck.approval` замкнулся бы в цикл.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from steward.approvalfacts.model import (
    SCHEMA_VERSION as SCHEMA_VERSION_V2,
    ActorType,
    Header,
    ObservationState,
    RequestId,
)
from steward.approvalfacts.reader import (
    _MERGE_SHA_RE,
    UnreadableFacts,
    check_bijection,
    check_state_for_kind,
    parse_header,
    parse_request,
    read_records,
)
from steward.approvalfacts.v3.model import BadTimestamp, Delivery, canon_ts
from steward.approvalfacts.v3.validate import actor_resolved

#: Версия схемы файла release-гейта v3 (`contracts/approval-facts/v3/SCHEMA.json`, `header`).
SCHEMA_VERSION_V3 = "3"

_RESULT_KEYS = frozenset({"kind", "request", "state", "delivery"})
_DELIVERY_KEYS = frozenset(
    {
        "merged",
        "merge_commit",
        "merged_at",
        "merged_by",
        "head_at_merge",
        "in_base",
        "paths",
        "complete",
    }
)
_MERGED_BY_KEYS = frozenset({"identity", "type_hint", "actor_class"})
_ACTOR_CLASSES = frozenset({"human", "agent", "unknown"})
#: `state` → требуется ли мержер в `delivery`; `None` — `delivery` обязан быть `null`.
_MERGER_REQUIRED: dict[str, bool | None] = {
    "merged": True,
    "actor_unavailable": False,
    "not_merged": None,
    "not_found": None,
    "no_matching_pr": None,
}
_IDENTITY_PREFIX = "github:"
_NO_WHITESPACE = re.compile(r"\S+")


@dataclass(frozen=True)
class ReleaseResult:
    """Терминальное наблюдение по элементу scope: состояние v2 и доставка (спека §6).

    `delivery` — у `merged` и `actor_unavailable` (у второго без мержера), иначе `None`;
    `actor_class` — классификация мержера `merged` по политике (`None` до классификации).
    """

    request: RequestId
    state: ObservationState
    delivery: Delivery | None
    actor_class: ActorType | None


@dataclass(frozen=True)
class ApprovalFactsV3:
    """Проверенный файл release-гейта v3."""

    header: Header
    results: tuple[ReleaseResult, ...]

    def by_merge_sha(self) -> dict[str, ReleaseResult]:
        """Индекс по `delivery.merge_commit`; записи без доставки сюда не попадают."""
        return {
            r.delivery.merge_commit: r
            for r in self.results
            if r.delivery is not None and r.delivery.merge_commit is not None
        }

    def scope_has_sha(self, sha: str) -> bool:
        """Входит ли `{kind: merge_sha, value: sha}` в объявленный scope."""
        return any(r.kind == "merge_sha" and r.value == sha for r in self.header.scope)


def detect_legacy_v2(path: Path) -> bool:
    """Header файла — `approval-facts/v2` (`schema_version: "2"`); иначе, в т.ч. для
    нечитаемого файла, — `False` (такой файл отвергнет сам `load_release_facts`)."""
    try:
        with Path(path).open(encoding="utf-8") as handle:
            first = json.loads(handle.readline())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(first, dict)
        and first.get("kind") == "header"
        and first.get("schema_version") == SCHEMA_VERSION_V2
    )


def load_release_facts(path: Path, *, expected_repository: str, now: datetime) -> ApprovalFactsV3:
    """Прочитать и полностью проверить файл release-гейта v3; нарушение — `UnreadableFacts`."""
    if detect_legacy_v2(path):
        raise UnreadableFacts(
            f"{path}: approval-facts/v2 (schema_version {SCHEMA_VERSION_V2!r}) — release-гейт "
            "читает v3; перевыпустите факты "
            "командой `steward approval-facts`"
        )
    records = read_records(path)
    header = parse_header(
        records,
        path,
        schema_version=SCHEMA_VERSION_V3,
        expected_repository=expected_repository,
        now=now,
    )
    results = [_result(raw, number, path) for number, raw in enumerate(records[1:], start=2)]
    check_bijection(header.scope, [r.request for r in results], path)
    _check_aliases(results, path)
    return ApprovalFactsV3(header=header, results=tuple(results))


def _result(raw: dict, number: int, path: Path) -> ReleaseResult:
    where = f"{path}:{number}"
    if set(raw) != _RESULT_KEYS:
        missing, extra = sorted(_RESULT_KEYS - set(raw)), sorted(set(raw) - _RESULT_KEYS)
        raise UnreadableFacts(
            f"{where}: неверный набор полей результата — не хватает {missing}, лишние {extra}"
        )
    if raw["kind"] != "result":
        raise UnreadableFacts(f"{where}: неизвестный kind {raw['kind']!r}")
    request = parse_request(raw["request"], where)
    state = raw["state"]
    if not isinstance(state, str) or state not in _MERGER_REQUIRED:
        raise UnreadableFacts(f"{where}: неизвестное состояние {state!r}")
    check_state_for_kind(state, request, number, path)
    merger_required = _MERGER_REQUIRED[state]
    if merger_required is None:
        if raw["delivery"] is not None:
            raise UnreadableFacts(f"{where}: состояние {state!r} требует delivery: null")
        return ReleaseResult(request, state, None, None)
    delivery, actor_class = _delivery(raw["delivery"], merger_required, f"{where} ({state})")
    if request.kind == "merge_sha" and delivery.merge_commit != request.value:
        raise UnreadableFacts(
            f"{where}: delivery.merge_commit {delivery.merge_commit!r} — не запрошенный "
            f"merge_sha {request.value!r}"
        )
    return ReleaseResult(request, state, delivery, actor_class)


def _delivery(raw: object, merger_required: bool, where: str) -> tuple[Delivery, ActorType | None]:
    """`delivery_record` смёрженного PR в файле гейта: `merged: true`, `paths: []`, мержер —
    по `merger_required`. Возвращает доставку и `actor_class` мержера."""
    if not isinstance(raw, dict) or set(raw) != _DELIVERY_KEYS:
        raise UnreadableFacts(f"{where}: delivery обязан быть объектом delivery_record")
    if raw["merged"] is not True:
        raise UnreadableFacts(f"{where}: delivery.merged обязан быть true")
    if raw["complete"] is not True:
        raise UnreadableFacts(f"{where}: delivery.complete обязан быть true")
    if raw["paths"] != []:
        raise UnreadableFacts(f"{where}: delivery.paths в файле гейта обязан быть []")
    if not isinstance(raw["in_base"], bool):
        raise UnreadableFacts(f"{where}: delivery.in_base обязан быть boolean")
    login, type_hint, actor_class = _merged_by(raw["merged_by"], merger_required, where)
    delivery = Delivery(
        merged=True,
        merge_commit=_sha(raw["merge_commit"], f"{where}: delivery.merge_commit"),
        merged_at=_stamp(raw["merged_at"], f"{where}: delivery.merged_at"),
        merged_by_login=login,
        merged_by_type_hint=type_hint,
        head_at_merge=_sha(raw["head_at_merge"], f"{where}: delivery.head_at_merge"),
        in_base=raw["in_base"],
        paths=(),
    )
    return delivery, actor_class


def _merged_by(
    raw: object, required: bool, where: str
) -> tuple[str | None, str | None, ActorType | None]:
    """Мержер `merged` — `{identity: github:<login>, type_hint, actor_class}` установленного
    актора (§3.4.1) с классом из словаря; у `actor_unavailable` — `null`."""
    if not required:
        if raw is not None:
            raise UnreadableFacts(f"{where}: delivery.merged_by обязан быть null")
        return None, None, None
    if not isinstance(raw, dict) or set(raw) != _MERGED_BY_KEYS:
        raise UnreadableFacts(
            f"{where}: delivery.merged_by обязан быть {{identity, type_hint, actor_class}}"
        )
    identity, type_hint, actor_class = raw["identity"], raw["type_hint"], raw["actor_class"]
    login = identity.removeprefix(_IDENTITY_PREFIX) if isinstance(identity, str) else None
    if login is None or login == identity or not _NO_WHITESPACE.fullmatch(login):
        raise UnreadableFacts(f"{where}: merged_by.identity {identity!r} — не github:<login>")
    if not actor_resolved(login, "User"):
        raise UnreadableFacts(f"{where}: merged_by.identity {identity!r} — актор не установлен")
    if not actor_resolved(login, type_hint):
        raise UnreadableFacts(f"{where}: merged_by.type_hint {type_hint!r} вне словаря")
    if not isinstance(actor_class, str) or actor_class not in _ACTOR_CLASSES:
        raise UnreadableFacts(f"{where}: merged_by.actor_class {actor_class!r} вне словаря")
    return login, type_hint, actor_class


def _sha(raw: object, where: str) -> str:
    if not isinstance(raw, str) or not _MERGE_SHA_RE.fullmatch(raw):
        raise UnreadableFacts(f"{where}: ожидался 40-символьный hex, получено {raw!r}")
    return raw


def _stamp(raw: object, where: str) -> str:
    """Метка в каноне провода `YYYY-MM-DDTHH:MM:SSZ` (инвариант 8) — ровно, без `+00:00`."""
    try:
        canonical = canon_ts(raw)
    except BadTimestamp as exc:
        raise UnreadableFacts(f"{where}: {exc}") from exc
    if canonical != raw:
        raise UnreadableFacts(f"{where}: не каноническая метка {raw!r}")
    return canonical


def _check_aliases(results: list[ReleaseResult], path: Path) -> None:
    """Инвариант 9: алиасы одного мержа (`pr:N` и `merge_sha:<oid>`) — одно наблюдение;
    сравнивается запись без `request`, ключ — `delivery.merge_commit`."""
    by_sha: dict[str, ReleaseResult] = {}
    for result in results:
        if result.delivery is None or result.delivery.merge_commit is None:
            continue
        sha = result.delivery.merge_commit
        twin = by_sha.setdefault(sha, result)
        if _comparable(twin) != _comparable(result):
            raise UnreadableFacts(f"{path}: алиасы одного мержа {sha} дают разные наблюдения")


def _comparable(r: ReleaseResult) -> tuple[object, ...]:
    return (r.state, r.delivery, r.actor_class)
