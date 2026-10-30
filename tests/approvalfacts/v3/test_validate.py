"""`validate_review_facts`: инварианты фактов ревью (спека steward §3.4, §3.4.1, §3.6).

Одна строка таблицы инвариантов — один отрицательный тест (`ROWS`); положительный
двойник — `BASE`, собранный из тех же ревью и отзывов без нарушения. Каждая строка
сверяется и на `effective_reviews`: проверка одна, пути не расходятся.
"""

from dataclasses import replace

import pytest

from steward.approvalfacts.v3.effective import effective_reviews
from steward.approvalfacts.v3.model import Dismissal, Review, ReviewFacts
from steward.approvalfacts.v3.validate import actor_resolved, validate_review_facts

T = "2026-10-12T10:00:00Z"
SHA = "a" * 40
ALICE = Review("1", "alice", "User", "APPROVED", SHA, "2026-10-12T09:00:00Z")
BOB = Review("2", "bob", "User", "DISMISSED", SHA, "2026-10-12T09:10:00Z")
BOT = Review("3", "copilot", "Bot", "COMMENTED", SHA, "2026-10-12T09:20:00Z")
DRAFT = Review("4", "dave", "User", "PENDING", SHA, None)
BOB_OUT = Dismissal("2", "carol", "2026-10-12T09:30:00Z", "CHANGES_REQUESTED")
BASE = ReviewFacts("o/r", 1, SHA, "master", "OPEN", (ALICE, BOB, BOT, DRAFT), (BOB_OUT,))


def _with(**reviews_or_dismissals: tuple) -> ReviewFacts:
    return replace(BASE, **reviews_or_dismissals)


def _alice(**changes: object) -> ReviewFacts:
    return _with(reviews=(replace(ALICE, **changes), BOB, BOT, DRAFT))


def _bob_out(**changes: object) -> ReviewFacts:
    return _with(dismissals=(replace(BOB_OUT, **changes),))


# (row, facts, code) — таблица инвариантов; порядок строк = порядок проверки.
ROWS = [
    *[("id-decimal", _alice(review_id=v), "malformed") for v in ["x", "07", " 7", "1_0", "٧", ""]],
    ("id-decimal-dismissal", _bob_out(review_id="2a"), "malformed"),
    ("id-unique", _alice(review_id="2"), "malformed"),
    *[
        ("ts-submitted", _alice(submitted_at=v), "bad_timestamp")
        for v in ["2026-10-12T09:00:00.5Z", "2026-10-12T09:00:00+00:00", "2026-10-12T09:00:00"]
    ],
    *[
        ("ts-created", _bob_out(created_at=v), "bad_timestamp")
        for v in ["2026-10-12T09:30:00.5Z", "2026-10-12T12:30:00+03:00", ""]
    ],
    *[("actor-review-login", _alice(login=v), "actor_unresolved") for v in ["ghost", "Ghost", ""]],
    *[
        ("actor-review-type", _alice(type_hint=v), "actor_unresolved")
        for v in ["Mannequin", "Organization", "EnterpriseUserAccount", ""]
    ],
    *[("actor-dismissal", _bob_out(actor=v), "actor_unresolved") for v in ["ghost", ""]],
    *[
        ("state-review", _alice(state=v), "unknown_state")
        for v in ["changes_requested", "approved", "", "MERGED"]
    ],
    *[
        ("state-previous", _bob_out(previous_state=v), "unknown_state")
        for v in ["CHANGES-REQUESTED", "DISMISSED", "PENDING", "approved"]
    ],
    ("commit", _alice(commit_id=""), "missing_commit_id"),
    ("dismissal-unique", _with(dismissals=(BOB_OUT, BOB_OUT)), "duplicate_dismissal"),
    ("dismissal-orphan", _with(dismissals=(BOB_OUT, replace(BOB_OUT, review_id="9"))),
     "orphan_dismissal"),
    ("submitted-at", _alice(submitted_at=None), "missing_submitted_at"),
    ("previous-state", _bob_out(previous_state=None), "dismissal_without_previous_state"),
    ("dismissed-has-event", _with(dismissals=()), "dismissed_without_event"),
    ("event-on-dismissed", _with(dismissals=(BOB_OUT, replace(BOB_OUT, review_id="1"))),
     "malformed"),
]  # fmt: skip


def test_base_is_valid():
    assert validate_review_facts(BASE) is None
    assert validate_review_facts(_with(reviews=(), dismissals=())) is None


@pytest.mark.parametrize("row,facts,code", ROWS, ids=[f"{r[0]}-{i}" for i, r in enumerate(ROWS)])
def test_invariant_violation(row, facts, code):
    result = validate_review_facts(facts)
    assert result is not None and result.code == code, (row, result)
    assert effective_reviews(facts, T, before=frozenset()) == result


@pytest.mark.parametrize(
    "dismissals",
    [
        (BOB_OUT, replace(BOB_OUT, created_at="2026-10-12T10:05:00Z")),
        (replace(BOB_OUT, created_at="2026-10-12T10:05:00Z"), BOB_OUT),
    ],
)
def test_duplicate_dismissal_any_order(dismissals):
    # иначе исход зависел бы от порядка кортежа: один из порядков снимает отзыв до T
    result = validate_review_facts(_with(dismissals=dismissals))
    assert result is not None and result.code == "duplicate_dismissal"


def test_fractional_stamp_in_t_second_rejected():
    # «.» < «Z»: без проверки approve в секунду T обходил бы группу равных меток (§8.2)
    facts = _alice(submitted_at="2026-10-12T10:00:00.5Z")
    result = effective_reviews(facts, T, before=frozenset())
    assert not isinstance(result, dict) and result.code == "bad_timestamp"


def test_bot_actor_resolved():
    assert actor_resolved("copilot", "Bot")
    assert actor_resolved("alice", "User")
    assert not actor_resolved("alice", None)
    assert not actor_resolved(None, "User")
