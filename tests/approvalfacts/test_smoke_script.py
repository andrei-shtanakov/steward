"""Verdict logic of the live smoke script (M0 Task 13), offline.

No network: every check that calls the library runs over ``replay_transport`` on the
committed live captures (``tests/fixtures/approvalfacts_v3/live``), mutated where a
case has no live target; scan pages are minimal synthetic exchanges.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from steward.approvalfacts.v3 import (
    Delivery,
    Dismissal,
    ReplayTransport,
    Request,
    Response,
    Review,
    ReviewFacts,
    Unavailable,
    read_act_facts,
    read_open_review,
    replay_transport,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "approval_facts_v3_smoke.py"
LIVE = ROOT / "tests" / "fixtures" / "approvalfacts_v3" / "live"
DEVTOOLS = "andrei-shtanakov/devtools"
POLICY = "andrei-shtanakov/approval-policy"
SHA = "b3651bcb2907bd6d1b6d421b018701520fb64066"
MERGE = "aa4e7e37d9f1ea34581c9c484d60074b6d3ac18e"
PR657_PATHS = ["docs/evidence/2026-10-30-df-stage2a-acceptance.md"]


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("approval_facts_v3_smoke", SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["approval_facts_v3_smoke"] = module
    spec.loader.exec_module(module)
    return module


SMOKE = _load()


# --- fixtures -------------------------------------------------------------------------


def _pr657() -> list[dict[str, Any]]:
    """The live #657 act sequence: E2, E1, E5, E6, E3, E4, E3, E4, E2, E1."""
    return copy.deepcopy(json.loads((LIVE / "pr657_act_sequence.json").read_text())["exchanges"])


def _op(exchanges: list[dict[str, Any]], op: str) -> list[dict[str, Any]]:
    return [ex for ex in exchanges if ex["request"].get("op") == op]


