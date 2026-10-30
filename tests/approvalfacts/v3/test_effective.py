"""`effective_reviews`: восстановление эффективного ревью на T (спека steward §3.5, devtools §5.3)."""

import pytest

from steward.approvalfacts.v3.effective import effective_reviews
from steward.approvalfacts.v3.model import Dismissal, Review, ReviewFacts, Unavailable

T = "2026-10-12T10:00:00Z"
OLD = "a" * 40
NEW = "b" * 40


def _at(clock: str | None) -> str | None:
    return None if clock is None else f"2026-10-12T{clock}Z"


def _review(
    review_id: str,
    state: str,
    clock: str | None,
    *,
    login: str = "alice",
    commit_id: str = OLD,
    type_hint: str = "User",
) -> Review:
    return Review(review_id, login, type_hint, state, commit_id, _at(clock))


def _dismissal(review_id: str, clock: str, previous_state: str | None) -> Dismissal:
    created_at = _at(clock)
    assert created_at is not None
    return Dismissal(review_id, "carol", created_at, previous_state)


def _facts(reviews: tuple[Review, ...] = (), dismissals: tuple[Dismissal, ...] = ()) -> ReviewFacts:
    return ReviewFacts("o/r", 1, NEW, "master", "OPEN", reviews, dismissals)


def eff(facts: ReviewFacts) -> dict[str, Review] | Unavailable:
    return effective_reviews(facts, T, before=frozenset())


def states(result: dict[str, Review] | Unavailable) -> dict[str, str]:
    assert isinstance(result, dict), result
    return {login: review.state for login, review in result.items()}


def _code(result: dict[str, Review] | Unavailable) -> str:
    assert isinstance(result, Unavailable), result
    return result.code


def test_approved_cr_then_second_dismissed_is_dismissed():
    # §5.3 контрпример 1: APPROVED(1, 09:00) → CR(2, 09:10) → отзыв 2 (09:20, prev CR)
    facts = _facts(
        (_review("1", "APPROVED", "09:00:00"), _review("2", "DISMISSED", "09:10:00")),
        (_dismissal("2", "09:20:00", "CHANGES_REQUESTED"),),
    )
    assert states(eff(facts)) == {"alice": "DISMISSED"}


def test_approved_dismissed_after_t_is_approved():
    # §5.3 контрпример 2: review 1 state=DISMISSED, отзыв 10:05 prev APPROVED
    facts = _facts(
        (_review("1", "DISMISSED", "09:00:00"),),
        (_dismissal("1", "10:05:00", "APPROVED"),),
    )
    assert states(eff(facts)) == {"alice": "APPROVED"}


def test_pending_without_submitted_at_ignored():
    facts = _facts((_review("1", "APPROVED", "09:00:00"), _review("2", "PENDING", None)))
    assert states(eff(facts)) == {"alice": "APPROVED"}


def test_submitted_without_submitted_at_unavailable():
    assert _code(eff(_facts((_review("1", "COMMENTED", None),)))) == "missing_submitted_at"


@pytest.mark.parametrize("created", ["09:30:00", "10:05:00"])
def test_dismissal_without_previous_state_unavailable_anywhere(created):
    # безусловно (спека devtools §5.3): ни положение относительно T, ни значимость
    # для исхода не важны
    facts = _facts((_review("1", "DISMISSED", "09:00:00"),), (_dismissal("1", created, None),))
    assert _code(eff(facts)) == "dismissal_without_previous_state"


def test_missing_previous_state_of_other_login_blocks_valid_act():
    # отрицательный двойник: Alice APPROVED действительна; у Bob отзыв до T без prev
    def facts(previous_state: str | None) -> ReviewFacts:
        return _facts(
            (
                _review("1", "APPROVED", "09:00:00"),
                _review("2", "DISMISSED", "09:10:00", login="bob"),
            ),
            (_dismissal("2", "09:30:00", previous_state),),
        )

    assert _code(eff(facts(None))) == "dismissal_without_previous_state"
    # положительный двойник — тот же набор с prev "CHANGES_REQUESTED"
    assert states(eff(facts("CHANGES_REQUESTED"))) == {"alice": "APPROVED", "bob": "DISMISSED"}


