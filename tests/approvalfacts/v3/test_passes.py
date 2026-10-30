"""Проходы по ревью и таймлайну, проекция на T, `read_open_review` (спека steward §3.4).

Положительные двойники — неизменённые живые последовательности (`pr657`/`pr636` — обмены
проходов из снимка акта, `pr603`/`closed606` — снимок прохода дважды). Отрицательные —
производные `_derive.py` (фикстура `derive`) от `closed606_review_pass` ×2: обмены 0, 1 —
проход 1 (ревью, таймлайн), 2, 3 — проход 2. Узел отзыва — `dismissal_node` (форма E4 не
снята живьём, `unverified_shape`).
"""

from collections.abc import Callable
from typing import Any

import pytest

from steward.approvalfacts.v3.effective import effective_reviews
from steward.approvalfacts.v3.model import Dismissal, Review, ReviewFacts, Unavailable
from steward.approvalfacts.v3.passes import (
    Pass,
    projection_at,
    read_open_review,
    read_pass,
    split_repo,
)
from steward.approvalfacts.v3.queries import Q_TIMELINE
from steward.approvalfacts.v3.transport import ReplayTransport, replay_transport

Record = dict[str, Any]
Derive = Callable[[list[str], list[Record]], Record]

REPO = "andrei-shtanakov/devtools"
PASS_OPS = ("StewardV3Reviews", "StewardV3Timeline")
BASE = ["closed606_review_pass", "closed606_review_pass"]
R1, T1, R2, T2 = 0, 1, 2, 3
PULL = ["response", "body", "data", "repository", "pullRequest"]
REVIEW_NODES = [*PULL, "reviews", "nodes"]
TIMELINE_NODES = [*PULL, "timelineItems", "nodes"]
HEAD = "a038c2156ac098dad4e1d0cba3ba936cc57940c8"
ALICE_ID, BOB_ID = 5493892414, 5493955117  # ai-prosto APPROVED, copilot COMMENTED (#606)
BOB = 1  # индекс узла второго ревью
LATER = "2026-10-12T17:20:00Z"


def _tf(op: str, path: list[Any], **kw: Any) -> Record:
    return {"op": op, "path": path, **kw}


def _reviews(op: str, sub: list[Any], **kw: Any) -> list[Record]:
    """Одно преобразование узлов ревью в обоих проходах."""
    return [_tf(op, [i, *REVIEW_NODES, *sub], **kw) for i in (R1, R2)]


def _timeline(op: str, sub: list[Any], **kw: Any) -> list[Record]:
    """Одно преобразование таймлайна в обоих проходах."""
    return [_tf(op, [i, *TIMELINE_NODES, *sub], **kw) for i in (T1, T2)]


def _review_node(review_id: int, login: str, state: str, submitted: str) -> Record:
    return {
        "databaseId": review_id,
        "state": state,
        "submittedAt": submitted,
        "commit": {"oid": HEAD},
        "author": {"login": login, "__typename": "User"},
    }


def _read(spec: Record, pr: int = 606) -> tuple[ReviewFacts | Unavailable, ReplayTransport]:
    t = replay_transport(spec)
    return read_open_review(t, REPO, pr), t


def _code(spec: Record) -> str:
    got, _ = _read(spec)
    assert isinstance(got, Unavailable), got
    return got.code


def _dismiss_bob(dismissal_node: Callable[..., Record], prev: str | None = "COMMENTED"):
    """Ревью Bob в DISMISSED и событие его отзыва актором User — в обоих проходах."""
    node = dismissal_node(BOB_ID, LATER, prev, "andrei-shtanakov", "User")
    return _reviews("set_field", [BOB, "state"], value="DISMISSED") + _timeline(
        "append_node", [], node=node
    )


# --- положительные двойники E3/E4 на неизменённых живых последовательностях -------------


@pytest.fixture
def live_pass_sequence(exchanges: Callable[[str], list[Record]]) -> Callable[[str], list[Record]]:
    """Обмены двух проходов: из снимка акта — как сняты, из снимка прохода — он дважды."""

    def sequence(name: str) -> list[Record]:
        if name.endswith("_act_sequence"):
            return [ex for ex in exchanges(name) if ex["request"].get("op") in PASS_OPS]
        return exchanges(name) * 2

    return sequence


