#!/usr/bin/env python3
"""Read-only live smoke of `steward.approvalfacts.v3` against real GitHub (M0 Task 13).

Checklist — the rows of spec §8 marked «смоук» that were **not** captured live on
2026-10-30 (plan Task 13, items 1–9). Every item ends as one row with an outcome:

- ``verified`` — the live answer confirms the hypothesis the library relies on;
- ``not_verified`` — no target exists in the allowed repositories, a read did not
  answer (any non-200 or unexpected shape before the verdict), or the check needs a
  write the owner did not authorize; the reason is recorded;
- ``contradicts`` — the hypothesis is wrong. This is a finding for the manifest and
  steward#211, never a reason to change library expectations. Only a case the
  library is specified to judge can contradict.

Rules, enforced by construction:

- only ``GET`` REST requests and GraphQL **queries**: authenticated reads go through
  the public ``GhTransport`` (read-only by construction) with the profile passed in
  ``--profile``; anonymous reads use ``AnonTransport`` — plain HTTPS ``GET`` without any
  credentials (no token is read, sent or printed);
- only the public repositories in ``ALLOWED_REPOS`` (spec §3.8); no target is created;
- the report carries verdicts, PR numbers, SHAs, statuses and rate-limit numbers — no
  response bodies.

Usage::

    uv run python scripts/approval_facts_v3_smoke.py --profile ~/.config/review
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from steward.approvalfacts.v3 import (
    POLICY_FILES,
    GhTransport,
    HistoryViolation,
    Request,
    Response,
    ReviewFacts,
    Transport,
    Unavailable,
    read_act_facts,
    read_open_review,
    read_policy_history,
)

DESCRIPTION = "Read-only live smoke of steward.approvalfacts.v3 against real GitHub."
DEVTOOLS = "andrei-shtanakov/devtools"
POLICY = "andrei-shtanakov/approval-policy"
ALLOWED_REPOS = frozenset({DEVTOOLS, POLICY})
OUTCOMES = ("verified", "not_verified", "contradicts")
API = "https://api.github.com/"
#: GitHub's anonymous REST quota; any other ``x-ratelimit-limit`` means credentials.
ANON_LIMIT = "60"
#: Bound of the PR scan (50 PRs per page): a stuck cursor cannot loop forever.
MAX_SCAN_PAGES = 40
#: At most this many candidates per item are read through the library.
MAX_CANDIDATES = 3
COMPARE_STATUSES = frozenset({"identical", "behind", "ahead", "diverged"})
ITEM_ANON = "7-anonymous-transport"
NO_WRITE = "запись на стенде не разрешена владельцем"

#: Target discovery (not a library query): every PR with the facts the items need.
#: Dismissal presence reads filtered nodes, not ``totalCount``: live,
#: ``timelineItems.totalCount`` ignores ``itemTypes`` (spec §3.4). Reviews and
#: dismissals are one page each; a PR with more is recorded as truncated (``Scan``).
Q_SCAN = (
    "query StewardV3SmokeScan($owner:String!,$name:String!,$after:String){"
    "viewer{login} repository(owner:$owner,name:$name){"
    "pullRequests(first:50,after:$after){nodes{number state merged baseRefName "
    "author{login __typename} mergedBy{login __typename} "
    "reviews(first:100){totalCount nodes{state author{login __typename}}} "
    "timelineItems(itemTypes:[REVIEW_DISMISSED_EVENT],first:100){"
    "nodes{__typename ... on ReviewDismissedEvent{actor{login __typename}}} "
    "pageInfo{hasNextPage}}} "
    "pageInfo{hasNextPage endCursor}}}}"
)


@dataclass(frozen=True)
class Row:
    """Outcome of one checklist item."""

    item: str
    outcome: str
    detail: str
    targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome: {self.outcome!r}")


@dataclass
class Scan:
    """PR scan of one repository: the viewer, every PR node, and coverage gaps.

    ``truncated`` — PRs whose reviews or dismissal events did not fit one page: the
    scan's negative claims ("no DISMISSED", "no PENDING", "no ghost") do not cover them.
    """

    viewer: str
    prs: list[dict[str, Any]] = field(default_factory=list)
    truncated: list[int] = field(default_factory=list)

    def coverage(self) -> str:
        """Coverage note for a row built on the scan's negative claims."""
        note = f"обход {len(self.prs)} PR"
        if self.truncated:
            note += f"; ревью/отзывы не в одну страницу у {self.truncated} — вне охвата"
        return note