def _rest_pull(exchanges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [ex for ex in exchanges if ex["request"]["path"].endswith("/pulls/657")]


def _pull(ex: dict[str, Any]) -> dict[str, Any]:
    return ex["response"]["body"]["data"]["repository"]["pullRequest"]


def _passes(exchanges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        ex
        for ex in exchanges
        if ex["request"].get("op") in ("StewardV3Reviews", "StewardV3Timeline")
    ]


def _single(name: str) -> dict[str, Any]:
    return copy.deepcopy(json.loads((LIVE / f"{name}.json").read_text()))


def _node(number: int, **over: Any) -> dict[str, Any]:
    """A scan PR node: merged by a User, no reviews, no dismissals, unless overridden."""
    node: dict[str, Any] = {
        "number": number,
        "state": "MERGED",
        "merged": True,
        "baseRefName": "master",
        "author": {"login": "alice", "__typename": "User"},
        "mergedBy": {"login": "ai-prosto", "__typename": "User"},
        "reviews": {"totalCount": 0, "nodes": []},
        "timelineItems": {"nodes": [], "pageInfo": {"hasNextPage": False}},
    }
    node.update(over)
    return node


def _scan(*nodes: dict[str, Any]) -> Any:
    return SMOKE.Scan("ai-prosto", list(nodes))


def _strict(t: ReplayTransport) -> None:
    assert t.unexpected == [] and t.unused() == []


# --- report shape ---------------------------------------------------------------------


def test_summary_counts_every_outcome() -> None:
    rows = [SMOKE.Row("a", "verified", "x"), SMOKE.Row("b", "not_verified", "y")]
    got = SMOKE.summarize(rows)
    assert got["counts"] == {"verified": 1, "not_verified": 1, "contradicts": 0}
    assert [r["item"] for r in got["rows"]] == ["a", "b"]


def test_row_rejects_unknown_outcome() -> None:
    with pytest.raises(ValueError, match="unknown outcome"):
        SMOKE.Row("a", "passed", "x")


def test_guarded_turns_a_failed_read_into_not_verified() -> None:
    def skip() -> Any:
        raise SMOKE._Skip("H2 HTTP 403 repos/o/r")

    row = SMOKE.guarded("7", skip)
    assert (row.outcome, "403" in row.detail) == ("not_verified", True)
    assert SMOKE.guarded("7", lambda: {}["x"]).outcome == "not_verified"


# --- item 1 ---------------------------------------------------------------------------


def _facts(dismissals: tuple[Dismissal, ...], state: str) -> ReviewFacts:
    review = Review("1", "alice", "User", state, SHA, "2026-10-30T11:34:44Z")
    return ReviewFacts("o/r", 7, SHA, "master", "MERGED", (review,), dismissals)


def test_no_dismissal_candidate_is_not_verified_with_owner_reason() -> None:
    row = SMOKE.dismissal_row("o/r", {})
    assert row.outcome == "not_verified"
    assert SMOKE.NO_WRITE in row.detail


def test_zero_events_read_is_not_verified() -> None:
    transport = replay_transport({"exchanges": _passes(_pr657())})
    facts = read_open_review(transport, DEVTOOLS, 657)
    _strict(transport)
    assert SMOKE.dismissal_row(DEVTOOLS, {657: facts}).outcome == "not_verified"


def test_dismissed_review_without_event_contradicts() -> None:
    passes = _passes(_pr657())
    for ex in _op(passes, "StewardV3Reviews"):
        _pull(ex)["reviews"]["nodes"][1]["state"] = "DISMISSED"
    facts = read_open_review(replay_transport({"exchanges": passes}), DEVTOOLS, 657)
    assert isinstance(facts, Unavailable) and facts.code == "dismissed_without_event"
    assert SMOKE.dismissal_row(DEVTOOLS, {657: facts}).outcome == "contradicts"


def test_dismissal_count_decides() -> None:
    assert SMOKE.dismissal_row("o/r", {7: _facts((), "DISMISSED")}).outcome == "contradicts"
    ok = _facts((Dismissal("1", "bob", "2026-10-30T12:00:00Z", "APPROVED"),), "DISMISSED")
    assert SMOKE.dismissal_row("o/r", {7: ok}).outcome == "verified"


def test_other_library_refusal_is_not_verified() -> None:
    row = SMOKE.dismissal_row("o/r", {7: Unavailable("transport: down")})
    assert row.outcome == "not_verified"


# --- item 2 ---------------------------------------------------------------------------


def _app_merged(graphql_login: str, rest_login: str) -> list[dict[str, Any]]:
    """#657 as if merged by an App; one extra E1 for the script's raw REST read."""
    exchanges = _pr657()
    for ex in _op(exchanges, "StewardV3Delivery"):
        _pull(ex)["mergedBy"] = {"login": graphql_login, "__typename": "Bot"}
    for ex in _rest_pull(exchanges):
        ex["response"]["body"]["merged_by"] = {"login": rest_login, "type": "Bot"}
    return [*exchanges, copy.deepcopy(_rest_pull(exchanges)[0])]


def _app_scan() -> Any:
    return _scan(_node(657, mergedBy={"login": "df-app", "__typename": "Bot"}))


def test_app_merger_documented_forms_are_verified() -> None:
    transport = replay_transport({"exchanges": _app_merged("df-app", "df-app[bot]")})
    row = SMOKE.check_app_merger(transport, DEVTOOLS, _app_scan(), PR657_PATHS)
    _strict(transport)
    assert row.outcome == "verified", row.detail
    assert "REST df-app[bot]/Bot" in row.detail and "GraphQL df-app/Bot" in row.detail


@pytest.mark.parametrize(
    ("graphql_login", "rest_login"),
    [("df-app", "other[bot]"), ("df-app[bot]", "df-app[bot]")],
)
def test_app_merger_refuted_forms_contradict(graphql_login: str, rest_login: str) -> None:
    transport = replay_transport({"exchanges": _app_merged(graphql_login, rest_login)})
    row = SMOKE.check_app_merger(transport, DEVTOOLS, _app_scan(), PR657_PATHS)
    assert row.outcome == "contradicts", row.detail


def test_no_app_merger_reads_nothing() -> None:
    transport = replay_transport({"exchanges": []})
    row = SMOKE.check_app_merger(transport, DEVTOOLS, _scan(_node(1)))
    assert (row.outcome, transport.log) == ("not_verified", [])


# --- item 3 ---------------------------------------------------------------------------


def _opened(rest_state: str) -> list[dict[str, Any]]:
    """#657 as an open PR: E2 and E1 unmerged (twice E1: script + library), two passes."""
    exchanges = _pr657()
    e2 = _op(exchanges, "StewardV3Delivery")[0]
    _pull(e2).update(state="OPEN", merged=False, mergedAt=None, mergeCommit=None, mergedBy=None)
    e1 = _rest_pull(exchanges)[0]
    e1["response"]["body"].update(state=rest_state, merged=False, merged_at=None, merged_by=None)
    passes = _passes(exchanges)
    for ex in _op(passes, "StewardV3Reviews"):
        _pull(ex)["state"] = "OPEN"
    return [e2, e1, copy.deepcopy(e1), *passes]


def test_open_pr_is_verified() -> None:
    transport = replay_transport({"exchanges": _opened("open")})
    row = SMOKE.check_open_pr(transport, DEVTOOLS, _scan(_node(657, state="OPEN", merged=False)))
    _strict(transport)
    assert row.outcome == "verified", row.detail


def test_open_pr_rest_state_mismatch_is_not_verified() -> None:
    """REST ``closed`` beside GraphQL ``OPEN`` may be a race between reads: not a refutation."""
    transport = replay_transport({"exchanges": _opened("closed")})
    row = SMOKE.check_open_pr(transport, DEVTOOLS, _scan(_node(657, state="OPEN", merged=False)))
    assert row.outcome == "not_verified"


def test_open_pr_undocumented_rest_state_contradicts() -> None:
    transport = replay_transport({"exchanges": _opened("draft")})
    row = SMOKE.check_open_pr(transport, DEVTOOLS, _scan(_node(657, state="OPEN", merged=False)))
    assert row.outcome == "contradicts"


# --- items 4 and 8 --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "outcome"),
    [
        (200, {"status": "ahead"}, "verified"),
        (200, {"status": "behind"}, "not_verified"),
        (200, {"status": "weird"}, "contradicts"),
        (404, {"message": "Not Found"}, "not_verified"),
        (403, {"message": "rate limit"}, "not_verified"),
    ],
)
def test_compare_status_row(status: int, body: object, outcome: str) -> None:
    assert (
        SMOKE.compare_status_row("4a-compare-ahead", "ahead", status, body, "t").outcome == outcome
    )