@pytest.mark.parametrize(
    "name,pr",
    [
        ("pr657_act_sequence", 657),
        ("pr636_act_sequence", 636),
        ("pr603_review_pass", 603),
        ("closed606_review_pass", 606),
    ],
)
def test_live_sequences_read_unmodified_in_order(name, pr, live_pass_sequence, key, key_of):
    seq = live_pass_sequence(name)
    t = replay_transport({"exchanges": seq})
    got = read_open_review(t, REPO, pr)
    assert isinstance(got, ReviewFacts), got
    assert t.unused() == [] and t.unexpected == []
    assert [key(r) for r in t.log] == [key_of(ex) for ex in seq]


def test_reviews_user_and_bot_live_positive_twin(live_pass_sequence):
    t = replay_transport({"exchanges": live_pass_sequence("pr657_act_sequence")})
    got = read_open_review(t, REPO, 657)
    head = "b3651bcb2907bd6d1b6d421b018701520fb64066"
    assert got == ReviewFacts(
        REPO,
        657,
        head,
        "master",
        "MERGED",
        (
            Review("5711633458", "ai-prosto", "User", "APPROVED", head, "2026-10-30T11:34:44Z"),
            Review(
                "5711735077",
                "copilot-pull-request-reviewer",
                "Bot",
                "COMMENTED",
                head,
                "2026-10-30T11:44:21Z",
            ),
        ),
        (),
    )


def test_closed_pr_bot_reviewer_is_resolved(live_pass_sequence):
    got, _ = _read({"exchanges": live_pass_sequence("closed606_review_pass")})
    assert isinstance(got, ReviewFacts) and got.pr_state == "CLOSED"
    assert [(r.login, r.type_hint) for r in got.reviews] == [
        ("ai-prosto", "User"),
        ("copilot-pull-request-reviewer", "Bot"),
    ]


def test_reviews_on_earlier_pushes_keep_their_commit(live_pass_sequence):
    got, _ = _read({"exchanges": live_pass_sequence("pr603_review_pass")}, pr=603)
    assert isinstance(got, ReviewFacts)
    assert got.head_sha == "d634ea2ace36501eb7cb84dac1ac01d6b9ba7680"
    assert [r.commit_id for r in got.reviews] == [
        "48cb50ac2a13736d70f6a933bc8a3a3239b5d662",
        "48cb50ac2a13736d70f6a933bc8a3a3239b5d662",
        "0172e10f0adecccd513027f785e29ee46d0c6b29",
    ]


def test_merged_event_commit_is_read(live_pass_sequence):
    t = replay_transport({"exchanges": live_pass_sequence("pr657_act_sequence")})
    p = read_pass(t, "andrei-shtanakov", "devtools", 657)
    assert p.merged_commits == ("aa4e7e37d9f1ea34581c9c484d60074b6d3ac18e",)
    assert p.dismissals == ()


def test_timeline_query_requests_actor_typename():
    assert "actor{login __typename}" in Q_TIMELINE


def test_open_review_clean(derive):
    got, t = _read(derive(BASE, []))
    assert isinstance(got, ReviewFacts) and got.head_sha == HEAD
    assert [r.state for r in got.reviews] == ["APPROVED", "COMMENTED"]
    assert t.unused() == [] and t.unexpected == []


def test_reviews_split_over_pages_read_whole(derive):
    # page_split сначала второго прохода, затем первого: индексы раньше не сдвигаются
    split = [_tf("page_split", [i, *REVIEW_NODES], n=1) for i in (R2, R1)]
    got, t = _read(derive(BASE, split))
    whole, _ = _read(derive(BASE, []))
    assert got == whole and len(t.log) == 6 and t.unused() == []


def test_submitted_at_offset_is_canonicalised(derive):
    spec = derive(BASE, _reviews("retime", [BOB, "submittedAt"], ts="2026-10-12T17:14:49+00:00"))
    got, _ = _read(spec)
    assert isinstance(got, ReviewFacts)
    assert got.reviews[BOB].submitted_at == "2026-10-12T17:14:49Z"


# --- отзыв: положительный двойник типа актора и его отрицательные двойники ---------------