class RecordingTransport(Transport, Protocol):
    """A transport that records ``(path, x-ratelimit-remaining, x-ratelimit-limit)``."""

    ratelimit: list[tuple[str, str | None, str | None]]


class _Skip(Exception):
    """A read before the verdict did not answer: the row is ``not_verified``."""


@dataclass
class AnonTransport:
    """Anonymous REST transport (docs/approval-facts-v3.md «собственный анонимный»).

    ``GET`` only, no credentials of any kind. Honours the transport contract: ``send``
    never raises — a non-GET request or a delivery failure is ``Response(None)``.
    Every HTTP response's rate-limit headers are recorded in ``ratelimit``.
    """

    timeout: float = 30
    ratelimit: list[tuple[str, str | None, str | None]] = field(default_factory=list)

    def send(self, request: Request) -> Response:
        """Send one anonymous ``GET``; anything else or a failure is ``Response(None)``."""
        if request.method != "GET":
            return Response(None, error=f"anonymous transport is GET-only: {request.method}")
        req = urllib.request.Request(API + request.path, headers=_ANON_HEADERS)
        try:
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    return self._response(request.path, resp.status, resp.headers, resp.read())
            except urllib.error.HTTPError as err:
                return self._response(request.path, err.code, err.headers, err.read())
        except (OSError, ValueError, http.client.HTTPException) as exc:
            return Response(None, error=f"anonymous transport: {exc}")

    def _response(self, path: str, status: int, headers: Any, raw: bytes) -> Response:
        self.ratelimit.append(
            (path, headers.get("x-ratelimit-remaining"), headers.get("x-ratelimit-limit"))
        )
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        return Response(status, body=body, link=headers.get("link"))


_ANON_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "steward-approval-facts-v3-smoke",
}


def summarize(rows: Sequence[Row]) -> dict[str, Any]:
    """Report shape: per-outcome counts (every outcome present) and the rows."""
    counts = Counter(row.outcome for row in rows)
    return {
        "counts": {outcome: counts.get(outcome, 0) for outcome in OUTCOMES},
        "rows": [asdict(row) for row in rows],
    }


def guarded(item: str, check: Callable[[], Row]) -> Row:
    """Run one check; a read that did not answer becomes a ``not_verified`` row, not a crash.

    ``_Skip`` carries the HTTP status (e.g. anonymous 403 once the quota runs out);
    a shape error of a 200 body is recorded with its exception type.
    """
    try:
        return check()
    except _Skip as skip:
        return Row(item, "not_verified", f"чтение не ответило: {skip}")
    except (KeyError, IndexError, TypeError, StopIteration) as exc:
        return Row(item, "not_verified", f"неожиданная форма ответа: {type(exc).__name__} {exc}")


def _split(repo: str) -> tuple[str, str]:
    if repo not in ALLOWED_REPOS:
        raise ValueError(f"repository not allowed for the smoke: {repo}")
    owner, name = repo.split("/")
    return owner, name


def _body(t: Transport, path: str) -> tuple[int | None, Any]:
    response = t.send(Request("GET", path))
    return response.status, response.body


def _get(t: Transport, path: str, label: str) -> Any:
    """Body of a pre-verdict read; non-200 or a non-JSON body — ``_Skip`` with the status."""
    status, body = _body(t, path)
    if status != 200 or not isinstance(body, dict | list):
        raise _Skip(f"{label} HTTP {status} {path}")
    return body


def compare_path(owner: str, name: str, base: str, head: str) -> str:
    """E5 path as the library builds it (spec §3.2 «Пути REST»): every segment ``quote``-d."""
    return f"repos/{owner}/{name}/compare/{quote(base, safe='')}...{quote(head, safe='')}"


