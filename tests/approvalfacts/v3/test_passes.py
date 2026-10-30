"""Проходы по ревью и таймлайну, проекция на T, `read_open_review` (спека steward §3.4).

Положительные двойники — неизменённые живые последовательности (`pr657`/`pr636` — обмены
проходов из снимка акта, `pr603`/`closed606` — снимок прохода дважды). Отрицательные —
производные `_derive.py` (фикстура `derive`) от `closed606_review_pass` ×2: обмены 0, 1 —
проход 1 (ревью, таймлайн), 2, 3 — проход 2. Узел отзыва — `dismissal_node` (форма E4 не
снята живьём, `unverified_shape`).
"""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from steward.approvalfacts import v3
from steward.approvalfacts.v3._github import _Fail
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
from steward.approvalfacts.v3.validate import validate_review_facts

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


def _dismiss_bob(
    dismissal_node: Callable[..., Record],
    prev: str | None = "COMMENTED",
    actor: tuple[str, str] = ("andrei-shtanakov", "User"),
):
    """Ревью Bob в DISMISSED и событие его отзыва актором `actor` — в обоих проходах."""
    node = dismissal_node(BOB_ID, LATER, prev, *actor)
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


@pytest.mark.parametrize(
    "actor", [("andrei-shtanakov", "User"), ("df-reviewer", "Bot")], ids=["user", "bot"]
)
def test_normal_dismissal_positive_twin(derive, dismissal_node, actor):
    spec = derive(
        BASE,
        _reviews("set_field", [BOB, "author", "login"], value="bob")
        + _dismiss_bob(dismissal_node, actor=actor),
    )
    assert spec["unverified_shape"] == "ReviewDismissedEvent"
    got, t = _read(spec)
    assert isinstance(got, ReviewFacts), got
    assert got.dismissals == (Dismissal(str(BOB_ID), actor[0], LATER, "COMMENTED"),)
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
        ("set_field", ["actor"], {"value": {}}),
        ("set_field", ["actor", "login"], {"value": ""}),
    ],
    ids=["no_typename", "mannequin", "actor_null", "ghost", "actor_empty", "login_empty"],
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


# --- граница нормализации: в модель не попадает сырое значение JSON (ревью Task 4, №1) ------

#: (что, путь от узла ревью/таймлайна прохода, значение, код). Объект и массив JSON на месте
#: строки нехешируемы: без типизированного доступа чтение бросало `TypeError`.
UNTYPED = [
    (kind, sub, value, code)
    for kind, sub, code in [
        ("review", ["state"], "unknown_state"),
        ("review", ["author", "__typename"], "actor_unresolved"),
        ("review", ["author", "login"], "actor_unresolved"),
        ("review", ["commit", "oid"], "missing_commit_id"),
        ("dismissal", ["actor", "__typename"], "actor_unresolved"),
        ("dismissal", ["actor", "login"], "actor_unresolved"),
        ("dismissal", ["previousReviewState"], "unknown_state"),
    ]
    for value in [{"x": 1}, ["APPROVED"]]
]


def _untyped(derive: Derive, dismissal_node: Callable[..., Record], kind: str, sub, value):
    if kind == "review":
        return derive(BASE, _reviews("set_field", [BOB, *sub], value=value))
    return derive(
        BASE, _dismiss_bob(dismissal_node) + _timeline("set_field", [0, *sub], value=value)
    )


@pytest.mark.parametrize("kind,sub,value,code", UNTYPED)
def test_untyped_json_value_is_refused(derive, dismissal_node, kind, sub, value, code):
    assert _code(_untyped(derive, dismissal_node, kind, sub, value)) == code


@pytest.mark.parametrize("kind,sub,value,code", UNTYPED)
def test_pass_never_holds_untyped_values(derive, dismissal_node, kind, sub, value, code):
    """`projection_at` хеширует поля прохода: `Pass` с сырым JSON не может быть построен."""
    t = replay_transport(_untyped(derive, dismissal_node, kind, sub, value))
    with pytest.raises(_Fail) as failed:
        read_pass(t, "andrei-shtanakov", "devtools", 606)
    assert failed.value.code == code


def test_projection_of_read_pass_is_hashable(derive, dismissal_node):
    t = replay_transport(derive(BASE, _dismiss_bob(dismissal_node)))
    p = read_pass(t, "andrei-shtanakov", "devtools", 606)
    assert len(projection_at(p, "2026-10-12T18:00:00Z")) == 3


def test_timeline_typename_not_string_is_malformed(derive, dismissal_node):
    spec = derive(
        BASE,
        _dismiss_bob(dismissal_node)
        + _timeline("set_field", [0, "__typename"], value=["ReviewDismissedEvent"]),
    )
    assert _code(spec) == "malformed"


# --- публичная поверхность (Ruling M0-E) -------------------------------------------------------


def test_read_open_review_is_exported():
    assert v3.read_open_review is read_open_review and "read_open_review" in v3.__all__
    for internal in ("Pass", "projection_at", "read_pass", "pass_facts"):
        assert internal not in v3.__all__


# --- проход, разорванный между своими страницами (ревью Task 4, №4) -----------------------------


