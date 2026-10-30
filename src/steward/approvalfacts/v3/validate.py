"""Fail-closed проверка фактов ревью (спека steward §3.4, §3.4.1, §3.6).

Одна функция на оба пути: чтение (`read_*`) и `effective_reviews` над фактами, собранными
вызывающим, — пути не расходятся. Проверка безусловна: не зависит от T и от того, влияет ли
нарушение на исход. Порядок строк — порядок проверки; отказ — по первой нарушенной.
"""

import re
from collections.abc import Iterator

from steward.approvalfacts.v3.model import (
    REVIEW_STATES,
    BadTimestamp,
    ReviewFacts,
    Unavailable,
    canon_ts,
    unavailable,
)

PREVIOUS_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "COMMENTED"})
ACTOR_TYPES = frozenset({"User", "Bot"})
_DECIMAL_ID = re.compile(r"0|[1-9][0-9]*", re.ASCII)


def validate_review_facts(facts: ReviewFacts) -> Unavailable | None:
    """Первое нарушенное правило фактов ревью — `Unavailable`; факты целы — `None`."""
    for code, detail in _violations(facts):
        return unavailable(code, detail)
    return None


def actor_resolved(login: object, type_hint: object) -> bool:
    """Актор установлен (§3.4.1): логин установлен и `type_hint` из {`User`, `Bot`}."""
    return login_resolved(login) and type_hint in ACTOR_TYPES


def login_resolved(login: object) -> bool:
    """Логин установлен: непустая строка, не `ghost` (без учёта регистра)."""
    return isinstance(login, str) and bool(login) and login.lower() != "ghost"


def _violations(facts: ReviewFacts) -> Iterator[tuple[str, str]]:
    """Нарушения в порядке проверки: `(code, detail)`."""
    reviews, dismissals = facts.reviews, facts.dismissals
    for review_id in [r.review_id for r in reviews] + [d.review_id for d in dismissals]:
        if not (isinstance(review_id, str) and _DECIMAL_ID.fullmatch(review_id)):
            yield "malformed", f"review_id {review_id!r} is not decimal"
    yield from (
        ("malformed", f"duplicate review {i}") for i in _duplicates([r.review_id for r in reviews])
    )
    for review in reviews:
        if review.submitted_at is not None and not _canonical(review.submitted_at):
            yield "bad_timestamp", f"review {review.review_id}: {review.submitted_at!r}"
        if not actor_resolved(review.login, review.type_hint):
            yield "actor_unresolved", f"review {review.review_id}"
        if review.state not in REVIEW_STATES:
            yield "unknown_state", f"review {review.review_id}: {review.state!r}"
        if not (isinstance(review.commit_id, str) and review.commit_id):
            yield "missing_commit_id", f"review {review.review_id}"
    for dismissal in dismissals:
        if not _canonical(dismissal.created_at):
            yield "bad_timestamp", f"dismissal {dismissal.review_id}: {dismissal.created_at!r}"
        # Тип актора отзыва в модели не хранится: его проверяет чтение до сборки `Dismissal`.
        if not login_resolved(dismissal.actor):
            yield "actor_unresolved", f"dismissal {dismissal.review_id}"
        previous = dismissal.previous_state
        if previous is not None and previous not in PREVIOUS_STATES:
            yield "unknown_state", f"dismissal {dismissal.review_id}: {previous!r}"
    yield from (("duplicate_dismissal", i) for i in _duplicates([d.review_id for d in dismissals]))
    states = {r.review_id: r.state for r in reviews}
    for dismissal in dismissals:
        if dismissal.review_id not in states:
            yield "orphan_dismissal", dismissal.review_id
    for review in reviews:
        if review.state != "PENDING" and review.submitted_at is None:
            yield "missing_submitted_at", f"review {review.review_id}"
    for dismissal in dismissals:
        if dismissal.previous_state is None:
            yield "dismissal_without_previous_state", dismissal.review_id
    dismissed = {d.review_id for d in dismissals}
    for review in reviews:
        if review.state == "DISMISSED" and review.review_id not in dismissed:
            yield "dismissed_without_event", f"review {review.review_id}"
    for dismissal in dismissals:
        if states.get(dismissal.review_id, "DISMISSED") != "DISMISSED":
            yield "malformed", f"dismissal of non-DISMISSED review {dismissal.review_id}"


def _duplicates(review_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for review_id in review_ids:
        if review_id in seen:
            repeated.append(review_id)
        seen.add(review_id)
    return repeated


def _canonical(stamp: object) -> bool:
    try:
        return canon_ts(stamp) == stamp
    except BadTimestamp:
        return False
