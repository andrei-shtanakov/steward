#!/usr/bin/env python3
"""Read-only live smoke of `steward.approvalfacts.v3` against real GitHub (M0 Task 13).

Checklist — the rows of spec §8 marked «смоук» that were **not** captured live on
2026-10-30 (plan Task 13, items 1–9). Every item ends as one row with an outcome:

- ``verified`` — the live answer confirms the hypothesis the library relies on;
- ``not_verified`` — no target exists in the allowed repositories, or the check
  needs a write the owner did not authorize; the reason is recorded;
- ``contradicts`` — the hypothesis is wrong. This is a finding for the manifest and
  steward#211, never a reason to change library expectations.

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
import json
import sys
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from steward.approvalfacts.v3 import (
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

DEVTOOLS = "andrei-shtanakov/devtools"
POLICY = "andrei-shtanakov/approval-policy"
ALLOWED_REPOS = frozenset({DEVTOOLS, POLICY})
OUTCOMES = ("verified", "not_verified", "contradicts")
API = "https://api.github.com/"
#: Bound of the PR scan (50 PRs per page): a stuck cursor cannot loop forever.
MAX_SCAN_PAGES = 40
#: At most this many candidates per item are read through the library.
MAX_CANDIDATES = 3
COMPARE_STATUSES = frozenset({"identical", "behind", "ahead", "diverged"})
NO_WRITE = "запись на стенде не разрешена владельцем"

#: Target discovery (not a library query): every PR with the facts the items need.
#: The dismissal flag reads one filtered node, not ``totalCount``: live,
#: ``timelineItems.totalCount`` ignores ``itemTypes`` (spec §3.4).
Q_SCAN = (
    "query StewardV3SmokeScan($owner:String!,$name:String!,$after:String){"
    "viewer{login} repository(owner:$owner,name:$name){"
    "pullRequests(first:50,after:$after){nodes{number state merged baseRefName "
    "author{login __typename} mergedBy{login __typename} "
    "reviews(first:100){totalCount nodes{state author{login __typename}}} "
    "timelineItems(itemTypes:[REVIEW_DISMISSED_EVENT],first:1){nodes{__typename}}} "
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
    """PR scan of one repository: the viewer and every PR node."""

    viewer: str
    prs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AnonTransport:
    """Anonymous REST transport (docs/approval-facts-v3.md «собственный анонимный»).

    ``GET`` only, no credentials of any kind. Every response's
    ``x-ratelimit-remaining`` is recorded in ``remaining`` (path → value).
    """

    timeout: float = 30
    remaining: list[tuple[str, str | None]] = field(default_factory=list)

    def send(self, request: Request) -> Response:
        """Send one anonymous ``GET``; a delivery failure is ``Response(None)``."""
        if request.method != "GET":
            raise ValueError(f"anonymous transport is GET-only: {request.method}")
        req = urllib.request.Request(API + request.path, headers=_ANON_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                return self._response(request.path, resp.status, resp.headers, resp.read())
        except urllib.error.HTTPError as err:
            return self._response(request.path, err.code, err.headers, err.read())
        except (OSError, ValueError) as exc:
            return Response(None, error=f"anonymous transport: {exc}")

    def _response(self, path: str, status: int, headers: Any, raw: bytes) -> Response:
        self.remaining.append((path, headers.get("x-ratelimit-remaining")))
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


def _split(repo: str) -> tuple[str, str]:
    if repo not in ALLOWED_REPOS:
        raise ValueError(f"repository not allowed for the smoke: {repo}")
    owner, name = repo.split("/")
    return owner, name


def _body(t: Transport, path: str) -> tuple[int | None, Any]:
    response = t.send(Request("GET", path))
    return response.status, response.body


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
        data = body["data"]
        scan.viewer = data["viewer"]["login"]
        conn = data["repository"]["pullRequests"]
        scan.prs.extend(conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            return scan
        after = conn["pageInfo"]["endCursor"]
    return Unavailable(f"pagination: scan {repo} exceeds {MAX_SCAN_PAGES} pages")


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


def _actors(pr: Mapping[str, Any]) -> list[object]:
    reviews = [r.get("author") for r in pr["reviews"]["nodes"]]
    return [pr.get("author"), *reviews, *([pr["mergedBy"]] if pr.get("merged") else [])]


def dismissal_row(repo: str, results: Mapping[int, ReviewFacts | Unavailable]) -> Row:
    """Item 1: dismissals read by the library — shape, ``previous_state``, count = DISMISSED.

    No candidate — ``not_verified`` (dismissing on the stand needs a write the owner did
    not authorize). The library refuses a ``null`` ``previousReviewState`` and a
    DISMISSED review without its event, so those codes are the contradiction.
    """
    if not results:
        return Row("1-review-dismissed-event", "not_verified", f"{NO_WRITE}; в {repo} отзывов нет")
    targets = tuple(f"{repo}#{n}" for n in results)
    lines = []
    for number, got in results.items():
        if isinstance(got, Unavailable):
            if got.code in ("dismissal_without_previous_state", "dismissed_without_event"):
                return Row(
                    "1-review-dismissed-event", "contradicts", f"#{number}: {got.reason}", targets
                )
            return Row(
                "1-review-dismissed-event", "not_verified", f"#{number}: {got.reason}", targets
            )
        dismissed = sum(r.state == "DISMISSED" for r in got.reviews)
        if dismissed != len(got.dismissals):
            detail = f"#{number}: DISMISSED={dismissed}, events={len(got.dismissals)}"
            return Row("1-review-dismissed-event", "contradicts", detail, targets)
        states = sorted({str(d.previous_state) for d in got.dismissals})
        lines.append(f"#{number}: events={len(got.dismissals)} previous={states}")
    return Row("1-review-dismissed-event", "verified", "; ".join(lines), targets)


def compare_status_row(item: str, want: str, status: int | None, body: Any, target: str) -> Row:
    """Items 4a/4b: a live ``compare`` whose topology must give the ``want`` status."""
    got = body.get("status") if isinstance(body, dict) else None
    if status != 200 or got not in COMPARE_STATUSES:
        return Row(item, "contradicts", f"HTTP {status}, status={got!r}", (target,))
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
        return Row(row.item, row.outcome, f"{row.detail} (обход {len(scan.prs)} PR)")
    return row


def check_app_merger(t: Transport, repo: str, scan: Scan) -> Row:
    """Item 2: REST ``slug[bot]``/``Bot`` and GraphQL ``slug``/``Bot`` of an App merge."""
    numbers = _numbers(
        scan.prs, lambda pr: pr["merged"] and (pr["mergedBy"] or {}).get("__typename") == "Bot"
    )
    item = "2-app-merger"
    if not numbers:
        detail = f"мержей App в {repo} нет (обход {len(scan.prs)} PR)"
        return Row(item, "not_verified", detail)
    number = numbers[0]
    owner, name = _split(repo)
    _, pull = _body(t, f"repos/{owner}/{name}/pulls/{number}")
    rest = (pull or {}).get("merged_by") or {}
    got = read_act_facts(t, repo, number, paths=())
    target = (f"{repo}#{number}",)
    if isinstance(got, Unavailable):
        outcome = "contradicts" if got.code == "bot_login_mismatch" else "not_verified"
        return Row(
            item, outcome, f"REST {rest.get('login')}/{rest.get('type')}; {got.reason}", target
        )
    delivery = got[0]
    detail = (
        f"REST {rest.get('login')}/{rest.get('type')} → "
        f"{delivery.merged_by_login}/{delivery.merged_by_type_hint}"
    )
    ok = rest.get("type") == "Bot" and str(rest.get("login")).endswith("[bot]")
    return Row(item, "verified" if ok else "contradicts", detail, target)


def check_open_pr(t: Transport, repo: str, scan: Scan) -> Row:
    """Item 3: REST ``state: open``, GraphQL ``OPEN``, ``read_open_review`` facts."""
    numbers = _numbers(scan.prs, lambda pr: pr["state"] == "OPEN")
    item = "3-open-pr"
    if not numbers:
        return Row(item, "not_verified", f"открытых PR в {repo} на момент прогона нет")
    number = numbers[0]
    owner, name = _split(repo)
    _, pull = _body(t, f"repos/{owner}/{name}/pulls/{number}")
    facts = read_open_review(t, repo, number)
    act = read_act_facts(t, repo, number, paths=())
    target = (f"{repo}#{number}",)
    if isinstance(facts, Unavailable):
        return Row(item, "not_verified", facts.reason, target)
    if isinstance(act, Unavailable):
        return Row(item, "not_verified", act.reason, target)
    rest_state = (pull or {}).get("state")
    ok = rest_state == "open" and facts.pr_state == "OPEN" and not act[0].merged
    detail = f"REST state={rest_state}, pr_state={facts.pr_state}, merged={act[0].merged}"
    return Row(item, "verified" if ok else "contradicts", detail, target)


def check_compare_topologies(t: Transport, repo: str, scan: Scan) -> list[Row]:
    """Items 4a/4b: ``parent...head`` of the default branch (ahead) and
    ``default...head of a closed unmerged PR`` (diverged)."""
    owner, name = _split(repo)
    _, meta = _body(t, f"repos/{owner}/{name}")
    branch = meta["default_branch"]
    _, head = _body(t, f"repos/{owner}/{name}/commits/{quote(branch, safe='')}")
    head_sha, parent = head["sha"], head["parents"][0]["sha"]
    path = f"repos/{owner}/{name}/compare/{parent}...{head_sha}"
    rows = [compare_status_row("4a-compare-ahead", "ahead", *_body(t, path), f"{repo} {path}")]
    closed = _numbers(scan.prs, lambda pr: pr["state"] == "CLOSED" and not pr["merged"])
    if not closed:
        rows.append(Row("4b-compare-diverged", "not_verified", "закрытых несмёрженных PR нет"))
        return rows
    _, pull = _body(t, f"repos/{owner}/{name}/pulls/{closed[-1]}")
    side = pull["head"]["sha"]
    path = f"repos/{owner}/{name}/compare/{quote(branch, safe='')}...{side}"
    target = f"{repo}#{closed[-1]} {path}"
    rows.append(compare_status_row("4b-compare-diverged", "diverged", *_body(t, path), target))
    return rows


def check_deleted_user(t: Transport, repo: str, scan: Scan) -> Row:
    """Item 5: a deleted user (``null``/``ghost``) among PR actors → ``actor_unresolved``."""
    numbers = _numbers(scan.prs, lambda pr: any(unresolved(a) for a in _actors(pr)))
    item = "5-deleted-user"
    if not numbers:
        return Row(item, "not_verified", f"удалённых пользователей в {repo} нет")
    number = numbers[0]
    forms = sorted(
        {
            json.dumps(a)
            for a in _actors(next(p for p in scan.prs if p["number"] == number))
            if unresolved(a)
        }
    )
    got = read_open_review(t, repo, number)
    target = (f"{repo}#{number}",)
    if isinstance(got, Unavailable) and got.code == "actor_unresolved":
        return Row(item, "verified", f"forms {forms} → actor_unresolved", target)
    return Row(
        item,
        "contradicts",
        f"forms {forms} → {got if isinstance(got, Unavailable) else 'facts'}",
        target,
    )


def check_pending(scan: Scan, repo: str) -> Row:
    """Item 6: a PENDING review of another account visible to this token."""
    foreign = [
        pr["number"]
        for pr in scan.prs
        for r in pr["reviews"]["nodes"]
        if r["state"] == "PENDING" and (r["author"] or {}).get("login") != scan.viewer
    ]
    own = sum(r["state"] == "PENDING" for pr in scan.prs for r in pr["reviews"]["nodes"])
    item = "6-pending-visibility"
    if foreign:
        return Row(
            item,
            "verified",
            f"чужие PENDING видны ({scan.viewer})",
            tuple(f"{repo}#{n}" for n in foreign),
        )
    detail = (
        f"чужих PENDING в обходе {repo} нет (PENDING всего: {own}); без второй учётки "
        "нельзя отличить «нет» от «не видно»"
    )
    return Row(item, "not_verified", detail)


def check_anonymous(repo: str, anon: AnonTransport) -> Row:
    """Item 7: H1–H6 without a token; ``read_policy_history`` over the anonymous transport."""
    owner, name = _split(repo)
    status, meta = _body(anon, f"repos/{owner}/{name}")
    item, target = "7-anonymous-transport", (repo,)
    if status != 200:
        return Row(item, "contradicts", f"H1 HTTP {status}", target)
    branch = meta["default_branch"]
    statuses = {"H1": status}
    statuses["H2"], br = _body(anon, f"repos/{owner}/{name}/branches/{quote(branch, safe='')}")
    head = br["commit"]["sha"]
    statuses["H3"], commit = _body(anon, f"repos/{owner}/{name}/commits/{head}")
    statuses["H4"], _ = _body(anon, f"repos/{owner}/{name}/commits/{head}/pulls?per_page=100")
    tree_sha = commit["commit"]["tree"]["sha"]
    statuses["H5"], tree = _body(anon, f"repos/{owner}/{name}/git/trees/{tree_sha}?recursive=1")
    blob = next(e["sha"] for e in tree["tree"] if e["type"] == "blob")
    statuses["H6"], _ = _body(anon, f"repos/{owner}/{name}/git/blobs/{blob}")
    history = read_policy_history(anon, repo, branch, anchor=head, until=None)
    if isinstance(history, HistoryViolation | Unavailable):
        outcome_lib = history.reason
    else:
        outcome_lib = f"{len(history)} activation(s)"
    remaining = [value for _, value in anon.remaining]
    detail = (
        f"{statuses}; read_policy_history(anchor=head {head[:12]}) → {outcome_lib}; "
        f"x-ratelimit-remaining {remaining[0]}→{remaining[-1]} over {len(remaining)} requests"
    )
    ok = all(s == 200 for s in statuses.values()) and not isinstance(history, Unavailable)
    return Row(item, "verified" if ok else "contradicts", detail, (*target, head))


def check_slash_base(t: Transport, anon: AnonTransport, repo: str) -> Row:
    """Item 8: ``compare`` with a ``/`` branch as base — encoded vs plain, both transports."""
    owner, name = _split(repo)
    _, branches = _body(t, f"repos/{owner}/{name}/branches?per_page=100")
    slashed = [b["name"] for b in branches or [] if "/" in b["name"]]
    if not slashed:
        return Row("8-compare-slash-base", "not_verified", f"веток с «/» в {repo} нет")
    _, meta = _body(t, f"repos/{owner}/{name}")
    _, head = _body(t, f"repos/{owner}/{name}/commits/{quote(meta['default_branch'], safe='')}")
    base, sha = slashed[0], head["sha"]
    encoded = f"repos/{owner}/{name}/compare/{quote(base, safe='')}...{sha}"
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


def run(profile: Path) -> dict[str, Any]:
    """Run every checklist item; return the report (summary + rows)."""
    gh = GhTransport(profile)
    anon = AnonTransport()
    scan = scan_prs(gh, DEVTOOLS)
    if isinstance(scan, Unavailable):
        raise SystemExit(f"scan failed: {scan.reason}")
    rows = [
        check_dismissals(gh, DEVTOOLS, scan),
        check_app_merger(gh, DEVTOOLS, scan),
        check_open_pr(gh, DEVTOOLS, scan),
        *check_compare_topologies(gh, DEVTOOLS, scan),
        check_deleted_user(gh, DEVTOOLS, scan),
        check_pending(scan, DEVTOOLS),
        check_anonymous(POLICY, anon),
        check_slash_base(gh, anon, DEVTOOLS),
        OUT_OF_SMOKE,
    ]
    report = summarize(rows)
    report["scan"] = {"repo": DEVTOOLS, "viewer": scan.viewer, "prs": len(scan.prs)}
    report["anonymous_ratelimit_remaining"] = anon.remaining
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: print the JSON report; exit 1 if any row ``contradicts``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True, help="GH_CONFIG_DIR (read-only)")
    args = parser.parse_args(argv)
    report = run(args.profile.expanduser())
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if report["counts"]["contradicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
