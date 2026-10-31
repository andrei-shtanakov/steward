"""`read_act_facts`: доставка первой, два прохода, срез на T (спека steward §3.3).

Положительные двойники — неизменённые живые последовательности акта (`pr657`, `pr636`,
`closed606_act_sequence`) с проверкой порядка запросов. Отрицательные — производные
преобразований §5 (фикстура `derive`) от `pr657_act_sequence`, по одному нарушению: обмены
0 (E2), 1 (E1), 2 (E5), 3 (E6), 4/5 — проход 1 (E3, E4), 6/7 — проход 2, 8 (E2), 9 (E1) —
повторное чтение доставки. Поле доставки меняется в обоих чтениях, поле ревью — в обоих
проходах, если вектор не про расхождение между ними. Узел отзыва — `dismissal_node`
(форма E4 живьём не снята, `unverified_shape`).
"""

from collections.abc import Callable
from typing import Any

import pytest

from steward.approvalfacts import v3
from steward.approvalfacts.v3.act import read_act_facts
from steward.approvalfacts.v3.effective import effective_reviews
from steward.approvalfacts.v3.model import Delivery, PathBlob, Review, ReviewFacts, Unavailable
from steward.approvalfacts.v3.transport import replay_transport

Record = dict[str, Any]
Derive = Callable[[list[str], list[Record]], Record]

REPO = "andrei-shtanakov/devtools"
SEQ = "pr657_act_sequence"
E2, E1, E5, E6, R1, T1, R2, T2, E2_AGAIN, E1_AGAIN = range(10)
MERGE = "aa4e7e37d9f1ea34581c9c484d60074b6d3ac18e"
HEAD = "b3651bcb2907bd6d1b6d421b018701520fb64066"
T = "2026-10-30T12:09:17Z"
P = "docs/evidence/2026-10-30-df-stage2a-acceptance.md"
BLOB = "1d902e2395fb61e960eefc21ef04f471d139e774"
ALICE_ID = 5711633458  # ai-prosto APPROVED (#657)
COPILOT = 1  # индекс узла второго ревью (copilot COMMENTED)
GQL_PULL = ["response", "body", "data", "repository", "pullRequest"]
REST_PULL = ["response", "body"]
REVIEW_NODES = [*GQL_PULL, "reviews", "nodes"]
TIMELINE_NODES = [*GQL_PULL, "timelineItems", "nodes"]


def _tf(op: str, path: list[Any], **kw: Any) -> Record:
    return {"op": op, "path": path, **kw}


def _graphql_delivery(op: str, sub: list[Any], **kw: Any) -> list[Record]:
    """Одно преобразование E2 в обоих чтениях доставки."""
    return [_tf(op, [i, *GQL_PULL, *sub], **kw) for i in (E2, E2_AGAIN)]


def _rest_delivery(op: str, sub: list[Any], **kw: Any) -> list[Record]:
    """Одно преобразование E1 в обоих чтениях доставки."""
    return [_tf(op, [i, *REST_PULL, *sub], **kw) for i in (E1, E1_AGAIN)]


def _reviews(op: str, sub: list[Any], passes=(R1, R2), **kw: Any) -> list[Record]:
    return [_tf(op, [i, *REVIEW_NODES, *sub], **kw) for i in passes]


def _timeline(op: str, sub: list[Any], passes=(T1, T2), **kw: Any) -> list[Record]:
    return [_tf(op, [i, *TIMELINE_NODES, *sub], **kw) for i in passes]


def _read(spec: Record, pr: int = 657, paths: list[str] | None = None):
    t = replay_transport(spec)
    return read_act_facts(t, REPO, pr, paths=[P] if paths is None else paths), t


def _derived(derive: Derive, transforms: list[Record]):
    return _read(derive([SEQ], transforms))


def _code(derive: Derive, transforms: list[Record]) -> str:
    got, _ = _derived(derive, transforms)
    assert isinstance(got, Unavailable), got
    return got.code


