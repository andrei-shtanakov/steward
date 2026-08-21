"""Читатель `approval-facts/v2`: разбор плюс одиннадцать инвариантов.

`complete: true` — заявление продюсера, не доказательство. Ни один
инвариант не пропускается «ради частичной пользы»: нарушение любого делает
файл целиком `unreadable`, потому что частично достоверный evidence в
enforcement неотличим от достоверного.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from steward.approvalfacts.model import (
    MAX_CLOCK_SKEW_SECONDS,
    MAX_LEASE_SECONDS,
    STATE_ONLY_FOR,
    ApprovalFactsV2,
    Header,
    RequestId,
    Result,
    scope_digest,
)

SCHEMA_VERSION = "2"

#: Матрица полей по `state` (README, «Матрица полей по state»): каждой записи
#: сопоставлены (обязателен ли resolved `merge_sha`, обязательны ли триплет
#: `identity`/`type_hint`/`actor_class`). Там, где поле не обязательно, оно
#: запрещено — «не требуется» значит «запрещено», как и в самой схеме.
_STATE_FIELD_RULES: dict[str, tuple[bool, bool]] = {
    "merged": (True, True),
    "actor_unavailable": (True, False),
    "not_merged": (False, False),
    "not_found": (False, False),
    "no_matching_pr": (False, False),
}

_ACTOR_CLASSES = {"human", "agent", "unknown"}

#: Единая каноническая форма провода (контракт, инвариант 8): UTC, суффикс
#: `Z`, секундная точность. Схема проверяет ту же форму через `pattern`; этот
#: читатель обязан быть не менее строгим, иначе schema и reader описывают
#: разные файлы.
_CANONICAL_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CANONICAL_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


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
    """Разобрать `generated_at`/`valid_until` по канонической форме провода.

    Форма (инвариант 8) проверяется регулярным выражением до парсинга —
    `+00:00`, строчная `z`, доли секунды всё это отклоняют, хотя
    `datetime.fromisoformat` их бы принял. Календарная валидность (инвариант,
    отдельный от формы: `2026-13-45T99:99:99Z` соответствует форме, но не
    существует) проверяется самим `strptime` — он валидирует диапазоны
    месяца/дня/часа/минуты/секунды и поднимает `ValueError`, если дата не
    существует (инвариант 3 руководства задачи).
    """
    if not isinstance(raw, str) or not _CANONICAL_TS_RE.match(raw):
        raise UnreadableFacts(
            f"{field}: ожидалась каноническая форма YYYY-MM-DDTHH:MM:SSZ, получено {raw!r}"
        )
    try:
        value = datetime.strptime(raw, _CANONICAL_TS_FORMAT)
    except ValueError as exc:
        raise UnreadableFacts(f"{field}: недопустимая календарная дата: {raw!r}") from exc
    return value.replace(tzinfo=UTC)


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

    # `complete` — заявление продюсера (§ README), а не доказательство: сама
    # заявка обязана быть `true`, иначе producer явно признаёт файл неполным.
    if raw_header.get("complete") is not True:
        raise UnreadableFacts(f"{path}: header.complete должен быть true")

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
            "merged",
            "not_merged",
            "not_found",
            "no_matching_pr",
            "actor_unavailable",
        }:
            raise UnreadableFacts(f"{path}:{number}: неизвестное state {state!r}")
        only_for = STATE_ONLY_FOR.get(str(state))
        if only_for is not None and request.kind != only_for:
            raise UnreadableFacts(
                f"{path}:{number}: state {state!r} допустимо только для request.kind "
                f"{only_for!r}, получено {request.kind!r}"
            )

        # Инвариант 7 — поля соответствуют state (матрица полей README).
        merge_sha_required, actor_required = _STATE_FIELD_RULES[str(state)]
        merge_sha = raw.get("merge_sha")
        if merge_sha_required and not isinstance(merge_sha, str):
            raise UnreadableFacts(f"{path}:{number}: state {state!r} требует resolved merge_sha")
        if not merge_sha_required and merge_sha is not None:
            raise UnreadableFacts(f"{path}:{number}: state {state!r} запрещает поле merge_sha")
        actor_fields = [
            ("identity", raw.get("identity")),
            ("type_hint", raw.get("type_hint")),
            ("actor_class", raw.get("actor_class")),
        ]
        if actor_required:
            missing_actor = [name for name, value in actor_fields if value is None]
            if missing_actor:
                raise UnreadableFacts(
                    f"{path}:{number}: state {state!r} требует поля {missing_actor}"
                )
            actor_class = raw.get("actor_class")
            if actor_class not in _ACTOR_CLASSES:
                raise UnreadableFacts(f"{path}:{number}: недопустимое actor_class {actor_class!r}")
        else:
            present_actor = [name for name, value in actor_fields if value is not None]
            if present_actor:
                raise UnreadableFacts(
                    f"{path}:{number}: state {state!r} запрещает поля {present_actor}"
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
