#!/usr/bin/env python3
"""Read-only capture of real GitHub responses for `approval-facts/v3` (M0).

Every fixture and vector of `approval-facts/v3` is derived from these captures,
not written by hand (spec §3.8, controller ruling R-M0-7): a validator may only
demand a shape that the real request/response actually has.

Rules, enforced by construction:

- only ``GET`` REST requests and GraphQL **queries** (no mutation keyword is
  ever sent; the script refuses any query text containing ``mutation``);
- only the **public** repositories in ``ALLOWED_REPOS`` (steward itself is
  public: nothing from a private repository may land in its tree); publicity is
  a property proven live (``prove_public``) before any other exchange for the
  repository, not a name in a list;
- **fail-closed**: every exchange must match its declared expectation — ``gh``
  exit code, an HTTP status line, a parseable JSON body, GraphQL ``errors``
  present only where the capture declares them, and completeness (no next page,
  no truncated tree) unless declared (``Expect``). Any mismatch aborts
  the run before a single file is written;
- **atomic**: all files are written to a temporary sibling directory and swapped
  in only after every exchange succeeded; stale files of a previous capture do
  not survive the swap;
- ``MANIFEST.json`` records the sha256 of every capture file;
- sanitized output: response headers are dropped except the status line and
  ``Link``; every ``email`` key, every key containing ``token``, commit
  ``signature``/``payload`` and ``files[].patch`` are removed; no tokens are
  ever written.

Usage (default ``gh`` account)::

    uv run python scripts/capture_approval_facts_v3_live.py \
        --out tests/fixtures/approvalfacts_v3/live

The GraphQL texts are the library's canonical query texts of spec §3.8, imported
from ``steward.approvalfacts.v3.queries`` so the script and the library cannot
diverge; a test asserts the captured ``request.query`` equals the library text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from steward.approvalfacts.v3.queries import (
    Q_COMMIT_PULLS,
    Q_DELIVERY,
    Q_REVIEWS,
    Q_TIMELINE,
    q_blobs,
)

DESCRIPTION = "Read-only, fail-closed capture of GitHub responses for approval-facts/v3."

ALLOWED_REPOS = frozenset(
    {
        "andrei-shtanakov/devtools",
        "andrei-shtanakov/approval-policy",
    }
)

_DROP_KEYS = frozenset({"email", "signature", "payload", "patch"})


class CaptureError(RuntimeError):
    """An exchange did not match its declared expectation — nothing is written."""


@dataclass(frozen=True)
class Expect:
    """Declared outcome of one exchange (spec §3.8: row R0/G0).

    The default is the normal case: ``gh`` exit 0, HTTP 200, a JSON body and —
    for GraphQL — no ``errors``. A capture of a *negative* shape declares it here
    explicitly (``neg_e2_delivery_absent_pr``: 200 with ``errors``;
    ``h5_tree_absent_422``: 422); an undeclared deviation aborts the run.
    """

    status: int = 200
    graphql_errors: bool = False
    #: Completeness is part of the expectation: by default every connection is
    #: single-page (``pageInfo.hasNextPage == false``), no REST ``Link`` has
    #: ``rel="next"`` and no tree is ``truncated``. Only a capture of the Link
    #: grammar itself (P1) declares a next page.
    next_page: bool = False

    @property
    def gh_ok(self) -> bool:
        """``gh api`` exits 0 only for a 2xx response without GraphQL ``errors``."""
        return 200 <= self.status < 300 and not self.graphql_errors


def _dropped(key: str) -> bool:
    return key in _DROP_KEYS or "token" in key


def sanitize(value: Any) -> Any:
    """Drop e-mail addresses, any ``*token*`` key, signatures and diff bodies, recursively."""
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if not _dropped(k)}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


def _split(raw: str, what: str) -> tuple[int, str | None, Any]:
    """Status line, ``Link`` header and JSON body of ``gh api --include`` output.

    Raises :class:`CaptureError` when there is no header/body separator, no HTTP
    status line, or the body is not JSON — never substitutes a placeholder.
    """
    head, sep, body = raw.replace("\r\n", "\n").partition("\n\n")
    if not sep:
        raise CaptureError(f"{what}: no header/body separator in gh output")
    lines = head.split("\n")
    parts = lines[0].split()
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise CaptureError(f"{what}: no HTTP status line: {lines[0][:80]!r}")
    link = next(
        (ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("link:")), None
    )
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CaptureError(f"{what}: body is not JSON: {exc}") from exc
    return int(parts[1]), link, parsed


def _run_gh(argv: list[str], stdin: str | None) -> tuple[int, str, str]:
    """The only process boundary; tests replace it with a stub."""
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, read-only gh api
            argv, input=stdin, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureError(f"{argv[2:]}: gh did not run: {exc}") from exc
    return proc.returncode, proc.stdout, proc.stderr


def _gh(
    args: list[str], what: str, expect: Expect, stdin: str | None = None
) -> tuple[int, str | None, Any]:
    rc, out, err = _run_gh(["gh", "api", "--include", *args], stdin)
    if (rc == 0) != expect.gh_ok:
        raise CaptureError(f"{what}: gh exit code {rc} (stderr: {err.strip()[:200]!r})")
    status, link, body = _split(out, what)
    if status != expect.status:
        raise CaptureError(f"{what}: HTTP {status}, expected {expect.status}")
    if body is None:
        raise CaptureError(f"{what}: empty JSON body")
    _check_complete(body, link, expect, what)
    return status, link, body


def _check_complete(body: Any, link: str | None, expect: Expect, what: str) -> None:
    """A capture declared complete must be complete (spec §3.4 «все страницы», H5).

    ``pageInfo.hasNextPage == true`` anywhere, a ``rel="next"`` link or a
    ``truncated: true`` tree abort the run unless the capture declares a next page.
    """
    if not expect.next_page and link is not None and 'rel="next"' in link:
        raise CaptureError(f"{what}: REST list has a next page, capture declared complete")
    stack = [body]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        page = node.get("pageInfo")
        if isinstance(page, dict) and page.get("hasNextPage") and not expect.next_page:
            raise CaptureError(f"{what}: pageInfo.hasNextPage, capture declared single-page")
        if node.get("truncated") is True:
            raise CaptureError(f"{what}: truncated tree, capture declared complete")
        stack.extend(node.values())


#: Repositories whose publicity was proven live in this run (`prove_public`).
_PROVEN_PUBLIC: set[str] = set()


def _check_repo_path(path: str) -> str:
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "repos" or f"{parts[1]}/{parts[2]}" not in ALLOWED_REPOS:
        raise CaptureError(f"repository outside the read-only allowlist: {path}")
    return f"{parts[1]}/{parts[2]}"


def _require_public(repo: str, what: str) -> None:
    if repo not in _PROVEN_PUBLIC:
        raise CaptureError(f"{what}: publicity of {repo} not proven before this exchange")


def prove_public(repo: str) -> dict[str, Any]:
    """First exchange for ``repo``: a live REST proof that it is public.

    steward is public, so nothing from a private repository may land in its tree.
    The allowlist alone is a name, not a property: the run proves the property
    (``"private": false`` and ``"visibility": "public"``) before any other
    exchange for that repository is allowed.
    """
    _check_repo_path(f"repos/{repo}")
    status, link, body = _gh([f"repos/{repo}"], f"repos/{repo}", Expect())
    fields = body if isinstance(body, dict) else {}
    if fields.get("private") is not False or fields.get("visibility") != "public":
        raise CaptureError(
            f"repos/{repo}: not proven public (private={fields.get('private')!r}, "
            f"visibility={fields.get('visibility')!r})"
        )
    _PROVEN_PUBLIC.add(repo)
    return {
        "request": {"method": "GET", "path": f"repos/{repo}"},
        "response": {"status": status, "link": link, "body": sanitize(body)},
    }


def rest(path: str, expect: Expect = Expect()) -> dict[str, Any]:
    _require_public(_check_repo_path(path), path)
    status, link, body = _gh([path], path, expect)
    return {
        "request": {"method": "GET", "path": path},
        "response": {"status": status, "link": link, "body": sanitize(body)},
    }


def graphql(
    op: str, query: str, variables: dict[str, Any], expect: Expect = Expect()
) -> dict[str, Any]:
    if "mutation" in query:
        raise CaptureError("refusing a GraphQL text containing 'mutation'")
    repo = f"{variables.get('owner')}/{variables.get('name')}"
    _repo(repo)
    _require_public(repo, op)
    payload = json.dumps({"query": query, "variables": variables, "operationName": op})
    status, link, body = _gh(["graphql", "--input", "-"], op, expect, stdin=payload)
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        raise CaptureError(f"{op}: GraphQL envelope without a data object")
    if bool(body.get("errors")) != expect.graphql_errors:
        raise CaptureError(f"{op}: GraphQL errors {body.get('errors')!r} not declared")
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
        raise CaptureError(f"repository outside the read-only allowlist: {full}")
    owner, name = full.split("/")
    return {"owner": owner, "name": name}


DEV = "andrei-shtanakov/devtools"
POL = "andrei-shtanakov/approval-policy"

# Real objects (found read-only 2026-10-30; see the README one level up).
# Each positive set is one PR with ONE real path that the vector uses: the
# exchanges are replayed unmodified (spec §5, R-M0-7).
POSITIVE_SETS = (
    # (label, repo, pr, path) — merge-commit merge by a User; squash merge by a User.
    ("pr657", DEV, 657, "docs/evidence/2026-10-30-df-stage2a-acceptance.md"),
    ("pr636", DEV, 636, "TODO.md"),
)
#: Closed, never merged PR of a public repo: REST still carries a test merge_commit_sha.
CLOSED_UNMERGED = (DEV, 606)
#: Merged PR whose reviews were submitted on commits other than its final head.
REVIEWS_OFF_HEAD = (DEV, 603)
POL_ROOT_BRANCH = "main"


def _full_sha(repo: str, ref: str) -> str:
    sha = rest(f"repos/{repo}/commits/{ref}")["response"]["body"].get("sha")
    if not isinstance(sha, str):
        raise CaptureError(f"repos/{repo}/commits/{ref}: no sha")
    return sha


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
    _PROVEN_PUBLIC.clear()
    for repo in sorted(ALLOWED_REPOS):  # publicity proof precedes every other exchange
        out[f"pub_{repo.split('/')[1].replace('-', '_')}"] = prove_public(repo)
    for label, repo, pr, path in POSITIVE_SETS:
        out[f"{label}_act_sequence"] = {
            "pr": pr,
            "repo": repo,
            "paths": [path],
            "exchanges": act_sequence(repo, pr, path),
        }
    # Closed, never merged: delivery only (the library stops there), plus one review pass.
    closed_repo, closed_pr = CLOSED_UNMERGED
    out[f"closed{closed_pr}_act_sequence"] = {
        "pr": closed_pr,
        "repo": closed_repo,
        "paths": ["README.md"],
        "exchanges": _delivery_pair(closed_repo, closed_pr),
    }
    out[f"closed{closed_pr}_review_pass"] = {"exchanges": _review_pass(closed_repo, closed_pr)}
    # Reviews submitted on commits other than the PR head (CHANGES_REQUESTED and APPROVED on
    # earlier pushes): the live shape of `commit_id != head_sha` (spec §3.5, Review Focus 3).
    review_repo, review_pr = REVIEWS_OFF_HEAD
    out[f"pr{review_pr}_review_pass"] = {"exchanges": _review_pass(review_repo, review_pr)}
    # Negative shapes, declared explicitly (Expect), kept separately.
    dev = _repo(DEV)
    out["neg_e2_delivery_absent_pr"] = graphql(
        "StewardV3Delivery",
        Q_DELIVERY,
        {**dev, "number": 999999},
        Expect(status=200, graphql_errors=True),
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
    out["h5_tree_absent_422"] = rest(
        f"repos/{POL}/git/trees/{'0' * 40}?recursive=1", Expect(status=422)
    )
    # P1 — REST Link header (pagination) on a list endpoint.
    out["p1_rest_list_with_link_next"] = rest(
        f"repos/{DEV}/pulls?state=closed&per_page=2", Expect(next_page=True)
    )
    return out


def _dump(record: Any) -> bytes:
    return (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def write_atomically(out: Path, data: dict[str, dict[str, Any]], captured_at: str) -> None:
    """Write every capture and MANIFEST into a temp sibling, then swap it in.

    The previous directory (if any) is replaced as a whole, so a renamed or
    dropped capture does not survive as a stale «live» file.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    try:
        digests: dict[str, str] = {}
        for name, record in sorted(data.items()):
            payload = _dump(record)
            (tmp / f"{name}.json").write_bytes(payload)
            digests[f"{name}.json"] = hashlib.sha256(payload).hexdigest()
        manifest = {"captured_at": captured_at, "repos": sorted(ALLOWED_REPOS), "files": digests}
        (tmp / "MANIFEST.json").write_bytes(_dump(manifest))
        old = out.with_name(f".{out.name}.old")
        shutil.rmtree(old, ignore_errors=True)
        if out.exists():
            out.rename(old)
        tmp.rename(out)
        shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        data = captures()
    except CaptureError as exc:
        print(f"capture aborted, nothing written: {exc}", file=sys.stderr)
        return 1
    write_atomically(args.out, data, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    print(f"{len(data)} captures → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