def scan_prs(t: Transport, repo: str) -> Scan | Unavailable:
    """Every PR of ``repo`` (bounded by ``MAX_SCAN_PAGES``); failure — ``Unavailable``."""
    owner, name = _split(repo)
    scan, after = Scan(viewer=""), None
    for _ in range(MAX_SCAN_PAGES):
        variables = {"owner": owner, "name": name, "after": after}
        response = t.send(
            Request("POST", "graphql", op="StewardV3SmokeScan", variables=variables, query=Q_SCAN)
        )
        body = response.body
        if response.status != 200 or not isinstance(body, dict) or body.get("errors"):
            return Unavailable(f"http: scan {repo} status {response.status}")
        try:
            conn = _scan_page(scan, body["data"])
        except (KeyError, TypeError) as exc:
            return Unavailable(f"malformed: scan {repo}: {type(exc).__name__} {exc}")
        if not conn["hasNextPage"]:
            return scan
        after = conn["endCursor"]
        if not isinstance(after, str):
            return Unavailable(f"pagination: scan {repo} next page without a cursor")
    return Unavailable(f"pagination: scan {repo} exceeds {MAX_SCAN_PAGES} pages")


def _scan_page(scan: Scan, data: Mapping[str, Any]) -> Mapping[str, Any]:
    scan.viewer = data["viewer"]["login"]
    conn = data["repository"]["pullRequests"]
    for pr in conn["nodes"]:
        reviews, events = pr["reviews"], pr["timelineItems"]
        if reviews["totalCount"] > len(reviews["nodes"]) or events["pageInfo"]["hasNextPage"]:
            scan.truncated.append(pr["number"])
        scan.prs.append(pr)
    return conn["pageInfo"]


def unresolved(actor: object) -> bool:
    """Spec §3.4.1 in the scan's terms: ``null``, ``ghost`` or a type outside User/Bot."""
    if not isinstance(actor, Mapping):
        return True
    login = actor.get("login")
    return (
        not isinstance(login, str)
        or login.lower() == "ghost"
        or actor.get("__typename") not in ("User", "Bot")
    )


def _numbers(
    prs: Sequence[Mapping[str, Any]], pred: Callable[[Mapping[str, Any]], bool]
) -> list[int]:
    return [pr["number"] for pr in prs if pred(pr)]


def _pass_actors(pr: Mapping[str, Any]) -> list[object]:
    """Actors ``read_open_review`` judges (§3.4.1): review authors and dismissal actors."""
    reviews = [r.get("author") for r in pr["reviews"]["nodes"]]
    return [*reviews, *(e.get("actor") for e in pr["timelineItems"]["nodes"])]


def dismissal_row(repo: str, results: Mapping[int, ReviewFacts | Unavailable]) -> Row:
    """Item 1: dismissals read by the library — shape, ``previous_state``, count = DISMISSED.

    ``verified`` needs at least one event actually read: zero events verify nothing.
    No candidate — ``not_verified`` (dismissing on the stand needs a write the owner did
    not authorize). The library refuses a ``null`` ``previousReviewState`` and a
    DISMISSED review without its event, so those codes are the contradiction.
    """
    item = "1-review-dismissed-event"
    if not results:
        return Row(item, "not_verified", f"{NO_WRITE}; в {repo} отзывов нет")
    targets = tuple(f"{repo}#{n}" for n in results)
    lines, events = [], 0
    for number, got in results.items():
        if isinstance(got, Unavailable):
            refuted = got.code in ("dismissal_without_previous_state", "dismissed_without_event")
            outcome = "contradicts" if refuted else "not_verified"
            return Row(item, outcome, f"#{number}: {got.reason}", targets)
        dismissed = sum(r.state == "DISMISSED" for r in got.reviews)
        if dismissed != len(got.dismissals):
            detail = f"#{number}: DISMISSED={dismissed}, events={len(got.dismissals)}"
            return Row(item, "contradicts", detail, targets)
        events += len(got.dismissals)
        states = sorted({str(d.previous_state) for d in got.dismissals})
        lines.append(f"#{number}: events={len(got.dismissals)} previous={states}")
    if not events:
        return Row(item, "not_verified", f"ни одного события не прочитано: {lines}", targets)
    return Row(item, "verified", "; ".join(lines), targets)


