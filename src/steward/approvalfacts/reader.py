"""Читатель `approval-facts/v2`: разбор плюс одиннадцать инвариантов.

`complete: true` — заявление продюсера, не доказательство. Ни один
инвариант не пропускается «ради частичной пользы»: нарушение любого делает
файл целиком `unreadable`, потому что частично достоверный evidence в
enforcement неотличим от достоверного.

Этот модуль обязан быть не менее строгим, чем `SCHEMA.json`: там, где схема
отвергает запись, читатель обязан отвергать её тоже — иначе схема и читатель
описывают разные файлы. Схема проверяет форму записи по отдельности; читатель
дополнительно проверяет то, что схема не может выразить (биекцию scope↔result,
согласованность алиасов, время, репозиторий).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from steward.approvalfacts.model import (
    MAX_CLOCK_SKEW_SECONDS,
    MAX_LEASE_SECONDS,
    SCHEMA_VERSION,
    STATE_ONLY_FOR,
    ApprovalFactsV2,
    Header,
    RequestId,
    Result,
    scope_digest,
)

#: Матрица полей по `state` (README, «Матрица полей по state»): каждому
#: состоянию сопоставлены — обязательный JSON-тип поля `merge_sha` (оно
#: обязано присутствовать ВСЕГДА, просто с разным типом: resolved oid как
#: строка для `merged`/`actor_unavailable`, явный `null` для остальных трёх)
#: и обязателен ли триплет `identity`/`type_hint`/`actor_class`. Там, где
#: триплет не обязателен, он запрещён — «не требуется» значит «запрещено»,
#: как и в самой схеме. Ключи этого словаря — единственный источник истины
#: о допустимых значениях `state` в этом модуле.
_STATE_FIELD_RULES: dict[str, tuple[str, bool]] = {
    "merged": ("string", True),
    "actor_unavailable": ("string", False),
    "not_merged": ("null", False),
    "not_found": ("null", False),
    "no_matching_pr": ("null", False),
}

_ACTOR_CLASSES = frozenset({"human", "agent", "unknown"})

#: Закрытый набор ключей `header` — все перечисленные в схеме свойства
#: обязательны, лишних не бывает (`additionalProperties: false`).
_HEADER_KEYS = frozenset(
    {
        "kind",
        "schema_version",
        "repository",
        "generated_at",
        "valid_until",
        "policy_version",
        "policy_digest",
        "complete",
        "scope_sha256",
        "scope",
    }
)

#: `result` — базовые поля обязательны всегда; `merge_sha`/`identity`/
#: `type_hint`/`actor_class` обязательны или запрещены по `_STATE_FIELD_RULES`,
#: но ни одного постороннего ключа сверх этого набора схема не допускает.
_RESULT_BASE_KEYS = frozenset({"kind", "request", "state"})
_RESULT_OPTIONAL_KEYS = frozenset({"merge_sha", "identity", "type_hint", "actor_class"})
_RESULT_ALLOWED_KEYS = _RESULT_BASE_KEYS | _RESULT_OPTIONAL_KEYS

_MERGE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_POLICY_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY_RE = re.compile(r"^[^/]+/[^/]+$")

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
    `datetime.fromisoformat` их бы принял. Календарная валидность (отдельная
    от формы: `2026-13-45T99:99:99Z` соответствует форме, но не существует)
    проверяется самим `strptime` — он валидирует диапазоны
    месяца/дня/часа/минуты/секунды и поднимает `ValueError`, если дата не
    существует.
    """
    if not isinstance(raw, str) or not _CANONICAL_TS_RE.match(raw):
        raise UnreadableFacts(
            f"{field}: ожидалась каноническая форма провода YYYY-MM-DDTHH:MM:SSZ, получено {raw!r}"
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
    if kind == "pr" and isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return RequestId("pr", value)
    if kind == "merge_sha" and isinstance(value, str) and _MERGE_SHA_RE.fullmatch(value):
        return RequestId("merge_sha", value)
    raise UnreadableFacts(f"{where}: недопустимая пара (kind, value): {raw!r}")


def _normalize_repo(name: str) -> str:
    return name.strip().lower()


def _parse_record(raw: object, number: int, path: Path) -> dict:
    """Строка обязана разбираться в JSON-объект — иначе `.get()` ниже упал бы
    с `AttributeError`, что нарушает fail-closed контракт модуля (любой
    отказ обязан быть `UnreadableFacts`, а не случайное исключение Python)."""
    if not isinstance(raw, dict):
        raise UnreadableFacts(
            f"{path}:{number}: запись должна быть JSON-объектом, получено {type(raw).__name__}"
        )
    return raw


def _validate_header_shape(raw_header: dict, path: Path) -> None:
    """Закрытый набор ключей header (инвариант, аналог `additionalProperties:
    false` схемы). Также закрывает `KeyError` на `policy_version`/
    `policy_digest`, отсутствующих в заявке продюсера — их нехватка теперь
    даёт `UnreadableFacts`, а не падение при индексации ниже."""
    if set(raw_header) != _HEADER_KEYS:
        missing = sorted(_HEADER_KEYS - set(raw_header))
        extra = sorted(set(raw_header) - _HEADER_KEYS)
        raise UnreadableFacts(
            f"{path}: header содержит неверный набор полей — не хватает {missing}, лишние {extra}"
        )


def _validate_policy_fields(raw_header: dict, path: Path) -> tuple[int, str]:
    policy_version = raw_header["policy_version"]
    if (
        not isinstance(policy_version, int)
        or isinstance(policy_version, bool)
        or policy_version < 1
    ):
        raise UnreadableFacts(
            f"{path}: policy_version обязан быть целым числом ≥ 1, получено {policy_version!r}"
        )
    policy_digest = raw_header["policy_digest"]
    if not isinstance(policy_digest, str) or not _POLICY_DIGEST_RE.fullmatch(policy_digest):
        raise UnreadableFacts(
            f"{path}: policy_digest обязан соответствовать форме sha256:<64 hex>, "
            f"получено {policy_digest!r}"
        )
    return policy_version, policy_digest


def _validate_result_fields(
    raw: dict, state: str, number: int, path: Path
) -> tuple[str | None, str | None, str | None, str | None]:
    """Инвариант 7: поля соответствуют state (README, «Матрица полей по
    state»). Возвращает `(merge_sha, identity, type_hint, actor_class)`.

    Присутствие проверяется через членство ключа в записи (`in raw`), а не
    через значение (`raw.get(...) is not None`) — иначе `"identity": null`
    (ключ присутствует со значением `null`) прошёл бы как «поле отсутствует»,
    хотя схема запрещает сам ключ на отрицательных состояниях."""
    merge_sha_type, actor_required = _STATE_FIELD_RULES[state]

    if "merge_sha" not in raw:
        raise UnreadableFacts(
            f"{path}:{number}: состояние {state!r} требует явное поле merge_sha "
            f"(resolved oid или null, но не отсутствие ключа)"
        )
    merge_sha = raw["merge_sha"]
    if merge_sha_type == "string":
        if not isinstance(merge_sha, str) or not _MERGE_SHA_RE.fullmatch(merge_sha):
            raise UnreadableFacts(
                f"{path}:{number}: merge_sha обязан быть 40-символьным hex, получено {merge_sha!r}"
            )
    elif merge_sha is not None:
        raise UnreadableFacts(
            f"{path}:{number}: состояние {state!r} требует merge_sha: null, получено {merge_sha!r}"
        )

    actor_keys = ("identity", "type_hint", "actor_class")
    if actor_required:
        missing_actor = [name for name in actor_keys if name not in raw]
        if missing_actor:
            raise UnreadableFacts(
                f"{path}:{number}: состояние {state!r} требует поля актора {missing_actor}"
            )
        identity, type_hint, actor_class = raw["identity"], raw["type_hint"], raw["actor_class"]
        if not isinstance(identity, str) or not isinstance(type_hint, str):
            raise UnreadableFacts(f"{path}:{number}: identity и type_hint обязаны быть строками")
        if actor_class not in _ACTOR_CLASSES:
            raise UnreadableFacts(
                f"{path}:{number}: недопустимое значение actor_class {actor_class!r}"
            )
        return merge_sha, identity, type_hint, actor_class

    present_actor = [name for name in actor_keys if name in raw]
    if present_actor:
        raise UnreadableFacts(
            f"{path}:{number}: состояние {state!r} запрещает поля актора {present_actor}"
        )
    return merge_sha, None, None, None


def load_facts(path: Path, *, expected_repository: str, now: datetime) -> ApprovalFactsV2:
    """Прочитать и полностью проверить файл фактов."""
    if detect_legacy_v1(path):
        raise UnreadableFacts(f"{path}: обнаружен устаревший approval-facts/v1 — не читается")
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise UnreadableFacts(f"{path}: не читается: {exc}") from exc
    if not lines:
        raise UnreadableFacts(f"{path}: пустой файл")

    records: list[dict] = []
    for number, line in enumerate(lines, start=1):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise UnreadableFacts(f"{path}:{number}: не JSON: {exc}") from exc
        records.append(_parse_record(parsed, number, path))

    # Инвариант 1 — header первой и только первой строкой. Count и position
    # проверяются раздельно (не как один составной тест на индекс 0), иначе
    # отключение любой из двух проверок маскируется другой: файл без header
    # на позиции 0 будет отвергнут проверкой "ровно один header" всегда,
    # когда header всё-таки где-то в файле есть, даже если проверка позиции
    # отключена.
    header_positions = [i for i, r in enumerate(records) if r.get("kind") == "header"]
    if len(header_positions) != 1:
        raise UnreadableFacts(
            f"{path}: header обязан присутствовать ровно один раз, найдено {len(header_positions)}"
        )
    if header_positions[0] != 0:
        raise UnreadableFacts(f"{path}: header обязан быть первой строкой файла")
    raw_header = records[0]

    # schema_version проверяется ДО закрытого набора ключей: будущий v3-header
    # почти наверняка несёт другой набор полей, и без этого порядка он был бы
    # диагностирован как «неверный набор полей», а не как «неверная версия» —
    # README требует от потребителя явно классифицировать именно версию.
    # `.get()`, а не `[...]`: набор ключей ещё не проверен закрытым, версии
    # может не быть вовсе.
    if raw_header.get("schema_version") != SCHEMA_VERSION:
        raise UnreadableFacts(
            f"{path}: неверный schema_version {raw_header.get('schema_version')!r}, "
            f"ожидался {SCHEMA_VERSION!r}"
        )
    _validate_header_shape(raw_header, path)

    # `complete` — заявление продюсера (§ README), а не доказательство: сама
    # заявка обязана быть `true`, иначе producer явно признаёт файл неполным.
    if raw_header["complete"] is not True:
        raise UnreadableFacts(f"{path}: поле complete обязано быть true")

    # Инвариант 11 — совпадение с наблюдаемым репозиторием (внешнее ожидание).
    # Форма (owner/repo) проверяется отдельно от совпадения: без этого
    # `repository: "steward"` при `expected_repository="steward"` (оба без
    # `/`) совпали бы друг с другом, хотя схема такую форму отвергает —
    # реальный владелец/репо не мог бы называться так в принципе.
    repository = raw_header["repository"]
    if not isinstance(repository, str) or not _REPOSITORY_RE.fullmatch(repository):
        raise UnreadableFacts(
            f"{path}: репозиторий в заголовке {repository!r} не соответствует форме owner/repo"
        )
    if _normalize_repo(repository) != _normalize_repo(expected_repository):
        raise UnreadableFacts(
            f"{path}: репозиторий в заголовке {repository!r} не совпадает с ожидаемым "
            f"{expected_repository!r}"
        )

    policy_version, policy_digest = _validate_policy_fields(raw_header, path)

    # Инвариант 2 — набор запросов непуст и без дублей.
    raw_scope = raw_header["scope"]
    if not isinstance(raw_scope, list) or not raw_scope:
        raise UnreadableFacts(f"{path}: заявленный набор запросов не может быть пустым")
    scope = [_request(item, f"{path}: scope") for item in raw_scope]
    scope_set = set(scope)
    if len(scope_set) != len(scope):
        raise UnreadableFacts(f"{path}: заявленный набор запросов содержит дублирующиеся элементы")

    # Инвариант 10 — время.
    generated_at = _parse_ts(raw_header["generated_at"], f"{path}: generated_at")
    valid_until = _parse_ts(raw_header["valid_until"], f"{path}: valid_until")
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
    if raw_header["scope_sha256"] != scope_digest(scope):
        raise UnreadableFacts(
            f"{path}: контрольная сумма scope_sha256 не совпадает с вычисленной по scope"
        )

    results: list[Result] = []
    for number, raw in enumerate(records[1:], start=2):
        if not _RESULT_BASE_KEYS <= set(raw):
            raise UnreadableFacts(
                f"{path}:{number}: в записи результата не хватает обязательных полей"
            )
        if not set(raw) <= _RESULT_ALLOWED_KEYS:
            unknown = sorted(set(raw) - _RESULT_ALLOWED_KEYS)
            raise UnreadableFacts(f"{path}:{number}: неизвестные поля результата: {unknown}")
        if raw["kind"] != "result":
            raise UnreadableFacts(f"{path}:{number}: неизвестный kind {raw['kind']!r}")
        request = _request(raw["request"], f"{path}:{number}")
        state = raw["state"]
        if not isinstance(state, str) or state not in _STATE_FIELD_RULES:
            raise UnreadableFacts(f"{path}:{number}: неизвестное состояние {state!r}")
        only_for = STATE_ONLY_FOR.get(state)
        if only_for is not None and request.kind != only_for:
            raise UnreadableFacts(
                f"{path}:{number}: состояние {state!r} допустимо только для "
                f"request.kind {only_for!r}, получено {request.kind!r}"
            )

        merge_sha, identity, type_hint, actor_class = _validate_result_fields(
            raw, state, number, path
        )
        results.append(
            Result(
                request=request,
                state=state,  # type: ignore[arg-type]  # проверено выше
                merge_sha=merge_sha,
                identity=identity,
                type_hint=type_hint,
                actor_class=actor_class,  # type: ignore[arg-type]  # проверено выше
            )
        )

    # Инварианты 3–6 — биекция scope ↔ results по идентичности (kind, value).
    seen: dict[RequestId, Result] = {}
    for result in results:
        if result.request in seen:
            raise UnreadableFacts(f"{path}: обнаружен повторный результат для {result.request}")
        if result.request not in scope_set:
            raise UnreadableFacts(
                f"{path}: результат ссылается на незаявленный элемент {result.request}"
            )
        seen[result.request] = result
    missing = [item for item in scope if item not in seen]
    if missing:
        raise UnreadableFacts(f"{path}: не хватает результата для заявленных элементов: {missing}")

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
        policy_version=policy_version,
        policy_digest=policy_digest,
        scope=tuple(scope),
        scope_sha256=raw_header["scope_sha256"],
    )
    return ApprovalFactsV2(header=header, results=tuple(results))