def test_compare_read_failure_is_a_row_not_a_crash() -> None:
    h1 = _single("h1_repo_policy")
    h1["request"]["path"] = f"repos/{DEVTOOLS}"
    h1["response"] = {"status": 403, "body": {"message": "rate limit"}}
    t = replay_transport({"exchanges": [h1]})
    row = SMOKE.guarded("4a", lambda: SMOKE.check_compare_ahead(t, DEVTOOLS))
    assert row.outcome == "not_verified" and "H1 HTTP 403" in row.detail


def _cmp(status: str, base: str = "m" * 40) -> dict[str, Any]:
    return {"status": status, "ahead_by": 1, "behind_by": 2, "merge_base_commit": {"sha": base}}


@pytest.mark.parametrize(
    ("encoded", "plain", "outcome"),
    [
        ((200, _cmp("diverged")), (200, _cmp("diverged")), "verified"),
        ((404, {"message": "Not Found"}), (200, _cmp("diverged")), "contradicts"),
        ((200, _cmp("diverged", "x" * 40)), (200, _cmp("diverged")), "not_verified"),
        ((403, {"message": "rate limit"}), (200, _cmp("diverged")), "not_verified"),
        ((502, None), (200, _cmp("diverged")), "not_verified"),
        ((None, None), (200, _cmp("diverged")), "not_verified"),
        ((200, _cmp("diverged")), (404, None), "not_verified"),
    ],
)
def test_slash_compare_row(encoded: Any, plain: Any, outcome: str) -> None:
    assert SMOKE.slash_compare_row(encoded, plain, ()).outcome == outcome