def compare_status_row(item: str, want: str, status: int | None, body: Any, target: str) -> Row:
    """Items 4a/4b: a live ``compare`` whose topology must give the ``want`` status.

    Non-200 — ``not_verified`` (no answer); 200 with a status outside §3.8 — contradicts.
    """
    if status != 200:
        return Row(item, "not_verified", f"HTTP {status}", (target,))
    got = body.get("status") if isinstance(body, dict) else None
    if got not in COMPARE_STATUSES:
        return Row(item, "contradicts", f"HTTP 200, status={got!r}", (target,))
    if got != want:
        return Row(item, "not_verified", f"topology gave {got!r}, not {want!r}", (target,))
    return Row(item, "verified", f"HTTP 200, status={got!r}", (target,))


def _compare_key(body: Any) -> tuple[object, ...] | None:
    """What identifies a compare answer: status, distances, merge base."""
    if not isinstance(body, dict):
        return None
    base = body.get("merge_base_commit")
    sha = base.get("sha") if isinstance(base, dict) else None
    return (body.get("status"), body.get("ahead_by"), body.get("behind_by"), sha)


def slash_compare_row(
    encoded: tuple[int | None, Any], plain: tuple[int | None, Any], targets: tuple[str, ...]
) -> Row:
    """Item 8: ``%2F``-encoded base must give 200 and the same answer as the plain name.

    The answer is compared as ``(status, ahead_by, behind_by, merge_base sha)``: the same
    status alone could come from a different branch.
    """
    keys = [_compare_key(body) for _, body in (encoded, plain)]
    detail = f"encoded HTTP {encoded[0]} {keys[0]}; plain HTTP {plain[0]} {keys[1]}"
    item = "8-compare-slash-base"
    if plain[0] != 200 or keys[1] is None or keys[1][0] not in COMPARE_STATUSES:
        return Row(item, "not_verified", f"plain compare failed: {detail}", targets)
    if encoded[0] != 200 or keys[0] != keys[1]:
        return Row(item, "contradicts", detail, targets)
    return Row(item, "verified", detail, targets)


def actor_unresolved_row(item: str, forms: str, got: object, target: tuple[str, ...]) -> Row:
    """Item 5 verdict for an actor the called function judges: must be ``actor_unresolved``."""
    if isinstance(got, Unavailable):
        if got.code == "actor_unresolved":
            return Row(item, "verified", f"{forms} → actor_unresolved", target)
        return Row(item, "not_verified", f"{forms} → {got.reason}", target)
    return Row(item, "contradicts", f"{forms} → факты выданы", target)


def check_dismissals(t: Transport, repo: str, scan: Scan) -> Row:
    """Item 1 over the scan: PRs with a dismissal event or a DISMISSED review."""
    numbers = _numbers(
        scan.prs,
        lambda pr: (
            bool(pr["timelineItems"]["nodes"])
            or any(r["state"] == "DISMISSED" for r in pr["reviews"]["nodes"])
        ),
    )
    results = {n: read_open_review(t, repo, n) for n in numbers[:MAX_CANDIDATES]}
    row = dismissal_row(repo, results)
    if not numbers:
        return Row(row.item, row.outcome, f"{row.detail} ({scan.coverage()})")
    return row


def check_app_merger(t: Transport, repo: str, scan: Scan, paths: Sequence[str] = ()) -> Row:
    """Item 2: REST ``slug[bot]``/``Bot`` and GraphQL ``slug``/``Bot`` of an App merge.

    Both halves are judged: the REST form via a raw read, the GraphQL form via the
    library's ``Delivery`` (GraphQL form, §3.3). ``actor_unresolved`` or
    ``bot_login_mismatch`` on a known Bot merger refutes the documented forms.
    """
    numbers = _numbers(
        scan.prs, lambda pr: pr["merged"] and (pr["mergedBy"] or {}).get("__typename") == "Bot"
    )
    item = "2-app-merger"
    if not numbers:
        return Row(item, "not_verified", f"мержей App в {repo} нет ({scan.coverage()})")
    number = numbers[0]
    owner, name = _split(repo)
    rest = _get(t, f"repos/{owner}/{name}/pulls/{number}", "E1").get("merged_by") or {}
    rest_form = f"REST {rest.get('login')}/{rest.get('type')}"
    got = read_act_facts(t, repo, number, paths=paths)
    target = (f"{repo}#{number}",)
    if isinstance(got, Unavailable):
        refuted = got.code in ("bot_login_mismatch", "actor_unresolved")
        return Row(
            item, "contradicts" if refuted else "not_verified", f"{rest_form}; {got.reason}", target
        )
    delivery = got[0]
    login, hint = delivery.merged_by_login, delivery.merged_by_type_hint
    detail = f"{rest_form} → GraphQL {login}/{hint}"
    ok = (
        rest.get("type") == "Bot"
        and rest.get("login") == f"{login}[bot]"
        and hint == "Bot"
        and isinstance(login, str)
        and not login.endswith("[bot]")
    )
    return Row(item, "verified" if ok else "contradicts", detail, target)