def test_dismissed_without_event_unavailable():
    assert _code(eff(_facts((_review("1", "DISMISSED", "09:00:00"),)))) == (
        "dismissed_without_event"
    )


def test_dismissed_after_t_without_event_unavailable():
    # безусловно: ревью после T всё равно проверяется до сравнения с T
    assert _code(eff(_facts((_review("1", "DISMISSED", "10:30:00"),)))) == (
        "dismissed_without_event"
    )


@pytest.mark.parametrize(
    "seq,expected",
    [
        (["APPROVED", "CHANGES_REQUESTED"], "CHANGES_REQUESTED"),
        (["CHANGES_REQUESTED", "APPROVED"], "APPROVED"),
        (["APPROVED", "APPROVED"], "APPROVED"),
        (["APPROVED", "COMMENTED"], "APPROVED"),
    ],
)
def test_several_reviews_of_one_login(seq, expected):
    reviews = tuple(
        _review(str(i + 1), state, f"09:{i * 10:02d}:00") for i, state in enumerate(seq)
    )
    assert states(eff(_facts(reviews))) == {"alice": expected}


def test_latest_review_commit_wins():
    facts = _facts(
        (
            _review("1", "APPROVED", "09:00:00", commit_id=OLD),
            _review("2", "APPROVED", "09:30:00", commit_id=NEW),
        )
    )
    result = eff(facts)
    assert isinstance(result, dict)
    assert result["alice"].commit_id == "b" * 40


def test_effective_review_keeps_read_fields():
    # состояние — восстановленное, остальные поля — как прочитаны
    review = _review("7", "DISMISSED", "09:00:00", commit_id=NEW)
    result = eff(_facts((review,), (_dismissal("7", "10:05:00", "APPROVED"),)))
    assert isinstance(result, dict)
    assert result == {
        "alice": Review("7", "alice", "User", "APPROVED", NEW, "2026-10-12T09:00:00Z")
    }


def test_review_after_t_dropped():
    assert eff(_facts((_review("1", "APPROVED", "10:00:01"),))) == {}


def test_review_after_t_does_not_hide_earlier_approve():
    facts = _facts(
        (_review("1", "APPROVED", "09:00:00"), _review("2", "CHANGES_REQUESTED", "10:00:01"))
    )
    assert states(eff(facts)) == {"alice": "APPROVED"}


def test_equal_stamp_dismissal_respects_before():
    facts = _facts(
        (_review("1", "DISMISSED", "09:00:00"),),
        (_dismissal("1", "10:00:00", "APPROVED"),),
    )
    assert states(effective_reviews(facts, T, before=frozenset({("dismissal", "1")}))) == {
        "alice": "DISMISSED"
    }
    assert states(effective_reviews(facts, T, before=frozenset())) == {"alice": "APPROVED"}


def test_equal_stamp_approve_respects_before():
    facts = _facts((_review("1", "APPROVED", "10:00:00"),))
    assert states(effective_reviews(facts, T, before=frozenset({("review", "1")}))) == {
        "alice": "APPROVED"
    }
    assert effective_reviews(facts, T, before=frozenset()) == {}


def test_before_keys_are_typed():
    # ключ ревью не делает отзыв того же id «до T», и наоборот
    facts = _facts(
        (_review("1", "DISMISSED", "10:00:00"),),
        (_dismissal("1", "10:00:00", "APPROVED"),),
    )
    assert effective_reviews(facts, T, before=frozenset({("dismissal", "1")})) == {}
    assert states(effective_reviews(facts, T, before=frozenset({("review", "1")}))) == {
        "alice": "APPROVED"
    }


