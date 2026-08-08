"""``steward approval-facts`` materializer: typed merge-actor evidence via
GitHub's ``mergedBy`` (AP-4).

Local git proves merge *provenance* only — ``LiveGitFacts.merge_provenance``
always reports ``actor=None``, ``actor_source="unavailable"`` (see
:mod:`steward.gatecheck.git_facts`) because identity of the merging actor is
a forge fact, not a git one: ``git log --merges`` proves a merge commit
exists, but its author/committer trailers are set at commit-creation time
and are not the canonical merger — only GitHub's ``PullRequest.mergedBy``
is. This module is the sole authorized way to fetch that fact: it shells
out to the ``gh`` CLI through a single choke point (:func:`_gh`), so tests
substitute a fake without ever touching the network or a real credential,
and writes the result as a typed ``approval-facts/v1`` file that gate-check
reads offline (wired in via ``--approval-facts``, a later task in this
workstream).

Schema::

    {"schema": "approval-facts/v1",
     "actors": {"<merge_sha>": {"identity": "github:<login>", "type_hint": "User"}}}

Honesty rule (owner requirement, 2026-08-08): a materialization failure for
ANY requested merge SHA / PR — ``gh`` missing, not authenticated, a
PR/commit that can't be resolved, a merged PR with no resolvable
``mergedBy`` — aborts the whole run with a distinguishable, non-zero exit
(see :func:`main`'s exit codes) and writes **no** output file. A partial or
empty ``actors`` mapping would be indistinguishable from "checked,
genuinely found nothing", a claim this module is never in a position to
make for an identifier it was explicitly asked to resolve — "unavailable"
must never masquerade as an empty result.

This module owns the schema and the reader (:func:`load_approval_facts`);
the separate ``merge_provenance.actor``/``actor_source`` keys accepted by
``InjectedGitFacts.from_file`` (facts.json) remain a distinct, optional
direct-injection path for deterministic ``--no-fs`` test fixtures — the two
are never conflated. Combining a live ``MergeProvenance.sha`` with this
file's ``actors`` mapping is the consuming check's job, not this module's.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from steward.gatecheck.approval import ActorFact

__all__ = [
    "SCHEMA",
    "ApprovalFacts",
    "ApprovalFactsError",
    "GhNotFoundError",
    "GhUnavailableError",
    "MaterializeError",
    "load_approval_facts",
    "materialize_approval_facts",
    "write_approval_facts",
]

SCHEMA = "approval-facts/v1"


class ApprovalFactsError(ValueError):
    """Malformed ``approval-facts.json`` file — fail-closed, config-level."""


class MaterializeError(RuntimeError):
    """Base: materializing approval-facts via ``gh`` failed honestly."""


class GhUnavailableError(MaterializeError):
    """``gh`` is missing from PATH, not authenticated, or the call errored.

    Distinguishes execution-level failure (can't even ask the question)
    from :class:`GhNotFoundError` (asked, got an authoritative "no").
    """


class GhNotFoundError(MaterializeError):
    """A requested PR / merge commit could not be resolved via ``gh``.

    Covers: no PR found for a merge SHA, a PR number that doesn't exist,
    an unmerged PR (no merge commit), and a merged PR with no resolvable
    ``mergedBy`` actor.
    """


@dataclass(frozen=True)
class ApprovalFacts:
    """Typed merge-actor evidence, keyed by merge commit SHA."""

    actors: dict[str, ActorFact]


def load_approval_facts(path: Path) -> ApprovalFacts:
    """Load and validate an ``approval-facts/v1`` file, fail-closed."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovalFactsError(f"cannot read approval-facts file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ApprovalFactsError(f"{path}: top level must be a mapping")
    if data.get("schema") != SCHEMA:
        raise ApprovalFactsError(f"{path}: 'schema' must be {SCHEMA!r}, got {data.get('schema')!r}")

    raw_actors = data.get("actors")
    if not isinstance(raw_actors, dict):
        raise ApprovalFactsError(f"{path}: 'actors' must be a mapping")

    actors: dict[str, ActorFact] = {}
    for sha, entry in raw_actors.items():
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("identity"), str)
            or not isinstance(entry.get("type_hint"), str)
        ):
            raise ApprovalFactsError(
                f"{path}: actors[{sha!r}] needs string 'identity' and 'type_hint'"
            )
        actors[sha] = ActorFact(identity=entry["identity"], actor_type_hint=entry["type_hint"])
    return ApprovalFacts(actors=actors)