def _ok(derive: Derive, transforms: list[Record]) -> tuple[Delivery, ReviewFacts]:
    got, t = _derived(derive, transforms)
    assert not isinstance(got, Unavailable), got
    delivery, facts = got
    assert facts is not None and t.unused() == [] and t.unexpected == []
    return delivery, facts


def _states(facts: ReviewFacts) -> dict[str, str]:
    effective = effective_reviews(facts, T, before=frozenset())
    assert not isinstance(effective, Unavailable), effective
    return {login: r.state for login, r in effective.items()}


def test_read_act_facts_is_public():
    assert v3.read_act_facts is read_act_facts


# --- положительные двойники: неизменённые живые последовательности ----------------------


def test_not_merged_live_capture_is_waiting_delivery(live, key, key_of):
    """Живой снимок devtools #606: REST merged=false c НЕНУЛЕВЫМ тестовым
    merge_commit_sha, GraphQL mergeCommit=null → сначала merged, поля мержа не сверяются."""
    seq = live("closed606_act_sequence")
    t = replay_transport(seq)
    pull = next(ex for ex in seq["exchanges"] if ex["request"]["method"] == "GET")
    assert pull["response"]["body"]["merge_commit_sha"]
    assert pull["response"]["body"]["merged"] is False
    got = read_act_facts(t, REPO, 606, paths=[P])
    assert not isinstance(got, Unavailable), got
    d, facts = got
    assert facts is None and d == Delivery(False, None, None, None, None, None, False, ())
    assert t.unused() == [] and [key(r) for r in t.log] == [key_of(ex) for ex in seq["exchanges"]]


def test_merged_mergecommit_live_positive_twin(live, key, key_of):
    seq = live(SEQ)
    got, t = _read(seq, paths=seq["paths"])
    assert not isinstance(got, Unavailable), got
    d, facts = got
    assert d == Delivery(
        True, MERGE, T, "andrei-shtanakov", "User", HEAD, True, (PathBlob(P, BLOB, BLOB),)
    )
    assert facts == ReviewFacts(
        REPO, 657, HEAD, "master", "MERGED",
        (
            Review(str(ALICE_ID), "ai-prosto", "User", "APPROVED", HEAD, "2026-10-30T11:34:44Z"),
            Review(
                "5711735077", "copilot-pull-request-reviewer", "Bot", "COMMENTED", HEAD,
                "2026-10-30T11:44:21Z",
            ),
        ),
        (),
    )  # fmt: skip
    assert t.unused() == [] and [key(r) for r in t.log] == [key_of(ex) for ex in seq["exchanges"]]


def test_merged_squash_live_positive_twin(live, key, key_of):
    seq = live("pr636_act_sequence")
    got, t = _read(seq, pr=636, paths=seq["paths"])
    assert not isinstance(got, Unavailable), got
    d, facts = got
    head = "dfe8b0110b2b81ff32fddcf2bc4bb845a5dfb120"
    assert (d.merged, d.merge_commit, d.merged_at, d.head_at_merge) == (
        True,
        "1f350c74a4909454619b820d29cbaea244a68733",
        "2026-10-26T15:48:49Z",
        head,
    )
    assert d.in_base is True  # живой compare `behind`
    assert [p.path for p in d.paths] == ["TODO.md"]
    assert facts is not None and facts.head_sha == head and len(facts.reviews) == 2
    assert t.unused() == [] and [key(r) for r in t.log] == [key_of(ex) for ex in seq["exchanges"]]


# --- §3.3 п. 1: доставка ----------------------------------------------------------------


@pytest.mark.parametrize("side", ["graphql", "rest"])
def test_merged_disagreement_is_delivery_mismatch(derive, side):
    one_side = _graphql_delivery if side == "graphql" else _rest_delivery
    assert _code(derive, one_side("set_field", ["merged"], value=False)) == "delivery_mismatch"