def test_normal_dismissal_positive_twin(derive, dismissal_node):
    spec = derive(
        BASE,
        _reviews("set_field", [BOB, "author", "login"], value="bob") + _dismiss_bob(dismissal_node),
    )
    assert spec["unverified_shape"] == "ReviewDismissedEvent"
    got, t = _read(spec)
    assert isinstance(got, ReviewFacts), got
    assert got.dismissals == (Dismissal(str(BOB_ID), "andrei-shtanakov", LATER, "COMMENTED"),)
    effective = effective_reviews(got, "2026-10-12T18:00:00Z", before=frozenset())
    assert isinstance(effective, dict)
    assert {login: r.state for login, r in effective.items()} == {
        "ai-prosto": "APPROVED",
        "bob": "DISMISSED",
    }
    assert t.unused() == [] and t.unexpected == []


@pytest.mark.parametrize(
    "op,sub,kw",
    [
        ("drop_field", ["actor", "__typename"], {}),
        ("set_field", ["actor", "__typename"], {"value": "Mannequin"}),
        ("set_field", ["actor"], {"value": None}),
        ("set_field", ["actor", "login"], {"value": "Ghost"}),
    ],
    ids=["no_typename", "mannequin", "actor_null", "ghost"],
)
def test_unresolved_dismissal_actor_fails_whole_pr(derive, dismissal_node, op, sub, kw):
    """R-M0-6: действительный APPROVED Alice + отзыв ревью Bob неустановленным актором."""
    spec = derive(BASE, _dismiss_bob(dismissal_node) + _timeline(op, [0, *sub], **kw))
    assert _code(spec) == "actor_unresolved"


def test_duplicate_dismissal(derive, dismissal_node):
    again = _timeline(
        "append_node", [], node=dismissal_node(BOB_ID, LATER, "COMMENTED", "x", "User")
    )
    assert _code(derive(BASE, _dismiss_bob(dismissal_node) + again)) == "duplicate_dismissal"


def test_orphan_dismissal(derive, dismissal_node):
    node = dismissal_node(42, LATER, "COMMENTED", "andrei-shtanakov", "User")
    assert _code(derive(BASE, _timeline("append_node", [], node=node))) == "orphan_dismissal"


def test_dismissal_review_null(derive, dismissal_node):
    null = _timeline("set_field", [0, "review"], value=None)
    assert _code(derive(BASE, _dismiss_bob(dismissal_node) + null)) == "malformed"


def test_dismissal_review_id_not_integer(derive, dismissal_node):
    text = _timeline("set_field", [0, "review", "databaseId"], value=str(BOB_ID))
    assert _code(derive(BASE, _dismiss_bob(dismissal_node) + text)) == "malformed"


def test_unknown_previous_state(derive, dismissal_node):
    assert _code(derive(BASE, _dismiss_bob(dismissal_node, prev="PENDING"))) == "unknown_state"


def test_dismissal_bad_timestamp(derive, dismissal_node):
    late = _timeline("retime", [0, "createdAt"], ts="2026-10-12T17:20:00.5Z")
    assert _code(derive(BASE, _dismiss_bob(dismissal_node) + late)) == "bad_timestamp"


def test_dismissed_without_event_is_read_refusal(derive):
    # R-M0-4: оба прохода — ревью DISMISSED без события отзыва
    spec = derive(BASE, _reviews("set_field", [BOB, "state"], value="DISMISSED"))
    assert _code(spec) == "dismissed_without_event"


def test_previous_state_null_is_read_refusal(derive, dismissal_node):
    # отказ чтения, безусловный (§3.4, О13): отзыв ревью Bob при действительном APPROVED Alice
    assert _code(derive(BASE, _dismiss_bob(dismissal_node, prev=None))) == (
        "dismissal_without_previous_state"
    )


def test_unknown_timeline_item_is_malformed(derive):
    spec = derive(BASE, _timeline("append_node", [], node={"__typename": "LabeledEvent"}))
    assert _code(spec) == "malformed"