def check_open_pr(t: Transport, repo: str, scan: Scan) -> Row:
    """Item 3: REST ``state: open``, GraphQL ``OPEN``, ``read_open_review`` facts."""
    numbers = _numbers(scan.prs, lambda pr: pr["state"] == "OPEN")
    item = "3-open-pr"
    if not numbers:
        return Row(item, "not_verified", f"открытых PR в {repo} на момент прогона нет")
    number = numbers[0]
    owner, name = _split(repo)
    rest_state = _get(t, f"repos/{owner}/{name}/pulls/{number}", "E1").get("state")
    facts = read_open_review(t, repo, number)
    act = read_act_facts(t, repo, number, paths=())
    target = (f"{repo}#{number}",)
    if isinstance(facts, Unavailable):
        return Row(item, "not_verified", facts.reason, target)
    if isinstance(act, Unavailable):
        return Row(item, "not_verified", act.reason, target)
    ok = rest_state == "open" and facts.pr_state == "OPEN" and not act[0].merged
    detail = f"REST state={rest_state}, pr_state={facts.pr_state}, merged={act[0].merged}"
    return Row(item, "verified" if ok else "contradicts", detail, target)


def check_compare_ahead(t: Transport, repo: str) -> Row:
    """Item 4a: ``parent...head`` of the default branch must be ``ahead``."""
    owner, name = _split(repo)
    branch = _get(t, f"repos/{owner}/{name}", "H1")["default_branch"]
    head = _get(t, f"repos/{owner}/{name}/commits/{quote(branch, safe='')}", "H3")
    path = compare_path(owner, name, head["parents"][0]["sha"], head["sha"])
    return compare_status_row("4a-compare-ahead", "ahead", *_body(t, path), f"{repo} {path}")


def check_compare_diverged(t: Transport, repo: str, scan: Scan) -> Row:
    """Item 4b: ``default...head of a closed unmerged PR`` must be ``diverged``."""
    closed = _numbers(scan.prs, lambda pr: pr["state"] == "CLOSED" and not pr["merged"])
    if not closed:
        return Row("4b-compare-diverged", "not_verified", "закрытых несмёрженных PR нет")
    owner, name = _split(repo)
    branch = _get(t, f"repos/{owner}/{name}", "H1")["default_branch"]
    side = _get(t, f"repos/{owner}/{name}/pulls/{closed[-1]}", "E1")["head"]["sha"]
    path = compare_path(owner, name, branch, side)
    target = f"{repo}#{closed[-1]} {path}"
    return compare_status_row("4b-compare-diverged", "diverged", *_body(t, path), target)


def check_deleted_user(t: Transport, repo: str, scan: Scan) -> Row:
    """Item 5: a deleted user (``null``/``ghost``) where the library judges actors.

    Review author or dismissal actor → ``read_open_review``; merger of a merged PR →
    ``read_act_facts``; both must give ``actor_unresolved`` (§3.4.1). The PR author is
    outside §3.4.1 — such a PR alone is ``not_verified``, never a contradiction.
    """
    item = "5-deleted-user"
    in_pass = _numbers(scan.prs, lambda pr: any(unresolved(a) for a in _pass_actors(pr)))
    merger = _numbers(scan.prs, lambda pr: bool(pr["merged"]) and unresolved(pr["mergedBy"]))
    by_pr = {pr["number"]: pr for pr in scan.prs}
    if in_pass:
        number = in_pass[0]
        forms = sorted({json.dumps(a) for a in _pass_actors(by_pr[number]) if unresolved(a)})
        got: object = read_open_review(t, repo, number)
        return actor_unresolved_row(item, f"ревью/отзыв {forms}", got, (f"{repo}#{number}",))
    if merger:
        number = merger[0]
        got = read_act_facts(t, repo, number, paths=())
        forms = json.dumps(by_pr[number]["mergedBy"])
        return actor_unresolved_row(item, f"мержер {forms}", got, (f"{repo}#{number}",))
    authors = _numbers(scan.prs, lambda pr: unresolved(pr.get("author")))
    if authors:
        detail = f"удалённый только автор PR {authors[:MAX_CANDIDATES]} — вне §3.4.1"
        return Row(item, "not_verified", detail)
    return Row(item, "not_verified", f"удалённых пользователей в {repo} нет ({scan.coverage()})")