def test_rest_graphql_head_mismatch(derive):
    other = "c" * 40
    assert _code(derive, _rest_delivery("set_field", ["head", "sha"], value=other)) == (
        "delivery_mismatch"
    )


def test_merge_commit_mismatch(derive):
    """Меняется REST-сторона: GraphQL-мерж-коммит, от которого зависят E5/E6 и сверка
    `MergedEvent`, остаётся живым — нарушение одно."""
    transforms = _rest_delivery("set_field", ["merge_commit_sha"], value="c" * 40)
    assert _code(derive, transforms) == "delivery_mismatch"


def test_base_mismatch(derive):
    assert _code(derive, _rest_delivery("set_field", ["base", "ref"], value="main")) == (
        "delivery_mismatch"
    )


def test_merged_at_mismatch(derive):
    transforms = _rest_delivery("retime", ["merged_at"], ts="2026-10-30T12:09:18Z")
    assert _code(derive, transforms) == "delivery_mismatch"


def test_rest_offset_and_graphql_z_agree(derive):
    """REST merged_at `…+00:00`, GraphQL `…Z` — одна метка; доставка совпадает."""
    d, _ = _ok(
        derive,
        _graphql_delivery("retime", ["mergedAt"], ts="2026-10-12T10:00:00Z")
        + _rest_delivery("retime", ["merged_at"], ts="2026-10-12T10:00:00+00:00"),
    )
    assert d.merged_at == "2026-10-12T10:00:00Z"


def _merger(graphql_by: Any, rest_by: Any) -> list[Record]:
    return _graphql_delivery("set_field", ["mergedBy"], value=graphql_by) + _rest_delivery(
        "set_field", ["merged_by"], value=rest_by
    )


def test_bot_login_forms_agree(derive):
    d, _ = _ok(
        derive,
        _merger(
            {"login": "df-reviewer", "__typename": "Bot"},
            {"login": "df-reviewer[bot]", "type": "Bot"},
        ),
    )
    assert (d.merged_by_login, d.merged_by_type_hint) == ("df-reviewer", "Bot")


def test_bot_login_forms_disagree(derive):
    transforms = _merger(
        {"login": "df-reviewer", "__typename": "Bot"}, {"login": "other[bot]", "type": "Bot"}
    )
    assert _code(derive, transforms) == "bot_login_mismatch"


def test_merger_type_disagrees(derive):
    transforms = _merger(
        {"login": "df-reviewer", "__typename": "Bot"}, {"login": "df-reviewer", "type": "User"}
    )
    assert _code(derive, transforms) == "bot_login_mismatch"


@pytest.mark.parametrize(
    "graphql_by,rest_by",
    [
        (None, None),
        ({"login": "ghost", "__typename": "User"}, {"login": "ghost", "type": "User"}),
        ({"login": "x", "__typename": "Organization"}, {"login": "x", "type": "Organization"}),
        ({"login": "", "__typename": "User"}, {"login": "", "type": "User"}),
    ],
)
def test_unresolved_merger(derive, graphql_by, rest_by):
    assert _code(derive, _merger(graphql_by, rest_by)) == "actor_unresolved"


@pytest.mark.parametrize("side", ["graphql", "rest"])
def test_unresolved_merger_on_one_side(derive, side):
    """Мержер не установлен по одну сторону (другая — живая) — тот же отказ, не
    `bot_login_mismatch`: идентичность проверяется до сверки."""
    transforms = (
        _graphql_delivery("set_field", ["mergedBy"], value=None)
        if side == "graphql"
        else _rest_delivery("set_field", ["merged_by"], value=None)
    )
    assert _code(derive, transforms) == "actor_unresolved"


def test_merged_without_merged_at_malformed(derive):
    transforms = _graphql_delivery("set_field", ["mergedAt"], value=None) + _rest_delivery(
        "set_field", ["merged_at"], value=None
    )
    assert _code(derive, transforms) == "malformed"