# --- узел ревью ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("state", "SUPERSEDED", "unknown_state"),
        ("commit", None, "missing_commit_id"),
        ("commit", {}, "missing_commit_id"),
        ("databaseId", "x1", "malformed"),
        ("databaseId", str(BOB_ID), "malformed"),
        ("databaseId", True, "malformed"),
        ("databaseId", -1, "malformed"),
        ("submittedAt", "2026-10-12T17:14:49.5Z", "bad_timestamp"),
    ],
)
def test_review_node_refusals(derive, field, value, code):
    assert _code(derive(BASE, _reviews("set_field", [BOB, field], value=value))) == code


@pytest.mark.parametrize(
    "author",
    [
        None,
        {},
        {"login": "", "__typename": "User"},
        {"login": "ghost", "__typename": "User"},
        {"login": "Ghost", "__typename": "User"},
        {"login": "x", "__typename": "Mannequin"},
        {"login": "x"},
    ],
)
def test_unresolved_reviewer_fails_whole_pr(derive, author):
    """R-M0-6, отрицательный двойник: действительный APPROVED Alice + ревью Bob с
    неустановленным автором → весь PR actor_unresolved."""
    assert _code(derive(BASE, _reviews("set_field", [BOB, "author"], value=author))) == (
        "actor_unresolved"
    )


def test_pending_review_of_unresolved_author_fails(derive):
    spec = derive(
        BASE,
        _reviews("set_field", [BOB, "state"], value="PENDING")
        + _reviews("set_field", [BOB, "submittedAt"], value=None)
        + _reviews("set_field", [BOB, "author", "login"], value="ghost"),
    )
    assert _code(spec) == "actor_unresolved"


def test_submitted_at_missing_is_read_refusal(derive):
    spec = derive(BASE, _reviews("set_field", [BOB, "submittedAt"], value=None))
    assert _code(spec) == "missing_submitted_at"


def test_pending_without_submitted_at_is_read(derive):
    spec = derive(
        BASE,
        _reviews("set_field", [BOB, "state"], value="PENDING")
        + _reviews("set_field", [BOB, "submittedAt"], value=None),
    )
    got, _ = _read(spec)
    assert isinstance(got, ReviewFacts)
    assert (got.reviews[BOB].state, got.reviews[BOB].submitted_at) == ("PENDING", None)


# --- форма ответа -------------------------------------------------------------------------


@pytest.mark.parametrize("index", [R1, T1])
def test_pull_request_null_is_refused(derive, index):
    """`graphql_pages` без `null_ok_at`: `pullRequest: null` у E3/E4 — `malformed`."""
    spec = derive(BASE, [_tf("set_field", [index, *PULL], value=None)])
    assert _code(spec) == "malformed"


@pytest.mark.parametrize("field", ["headRefOid", "baseRefName", "state"])
def test_pull_request_field_missing_is_malformed(derive, field):
    assert _code(derive(BASE, [_tf("set_field", [R1, *PULL, field], value=None)])) == "malformed"


def test_head_changed_between_review_pages_unstable(derive):
    spec = derive(
        BASE,
        [
            _tf("page_split", [R1, *REVIEW_NODES], n=1),
            _tf("set_field", [1, *PULL, "headRefOid"], value="b" * 40),
        ],
    )
    assert _code(spec) == "unstable"


# --- устойчивость двух проходов ------------------------------------------------------------


def test_open_review_head_changed_between_passes_unstable(derive):
    spec = derive(BASE, [_tf("set_field", [R2, *PULL, "headRefOid"], value="b" * 40)])
    got, _ = _read(spec)
    assert isinstance(got, Unavailable) and got.code == "unstable"
    assert "head_sha" in got.reason


def test_dismissal_and_new_cr_between_pages_unstable(derive, dismissal_node):
    # проход 2: Alice DISMISSED + отзыв, новое CHANGES_REQUESTED Bob на второй странице;
    # голова та же
    alice = dismissal_node(ALICE_ID, LATER, "APPROVED", "andrei-shtanakov", "User")
    cr = _review_node(5493999999, "bob", "CHANGES_REQUESTED", LATER)
    spec = derive(
        BASE,
        [
            _tf("set_field", [R2, *REVIEW_NODES, 0, "state"], value="DISMISSED"),
            _tf("append_node", [T2, *TIMELINE_NODES], node=alice),
            _tf("append_node", [R2, *REVIEW_NODES], node=cr),
            _tf("page_split", [R2, *REVIEW_NODES], n=1),
            _tf("page_split", [R1, *REVIEW_NODES], n=1),
        ],
    )
    assert _code(spec) == "unstable"