def check_pending(scan: Scan, repo: str) -> Row:
    """Item 6: a PENDING review of another account visible to this token."""
    foreign = [
        pr["number"]
        for pr in scan.prs
        for r in pr["reviews"]["nodes"]
        if r["state"] == "PENDING" and (r["author"] or {}).get("login") != scan.viewer
    ]
    total = sum(r["state"] == "PENDING" for pr in scan.prs for r in pr["reviews"]["nodes"])
    item = "6-pending-visibility"
    if foreign:
        targets = tuple(f"{repo}#{n}" for n in foreign)
        return Row(item, "verified", f"чужие PENDING видны ({scan.viewer})", targets)
    detail = (
        f"чужих PENDING в обходе {repo} нет (PENDING всего: {total}; {scan.coverage()}); "
        "без второй учётки нельзя отличить «нет» от «не видно»"
    )
    return Row(item, "not_verified", detail)


def check_anonymous(repo: str, anon: RecordingTransport) -> Row:
    """Item 7: H1–H6 without a token; ``read_policy_history`` over the anonymous transport.

    ``verified`` needs every H1–H6 read 200, a library result that is not
    ``Unavailable``, and the rate-limit headers with the anonymous quota.
    """
    owner, name = _split(repo)
    start = len(anon.ratelimit)
    branch = _get(anon, f"repos/{owner}/{name}", "H1")["default_branch"]
    head = _get(anon, f"repos/{owner}/{name}/branches/{quote(branch, safe='')}", "H2")
    head = head["commit"]["sha"]
    commit = _get(anon, f"repos/{owner}/{name}/commits/{head}", "H3")
    _get(anon, f"repos/{owner}/{name}/commits/{head}/pulls?per_page=100", "H4")
    tree_sha = commit["commit"]["tree"]["sha"]
    tree = _get(anon, f"repos/{owner}/{name}/git/trees/{tree_sha}?recursive=1", "H5")
    blobs = {e["path"]: e["sha"] for e in tree["tree"] if e["type"] == "blob"}
    blob = next((blobs[p] for p in POLICY_FILES if p in blobs), None) or next(iter(blobs.values()))
    _get(anon, f"repos/{owner}/{name}/git/blobs/{blob}", "H6")
    history = read_policy_history(anon, repo, branch, anchor=head, until=None)
    if isinstance(history, HistoryViolation | Unavailable):
        lib = history.reason
    else:
        lib = f"{len(history)} activation(s)"
    seen = anon.ratelimit[start:]
    remaining = [r for _, r, _ in seen]
    limits = sorted({str(lim) for _, _, lim in seen})
    detail = (
        f"H1–H6 HTTP 200; read_policy_history(anchor=head {head[:12]}) → {lib}; "
        f"x-ratelimit-limit {limits}, remaining {remaining[0]}→{remaining[-1]} "
        f"over {len(seen)} requests"
    )
    target = (repo, head)
    if isinstance(history, Unavailable):
        return Row(ITEM_ANON, "contradicts", detail, target)
    if None in remaining or limits != [ANON_LIMIT]:
        return Row(
            ITEM_ANON, "not_verified", f"нет анонимного лимита в заголовках: {detail}", target
        )
    return Row(ITEM_ANON, "verified", detail, target)


