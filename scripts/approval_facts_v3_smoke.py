#!/usr/bin/env python3
"""Read-only live smoke of `steward.approvalfacts.v3` against real GitHub (M0 Task 13).

Checklist — the rows of spec §8 marked «смоук» that were **not** captured live on
2026-10-30 (plan Task 13, items 1–9). Every item ends as one row with an outcome:

- ``verified`` — the live answer confirms the hypothesis the library relies on;
- ``not_verified`` — no target exists in the allowed repositories, a read did not
  answer (any non-200 or unexpected shape before the verdict), or the check needs a
  write the owner did not authorize; the reason is recorded;
- ``contradicts`` — the hypothesis is wrong. This is a finding for the manifest and
  steward#211, never a reason to change library expectations.

One mechanism decides every row: a check reduces what it saw to a **kind**
(``kind_of`` for library results, plus item-specific facts folded in), and the table
``RULES`` lists, per item, the kinds that verify and the kinds that contradict. Any
other kind is ``not_verified``: a failure is never attributed to a hypothesis the failing
call did not judge.

Exit codes: 0 — no contradiction; 1 — a row ``contradicts``; 2 — a check raised
(``report.internal_errors``), so the evidence is not trustworthy. A forge non-200 is
``not_verified`` with exit 0.

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
import re
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
    #: The check raised (a script/library bug or a body of unexpected shape): the row is
    #: ``not_verified`` and the run exits 2 (``report.internal_errors``).
    internal: bool = False

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
    """Report shape: per-outcome counts (every outcome present), the rows, internal errors."""
    counts = Counter(row.outcome for row in rows)
    return {
        "counts": {outcome: counts.get(outcome, 0) for outcome in OUTCOMES},
        "rows": [asdict(row) for row in rows],
        "internal_errors": [row.item for row in rows if row.internal],
    }


def exit_code(report: Mapping[str, Any]) -> int:
    """2 — a check raised; 1 — a row contradicts; 0 — otherwise."""
    if report["internal_errors"]:
        return 2
    return 1 if report["counts"]["contradicts"] else 0


def guarded(item: str, check: Callable[[], Row]) -> Row:
    """Run one check; it always yields a row.

    ``_Skip`` (a pre-verdict read that did not answer, e.g. anonymous 403) — a plain
    ``not_verified`` row with the status. Any other caught exception — ``not_verified``
    marked ``internal``: it is a script/library bug or a 200 body of unexpected shape, and
    the run exits 2. Neither path can produce ``verified`` or ``contradicts``.
    """
    try:
        return check()
    except _Skip as skip:
        return Row(item, "not_verified", f"чтение не ответило: {skip}")
    except (KeyError, IndexError, TypeError, AttributeError, StopIteration, ValueError) as exc:
        detail = f"внутренняя ошибка/форма: {type(exc).__name__} {exc}"
        return Row(item, "not_verified", detail, internal=True)


# --- the decision rule ----------------------------------------------------------------
#
# A kind names what a check saw. For a library refusal it is
# ``<function>:<code>:<origin>``: which public function refused (``open_review``,
# ``act``, ``history``), the ``UNAVAILABLE_CODES`` code, and which object the library
# refused — derived from the library's own detail, never guessed. An origin the script
# cannot derive is ``unknown`` and appears in no allow-list. ``RULES`` holds, per item,
# explicit allow-lists of kinds that verify and kinds that contradict; there are no
# "all except" sets. Every other kind is not_verified.

#: Every origin ``origin_of`` can return.
ORIGINS = (
    "pr",
    "pass_pr",
    "merger",
    "review",
    "dismissal",
    "timeline",
    "compare",
    "blob",
    "tree",
    "commit",
    "commit_pulls",
    "branch",
    "repo",
    "graphql",
    "link",
    "chain",
    "unknown",
)

#: Codes the library raises at exactly one site: the code itself names the object.
CODE_ORIGIN = {
    "bot_login_mismatch": "merger",
    "dismissal_without_previous_state": "dismissal",
    "duplicate_dismissal": "dismissal",
    "orphan_dismissal": "dismissal",
    "branch_not_default": "repo",
    "chain_limit": "chain",
    "no_pr_for_commit": "commit_pulls",
    "tree_truncated": "tree",
}

#: Detail prefixes the library writes (its ``where``), in match order.
_PREFIX_ORIGIN = (
    ("GraphQL pull request", "pr"),
    ("REST pull request", "pr"),
    ("StewardV3Delivery", "pr"),
    ("pull request", "pass_pr"),
    ("review", "review"),
    ("dismissal", "dismissal"),
    ("ReviewDismissedEvent", "dismissal"),
    ("StewardV3Reviews", "review"),
    ("StewardV3Timeline", "timeline"),
    ("timeline items", "timeline"),
    ("StewardV3Blobs", "blob"),
    ("Link ", "link"),
    ("anchor ", "chain"),
)
#: REST path markers (``http: <status> <path>``, ``<path>: …``), most specific first.
_PATH_ORIGIN = (
    ("/compare/", "compare"),
    ("/git/trees/", "tree"),
    ("/git/blobs/", "blob"),
    ("/pulls?", "commit_pulls"),
    ("/pulls/", "pr"),
    ("/commits/", "commit"),
    ("/branches/", "branch"),
)
_MERGER = re.compile(r"\bmerged(?:By|_by)\b")
_GRAPHQL_HTTP = re.compile(r"\d{3} graphql")
_REPO_PATH = re.compile(r"\brepos/[^/\s]+/[^/\s:]+(?:$|[\s:])")


def origin_of(code: str, detail: str) -> str:
    """Object the library refused, from its own detail (or the code, if single-site)."""
    if code in CODE_ORIGIN:
        return CODE_ORIGIN[code]
    for prefix, origin in _PREFIX_ORIGIN:
        if detail.startswith(prefix):
            return "merger" if origin == "pr" and _MERGER.search(detail) else origin
    for marker, origin in _PATH_ORIGIN:
        if marker in detail:
            return origin
    if _GRAPHQL_HTTP.fullmatch(detail):
        return "graphql"
    if _REPO_PATH.search(detail):
        return "repo"
    return "unknown"


def kind_of(fn: str, got: object) -> str:
    """Kind of a library result of public function ``fn``: ``<fn>:ok``,
    ``<fn>:violation``, or ``<fn>:<code>:<origin>``."""
    if isinstance(got, HistoryViolation):
        return f"{fn}:violation"
    if not isinstance(got, Unavailable):
        return f"{fn}:ok"
    detail = got.reason.removeprefix(got.code).removeprefix(": ")
    return f"{fn}:{got.code}:{origin_of(got.code, detail)}"


#: History origins whose ``malformed`` means "answered anonymously, not in §3.8 shape".
_H_ORIGINS = ("repo", "branch", "commit", "commit_pulls", "tree", "blob", "link")

#: Per item: (kinds that verify, kinds that contradict) — explicit allow-lists only.
RULES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "1-review-dismissed-event": (
        frozenset({"open_review:ok:events"}),
        frozenset(
            {
                "open_review:ok:count_mismatch",
                "open_review:dismissal_without_previous_state:dismissal",
                "open_review:dismissed_without_event:review",
            }
        ),
    ),
    "2-app-merger": (
        frozenset({"act:ok:documented_forms"}),
        frozenset(
            {
                "act:ok:undocumented_forms",
                "act:actor_unresolved:merger:clean",
                "act:bot_login_mismatch:merger",
            }
        ),
    ),
    "3-open-pr": (
        frozenset({"open:ok:open"}),
        frozenset({"open:ok:undocumented_state", "act:unknown_state:pr"}),
    ),
    "4a-compare-ahead": (
        frozenset({"compare:want"}),
        frozenset({"compare:undocumented_status"}),
    ),
    "4b-compare-diverged": (
        frozenset({"compare:want"}),
        frozenset({"compare:undocumented_status"}),
    ),
    "5-deleted-user": (
        frozenset(
            {
                "open_review:actor_unresolved:review",
                "open_review:actor_unresolved:dismissal",
                "act:actor_unresolved:merger:clean",
            }
        ),
        frozenset({"open_review:ok:ghost_accepted", "act:ok:ghost_accepted"}),
    ),
    "6-pending-visibility": (frozenset({"pending:foreign_visible"}), frozenset()),
    ITEM_ANON: (
        frozenset({"history:answered:anon_quota"}),
        frozenset(f"history:malformed:{origin}" for origin in _H_ORIGINS),
    ),
    "8-compare-slash-base": (frozenset({"slash:same"}), frozenset({"slash:encoded_404"})),
}


def decide(item: str, kind: str, detail: str, targets: tuple[str, ...] = ()) -> Row:
    """The single verdict rule: ``RULES[item]`` decides; an unlisted kind is not_verified."""
    verifying, contradicting = RULES[item]
    if kind in contradicting:
        outcome = "contradicts"
    elif kind in verifying:
        outcome = "verified"
    else:
        outcome = "not_verified"
    return Row(item, outcome, f"[{kind}] {detail}", targets)


def _reason(got: object) -> str:
    return got.reason if isinstance(got, Unavailable | HistoryViolation) else "facts"


# --- reads ----------------------------------------------------------------------------


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


def _ghost(login: object) -> bool:
    return not isinstance(login, str) or not login or login.lower() == "ghost"


def deleted(actor: object) -> bool:
    """Item 5's case: a deleted user — ``null`` actor or the ``ghost`` placeholder login."""
    return not isinstance(actor, Mapping) or _ghost(actor.get("login"))