def test_unknown_pr_state(derive):
    assert _code(derive, _graphql_delivery("set_field", ["state"], value="DRAFT")) == (
        "unknown_state"
    )


# --- §3.3 п. 4: пути через read_act_facts ------------------------------------------------


def test_path_deleted_at_merge_is_none(derive):
    d, _ = _ok(
        derive, [_tf("set_field", [E6, "response", "body", "data", "repository", "m0"], value=None)]
    )
    assert d.paths == (PathBlob(P, None, BLOB),)


@pytest.mark.parametrize(
    "alias,value,expected",
    [
        ("h0", None, "path_absent_at_head"),
        ("h0", {"__typename": "Tree"}, "not_a_blob"),
        ("mc", None, "object_missing"),
    ],
)
def test_paths_refused(derive, alias, value, expected):
    path = [E6, "response", "body", "data", "repository", alias]
    assert _code(derive, [_tf("set_field", path, value=value)]) == expected


@pytest.mark.parametrize("bad", ["", "/abs", "../x", "a/../b", "a\x00b"])
def test_bad_path_raises(bad):
    t = replay_transport({"exchanges": []})
    with pytest.raises(ValueError):
        read_act_facts(t, REPO, 657, paths=[bad])
    assert t.log == []


def test_bad_repo_raises():
    with pytest.raises(ValueError):
        read_act_facts(replay_transport({"exchanges": []}), "devtools", 657, paths=[P])


# --- §3.4: отказы чтения ревью ----------------------------------------------------------


def test_dismissed_without_event_refused_by_read_act_facts(derive):
    transforms = _reviews("set_field", [COPILOT, "state"], value="DISMISSED")
    assert _code(derive, transforms) == "dismissed_without_event"


def test_missing_previous_state_refused_by_read_act_facts(derive, dismissal_node):
    """Вектор devtools Task 11 ждёт unavailable от read, не от effective (О13): отзыв после
    T без `previous_state` — отказ безусловный."""
    node = dismissal_node(5711735077, "2026-10-30T12:30:00Z", None, "andrei-shtanakov", "User")
    transforms = _reviews("set_field", [COPILOT, "state"], value="DISMISSED") + _timeline(
        "append_node", [], node=node
    )
    assert _code(derive, transforms) == "dismissal_without_previous_state"


def test_integrity_checked_in_first_pass_too(derive, dismissal_node):
    """Дефект только в проходе 1 и после T (проекции равны: отзыв после T вне среза) — всё
    равно отказ: факты первого прохода проверяются так же, как возвращаемого второго."""
    node = dismissal_node(999, "2026-10-30T12:30:00Z", "APPROVED", "andrei-shtanakov", "User")
    transforms = _timeline("append_node", [], node=node, passes=(T1,))
    assert _code(derive, transforms) == "orphan_dismissal"


# --- §3.3 пп. 5–7: устойчивость ---------------------------------------------------------


def test_delivery_reread_differs_unstable(derive):
    other = "c" * 40
    transforms = [
        _tf("set_field", [E2_AGAIN, *GQL_PULL, "headRefOid"], value=other),
        _tf("set_field", [E1_AGAIN, *REST_PULL, "head", "sha"], value=other),
    ]
    assert _code(derive, transforms) == "unstable"


def test_delivery_reread_not_merged_unstable(derive):
    transforms = [
        _tf("set_field", [E2_AGAIN, *GQL_PULL, "merged"], value=False),
        _tf("set_field", [E1_AGAIN, *REST_PULL, "merged"], value=False),
    ]
    assert _code(derive, transforms) == "unstable"


def test_merged_event_must_match(derive):
    transforms = _timeline("set_field", [0, "commit", "oid"], value="c" * 40)
    assert _code(derive, transforms) == "delivery_mismatch"


def test_two_merged_events(derive):
    node = {"__typename": "MergedEvent", "createdAt": T, "commit": {"oid": MERGE}}
    assert _code(derive, _timeline("append_node", [], node=node)) == "delivery_mismatch"