def test_compare_path_is_the_library_path() -> None:
    """The smoke's E5 path for a ``/`` base equals the path the library itself sends."""
    exchanges = _pr657()[:2]
    _pull(exchanges[0])["baseRefName"] = "feat/x"
    exchanges[1]["response"]["body"]["base"]["ref"] = "feat/x"
    transport = replay_transport({"exchanges": exchanges})
    read_act_facts(transport, DEVTOOLS, 657, paths=PR657_PATHS)
    sent = [r.path for r in transport.log if "/compare/" in r.path]
    owner, name = DEVTOOLS.split("/")
    assert sent == [SMOKE.compare_path(owner, name, "feat/x", MERGE)]
    assert "feat%2Fx..." in sent[0]


# --- item 5 ---------------------------------------------------------------------------

GHOST = {"login": "ghost", "__typename": "User"}


def _review(author: object) -> dict[str, Any]:
    return {"totalCount": 1, "nodes": [{"state": "APPROVED", "author": author}]}


def test_ghost_review_author_gives_actor_unresolved() -> None:
    passes = _passes(_pr657())
    for ex in _op(passes, "StewardV3Reviews"):
        _pull(ex)["reviews"]["nodes"][0]["author"] = GHOST
    transport = replay_transport({"exchanges": passes})
    row = SMOKE.check_deleted_user(transport, DEVTOOLS, _scan(_node(657, reviews=_review(GHOST))))
    assert row.outcome == "verified", row.detail


def test_facts_without_a_ghost_login_do_not_contradict() -> None:
    """The scan saw a ghost, the library's facts carry none: nothing positively refuted."""
    transport = replay_transport({"exchanges": _passes(_pr657())})
    row = SMOKE.check_deleted_user(transport, DEVTOOLS, _scan(_node(657, reviews=_review(GHOST))))
    assert row.outcome == "not_verified"


def test_ghost_merger_is_judged_by_read_act_facts() -> None:
    exchanges = _pr657()[:2]
    _pull(exchanges[0])["mergedBy"] = None
    exchanges[1]["response"]["body"]["merged_by"] = None
    transport = replay_transport({"exchanges": exchanges})
    row = SMOKE.check_deleted_user(transport, DEVTOOLS, _scan(_node(657, mergedBy=None)))
    _strict(transport)
    assert row.outcome == "verified", row.detail


@pytest.mark.parametrize("author", [None, GHOST])
def test_deleted_pr_author_alone_is_not_verified(author: object) -> None:
    transport = replay_transport({"exchanges": []})
    row = SMOKE.check_deleted_user(transport, DEVTOOLS, _scan(_node(657, author=author)))
    assert (row.outcome, transport.log) == ("not_verified", [])
    assert "вне §3.4.1" in row.detail


@pytest.mark.parametrize(
    ("actor", "want"),
    [
        (None, True),
        ({"login": "Ghost", "__typename": "User"}, True),
        ({"login": "m", "__typename": "Mannequin"}, True),
        ({"login": "alice", "__typename": "User"}, False),
        ({"login": "app", "__typename": "Bot"}, False),
    ],
)
def test_unresolved_actor(actor: object, want: bool) -> None:
    assert SMOKE.unresolved(actor) is want


# --- item 6 and scan ------------------------------------------------------------------


def _pending(*reviews: tuple[str, str]) -> Any:
    nodes = [{"state": s, "author": {"login": who, "__typename": "User"}} for s, who in reviews]
    return _scan(_node(1, reviews={"totalCount": len(nodes), "nodes": nodes}))