def write_approval_facts(path: Path, actors: dict[str, ActorFact]) -> None:
    """Write the typed facts file (schema ``approval-facts/v1``)."""
    payload = {
        "schema": SCHEMA,
        "actors": {
            sha: {"identity": fact.identity, "type_hint": fact.actor_type_hint}
            for sha, fact in actors.items()
        },
    }
    Path(path).write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _gh(args: list[str]) -> tuple[int, str]:
    """Single call-point for the ``gh`` CLI — tests monkeypatch this.

    Returns ``(returncode, stdout-on-success-else-stderr)``, stripped. A
    missing ``gh`` binary (or any other failure to even launch it) is
    folded into a non-zero returncode rather than propagating a raw
    ``OSError`` — the caller only has to check one thing.
    """
    try:
        proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    out = proc.stdout if proc.returncode == 0 else proc.stderr
    return proc.returncode, out.strip()


_QUERY_BY_SHA = (
    "query($owner:String!,$name:String!,$sha:GitObjectID!){"
    "repository(owner:$owner,name:$name){object(oid:$sha){"
    "... on Commit{associatedPullRequests(first:10){nodes{"
    "mergeCommit{oid} mergedBy{login __typename}}}}}}}"
)

_QUERY_BY_PR = (
    "query($owner:String!,$name:String!,$number:Int!){"
    "repository(owner:$owner,name:$name){pullRequest(number:$number){"
    "mergeCommit{oid} mergedBy{login __typename}}}}"
)


def _run_graphql(query: str, variables: dict[str, str | int], *, what: str) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        flag = "-F" if isinstance(value, int) else "-f"
        args += [flag, f"{key}={value}"]
    code, out = _gh(args)
    if code != 0:
        raise GhUnavailableError(f"gh api graphql failed for {what}: {out}")
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError as exc:
        raise GhUnavailableError(f"gh api graphql returned non-JSON for {what}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise GhUnavailableError(f"gh api graphql returned a non-object payload for {what}")
    return parsed


def _actor_fact_from_node(node: dict, *, what: str) -> ActorFact:
    merged_by = node.get("mergedBy")
    if not isinstance(merged_by, dict) or not isinstance(merged_by.get("login"), str):
        raise GhNotFoundError(f"{what}: PR has no resolvable 'mergedBy' actor")
    login = merged_by["login"]
    type_hint = merged_by.get("__typename")
    if not isinstance(type_hint, str):
        raise GhNotFoundError(f"{what}: mergedBy has no '__typename'")
    return ActorFact(identity=f"github:{login}", actor_type_hint=type_hint)


def _resolve_by_merge_sha(owner: str, name: str, sha: str) -> ActorFact:
    what = f"merge-sha {sha}"
    payload = _run_graphql(_QUERY_BY_SHA, {"owner": owner, "name": name, "sha": sha}, what=what)
    repository = (payload.get("data") or {}).get("repository") or {}
    obj = repository.get("object")
    if not isinstance(obj, dict):
        raise GhNotFoundError(f"{what}: commit not found in {owner}/{name}")
    nodes = ((obj.get("associatedPullRequests") or {}).get("nodes")) or []
    for node in nodes:
        merge_commit = node.get("mergeCommit") or {}
        if merge_commit.get("oid") == sha:
            return _actor_fact_from_node(node, what=what)
    raise GhNotFoundError(f"{what}: no associated pull request has this as its merge commit")


def _resolve_by_pr(owner: str, name: str, number: int) -> tuple[str, ActorFact]:
    what = f"PR #{number}"
    payload = _run_graphql(
        _QUERY_BY_PR, {"owner": owner, "name": name, "number": number}, what=what
    )
    repository = (payload.get("data") or {}).get("repository") or {}
    pr = repository.get("pullRequest")
    if not isinstance(pr, dict):
        raise GhNotFoundError(f"{what}: not found in {owner}/{name}")
    merge_commit = pr.get("mergeCommit") or {}
    sha = merge_commit.get("oid")
    if not isinstance(sha, str):
        raise GhNotFoundError(f"{what}: has no merge commit (not merged?)")
    return sha, _actor_fact_from_node(pr, what=what)


def materialize_approval_facts(
    repo: str,
    *,
    merge_shas: list[str] | None = None,
    prs: list[int] | None = None,
) -> dict[str, ActorFact]:
    """Resolve merge actors for the requested SHAs/PRs via ``gh``.

    Raises a :class:`MaterializeError` subclass (honest, distinguishable —
    :class:`GhUnavailableError` vs :class:`GhNotFoundError`) on the FIRST
    failure and returns nothing: never a partial mapping. ``repo`` must be
    ``"owner/name"``.
    """
    owner, sep, name = repo.partition("/")
    if not sep or not owner or not name:
        raise GhNotFoundError(f"--repo must be 'owner/name', got {repo!r}")

    actors: dict[str, ActorFact] = {}
    for sha in merge_shas or ():
        actors[sha] = _resolve_by_merge_sha(owner, name, sha)
    for number in prs or ():
        sha, fact = _resolve_by_pr(owner, name, number)
        actors[sha] = fact
    return actors
