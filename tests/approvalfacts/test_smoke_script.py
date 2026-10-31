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
from collections.abc import Callable
from typing import Any

import pytest

from steward.approvalfacts.v3 import (
    UNAVAILABLE_CODES,
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


def test_guarded_turns_a_value_error_into_an_internal_row() -> None:
    """Paid review #228: a library ValueError (bad anchor) must not abort the run."""

    def bad_anchor() -> Any:
        raise ValueError("anchor is not a 40-hex sha")

    row = SMOKE.guarded("7", bad_anchor)
    assert (row.outcome, row.internal) == ("not_verified", True)


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


# --- the decision rule ------------------------------------------------------------------
#
# Fix round 3 (Task 13 report): a library refusal's kind is
# ``<function>:<code>:<origin>``, and ``RULES`` holds explicit allow-lists. The exhaustive
# test below enumerates every code the library defines × every origin the script derives
# × every item position that consumes a library result, against an independent table
# (``EXPECTED``): only the listed cells verify/contradict, every other cell is not_verified.

REST_BOT = {"login": "df-app[bot]", "type": "Bot"}
QUOTA = (["59", "58"], ["60"])


def _delivery(login: str | None = "df-app", hint: str | None = "Bot") -> Any:
    return Delivery(True, MERGE, "2026-10-30T12:09:17Z", login, hint, SHA, True, ())


def _outcome(item: str, kind: str) -> str:
    return SMOKE.decide(item, kind, "").outcome


#: A detail the library would write for each origin (its ``where`` or REST path).
ORIGIN_DETAIL = {
    "pr": "GraphQL pull request: state 'DRAFT'",
    "pass_pr": "pull request: state None is not a string",
    "merger": "GraphQL pull request mergedBy",
    "review": "review 5711633458: 'WEIRD'",
    "dismissal": "dismissal 5711633458: 'WEIRD'",
    "timeline": "timeline items ['X']",
    "compare": "repos/o/r/compare/master...abc: compare status 'x'",
    "blob": "StewardV3Blobs: m0 is missing",
    "tree": "repos/o/r/git/trees/abc?recursive=1: tree is not a list",
    "commit": "404 repos/o/r/commits/abc",
    "commit_pulls": "403 repos/o/r/commits/abc/pulls?per_page=100",
    "branch": "403 repos/o/r/branches/main",
    "repo": "403 repos/o/r",
    "graphql": "403 graphql",
    "link": "Link header outside P1 grammar: '<x>'",
    "chain": "anchor abc not within 1000 links of def",
    "unknown": "gh did not run: timed out",
}
#: Codes the library raises at exactly one site (the code names the object).
CODE_IMPLIED = {
    "bot_login_mismatch": "merger",
    "dismissal_without_previous_state": "dismissal",
    "duplicate_dismissal": "dismissal",
    "orphan_dismissal": "dismissal",
    "branch_not_default": "repo",
    "chain_limit": "chain",
    "no_pr_for_commit": "commit_pulls",
    "tree_truncated": "tree",
}

#: Item positions that consume a library ``Unavailable`` → the kind function at it.
POSITIONS: dict[tuple[str, str], Callable[[Unavailable], str]] = {
    ("1-review-dismissed-event", "open_review"): SMOKE.dismissal_kind,
    ("2-app-merger", "act clean"): lambda u: SMOKE.app_merger_kind(u, REST_BOT, True),
    ("2-app-merger", "act tainted"): lambda u: SMOKE.app_merger_kind(u, REST_BOT, False),
    ("3-open-pr", "act"): lambda u: SMOKE.open_pr_kind("open", _facts((), "APPROVED"), u),
    ("3-open-pr", "open_review"): lambda u: SMOKE.open_pr_kind("open", u, (_delivery(), None)),
    ("5-deleted-user", "open_review"): SMOKE.pass_ghost_kind,
    ("5-deleted-user", "act clean"): lambda u: SMOKE.merger_ghost_kind(u, True),
    ("5-deleted-user", "act tainted"): lambda u: SMOKE.merger_ghost_kind(u, False),
    (SMOKE.ITEM_ANON, "history"): lambda u: SMOKE.anonymous_kind(u, *QUOTA),
}
#: Items whose rows never consume a library ``Unavailable``.
NON_LIBRARY_ITEMS = {
    "4a-compare-ahead",
    "4b-compare-diverged",
    "6-pending-visibility",
    "8-compare-slash-base",
}

_H = ("repo", "branch", "commit", "commit_pulls", "tree", "blob", "link")
#: (item, position, code, origin) → outcome. Every cell not listed is not_verified.
EXPECTED: dict[tuple[str, str, str, str], str] = {
    ("1-review-dismissed-event", "open_review", "dismissal_without_previous_state", "dismissal"): (
        "contradicts"
    ),
    ("1-review-dismissed-event", "open_review", "dismissed_without_event", "review"): (
        "contradicts"
    ),
    ("2-app-merger", "act clean", "actor_unresolved", "merger"): "contradicts",
    ("2-app-merger", "act clean", "bot_login_mismatch", "merger"): "contradicts",
    ("2-app-merger", "act tainted", "bot_login_mismatch", "merger"): "contradicts",
    ("3-open-pr", "act", "unknown_state", "pr"): "contradicts",
    ("5-deleted-user", "open_review", "actor_unresolved", "review"): "verified",
    ("5-deleted-user", "open_review", "actor_unresolved", "dismissal"): "verified",
    ("5-deleted-user", "act clean", "actor_unresolved", "merger"): "verified",
    **{(SMOKE.ITEM_ANON, "history", "malformed", origin): "contradicts" for origin in _H},
}


def test_origin_samples_cover_every_derivable_origin() -> None:
    assert set(ORIGIN_DETAIL) == set(SMOKE.ORIGINS)
    for origin, detail in ORIGIN_DETAIL.items():
        assert SMOKE.origin_of("malformed", detail) == origin, detail


def test_code_implied_origins_match_the_script() -> None:
    assert CODE_IMPLIED == SMOKE.CODE_ORIGIN
    assert set(CODE_IMPLIED) <= UNAVAILABLE_CODES


def test_every_item_is_covered() -> None:
    assert {item for item, _ in POSITIONS} | NON_LIBRARY_ITEMS == set(SMOKE.RULES)


def test_rules_are_explicit_allow_lists() -> None:
    """Every listed library kind names a real code and a derivable, known origin."""
    for verifying, contradicting in SMOKE.RULES.values():
        for kind in verifying | contradicting:
            fn, _, rest = kind.partition(":")
            code, _, origin = rest.partition(":")
            if fn in ("open_review", "act", "history") and code in UNAVAILABLE_CODES:
                assert origin.split(":")[0] in set(SMOKE.ORIGINS) - {"unknown"}, kind


@pytest.mark.parametrize("position", list(POSITIONS), ids=lambda p: f"{p[0]}|{p[1]}")
def test_every_code_and_origin_against_the_table(position: tuple[str, str]) -> None:
    """All 24 library codes × every origin at this item position: the verdict is the
    ``EXPECTED`` cell, and every unlisted cell is not_verified."""
    item, where = position
    wrong = []
    for code in sorted(UNAVAILABLE_CODES):
        for origin, detail in ORIGIN_DETAIL.items():
            derived = CODE_IMPLIED.get(code, origin)
            kind = POSITIONS[position](Unavailable(f"{code}: {detail}"))
            got = _outcome(item, kind)
            want = EXPECTED.get((item, where, code, derived), "not_verified")
            if got != want:
                wrong.append((code, origin, kind, got, want))
    assert wrong == []
    assert len(UNAVAILABLE_CODES) == 24


@pytest.mark.parametrize(
    ("detail", "origin"),
    [
        ("GraphQL pull request: state 'DRAFT'", "pr"),
        ("GraphQL pull request: state None is not a string", "pr"),
        ("GraphQL pull request mergedBy", "merger"),
        ("REST pull request merged_by", "merger"),
        ("GraphQL pull request mergedAt is null for a merged pull request", "pr"),
        ("REST pull request merged_at is null for a merged pull request", "pr"),
        ("pull request fields changed between review pages", "pass_pr"),
        ("review 57: 'WEIRD'", "review"),
        ("review_id 'x' is not decimal", "review"),
        ("dismissal of non-DISMISSED review 5", "dismissal"),
        ("ReviewDismissedEvent without review", "dismissal"),
        ("404 repos/o/r/pulls/7", "pr"),
        ("404 repos/o/r/compare/feat%2Fx...abc", "compare"),
        ("403 repos/o/r/commits/abc/pulls?per_page=100&page=2", "commit_pulls"),
        ("403 repos/o/r/git/blobs/abc", "blob"),
        ("403 repos/o/r", "repo"),
        ("Link next outside https://api.github.com/: 'x'", "link"),
        ("abc: merge commit of PRs [1, 2]", "unknown"),
    ],
)
def test_origin_of_real_library_details(detail: str, origin: str) -> None:
    assert SMOKE.origin_of("malformed", detail) == origin


# --- named twins for the round-3 findings (also cells of the table above) ---------------


def test_r4_review_state_refusal_does_not_contradict_open_pr() -> None:
    facts = Unavailable("unknown_state: review 5711633458: 'WEIRD'")
    kind = SMOKE.open_pr_kind("open", facts, (_delivery(), None))
    assert (kind, _outcome("3-open-pr", kind)) == (
        "open_review:unknown_state:review",
        "not_verified",
    )


def test_r4_pr_state_refusal_from_act_contradicts_first() -> None:
    act = Unavailable("unknown_state: GraphQL pull request: state 'DRAFT'")
    facts = Unavailable("unknown_state: review 1: 'WEIRD'")
    assert _outcome("3-open-pr", SMOKE.open_pr_kind("open", facts, act)) == "contradicts"


@pytest.mark.parametrize(
    "reason",
    [
        "tree_truncated: repos/o/r/git/trees/abc?recursive=1",
        "chain_limit: anchor abc not within 1000 links of def",
        "branch_not_default: default branch 'main', not 'master'",
        "not_a_blob: policy/approvers.env",
        "bad_timestamp: '2026-10-30T12:09:17.5Z'",
        "pagination: repos/o/r/commits/abc/pulls?per_page=100: more than 50 pages",
    ],
)
def test_r5_content_refusals_do_not_contradict_anonymous(reason: str) -> None:
    kind = SMOKE.anonymous_kind(Unavailable(reason), *QUOTA)
    assert _outcome(SMOKE.ITEM_ANON, kind) == "not_verified", kind


@pytest.mark.parametrize(
    ("history", "quota", "outcome"),
    [
        (Unavailable("http: 403 repos/o/r/branches/main"), QUOTA, "not_verified"),
        (Unavailable("transport: timed out"), QUOTA, "not_verified"),
        (Unavailable("malformed: repos/o/r/git/blobs/abc: encoding 'utf-8'"), QUOTA, "contradicts"),
        ((), (["59", None], ["60"]), "not_verified"),
        ((), (["59"], ["5000"]), "not_verified"),
        ((), QUOTA, "verified"),
    ],
)
def test_item7_answered_and_quota(history: object, quota: Any, outcome: str) -> None:
    assert _outcome(SMOKE.ITEM_ANON, SMOKE.anonymous_kind(history, *quota)) == outcome


@pytest.mark.parametrize(
    ("got", "rest_login", "outcome"),
    [
        ((_delivery("df-app"), None), "df-app[bot]", "verified"),
        ((_delivery("df-app"), None), "df-app", "contradicts"),
    ],
)
def test_item2_ok_forms(got: object, rest_login: str, outcome: str) -> None:
    kind = SMOKE.app_merger_kind(got, {"login": rest_login, "type": "Bot"}, True)
    assert _outcome("2-app-merger", kind) == outcome


@pytest.mark.parametrize(
    ("got", "outcome"),
    [
        (
            _facts((Dismissal("1", "ghost", "2026-10-30T12:00:00Z", "APPROVED"),), "DISMISSED"),
            "contradicts",
        ),
        (_facts((), "APPROVED"), "not_verified"),
    ],
)
def test_item5_pass_facts(got: object, outcome: str) -> None:
    assert _outcome("5-deleted-user", SMOKE.pass_ghost_kind(got)) == outcome


@pytest.mark.parametrize(
    ("login", "outcome"), [("ghost", "contradicts"), ("alice", "not_verified")]
)
def test_item5_merger_facts(login: str, outcome: str) -> None:
    kind = SMOKE.merger_ghost_kind((_delivery(login, "User"), None), True)
    assert _outcome("5-deleted-user", kind) == outcome


@pytest.mark.parametrize(
    ("actor", "want"),
    [
        (None, True),
        ({"login": "Ghost", "__typename": "User"}, True),
        ({"login": None, "__typename": "User"}, True),
        ({"login": "m", "__typename": "Mannequin"}, False),
        ({"login": "alice", "__typename": "User"}, False),
    ],
)
def test_deleted_actor(actor: object, want: bool) -> None:
    assert SMOKE.deleted(actor) is want


def test_r6_mannequin_reviewer_is_not_an_item5_candidate() -> None:
    mannequin = {"login": "m", "__typename": "Mannequin"}
    transport = replay_transport({"exchanges": []})
    row = SMOKE.check_deleted_user(
        transport, DEVTOOLS, _scan(_node(657, reviews=_review(mannequin)))
    )
    assert (row.outcome, transport.log) == ("not_verified", [])
    assert "не удалённые" in row.detail


def test_mannequin_beside_ghost_is_not_an_item5_pass_candidate() -> None:
    """Paid review #228: the library refuses the first unresolved actor, so a PR with a
    Mannequin and a ghost reviewer cannot confirm the ghost case."""
    mannequin = {"login": "m", "__typename": "Mannequin"}
    reviews = {
        "totalCount": 2,
        "nodes": [
            {"state": "APPROVED", "author": mannequin},
            {"state": "APPROVED", "author": GHOST},
        ],
    }
    transport = replay_transport({"exchanges": []})
    row = SMOKE.check_deleted_user(transport, DEVTOOLS, _scan(_node(657, reviews=reviews)))
    assert (row.outcome, transport.log) == ("not_verified", [])


def test_item2_ghost_reviewer_on_app_merged_pr_is_not_verified() -> None:
    """R1: correct App forms, a reviewer later deleted — the merger is not refuted."""
    exchanges = _app_merged("df-app", "df-app[bot]")
    for ex in _op(exchanges, "StewardV3Reviews"):
        _pull(ex)["reviews"]["nodes"][0]["author"] = None
    transport = replay_transport({"exchanges": exchanges})
    row = SMOKE.check_app_merger(transport, DEVTOOLS, _app_scan(), PR657_PATHS)
    assert row.outcome == "not_verified", row.detail
    assert "[act:actor_unresolved:review]" in row.detail


def test_item7_quota_running_out_mid_history_is_not_verified() -> None:
    """R2: the six H-reads answer, the library's own H2 gets 403 — no refutation."""
    anon = _anon()
    h2 = Request("GET", "repos/andrei-shtanakov/approval-policy/branches/main")
    queue = anon.replay.queues[h2]
    assert len(queue) == 2  # the script's read, then the library's
    queue[1] = Response(403, body={"message": "API rate limit exceeded"})
    row = SMOKE.check_anonymous(POLICY, anon)
    assert row.outcome == "not_verified", row.detail
    assert "[history:http:branch]" in row.detail


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
