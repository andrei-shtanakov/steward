#!/usr/bin/env python3
"""Read-only capture of real GitHub responses for `approval-facts/v3` (M0).

Every fixture and vector of `approval-facts/v3` is derived from these captures,
not written by hand (spec §3.8, controller ruling R-M0-7): a validator may only
demand a shape that the real request/response actually has.

Rules, enforced by construction:

- only ``GET`` REST requests and GraphQL **queries** (no mutation keyword is
  ever sent; the script refuses any query text containing ``mutation``);
- only the repositories in ``ALLOWED_REPOS``;
- sanitized output: response headers are dropped except the status line and
  ``Link``; every ``email`` key, every key containing ``token``, commit
  ``signature``/``payload`` and
  ``files[].patch`` are removed; no tokens are ever written.

Usage (default ``gh`` account)::

    uv run python scripts/capture_approval_facts_v3_live.py \
        --out tests/fixtures/approvalfacts_v3/live

The GraphQL texts below are the canonical query texts of spec §3.8 until the
library owns them (plan Task 2 moves them to
``src/steward/approvalfacts/v3/queries.py`` and this script imports them from
there; a test asserts the captured ``request.query`` equals the library text).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ALLOWED_REPOS = frozenset(
    {
        "andrei-shtanakov/devtools",
        "andrei-shtanakov/approval-policy",
        "andrei-shtanakov/df-roles-probe-private",
    }
)

Q_DELIVERY = (
    "query StewardV3Delivery($owner:String!,$name:String!,$number:Int!)"
    "{repository(owner:$owner,name:$name){pullRequest(number:$number)"
    "{number state merged mergedAt mergeCommit{oid} mergedBy{login __typename}"
    " headRefOid baseRefName}}}"
)
Q_REVIEWS = (
    "query StewardV3Reviews($owner:String!,$name:String!,$number:Int!,$after:String)"
    "{repository(owner:$owner,name:$name){pullRequest(number:$number)"
    "{state headRefOid baseRefName reviews(first:100,after:$after)"
    "{nodes{databaseId state submittedAt commit{oid} author{login __typename}}"
    " pageInfo{hasNextPage endCursor}}}}}"
)
Q_TIMELINE = (
    "query StewardV3Timeline($owner:String!,$name:String!,$number:Int!,$after:String)"
    "{repository(owner:$owner,name:$name){pullRequest(number:$number)"
    "{timelineItems(itemTypes:[REVIEW_DISMISSED_EVENT,MERGED_EVENT],first:100,after:$after)"
    "{nodes{__typename ... on ReviewDismissedEvent{createdAt actor{login __typename}"
    " previousReviewState review{databaseId}} ... on MergedEvent{createdAt commit{oid}}}"
    " pageInfo{hasNextPage endCursor}}}}}"
)
Q_COMMIT_PULLS = (
    "query StewardV3CommitPulls($owner:String!,$name:String!,$sha:GitObjectID!,$after:String)"
    "{repository(owner:$owner,name:$name){object(oid:$sha){... on Commit"
    "{associatedPullRequests(first:100,after:$after){nodes{number mergeCommit{oid}}"
    " pageInfo{hasNextPage endCursor}}}}}}"
)


def q_blobs(n: int) -> str:
    """`StewardV3Blobs` for ``n`` paths: aliases m0..m{n-1}, h0..h{n-1} (spec §3.8)."""
    decl = "".join(f",$m{i}:String!,$h{i}:String!" for i in range(n))
    sel = "".join(
        f" m{i}:object(expression:$m{i}){{__typename ... on Blob{{oid}}}}"
        f" h{i}:object(expression:$h{i}){{__typename ... on Blob{{oid}}}}"
        for i in range(n)
    )
    return (
        "query StewardV3Blobs($owner:String!,$name:String!,$merge:GitObjectID!,"
        f"$head:GitObjectID!{decl}){{repository(owner:$owner,name:$name)"
        f"{{mc:object(oid:$merge){{oid}} hc:object(oid:$head){{oid}}{sel}}}}}"
    )


_DROP_KEYS = frozenset({"email", "signature", "payload", "patch"})


def _dropped(key: str) -> bool:
    return key in _DROP_KEYS or "token" in key


def sanitize(value: Any) -> Any:
    """Drop e-mail addresses, any ``*token*`` key, signatures and diff bodies, recursively."""
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if not _dropped(k)}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


def _split(raw: str) -> tuple[int | None, str | None, Any]:
    head, sep, body = raw.replace("\r\n", "\n").partition("\n\n")
    if not sep:
        return None, None, None
    lines = head.split("\n")
    status: int | None = None
    parts = lines[0].split()
    if len(parts) >= 2 and parts[0].startswith("HTTP/") and parts[1].isdigit():
        status = int(parts[1])
    link = next(
        (ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("link:")), None
    )
    try:
        parsed: Any = json.loads(body) if body.strip() else None
    except json.JSONDecodeError:
        parsed = {"__unparsed_body__": body[:200]}
    return status, link, parsed


def _gh(args: list[str], stdin: str | None = None) -> tuple[int | None, str | None, Any]:
    proc = subprocess.run(  # noqa: S603 S607 — fixed argv, read-only gh api
        ["gh", "api", "--include", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return _split(proc.stdout)


def rest(path: str) -> dict[str, Any]:
    status, link, body = _gh([path])
    return {
        "request": {"method": "GET", "path": path},
        "response": {"status": status, "link": link, "body": sanitize(body)},
    }


def graphql(op: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    if "mutation" in query:
        raise SystemExit("refusing a GraphQL text containing 'mutation'")
    payload = json.dumps({"query": query, "variables": variables, "operationName": op})
    status, link, body = _gh(["graphql", "--input", "-"], stdin=payload)
    return {
        "request": {
            "method": "POST",
            "path": "graphql",
            "op": op,
            "variables": variables,
            "query": query,
        },
        "response": {"status": status, "link": link, "body": sanitize(body)},
    }


def _repo(full: str) -> dict[str, str]:
    if full not in ALLOWED_REPOS:
        raise SystemExit(f"repository outside the read-only allowlist: {full}")
    owner, name = full.split("/")
    return {"owner": owner, "name": name}


DEV = "andrei-shtanakov/devtools"
POL = "andrei-shtanakov/approval-policy"
PROBE = "andrei-shtanakov/df-roles-probe-private"

# Real objects (found read-only 2026-10-30; see README in the output dir).
# Each positive set is one PR with ONE real path that the vector uses: the
# exchanges are replayed unmodified (spec §5, R-M0-7).
POSITIVE_SETS = (
    # (label, repo, pr, path) — merge-commit merge by a User; squash merge by a User.
    (
        "pr657",
        "andrei-shtanakov/devtools",
        657,
        "docs/evidence/2026-10-30-df-stage2a-acceptance.md",
    ),
    ("pr636", "andrei-shtanakov/devtools", 636, "TODO.md"),
)
PROBE_CLOSED_PR = 1
POL_ROOT_BRANCH = "main"


def _full_sha(repo: str, ref: str) -> str:
    _, _, body = _gh([f"repos/{repo}/commits/{ref}"])
    return str(body["sha"])


def _delivery_pair(repo: str, pr: int) -> list[dict[str, Any]]:
    """E2 then E1 — the library's delivery read (spec §3.3 п. 1)."""
    return [
        graphql("StewardV3Delivery", Q_DELIVERY, {**_repo(repo), "number": pr}),
        rest(f"repos/{repo}/pulls/{pr}"),
    ]