def test_pending_visibility() -> None:
    assert SMOKE.check_pending(_pending(("PENDING", "ai-prosto")), "o/r").outcome == "not_verified"
    got = SMOKE.check_pending(_pending(("PENDING", "alice")), "o/r")
    assert got.outcome == "verified" and got.targets == ("o/r#1",)


def _scan_exchange(after: str | None, body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    owner, name = DEVTOOLS.split("/")
    return {
        "request": {
            "method": "POST",
            "path": "graphql",
            "op": "StewardV3SmokeScan",
            "variables": {"owner": owner, "name": name, "after": after},
            "query": SMOKE.Q_SCAN,
        },
        "response": {"status": status, "body": body},
    }


def _page(nodes: list[dict[str, Any]], cursor: str | None, more: bool) -> dict[str, Any]:
    conn = {"nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": cursor}}
    return {"data": {"viewer": {"login": "ai-prosto"}, "repository": {"pullRequests": conn}}}


def test_scan_follows_cursor_and_records_truncation() -> None:
    big = _node(2, reviews={"totalCount": 101, "nodes": [{"state": "APPROVED", "author": None}]})
    transport = replay_transport(
        {
            "exchanges": [
                _scan_exchange(None, _page([_node(1)], "c1", True)),
                _scan_exchange("c1", _page([big], "c2", False)),
            ]
        }
    )
    scan = SMOKE.scan_prs(transport, DEVTOOLS)
    _strict(transport)
    assert [pr["number"] for pr in scan.prs] == [1, 2]
    assert (scan.viewer, scan.truncated) == ("ai-prosto", [2])
    assert "[2]" in scan.coverage()


def test_scan_stuck_cursor_hits_page_cap() -> None:
    stuck = [_scan_exchange("c1", _page([], "c1", True)) for _ in range(SMOKE.MAX_SCAN_PAGES)]
    first = _scan_exchange(None, _page([], "c1", True))
    got = SMOKE.scan_prs(replay_transport({"exchanges": [first, *stuck]}), DEVTOOLS)
    assert isinstance(got, Unavailable) and got.code == "pagination"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"errors": [{"message": "x"}], "data": None}, 200, "http"),
        ({"message": "Bad credentials"}, 401, "http"),
        (_page([], None, True), 200, "pagination"),
        ({"data": {"viewer": {}}}, 200, "malformed"),
    ],
)
def test_scan_failures_are_unavailable(body: dict[str, Any], status: int, code: str) -> None:
    transport = replay_transport({"exchanges": [_scan_exchange(None, body, status)]})
    got = SMOKE.scan_prs(transport, DEVTOOLS)
    assert isinstance(got, Unavailable) and got.code == code


def test_failed_scan_makes_every_scan_item_not_verified() -> None:
    rows = SMOKE.scan_rows(replay_transport({"exchanges": []}), DEVTOOLS, Unavailable("http: x"))
    assert [r.item for r in rows] == list(SMOKE.SCAN_ITEMS)
    assert {r.outcome for r in rows} == {"not_verified"}


# --- item 7 ---------------------------------------------------------------------------


@dataclass
class _Recording:
    """Replay of the live H1–H6 captures with rate-limit headers (as AnonTransport)."""

    replay: ReplayTransport
    limit: str | None = "60"
    ratelimit: list[tuple[str, str | None, str | None]] = field(default_factory=list)

    def send(self, request: Request) -> Response:
        response = self.replay.send(request)
        remaining = None if self.limit is None else str(59 - len(self.ratelimit))
        self.ratelimit.append((request.path, remaining, self.limit))
        return response


H_CAPTURES = (
    "h1_repo_policy",
    "h2_branch_policy",
    "h3_commit_root_no_parents",
    "h4_commit_pulls_direct_push",
    "h5_tree_recursive_policy",
    "h6_blob_policy_approvers",
)