def test_merged_event_time_is_not_checked(derive):
    """Нормативна только сверка SHA: `createdAt` события ≠ `merged_at` — не отказ."""
    _ok(derive, _timeline("retime", [0, "createdAt"], ts="2026-10-30T12:09:20Z"))


def test_pass_head_differs_from_delivery(derive):
    """Голова в проходах ≠ голова доставки — `delivery_mismatch`, а не тихая подмена
    `head_sha` на `head_at_merge`."""
    transforms = [_tf("set_field", [i, *GQL_PULL, "headRefOid"], value="c" * 40) for i in (R1, R2)]
    assert _code(derive, transforms) == "delivery_mismatch"


def test_pass_base_differs_from_delivery(derive):
    """M0-F распространяется и на базу: `baseRefName` проходов ≠ базе доставки."""
    transforms = [_tf("set_field", [i, *GQL_PULL, "baseRefName"], value="main") for i in (R1, R2)]
    assert _code(derive, transforms) == "delivery_mismatch"


def test_iterator_paths_reach_read_paths(live):
    """`paths=iter([P])` не уходит в ветку «путей не запрошено» (§13)."""
    paths: Any = iter([P])
    got, _ = _read(live(SEQ), paths=paths)
    assert not isinstance(got, Unavailable), got
    assert got[0].paths == (PathBlob(P, BLOB, BLOB),)


def test_review_changed_before_t_between_passes_unstable(derive):
    transforms = _reviews("set_field", [0, "state"], value="CHANGES_REQUESTED", passes=(R2,))
    assert _code(derive, transforms) == "unstable"


def test_review_after_t_between_passes_is_stable(derive):
    """Ревью после T, появившееся во втором проходе, срез отбрасывает."""
    node = {
        "databaseId": 5711999999,
        "state": "CHANGES_REQUESTED",
        "submittedAt": "2026-10-30T12:20:00Z",
        "commit": {"oid": HEAD},
        "author": {"login": "bob", "__typename": "User"},
    }
    _, facts = _ok(derive, _reviews("append_node", [], node=node, passes=(R2,)))
    assert len(facts.reviews) == 3 and _states(facts) == {"ai-prosto": "APPROVED"}


def test_post_merge_dismissal_between_passes_is_stable(derive, dismissal_node):
    """Review Focus #1: проход 1 — ai-prosto APPROVED; проход 2 — его ревью DISMISSED и
    отзыв в T+5s. Устойчиво, на T — APPROVED."""
    node = dismissal_node(ALICE_ID, "2026-10-30T12:09:22Z", "APPROVED", "andrei-shtanakov", "User")
    transforms = _reviews("set_field", [0, "state"], value="DISMISSED", passes=(R2,))
    transforms += _timeline("append_node", [], node=node, passes=(T2,))
    _, facts = _ok(derive, transforms)
    assert facts.dismissals and facts.reviews[0].state == "DISMISSED"  # факты второго прохода
    assert _states(facts) == {"ai-prosto": "APPROVED"}


def test_dismissal_before_merge_after_preflight(derive, dismissal_node):
    """§5.6: отзыв после второго прохода предпроверки, но до мержа — чтение после мержа
    видит отзыв до T → эффективное DISMISSED."""
    node = dismissal_node(ALICE_ID, "2026-10-30T12:08:59Z", "APPROVED", "andrei-shtanakov", "User")
    transforms = _reviews("set_field", [0, "state"], value="DISMISSED") + _timeline(
        "append_node", [], node=node
    )
    _, facts = _ok(derive, transforms)
    assert _states(facts) == {"ai-prosto": "DISMISSED"}


def test_transport_failure_mid_sequence_is_unavailable(derive):
    """Обрыв на проходе — `Unavailable`, а не частичные факты."""
    spec = derive([SEQ], [])
    spec["exchanges"] = spec["exchanges"][:R2]
    got, _ = _read(spec)
    assert isinstance(got, Unavailable) and got.code == "transport"