def _numbers(
    prs: Sequence[Mapping[str, Any]], pred: Callable[[Mapping[str, Any]], bool]
) -> list[int]:
    return [pr["number"] for pr in prs if pred(pr)]


def _pass_actors(pr: Mapping[str, Any]) -> list[object]:
    """Actors ``read_open_review`` judges (§3.4.1): review authors and dismissal actors."""
    reviews = [r.get("author") for r in pr["reviews"]["nodes"]]
    return [*reviews, *(e.get("actor") for e in pr["timelineItems"]["nodes"])]


def _clean(scan: Scan, number: int) -> bool:
    """The PR's passes were fully scanned and hold no unresolved review/dismissal actor:
    an ``actor_unresolved`` naming the merger can only be about the merger."""
    pr = next(p for p in scan.prs if p["number"] == number)
    return number not in scan.truncated and not any(unresolved(a) for a in _pass_actors(pr))


# --- item checks ----------------------------------------------------------------------


def dismissal_kind(got: ReviewFacts | Unavailable) -> str:
    """Item 1 kind of one ``read_open_review`` candidate: ``ok:events`` needs ≥1 event and
    count = DISMISSED."""
    if not isinstance(got, ReviewFacts):
        return kind_of("open_review", got)
    dismissed = sum(r.state == "DISMISSED" for r in got.reviews)
    if dismissed != len(got.dismissals):
        return "open_review:ok:count_mismatch"
    return "open_review:ok:events" if got.dismissals else "open_review:ok:no_events"