def _anon(limit: str | None = "60", **status: int) -> _Recording:
    """H1–H6 for the script's reads, H1–H4 again for ``read_policy_history``."""
    exchanges = [_single(name) for name in H_CAPTURES]
    for ex in exchanges:
        key = ex_key(ex)
        if key in status:
            ex["response"] = {"status": status[key], "body": {"message": "rate limit"}}
    return _Recording(
        replay_transport({"exchanges": [*exchanges, *copy.deepcopy(exchanges[:4])]}), limit
    )


def ex_key(ex: dict[str, Any]) -> str:
    """Row name (``h1``…``h6``) of a captured H exchange, by its path."""
    path = ex["request"]["path"]
    for key, marker in (
        ("h6", "/git/blobs/"),
        ("h5", "/git/trees/"),
        ("h4", "/pulls"),
        ("h3", "/commits/"),
        ("h2", "/branches/"),
    ):
        if marker in path:
            return key
    return "h1"


def test_anonymous_h1_h6_verified() -> None:
    anon = _anon()
    row = SMOKE.check_anonymous(POLICY, anon)
    assert anon.replay.unexpected == []
    assert row.outcome == "verified", row.detail
    assert "commit_without_pr" in row.detail and "['60']" in row.detail


@pytest.mark.parametrize("limit", [None, "5000"])
def test_anonymous_without_anonymous_quota_is_not_verified(limit: str | None) -> None:
    assert SMOKE.check_anonymous(POLICY, _anon(limit)).outcome == "not_verified"


def test_anonymous_403_is_a_not_verified_row_with_status() -> None:
    anon = _anon(h2=403)
    row = SMOKE.guarded(SMOKE.ITEM_ANON, lambda: SMOKE.check_anonymous(POLICY, anon))
    assert row.outcome == "not_verified" and "H2 HTTP 403" in row.detail


# --- transport and allowlist ----------------------------------------------------------


def test_anonymous_transport_never_raises_on_non_get() -> None:
    got = SMOKE.AnonTransport().send(Request("POST", "graphql", op="X", query="query X{a}"))
    assert got.status is None and "GET-only" in str(got.error)


def test_repository_outside_allowlist_is_refused() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        SMOKE.scan_prs(replay_transport({"exchanges": []}), "someone/private")


# --- the decision rule: one cell of the combination table per case ---------------------
#
# Fix round 2 (Task 13 report): a verdict is ``contradicts`` / ``verified`` only for kinds
# listed in ``RULES``; every other outcome kind is ``not_verified``. Each cell that must be
# neither has its negative twin here.

AU_MERGER = Unavailable("actor_unresolved: GraphQL pull request mergedBy")
AU_MERGER_REST = Unavailable("actor_unresolved: REST pull request merged_by")
AU_REVIEW = Unavailable("actor_unresolved: review 5711633458")
AU_DISMISSAL = Unavailable("actor_unresolved: dismissal 5711633458")
HTTP_403 = Unavailable("http: 403 graphql")
HTTP_404 = Unavailable("http: 404 repos/o/r/pulls/7")
TRANSPORT = Unavailable("transport: gh did not run: timed out")
MALFORMED = Unavailable("malformed: pull request: state 1 is not a string")
NO_ANSWER = (HTTP_403, HTTP_404, TRANSPORT)


def _delivery(login: str | None = "df-app", hint: str | None = "Bot") -> Any:
    return Delivery(True, MERGE, "2026-10-30T12:09:17Z", login, hint, SHA, True, ())


def _outcome(item: str, kind: str) -> str:
    return SMOKE.decide(item, kind, "").outcome


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        (AU_MERGER, "actor_unresolved:merger"),
        (AU_MERGER_REST, "actor_unresolved:merger"),
        (AU_REVIEW, "actor_unresolved:review"),
        (AU_DISMISSAL, "actor_unresolved:dismissal"),
        (Unavailable("actor_unresolved: somewhere else"), "actor_unresolved:other"),
        (HTTP_403, "refusal:http"),
        (TRANSPORT, "refusal:transport"),
        (MALFORMED, "refusal:malformed"),
    ],
)
def test_kind_of_splits_by_library_where(reason: Unavailable, kind: str) -> None:
    assert SMOKE.kind_of(reason) == kind