def test_both_keys_in_before():
    # ревью и его отзыв в секунду T, оба «до T» (полный набор свидетеля devtools `resolve`)
    facts = _facts(
        (_review("1", "DISMISSED", "10:00:00"),),
        (_dismissal("1", "10:00:00", "APPROVED"),),
    )
    both = frozenset({("review", "1"), ("dismissal", "1")})
    assert states(effective_reviews(facts, T, before=both)) == {"alice": "DISMISSED"}


@pytest.mark.parametrize("reverse", [False, True])
def test_tie_breaks_numerically(reverse):
    # CR id "99" и APPROVED id "100" в одну секунду: эффективное — "100" (APPROVED).
    # Оба порядка кортежа: исход не от порядка чтения и не от строкового сравнения id.
    pair = (_review("99", "CHANGES_REQUESTED", "09:00:00"), _review("100", "APPROVED", "09:00:00"))
    facts = _facts(pair[::-1] if reverse else pair)
    assert states(eff(facts)) == {"alice": "APPROVED"}


@pytest.mark.parametrize("reverse", [False, True])
def test_tie_breaks_numerically_cr_wins(reverse):
    # зеркально: больший id — CR; эффективное — CR при любом порядке кортежа
    pair = (_review("99", "APPROVED", "09:00:00"), _review("100", "CHANGES_REQUESTED", "09:00:00"))
    facts = _facts(pair[::-1] if reverse else pair)
    assert states(eff(facts)) == {"alice": "CHANGES_REQUESTED"}


def test_commented_only_login_absent():
    facts = _facts(
        (
            _review("1", "APPROVED", "09:00:00"),
            _review("2", "COMMENTED", "09:10:00", login="bob"),
            _review("3", "PENDING", None, login="dave"),
        )
    )
    assert states(eff(facts)) == {"alice": "APPROVED"}


def test_restored_cr_hides_earlier_approve():
    # отзыв после T восстанавливает CR, и он, а не раннее APPROVED, эффективен на T
    facts = _facts(
        (_review("1", "APPROVED", "09:00:00"), _review("2", "DISMISSED", "09:10:00")),
        (_dismissal("2", "10:05:00", "CHANGES_REQUESTED"),),
    )
    assert states(eff(facts)) == {"alice": "CHANGES_REQUESTED"}


def test_bot_review_keeps_type_hint():
    review = _review("1", "APPROVED", "09:00:00", login="copilot", type_hint="Bot")
    result = eff(_facts((review,)))
    assert isinstance(result, dict)
    assert result == {"copilot": review}


def test_user_and_bot_with_one_login_share_key():
    # ключ — логин в GraphQL-форме; последнее ревью побеждает, `type_hint` сохраняется
    facts = _facts(
        (
            _review("1", "APPROVED", "09:00:00", login="foo"),
            _review("2", "CHANGES_REQUESTED", "09:10:00", login="foo", type_hint="Bot"),
        )
    )
    result = eff(facts)
    assert isinstance(result, dict)
    assert {k: (v.state, v.type_hint) for k, v in result.items()} == {
        "foo": ("CHANGES_REQUESTED", "Bot")
    }


def test_dismissed_commented_does_not_change_state():
    # отозванное ревью с prev COMMENTED: на T до отзыва состояние COMMENTED — не эффективное
    facts = _facts(
        (_review("1", "APPROVED", "09:00:00"), _review("2", "DISMISSED", "09:10:00")),
        (_dismissal("2", "10:05:00", "COMMENTED"),),
    )
    assert states(eff(facts)) == {"alice": "APPROVED"}


def test_empty_facts():
    assert eff(_facts()) == {}


@pytest.mark.parametrize(
    "t", ["2026-10-12T10:00:00.1Z", "2026-10-12T10:00:00+00:00", "2026-10-12T10:00:00", ""]
)
def test_bad_t_raises(t):
    with pytest.raises(ValueError):
        effective_reviews(_facts(), t, before=frozenset())