def test_pass_torn_between_its_pages_unstable(derive, dismissal_node):
    """§5.6 «отзыв + новое CR между страницами»: проход 1 — страница 1 до перемены (Alice
    APPROVED), страница 2 и таймлайн после (CR Bob, отзыв Alice); проход 2 — целиком после.
    Сам проход 1 нецелен (отзыв ревью не в DISMISSED), но проходы сравниваются раньше
    проверки фактов — `unstable`, не `malformed`."""
    alice = dismissal_node(ALICE_ID, LATER, "APPROVED", "andrei-shtanakov", "User")
    cr = _review_node(5493999999, "bob", "CHANGES_REQUESTED", LATER)
    spec = derive(
        BASE,
        [
            _tf("set_field", [R2, *REVIEW_NODES, 0, "state"], value="DISMISSED"),
            *_reviews("append_node", [], node=cr),
            *_timeline("append_node", [], node=alice),
            _tf("page_split", [R2, *REVIEW_NODES], n=1),
            _tf("page_split", [R1, *REVIEW_NODES], n=1),
        ],
    )
    pages = spec["exchanges"]
    assert (
        pages[0]["response"]["body"]["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0][
            "state"
        ]
        == "APPROVED"
    )
    assert _code(spec) == "unstable"


# --- отказы страниц E3/E4 на поверхности read_open_review (ревью Task 4, №5) ------------------

SPLIT_REVIEWS = [_tf("page_split", [i, *REVIEW_NODES], n=1) for i in (R2, R1)]
R2_PAGE2 = 4  # R1a, R1b, T1, R2a, R2b, T2


def test_later_page_graphql_errors_with_visible_approved(derive):
    """`200` + частичные `data` (APPROVED виден) + `errors` на странице 2 прохода 2."""
    split = derive(BASE, SPLIT_REVIEWS)
    body = split["exchanges"][R2_PAGE2]["response"]["body"]
    body["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]["state"] = "APPROVED"
    partial = {"data": body["data"], "errors": [{"message": "Something went wrong"}]}
    spec = derive(
        BASE, [*SPLIT_REVIEWS, _tf("set_field", [R2_PAGE2, "response", "body"], value=partial)]
    )
    assert _code(spec) == "graphql_errors"


@pytest.mark.parametrize("lost", [PULL, [*PULL, "reviews"]], ids=["pullRequest", "reviews"])
def test_later_page_lost_path_is_pagination(derive, lost):
    spec = derive(BASE, [*SPLIT_REVIEWS, _tf("set_field", [R2_PAGE2, *lost], value=None)])
    assert _code(spec) == "pagination"


def test_timeline_split_over_pages_read_whole(derive, dismissal_node):
    alice = dismissal_node(ALICE_ID, LATER, "APPROVED", "andrei-shtanakov", "User")
    whole = (
        _dismiss_bob(dismissal_node)
        + _reviews("set_field", [0, "state"], value="DISMISSED")
        + _timeline("append_node", [], node=alice)
    )
    split = [_tf("page_split", [i, *TIMELINE_NODES], n=1) for i in (T2, T1)]
    got, t = _read(derive(BASE, whole + split))
    expected, _ = _read(derive(BASE, whole))
    assert isinstance(got, ReviewFacts) and got == expected and len(got.dismissals) == 2
    assert len(t.log) == 6 and t.unused() == [] and t.unexpected == []


# --- _derive: set_field не вносит незапрошенного (ревью Task 4, №6) ------------------------------


def test_derive_set_field_rejects_unrequested_nested_key(derive):
    with pytest.raises(ValueError, match="query fields"):
        derive(BASE, _reviews("set_field", [BOB, "author"], value={"login": "x", "email": "e"}))


def test_derive_retime_rejects_unrequested_field(derive):
    # живой снимок несёт только запрошенное, поэтому лишний ключ отвергает уже проверка пути;
    # «не запрошено» ловится на значении — `test_derive_set_field_rejects_unrequested_nested_key`
    with pytest.raises(ValueError, match="misses"):
        derive(BASE, [_tf("retime", [R1, *PULL, "createdAt"], ts=LATER)])


def test_derive_body_replacement_checks_data(derive):
    body = {"data": {"repository": {"pullRequest": {"title": "x"}}}, "errors": []}
    with pytest.raises(ValueError, match="query fields"):
        derive(BASE, [_tf("set_field", [R1, "response", "body"], value=body)])


def test_derive_non_string_typename_is_value_error(derive, dismissal_node):
    node = dismissal_node(BOB_ID, LATER, "COMMENTED", "x", "User")
    node["__typename"] = ["ReviewDismissedEvent"]
    with pytest.raises(ValueError):
        derive(BASE, [_tf("append_node", [T1, *TIMELINE_NODES], node=node)])


# --- один дефект — один код на чтении и в валидаторе (ре-ревью Task 4, №8) ----------------

#: (поле ридера, поле модели, значение): не-строка и неизвестная строка.
SAME_DEFECT = [
    (kind, sub, attr, value)
    for kind, sub, attr in [
        ("review", ["state"], "state"),
        ("review", ["commit", "oid"], "commit_id"),
        ("dismissal", ["previousReviewState"], "previous_state"),
    ]
    for value in [{"x": 1}, ["APPROVED"], 5, True]
] + [
    ("review", ["state"], "state", "SUPERSEDED"),
    ("dismissal", ["previousReviewState"], "previous_state", "PENDING"),
]


@pytest.mark.parametrize("kind,sub,attr,value", SAME_DEFECT)
def test_read_path_code_equals_validator_code(derive, dismissal_node, kind, sub, attr, value):
    clean, _ = _read(derive(BASE, _dismiss_bob(dismissal_node)))
    assert isinstance(clean, ReviewFacts)
    if kind == "review":
        broken = replace(
            clean, reviews=(clean.reviews[0], replace(clean.reviews[BOB], **{attr: value}))
        )
    else:
        broken = replace(clean, dismissals=(replace(clean.dismissals[0], **{attr: value}),))
    by_validator = validate_review_facts(broken)
    assert by_validator is not None
    assert _code(_untyped(derive, dismissal_node, kind, sub, value)) == by_validator.code