def test_violation_in_one_pass_only_is_unstable(derive):
    # проходы сравниваются до проверки фактов: различие само по себе — `unstable`
    spec = derive(BASE, [_tf("set_field", [R2, *REVIEW_NODES, BOB, "state"], value="X")])
    assert _code(spec) == "unstable"


# --- проекция на T --------------------------------------------------------------------------

T = "2026-10-12T10:00:00Z"
_ALICE = Review("1", "alice", "User", "APPROVED", "a" * 40, "2026-10-12T09:00:00Z")


def _pass(reviews: tuple[Review, ...], dismissals: tuple[Dismissal, ...] = ()) -> Pass:
    return Pass(HEAD, "master", "MERGED", reviews, dismissals, ())


def test_projection_ignores_events_after_t():
    p1 = _pass((_ALICE,))
    carol = Review("3", "carol", "User", "APPROVED", "a" * 40, "2026-10-12T10:00:06Z")
    p2 = _pass(
        (Review("1", "alice", "User", "DISMISSED", "a" * 40, "2026-10-12T09:00:00Z"), carol),
        (Dismissal("1", "bob", "2026-10-12T10:00:05Z", "APPROVED"),),
    )
    assert projection_at(p1, T) == projection_at(p2, T)


def test_projection_sees_event_at_t():
    p1 = _pass((_ALICE,))
    p2 = _pass(
        (Review("1", "alice", "User", "DISMISSED", "a" * 40, "2026-10-12T09:00:00Z"),),
        (Dismissal("1", "bob", T, "APPROVED"),),
    )
    assert projection_at(p1, T) != projection_at(p2, T)


def test_projection_sees_review_at_t():
    late = Review("2", "carol", "User", "APPROVED", "a" * 40, T)
    assert projection_at(_pass((_ALICE,)), T) != projection_at(_pass((_ALICE, late)), T)


def test_projection_keeps_pending_without_stamp():
    pending = Review("2", "carol", "User", "PENDING", "a" * 40, None)
    assert projection_at(_pass((_ALICE,)), T) != projection_at(_pass((_ALICE, pending)), T)


def test_projection_rejects_non_canonical_t():
    with pytest.raises(ValueError):
        projection_at(_pass((_ALICE,)), "2026-10-12T10:00:00+00:00")


# --- repo ------------------------------------------------------------------------------------


@pytest.mark.parametrize("repo", ["o/r/x", "or", "/r", "o/", ""])
def test_repo_must_have_one_slash(repo):
    with pytest.raises(ValueError):
        read_open_review(replay_transport({"exchanges": []}), repo, 7)


def test_split_repo():
    assert split_repo("o/r") == ("o", "r")


# --- _derive: закрытый список преобразований ------------------------------------------------


def test_derive_append_node_rejects_unrequested_field(derive):
    node = {**_review_node(1, "bob", "APPROVED", LATER), "body": "lgtm"}
    with pytest.raises(ValueError, match="query fields"):
        derive(BASE, [_tf("append_node", [R1, *REVIEW_NODES], node=node)])


def test_derive_append_node_rejects_missing_field(derive, dismissal_node):
    node = dismissal_node(BOB_ID, LATER, "COMMENTED", "x", "User")
    del node["actor"]["__typename"]
    with pytest.raises(ValueError, match="query fields"):
        derive(BASE, [_tf("append_node", [T1, *TIMELINE_NODES], node=node)])


def test_derive_set_field_cannot_add_field(derive):
    with pytest.raises(ValueError, match="misses"):
        derive(BASE, [_tf("set_field", [R1, *REVIEW_NODES, 0, "body"], value="x")])


def test_derive_rejects_unknown_transform(derive):
    with pytest.raises(ValueError, match="unknown transform"):
        derive(BASE, [{"op": "rewrite", "path": [0]}])


def test_derive_repeat_exchange_adds_second_pass(derive):
    once = derive(["closed606_review_pass"], [])
    twice = derive(
        ["closed606_review_pass"],
        [{"op": "repeat_exchange", "name": op} for op in PASS_OPS],
    )
    assert twice["exchanges"] == once["exchanges"] * 2
    assert "unverified_shape" not in twice