def dismissal_row(repo: str, results: Mapping[int, ReviewFacts | Unavailable]) -> Row:
    """Item 1: dismissals read by the library — shape, ``previous_state``, count = DISMISSED.

    The row's kind: a contradicting candidate wins, then a verifying one, else the first.
    No candidate — ``not_verified`` (dismissing on the stand needs a write the owner did
    not authorize).
    """
    item = "1-review-dismissed-event"
    if not results:
        return Row(item, "not_verified", f"{NO_WRITE}; в {repo} отзывов нет")
    kinds = {n: dismissal_kind(got) for n, got in results.items()}
    verifying, contradicting = RULES[item]
    ordered = sorted(kinds.values(), key=lambda k: (k not in contradicting, k not in verifying))
    detail = "; ".join(
        f"#{n}: {kinds[n]} {_reason(got) if isinstance(got, Unavailable) else _events(got)}"
        for n, got in results.items()
    )
    return decide(item, ordered[0], detail, tuple(f"{repo}#{n}" for n in results))


def _events(facts: ReviewFacts) -> str:
    states = sorted({str(d.previous_state) for d in facts.dismissals})
    return f"events={len(facts.dismissals)} previous={states}"


def compare_status_row(item: str, want: str, status: int | None, body: Any, target: str) -> Row:
    """Items 4a/4b: a live ``compare`` whose topology must give the ``want`` status."""
    got = body.get("status") if isinstance(body, dict) else None
    if status != 200:
        kind = f"compare:http_{status}"
    elif not isinstance(got, str):
        kind = "compare:no_status"
    elif got not in COMPARE_STATUSES:
        kind = "compare:undocumented_status"
    else:
        kind = "compare:want" if got == want else "compare:other_topology"
    return decide(item, kind, f"HTTP {status}, status={got!r}, want {want!r}", (target,))


