"""Эффективное ревью по логину на момент T (спека steward §3.5, спека devtools §5.3).

Чистая логика над фактами `ReviewFacts`: сети нет. Метки сравниваются строками —
лексикографический порядок канона `YYYY-MM-DDTHH:MM:SSZ` совпадает с временным.
"""

from dataclasses import replace

from steward.approvalfacts.v3.model import (
    EFFECTIVE_STATES,
    Dismissal,
    EventKey,
    Review,
    ReviewFacts,
    Unavailable,
    canon_ts,
    unavailable,
)


def effective_reviews(
    facts: ReviewFacts, t: str, *, before: frozenset[EventKey]
) -> dict[str, Review] | Unavailable:
    """Эффективное ревью каждого логина на момент `t`: `login → Review` с состоянием на T.

    Событие с меткой `== t` — до T тогда и только тогда, когда его ключ в `before`
    (`("review", id)`, `("dismissal", review_id)`). Нарушение целостности фактов —
    `Unavailable` безусловно, до сравнения с T; `t` не в каноне — `ValueError`.
    """
    if canon_ts(t) != t:
        raise ValueError(f"t is not canonical: {t!r}")
    broken = _integrity(facts)
    if broken is not None:
        return broken
    dismissals = {d.review_id: d for d in facts.dismissals}
    latest: dict[str, Review] = {}
    for review in sorted(_submitted_before(facts.reviews, t, before), key=_order):
        state = _state_at(review, dismissals.get(review.review_id), t, before)
        if state in EFFECTIVE_STATES:
            latest[review.login] = replace(review, state=state)
    return latest


def _integrity(facts: ReviewFacts) -> Unavailable | None:
    """Шаг 0 §3.5: отказы, не зависящие от T и от исхода."""
    if any(d.previous_state is None for d in facts.dismissals):
        return unavailable("dismissal_without_previous_state")
    if any(r.state != "PENDING" and r.submitted_at is None for r in facts.reviews):
        return unavailable("missing_submitted_at")
    dismissed_ids = {d.review_id for d in facts.dismissals}
    if any(r.state == "DISMISSED" and r.review_id not in dismissed_ids for r in facts.reviews):
        return unavailable("dismissed_without_event")
    return None


def _is_before(stamp: str, t: str, key: EventKey, before: frozenset[EventKey]) -> bool:
    return stamp < t or (stamp == t and key in before)


def _submitted_before(
    reviews: tuple[Review, ...], t: str, before: frozenset[EventKey]
) -> list[Review]:
    """Отправленные ревью «до T»; PENDING без метки исключены."""
    return [
        r
        for r in reviews
        if r.submitted_at is not None
        and _is_before(r.submitted_at, t, ("review", r.review_id), before)
    ]


def _order(review: Review) -> tuple[str, int]:
    """Порядок «последнего»: `submitted_at`, при равенстве — `review_id` численно."""
    assert review.submitted_at is not None
    return review.submitted_at, int(review.review_id)


def _state_at(
    review: Review, dismissal: Dismissal | None, t: str, before: frozenset[EventKey]
) -> str | None:
    """Шаг 1 §3.5: отзыв до T — `DISMISSED`, после T — `previous_state`, нет — `state`."""
    if dismissal is None:
        return review.state
    if _is_before(dismissal.created_at, t, ("dismissal", dismissal.review_id), before):
        return "DISMISSED"
    return dismissal.previous_state
