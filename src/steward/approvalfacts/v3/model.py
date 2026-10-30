"""Типы фактов `approval-facts/v3`, закрытые словари причин, канон меток времени."""

import re
from dataclasses import dataclass
from datetime import datetime

EventKey = tuple[str, str]  # ("review", "<review_id>") | ("dismissal", "<review_id>")

REVIEW_STATES = frozenset({"PENDING", "COMMENTED", "APPROVED", "CHANGES_REQUESTED", "DISMISSED"})
EFFECTIVE_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})

UNAVAILABLE_CODES = frozenset(
    {
        "transport", "http", "graphql_errors", "malformed", "pagination", "bad_timestamp",
        "unknown_state", "missing_commit_id", "missing_submitted_at",
        "dismissal_without_previous_state", "dismissed_without_event", "duplicate_dismissal",
        "orphan_dismissal", "delivery_mismatch", "bot_login_mismatch", "unstable",
        "object_missing", "path_absent_at_head", "not_a_blob", "chain_limit",
        "branch_not_default", "no_pr_for_commit", "tree_truncated", "actor_unresolved",
    }
)  # fmt: skip
HISTORY_VIOLATION_CODES = frozenset(
    {"commit_without_pr", "rebase_intermediate", "pr_base_mismatch", "anchor_not_reached"}
)

_TS_PATTERN = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(Z|\+00:00)", re.ASCII
)
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S"


class BadTimestamp(ValueError):
    """Метка времени не в канонической форме `YYYY-MM-DDTHH:MM:SSZ` (или `+00:00`)."""


def canon_ts(raw: object) -> str:
    """Привести метку форджа к канону `YYYY-MM-DDTHH:MM:SSZ`.

    `+00:00` нормализуется в `Z`; доли секунды, метки без зоны, иные смещения и
    не-строки — `BadTimestamp`.
    """
    match = _TS_PATTERN.fullmatch(raw) if isinstance(raw, str) else None
    if match is None:
        raise BadTimestamp(f"not a canonical timestamp: {raw!r}")
    body = match.group(1)
    try:
        datetime.strptime(body, _TS_FORMAT)
    except ValueError as exc:
        raise BadTimestamp(f"not a valid timestamp: {raw!r}") from exc
    return f"{body}Z"


def _code_of(reason: str) -> str:
    return reason.split(":", 1)[0]


def _check_code(reason: str, vocabulary: frozenset[str]) -> None:
    """Причина — `<code>` или `<code>: <detail>` с непустым detail, code из словаря."""
    code, sep, detail = reason.partition(": ")
    if code not in vocabulary or (sep and not detail):
        raise ValueError(f"bad reason: {reason!r}")


@dataclass(frozen=True)
class Review:
    """Ревью PR: `login` в GraphQL-форме (у бота без `[bot]`), `submitted_at` — канон."""

    review_id: str
    login: str
    type_hint: str
    state: str
    commit_id: str
    submitted_at: str | None


@dataclass(frozen=True)
class Dismissal:
    """Событие отзыва ревью; `previous_state is None` — отказ в `effective_reviews`."""

    review_id: str
    actor: str
    created_at: str
    previous_state: str | None


@dataclass(frozen=True)
class ReviewFacts:
    """Ревью и отзывы одного PR в одном снимке чтения."""

    repository: str
    pr: int
    head_sha: str
    base_ref: str
    pr_state: str
    reviews: tuple[Review, ...]
    dismissals: tuple[Dismissal, ...]


@dataclass(frozen=True)
class PathBlob:
    """Blob пути в merge-коммите (None — установленно отсутствует) и на голове PR."""

    path: str
    blob_at_merge: str | None
    blob_at_head: str


@dataclass(frozen=True)
class Delivery:
    """Факты доставки PR: мерж, мержер, голова на момент мержа, blob'ы путей."""

    merged: bool
    merge_commit: str | None
    merged_at: str | None
    merged_by_login: str | None
    merged_by_type_hint: str | None
    head_at_merge: str | None
    in_base: bool
    paths: tuple[PathBlob, ...]


@dataclass(frozen=True)
class Unavailable:
    """Факт недоступен: `reason` — `<code>` или `<code>: <detail>`, code из словаря."""

    reason: str

    def __post_init__(self) -> None:
        _check_code(self.reason, UNAVAILABLE_CODES)

    @property
    def code(self) -> str:
        """Часть `reason` до первого `":"`."""
        return _code_of(self.reason)


@dataclass(frozen=True)
class Activation:
    """Активация политики: PR, merge-коммит, sha256 файлов политики (hex без префикса)."""

    pr: int
    merge_commit: str
    merged_at: str
    files_sha256: dict[str, str]


@dataclass(frozen=True)
class HistoryViolation:
    """Нарушение истории источника политики: `<code>: <detail>`, code из словаря."""

    reason: str

    def __post_init__(self) -> None:
        _check_code(self.reason, HISTORY_VIOLATION_CODES)

    @property
    def code(self) -> str:
        """Часть `reason` до первого `":"`."""
        return _code_of(self.reason)


def unavailable(code: str, detail: str = "") -> Unavailable:
    """Собрать `Unavailable`; код вне словаря — `ValueError`."""
    return Unavailable(f"{code}: {detail}" if detail else code)


def violation(code: str, detail: str) -> HistoryViolation:
    """Собрать `HistoryViolation`; код вне словаря — `ValueError`."""
    return HistoryViolation(f"{code}: {detail}")