def _compare_key(body: Any) -> tuple[object, ...] | None:
    """What identifies a compare answer: status, distances, merge base."""
    if not isinstance(body, dict):
        return None
    base = body.get("merge_base_commit")
    sha = base.get("sha") if isinstance(base, dict) else None
    return (body.get("status"), body.get("ahead_by"), body.get("behind_by"), sha)


def slash_kind(encoded: tuple[int | None, Any], plain: tuple[int | None, Any]) -> str:
    """Item 8 kind of one transport: only an encoded 404 beside a valid plain answer
    refutes «GitHub decodes ``%2F`` in a branch segment»."""
    key_e, key_p = _compare_key(encoded[1]), _compare_key(plain[1])
    if plain[0] != 200 or key_p is None or key_p[0] not in COMPARE_STATUSES:
        return "slash:plain_failed"
    if encoded[0] == 404:
        return "slash:encoded_404"
    if encoded[0] != 200:
        return f"slash:encoded_http_{encoded[0]}"
    return "slash:same" if key_e == key_p else "slash:different_answer"


def slash_compare_row(
    encoded: tuple[int | None, Any], plain: tuple[int | None, Any], targets: tuple[str, ...]
) -> Row:
    """Item 8 for one transport: ``%2F``-encoded base vs the plain name.

    The answer is compared as ``(status, ahead_by, behind_by, merge_base sha)``: the same
    status alone could come from a different branch.
    """
    keys = [_compare_key(body) for _, body in (encoded, plain)]
    detail = f"encoded HTTP {encoded[0]} {keys[0]}; plain HTTP {plain[0]} {keys[1]}"
    return decide("8-compare-slash-base", slash_kind(encoded, plain), detail, targets)


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


def app_merger_kind(got: object, rest: Mapping[str, Any], clean: bool) -> str:
    """Item 2 kind of ``read_act_facts``: a merger refusal is suffixed ``clean``/``tainted``;
    ``ok`` splits by whether both raw forms are the documented ``slug[bot]``/``Bot`` and
    ``slug``/``Bot``."""
    kind = kind_of("act", got)
    if kind == "act:actor_unresolved:merger":
        return f"{kind}:{'clean' if clean else 'tainted'}"
    if not isinstance(got, tuple):
        return kind
    login, hint = got[0].merged_by_login, got[0].merged_by_type_hint
    documented = (
        rest.get("type") == "Bot"
        and isinstance(login, str)
        and rest.get("login") == f"{login}[bot]"
        and hint == "Bot"
        and not login.endswith("[bot]")
    )
    return "act:ok:documented_forms" if documented else "act:ok:undocumented_forms"


