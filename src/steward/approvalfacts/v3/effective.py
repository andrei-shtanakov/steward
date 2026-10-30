"""Эффективное ревью по логину на момент T (спека steward §3.5, спека devtools §5.3).

Чистая логика над фактами `ReviewFacts`: сети нет. Метки сравниваются строками —
лексикографический порядок канона `YYYY-MM-DDTHH:MM:SSZ` совпадает с временным.
"""

from dataclasses import replace
from operator import itemgetter

from steward.approvalfacts.v3.model import (
    EFFECTIVE_STATES,
    Dismissal,
    EventKey,
    Review,
    ReviewFacts,
    Unavailable,
    canon_ts,
)
from steward.approvalfacts.v3.validate import validate_review_facts


def effective_reviews(
    facts: ReviewFacts, t: str, *, before: frozenset[EventKey]
) -> dict[str, Review] | Unavailable:
    """Эффективное ревью каждого логина на момент `t`: `login → Review` с состоянием на T.

    Событие с меткой `== t` — до T тогда и только тогда, когда его ключ в `before`
    (`("review", id)`, `("dismissal", review_id)`). Нарушение целостности фактов —
    `Unavailable` безусловно, до сравнения с T (`validate_review_facts`); `t` не в каноне —
    `ValueError`. Ключ результата — логин в GraphQL-форме (у бота без `[bot]`): пользователь
    и бот с одним логином делят ключ, побеждает последнее ревью, `type_hint` сохраняется.
    """
    if canon_ts(t) != t:
        raise ValueError(f"t is not canonical: {t!r}")
    broken = validate_review_facts(facts)
    if broken is not None:
        return broken
    dismissals = {d.review_id: d for d in facts.dismissals}
    latest: dict[str, Review] = {}
    for _, review in sorted(_submitted_before(facts.reviews, t, before), key=itemgetter(0)):
        state = _state_at(review, dismissals.get(review.review_id), t, before)
        if state in EFFECTIVE_STATES:
            latest[review.login] = replace(review, state=state)
    return latest


def _is_before(stamp: str, t: str, key: EventKey, before: frozenset[EventKey]) -> bool:
    return stamp < t or (stamp == t and key in before)


def _submitted_before(
    reviews: tuple[Review, ...], t: str, before: frozenset[EventKey]
) -> list[tuple[tuple[str, int], Review]]:
    """Отправленные ревью «до T» (PENDING без метки исключены) с ключом порядка «последнего»:
    `submitted_at`, при равенстве — `review_id` численно; id уникальны (`validate`)."""
    return [
        ((r.submitted_at, int(r.review_id)), r)
        for r in reviews
        if r.submitted_at is not None
        and _is_before(r.submitted_at, t, ("review", r.review_id), before)
    ]


def _state_at(
    review: Review, dismissal: Dismissal | None, t: str, before: frozenset[EventKey]
) -> str | None:
    """Шаг 1 §3.5: отзыв до T — `DISMISSED`, после T — `previous_state`, нет — `state`."""
    if dismissal is None:
        return review.state
    if _is_before(dismissal.created_at, t, ("dismissal", dismissal.review_id), before):
        return "DISMISSED"
    return dismissal.previous_state