def check_slash_base(t: Transport, anon: Transport, repo: str) -> Row:
    """Item 8: ``compare`` with a ``/`` branch as base — encoded vs plain, both transports."""
    owner, name = _split(repo)
    branches = _get(t, f"repos/{owner}/{name}/branches?per_page=100", "branches")
    slashed = [b["name"] for b in branches if "/" in b["name"]]
    if not slashed:
        return Row("8-compare-slash-base", "not_verified", f"веток с «/» в {repo} нет")
    default = _get(t, f"repos/{owner}/{name}", "H1")["default_branch"]
    sha = _get(t, f"repos/{owner}/{name}/commits/{quote(default, safe='')}", "H3")["sha"]
    base = slashed[0]
    encoded = compare_path(owner, name, base, sha)
    plain = f"repos/{owner}/{name}/compare/{base}...{sha}"
    targets = (repo, base, sha)
    gh_row = slash_compare_row(_body(t, encoded), _body(t, plain), targets)
    anon_row = slash_compare_row(_body(anon, encoded), _body(anon, plain), targets)
    detail = f"gh: {gh_row.detail}; anonymous: {anon_row.detail}"
    outcome = next(
        (o for o in ("contradicts", "not_verified") if o in (gh_row.outcome, anon_row.outcome)),
        "verified",
    )
    return Row("8-compare-slash-base", outcome, detail, targets)


OUT_OF_SMOKE = Row(
    "9-out-of-smoke",
    "not_verified",
    "вне смоука: rebase-merge (промежуточный коммит, однокоммитный merge_commit_sha), "
    "ассоциации вне ветки по умолчанию — стенд devtools, Task 18 №28",
)
SCAN_ITEMS = (
    "1-review-dismissed-event",
    "2-app-merger",
    "3-open-pr",
    "4b-compare-diverged",
    "5-deleted-user",
    "6-pending-visibility",
)


def scan_rows(t: Transport, repo: str, scan: Scan | Unavailable) -> list[Row]:
    """Rows of the items that need the PR scan; a failed scan makes each ``not_verified``."""
    if isinstance(scan, Unavailable):
        return [
            Row(item, "not_verified", f"обход PR не удался: {scan.reason}") for item in SCAN_ITEMS
        ]
    checks: dict[str, Callable[[], Row]] = {
        "1-review-dismissed-event": lambda: check_dismissals(t, repo, scan),
        "2-app-merger": lambda: check_app_merger(t, repo, scan),
        "3-open-pr": lambda: check_open_pr(t, repo, scan),
        "4b-compare-diverged": lambda: check_compare_diverged(t, repo, scan),
        "5-deleted-user": lambda: check_deleted_user(t, repo, scan),
        "6-pending-visibility": lambda: check_pending(scan, repo),
    }
    return [guarded(item, checks[item]) for item in SCAN_ITEMS]


def run(profile: Path) -> dict[str, Any]:
    """Run every checklist item; return the report (summary + rows)."""
    gh = GhTransport(profile)
    anon = AnonTransport()
    scan = scan_prs(gh, DEVTOOLS)
    by_item = {row.item: row for row in scan_rows(gh, DEVTOOLS, scan)}
    by_item["4a-compare-ahead"] = guarded(
        "4a-compare-ahead", lambda: check_compare_ahead(gh, DEVTOOLS)
    )
    by_item[ITEM_ANON] = guarded(ITEM_ANON, lambda: check_anonymous(POLICY, anon))
    by_item["8-compare-slash-base"] = guarded(
        "8-compare-slash-base", lambda: check_slash_base(gh, anon, DEVTOOLS)
    )
    by_item[OUT_OF_SMOKE.item] = OUT_OF_SMOKE
    report = summarize([by_item[item] for item in sorted(by_item)])
    if isinstance(scan, Scan):
        report["scan"] = {
            "repo": DEVTOOLS,
            "viewer": scan.viewer,
            "prs": len(scan.prs),
            "truncated": scan.truncated,
        }
    report["anonymous_ratelimit"] = anon.ratelimit
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: print the JSON report; exit 1 if any row ``contradicts``."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--profile", type=Path, required=True, help="GH_CONFIG_DIR (read-only)")
    args = parser.parse_args(argv)
    report = run(args.profile.expanduser())
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if report["counts"]["contradicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