def _review_pass(repo: str, pr: int) -> list[dict[str, Any]]:
    """One full pass: E3 then E4 (spec §3.4); single page in the captured PRs."""
    vars_ = {**_repo(repo), "number": pr, "after": None}
    return [
        graphql("StewardV3Reviews", Q_REVIEWS, vars_),
        graphql("StewardV3Timeline", Q_TIMELINE, vars_),
    ]


def act_sequence(repo: str, pr: int, path: str) -> list[dict[str, Any]]:
    """The exact request sequence of `read_act_facts(repo, pr, paths=[path])`.

    delivery (E2, E1) → compare (E5) → blobs (E6, one path) → pass 1 (E3, E4) →
    pass 2 (E3, E4) → delivery re-read (E2, E1). Every exchange is captured live,
    in this order, so a positive vector replays the set without any transform.
    """
    seq = _delivery_pair(repo, pr)
    pull = seq[1]["response"]["body"]
    merge, head, base = pull["merge_commit_sha"], pull["head"]["sha"], pull["base"]["ref"]
    seq.append(rest(f"repos/{repo}/compare/{base}...{merge}"))
    variables: dict[str, Any] = {
        **_repo(repo),
        "merge": merge,
        "head": head,
        "m0": f"{merge}:{path}",
        "h0": f"{head}:{path}",
    }
    seq.append(graphql("StewardV3Blobs", q_blobs(1), variables))
    seq += _review_pass(repo, pr) + _review_pass(repo, pr) + _delivery_pair(repo, pr)
    return seq