def test_unlisted_kind_is_not_verified_for_every_item() -> None:
    assert {_outcome(item, "anything:else") for item in SMOKE.RULES} == {"not_verified"}


@pytest.mark.parametrize("got", [AU_MERGER, AU_REVIEW, AU_DISMISSAL, *NO_ANSWER, MALFORMED])
def test_item1_cells_not_verified(got: Unavailable) -> None:
    assert SMOKE.dismissal_row("o/r", {7: got}).outcome == "not_verified"


@pytest.mark.parametrize(
    ("got", "clean"),
    [
        (AU_MERGER, False),
        (AU_MERGER_REST, False),
        (AU_REVIEW, True),
        (AU_DISMISSAL, True),
        (MALFORMED, True),
        *((g, True) for g in NO_ANSWER),
    ],
)
def test_item2_cells_not_verified(got: Unavailable, clean: bool) -> None:
    rest = {"login": "df-app[bot]", "type": "Bot"}
    assert _outcome("2-app-merger", SMOKE.app_merger_kind(got, rest, clean)) == "not_verified"


@pytest.mark.parametrize(
    ("got", "rest_login", "outcome"),
    [
        (AU_MERGER, "df-app[bot]", "contradicts"),
        (Unavailable("bot_login_mismatch: x"), "df-app[bot]", "contradicts"),
        ((_delivery("df-app"), None), "df-app[bot]", "verified"),
        ((_delivery("df-app"), None), "df-app", "contradicts"),
    ],
)
def test_item2_cells_decided(got: object, rest_login: str, outcome: str) -> None:
    kind = SMOKE.app_merger_kind(got, {"login": rest_login, "type": "Bot"}, True)
    assert _outcome("2-app-merger", kind) == outcome


def test_item2_ghost_reviewer_on_app_merged_pr_is_not_verified() -> None:
    """R1: correct App forms, a reviewer later deleted — the merger is not refuted."""
    exchanges = _app_merged("df-app", "df-app[bot]")
    for ex in _op(exchanges, "StewardV3Reviews"):
        _pull(ex)["reviews"]["nodes"][0]["author"] = None
    transport = replay_transport({"exchanges": exchanges})
    row = SMOKE.check_app_merger(transport, DEVTOOLS, _app_scan(), PR657_PATHS)
    assert row.outcome == "not_verified", row.detail
    assert "[actor_unresolved:review]" in row.detail


@pytest.mark.parametrize("bad", ["facts", "act"])
@pytest.mark.parametrize("got", [AU_REVIEW, AU_DISMISSAL, *NO_ANSWER, MALFORMED])
def test_item3_cells_not_verified(bad: str, got: Unavailable) -> None:
    facts = _facts((), "APPROVED") if bad == "act" else got
    act = got if bad == "act" else (_delivery(), None)
    assert _outcome("3-open-pr", SMOKE.open_pr_kind("open", facts, act)) == "not_verified"


def test_item3_unknown_state_contradicts() -> None:
    kind = SMOKE.open_pr_kind("open", Unavailable("unknown_state: x"), None)
    assert _outcome("3-open-pr", kind) == "contradicts"


@pytest.mark.parametrize(
    ("status", "body"), [(403, None), (404, None), (None, None), (200, None), (200, [1])]
)
def test_item4_cells_not_verified(status: int | None, body: object) -> None:
    row = SMOKE.compare_status_row("4b-compare-diverged", "diverged", status, body, "t")
    assert row.outcome == "not_verified"