def check_app_merger(t: Transport, repo: str, scan: Scan, paths: Sequence[str] = ()) -> Row:
    """Item 2: REST ``slug[bot]``/``Bot`` and GraphQL ``slug``/``Bot`` of an App merge.

    The REST form via a raw read, the GraphQL form via the library's ``Delivery``.
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
    got = read_act_facts(t, repo, number, paths=paths)
    detail = f"REST {rest.get('login')}/{rest.get('type')}; {_reason(got)}"
    if isinstance(got, tuple):
        detail += f"; GraphQL {got[0].merged_by_login}/{got[0].merged_by_type_hint}"
    kind = app_merger_kind(got, rest, _clean(scan, number))
    return decide(item, kind, detail, (f"{repo}#{number}",))


def open_pr_kind(rest_state: object, facts: object, act: object) -> str:
    """Item 3 kind. ``read_act_facts`` is looked at first: its PR-state refusal
    (``act:unknown_state:pr``) is the only library refusal about E1/E2 state. Any other
    refusal keeps its own kind. Both answered → ``open:ok:open`` /
    ``open:ok:undocumented_state`` (REST state outside {open, closed}) /
    ``open:ok:state_mismatch`` (a race between reads, not a refutation)."""
    if not isinstance(act, tuple):
        return kind_of("act", act)
    if not isinstance(facts, ReviewFacts):
        return kind_of("open_review", facts)
    if rest_state not in ("open", "closed"):
        return "open:ok:undocumented_state"
    if rest_state == "open" and facts.pr_state == "OPEN" and not act[0].merged:
        return "open:ok:open"
    return "open:ok:state_mismatch"


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
    pr_state = facts.pr_state if isinstance(facts, ReviewFacts) else _reason(facts)
    merged = act[0].merged if isinstance(act, tuple) else _reason(act)
    detail = f"REST state={rest_state}, pr_state={pr_state}, merged={merged}"
    kind = open_pr_kind(rest_state, facts, act)
    return decide(item, kind, detail, (f"{repo}#{number}",))


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


def pass_ghost_kind(got: object) -> str:
    """Item 5, ``read_open_review`` branch: facts that still carry a ghost login are an
    accepted deleted user; facts without one refute nothing."""
    if not isinstance(got, ReviewFacts):
        return kind_of("open_review", got)
    logins = [r.login for r in got.reviews] + [d.actor for d in got.dismissals]
    accepted = any(map(_ghost, logins))
    return "open_review:ok:ghost_accepted" if accepted else "open_review:ok"


def merger_ghost_kind(got: object, clean: bool) -> str:
    """Item 5, ``read_act_facts`` branch: a merger refusal is suffixed ``clean``/
    ``tainted``; ``ok`` refutes only when the delivered merger itself is a ghost."""
    kind = kind_of("act", got)
    if kind == "act:actor_unresolved:merger":
        return f"{kind}:{'clean' if clean else 'tainted'}"
    if isinstance(got, tuple) and _ghost(got[0].merged_by_login):
        return "act:ok:ghost_accepted"
    return kind


def _only_deleted_unresolved(scan: Scan, pr: Mapping[str, Any]) -> bool:
    """A pass candidate for item 5: fully scanned, with a deleted actor, and every
    unresolved actor of its passes deleted — the library refuses the first unresolved
    actor it reads, so a Mannequin beside a ghost would answer for the ghost."""
    actors = _pass_actors(pr)
    return (
        pr["number"] not in scan.truncated
        and any(deleted(a) for a in actors)
        and all(deleted(a) for a in actors if unresolved(a))
    )


def check_deleted_user(t: Transport, repo: str, scan: Scan) -> Row:
    """Item 5: a deleted user (``null``/``ghost``) where the library judges actors.

    Review author or dismissal actor → ``read_open_review``; merger of a merged PR →
    ``read_act_facts``; both must give ``actor_unresolved`` (§3.4.1) naming that actor.
    Candidates are only ``null``/``ghost`` actors (``deleted``): a live actor of another
    type is §3.4.1 too, but not this item's case. The PR author is outside §3.4.1 — such
    a PR alone is ``not_verified``.
    """
    item = "5-deleted-user"
    in_pass = _numbers(scan.prs, lambda pr: _only_deleted_unresolved(scan, pr))
    merger = _numbers(scan.prs, lambda pr: bool(pr["merged"]) and deleted(pr["mergedBy"]))
    by_pr = {pr["number"]: pr for pr in scan.prs}
    if in_pass:
        number = in_pass[0]
        forms = sorted({json.dumps(a) for a in _pass_actors(by_pr[number]) if deleted(a)})
        got: object = read_open_review(t, repo, number)
        detail = f"ревью/отзыв {forms} → {_reason(got)}"
        return decide(item, pass_ghost_kind(got), detail, (f"{repo}#{number}",))
    if merger:
        number = merger[0]
        got = read_act_facts(t, repo, number, paths=())
        detail = f"мержер {json.dumps(by_pr[number]['mergedBy'])} → {_reason(got)}"
        kind = merger_ghost_kind(got, _clean(scan, number))
        return decide(item, kind, detail, (f"{repo}#{number}",))
    authors = _numbers(scan.prs, lambda pr: deleted(pr.get("author")))
    if authors:
        detail = f"удалённый только автор PR {authors[:MAX_CANDIDATES]} — вне §3.4.1"
        return Row(item, "not_verified", detail)
    others = _numbers(scan.prs, lambda pr: any(unresolved(a) for a in _pass_actors(pr)))
    if others:
        detail = (
            f"акторы типа вне User/Bot (не удалённые) в {others[:MAX_CANDIDATES]} — "
            "пункт про null/ghost, не проверялся"
        )
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
        return decide(
            item, "pending:foreign_visible", f"чужие PENDING видны ({scan.viewer})", targets
        )
    detail = (
        f"чужих PENDING в обходе {repo} нет (PENDING всего: {total}; {scan.coverage()}); "
        "без второй учётки нельзя отличить «нет» от «не видно»"
    )
    return decide(item, "pending:none_seen", detail)


def anonymous_kind(history: object, remaining: Sequence[str | None], limits: Sequence[str]) -> str:
    """Item 7 kind of ``read_policy_history``: answered (activations or a violation) with
    the anonymous quota in every response — ``history:answered:anon_quota``; a refusal
    keeps ``history:<code>:<origin>``."""
    kind = kind_of("history", history)
    if kind not in ("history:ok", "history:violation"):
        return kind
    if remaining and None not in remaining and list(limits) == [ANON_LIMIT]:
        return "history:answered:anon_quota"
    return "history:answered:no_anon_quota"


def check_anonymous(repo: str, anon: RecordingTransport) -> Row:
    """Item 7: H1–H6 without a token; ``read_policy_history`` over the anonymous transport."""
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
    seen = anon.ratelimit[start:]
    remaining = [r for _, r, _ in seen]
    limits = sorted({str(lim) for _, _, lim in seen})
    lib = _reason(history) if not isinstance(history, tuple) else f"{len(history)} activation(s)"
    detail = (
        f"H1–H6 HTTP 200; read_policy_history(anchor=head {head[:12]}) → {lib}; "
        f"x-ratelimit-limit {limits}, remaining {remaining[0]}→{remaining[-1]} "
        f"over {len(seen)} requests"
    )
    return decide(ITEM_ANON, anonymous_kind(history, remaining, limits), detail, (repo, head))


def check_slash_base(t: Transport, anon: Transport, repo: str) -> Row:
    """Item 8: ``compare`` with a ``/`` branch as base — encoded vs plain, both transports.

    The row's kind: a refuting transport wins, ``slash:same`` needs both transports.
    """
    owner, name = _split(repo)
    branches = _get(t, f"repos/{owner}/{name}/branches?per_page=100", "branches")
    slashed = [b["name"] for b in branches if "/" in b["name"]]
    item = "8-compare-slash-base"
    if not slashed:
        return Row(item, "not_verified", f"веток с «/» в {repo} нет")
    default = _get(t, f"repos/{owner}/{name}", "H1")["default_branch"]
    sha = _get(t, f"repos/{owner}/{name}/commits/{quote(default, safe='')}", "H3")["sha"]
    base = slashed[0]
    encoded = compare_path(owner, name, base, sha)
    plain = f"repos/{owner}/{name}/compare/{base}...{sha}"
    targets = (repo, base, sha)
    answers = {
        label: (_body(tr, encoded), _body(tr, plain))
        for label, tr in (("gh", t), ("anonymous", anon))
    }
    kinds = {label: slash_kind(*pair) for label, pair in answers.items()}
    if "slash:encoded_404" in kinds.values():
        kind = "slash:encoded_404"
    elif all(k == "slash:same" for k in kinds.values()):
        kind = "slash:same"
    else:
        kind = next(k for k in kinds.values() if k != "slash:same")
    detail = "; ".join(
        f"{label}: {kinds[label]} encoded HTTP {e[0]} {_compare_key(e[1])}, "
        f"plain HTTP {p[0]} {_compare_key(p[1])}"
        for label, (e, p) in answers.items()
    )
    return decide(item, kind, detail, targets)


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
    """CLI: print the JSON report; exit code — ``exit_code``."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--profile", type=Path, required=True, help="GH_CONFIG_DIR (read-only)")
    args = parser.parse_args(argv)
    report = run(args.profile.expanduser())
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