def captures() -> dict[str, dict[str, Any]]:
    """Every endpoint and state the design uses (spec §3.8, column «снимки»)."""
    out: dict[str, dict[str, Any]] = {}
    for label, repo, pr, path in POSITIVE_SETS:
        out[f"{label}_act_sequence"] = {
            "pr": pr,
            "repo": repo,
            "paths": [path],
            "exchanges": act_sequence(repo, pr, path),
        }
    # Closed, never merged: delivery only (REST carries a test merge_commit_sha).
    out["probe1_act_sequence"] = {
        "pr": PROBE_CLOSED_PR,
        "repo": PROBE,
        "paths": ["README.md"],
        "exchanges": _delivery_pair(PROBE, PROBE_CLOSED_PR),
    }
    out["probe1_review_pass"] = {"exchanges": _review_pass(PROBE, PROBE_CLOSED_PR)}
    # Negative shapes kept separately.
    dev = _repo(DEV)
    out["neg_e2_delivery_absent_pr"] = graphql(
        "StewardV3Delivery", Q_DELIVERY, {**dev, "number": 999999}
    )
    m657 = out["pr657_act_sequence"]["exchanges"][1]["response"]["body"]
    merge_sha, merge_head = m657["merge_commit_sha"], m657["head"]["sha"]
    paths = ["CLAUDE.md", "no/such/path.md", "docs"]
    variables: dict[str, Any] = {**dev, "merge": merge_sha, "head": merge_head}
    for i, p in enumerate(paths):
        variables[f"m{i}"] = f"{merge_sha}:{p}"
        variables[f"h{i}"] = f"{merge_head}:{p}"
    out["neg_e6_blobs_present_absent_tree"] = graphql("StewardV3Blobs", q_blobs(3), variables)
    # E7 — merge sha → PR (release producer).
    out["e7_commit_pulls_graphql_merge"] = graphql(
        "StewardV3CommitPulls", Q_COMMIT_PULLS, {**dev, "sha": merge_sha, "after": None}
    )
    # H1..H6 — history endpoints.
    out["h1_repo_policy"] = rest(f"repos/{POL}")
    out["h2_branch_policy"] = rest(f"repos/{POL}/branches/{POL_ROOT_BRANCH}")
    root = _full_sha(POL, POL_ROOT_BRANCH)
    out["h3_commit_root_no_parents"] = rest(f"repos/{POL}/commits/{root}")
    out["h3_commit_merge_two_parents"] = rest(f"repos/{DEV}/commits/{merge_sha}")
    out["h3_commit_side_one_parent"] = rest(f"repos/{DEV}/commits/{merge_head}")
    out["h4_commit_pulls_direct_push"] = rest(f"repos/{POL}/commits/{root}/pulls?per_page=100")
    out["h4_commit_pulls_merge_commit"] = rest(
        f"repos/{DEV}/commits/{merge_sha}/pulls?per_page=100"
    )
    out["h4_commit_pulls_side_commit"] = rest(
        f"repos/{DEV}/commits/{merge_head}/pulls?per_page=100"
    )
    m636 = out["pr636_act_sequence"]["exchanges"][1]["response"]["body"]
    out["h4_commit_pulls_squash"] = rest(
        f"repos/{DEV}/commits/{m636['merge_commit_sha']}/pulls?per_page=100"
    )
    tree = out["h3_commit_root_no_parents"]["response"]["body"]["commit"]["tree"]["sha"]
    out["h5_tree_recursive_policy"] = rest(f"repos/{POL}/git/trees/{tree}?recursive=1")
    blob = next(
        e["sha"]
        for e in out["h5_tree_recursive_policy"]["response"]["body"]["tree"]
        if e["path"] == "policy/approvers.env"
    )
    out["h6_blob_policy_approvers"] = rest(f"repos/{POL}/git/blobs/{blob}")
    out["h5_tree_absent_422"] = rest(f"repos/{POL}/git/trees/{'0' * 40}?recursive=1")
    # P1 — REST Link header (pagination) on a list endpoint.
    out["p1_rest_list_with_link_next"] = rest(f"repos/{DEV}/pulls?state=closed&per_page=2")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    data = captures()
    for name, record in sorted(data.items()):
        path = args.out / f"{name}.json"
        path.write_text(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    manifest = {
        "captured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "captures": sorted(data),
        "repos": sorted(ALLOWED_REPOS),
    }
    (args.out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{len(data)} captures → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