@pytest.mark.parametrize("got", [AU_MERGER, *NO_ANSWER, MALFORMED])
def test_item5_pass_cells_not_verified(got: Unavailable) -> None:
    assert _outcome("5-deleted-user", SMOKE.pass_ghost_kind(got)) == "not_verified"


@pytest.mark.parametrize(
    ("got", "outcome"),
    [
        (AU_REVIEW, "verified"),
        (AU_DISMISSAL, "verified"),
        (
            _facts((Dismissal("1", "ghost", "2026-10-30T12:00:00Z", "APPROVED"),), "DISMISSED"),
            "contradicts",
        ),
        (_facts((), "APPROVED"), "not_verified"),
    ],
)
def test_item5_pass_cells_decided(got: object, outcome: str) -> None:
    assert _outcome("5-deleted-user", SMOKE.pass_ghost_kind(got)) == outcome


@pytest.mark.parametrize(
    ("got", "clean"),
    [
        (AU_REVIEW, True),
        (AU_DISMISSAL, True),
        (AU_MERGER, False),
        (MALFORMED, True),
        ((_delivery("alice", "User"), None), True),
        *((g, True) for g in NO_ANSWER),
    ],
)
def test_item5_merger_cells_not_verified(got: object, clean: bool) -> None:
    """Incl. the R1 mirror: a review-side refusal never verifies the merger branch."""
    kind = SMOKE.merger_ghost_kind(got, clean)
    assert _outcome("5-deleted-user", kind) == "not_verified"


@pytest.mark.parametrize(
    ("got", "outcome"),
    [(AU_MERGER, "verified"), ((_delivery("ghost", "User"), None), "contradicts")],
)
def test_item5_merger_cells_decided(got: object, outcome: str) -> None:
    assert _outcome("5-deleted-user", SMOKE.merger_ghost_kind(got, True)) == outcome


QUOTA = (["59", "58"], ["60"])


@pytest.mark.parametrize(
    ("history", "quota", "outcome"),
    [
        (HTTP_403, QUOTA, "not_verified"),
        (HTTP_404, QUOTA, "not_verified"),
        (TRANSPORT, QUOTA, "not_verified"),
        (MALFORMED, QUOTA, "contradicts"),
        (Unavailable("pagination: x"), QUOTA, "contradicts"),
        ((), (["59", None], ["60"]), "not_verified"),
        ((), (["59"], ["5000"]), "not_verified"),
        ((), QUOTA, "verified"),
    ],
)
def test_item7_cells(history: object, quota: Any, outcome: str) -> None:
    assert _outcome(SMOKE.ITEM_ANON, SMOKE.anonymous_kind(history, *quota)) == outcome


def test_item7_quota_running_out_mid_history_is_not_verified() -> None:
    """R2: the six H-reads answer, the library's own H2 gets 403 — no refutation."""
    anon = _anon()
    h2 = Request("GET", "repos/andrei-shtanakov/approval-policy/branches/main")
    queue = anon.replay.queues[h2]
    assert len(queue) == 2  # the script's read, then the library's
    queue[1] = Response(403, body={"message": "API rate limit exceeded"})
    row = SMOKE.check_anonymous(POLICY, anon)
    assert row.outcome == "not_verified", row.detail
    assert "[refusal:http]" in row.detail


def test_internal_exception_is_flagged_and_exits_2() -> None:
    row = SMOKE.guarded("5-deleted-user", lambda: {}["missing"])
    assert (row.outcome, row.internal) == ("not_verified", True)
    report = SMOKE.summarize([row, SMOKE.Row("4a-compare-ahead", "contradicts", "x")])
    assert report["internal_errors"] == ["5-deleted-user"]
    assert SMOKE.exit_code(report) == 2


def test_forge_non_200_is_not_internal_and_exits_0() -> None:
    def skip() -> Any:
        raise SMOKE._Skip("H1 HTTP 403")

    report = SMOKE.summarize([SMOKE.guarded("4a-compare-ahead", skip)])
    assert (report["internal_errors"], SMOKE.exit_code(report)) == ([], 0)
