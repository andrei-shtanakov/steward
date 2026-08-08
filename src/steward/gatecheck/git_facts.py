"""GitFacts adapters: the determinism key of gate-check (WS-002, DESIGN-204).

Checks depend only on the :class:`GitFacts` protocol. Two implementations:

- :class:`InjectedGitFacts` — facts loaded from a JSON file for ``--no-fs`` CI
  runs (REQ-207): fully deterministic, no git, no network.
- :class:`LiveGitFacts` — local-dev convenience over the ``git`` CLI. It can
  confirm presence on the default branch and blob hashes, but NOT PR approvals
  (that needs forge API access) — ``approvals`` returns ``None``: the facts
  are unavailable, not authoritatively empty. Absence of a role-mapping is
  therefore not treated as a proven role violation — ``GC-GIT-ROLE`` skips the
  artifact rather than emitting a finding (owner ruling). CI must use
  ``--no-fs`` for an authoritative check.

facts.json shape::

    {
      "default_branch_files": ["spec/10-requirements.md", ...],
      "approvals": {"spec/10-requirements.md": [{"handle": "@alice", "role": "@product"}]},
      "blob_hashes": {"spec/10-requirements.md": "abc123..."},
      "ancestors": ["<sha>", ...],
      "changed_paths_since": {"<sha>": ["path", ...]}
    }

Paths are bundle-relative POSIX strings, except ``changed_paths_since`` values
which are repo-relative POSIX (D9: self-freshness compares against the
manifest's own scopes, defined repo-relative).

``ancestors`` / ``changed_paths_since`` are optional: only ``GC-ARCH-CONFORMANCE``
under ``require_self_fresh`` needs them (D9). When required and absent, callers
must treat this as an unprovable-freshness condition, not a crash — see
:func:`steward.gatecheck.architecture.check_arch_conformance`, which converts
the resulting :class:`FactsError` from :meth:`InjectedGitFacts.is_ancestor` /
:meth:`InjectedGitFacts.changed_paths_since` into a blocking finding rather
than propagating it.

``merge_provenance`` is likewise optional, same absent-means-unprovable shape
as ``ancestors``::

    "merge_provenance": {
      "spec/10-requirements.md": {
        "sha": "<merge commit sha>",
        "subject": "<merge commit subject>",
        "current_blob_sha": "<blob sha>",
        "merge_method": "merge_commit"
      }
    }

A path absent from the mapping means provenance was checked and found
absent (mirrors ``LiveGitFacts.merge_provenance`` returning ``None``); the
whole key absent from the facts file means it was never computed — see
:meth:`InjectedGitFacts.merge_provenance`. Task 4 owns the full v2 schema;
this is the minimal shape Task 3 needs. Local git can prove provenance but
never an actor (WS-003 was invalidated for that reason — no forge API
locally), so ``actor``/``actor_source`` are not part of the minimal local
key: readers get ``actor=None``, ``actor_source="unavailable"`` unless the
facts file explicitly overrides them.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

__all__ = [
    "Approval",
    "FactsError",
    "GitFacts",
    "InjectedGitFacts",
    "LiveGitFacts",
    "MergeProvenance",
]


class FactsError(ValueError):
    """Malformed facts file (config-level error, exit 2)."""


@dataclass(frozen=True)
class Approval:
    """One recorded PR approval for an artifact."""

    handle: str
    role: str


@dataclass(frozen=True)
class MergeProvenance:
    """Evidence that an artifact's current blob was introduced by a merge.

    Local git can prove *provenance* — which merge commit on the default
    branch's first-parent chain introduced the blob currently on disk — but
    never the *actor* who approved or performed that merge (that needs forge
    API access). ``actor``/``actor_source`` exist on this dataclass because a
    future, forge-backed provider will populate them; the local provider
    always reports ``actor=None``, ``actor_source="unavailable"``. ``subject``
    is a hint (e.g. it may embed a PR number) — never treat it as authority.
    """

    sha: str
    subject: str
    current_blob_sha: str
    merge_method: str  # v1: "merge_commit"
    actor: str | None
    actor_source: str


class GitFacts(Protocol):
    """The only git surface checks may touch (DESIGN-204)."""

    def on_default_branch(self, path: str) -> bool:
        """True when the artifact exists on the default branch."""
        ...

    def approvals(self, path: str) -> tuple[Approval, ...] | None:
        """PR approvals recorded for the artifact.

        ``None`` means the facts are unavailable (no authoritative source —
        e.g. :class:`LiveGitFacts`, which cannot reach forge APIs). An empty
        tuple means an authoritative source confirmed there are no approvals.
        Callers must not conflate the two: only the latter proves a role
        violation.
        """
        ...

    def blob_hash(self, path: str) -> str | None:
        """Current blob hash of the artifact, if known."""
        ...

    def is_ancestor(self, commit: str) -> bool:
        """True when ``commit`` is an ancestor of (or equal to) HEAD (D9)."""
        ...

    def changed_paths_since(self, commit: str) -> list[str]:
        """Repo-relative POSIX paths changed in ``commit..HEAD`` (D9)."""
        ...

    def merge_provenance(self, path: str) -> MergeProvenance | None:
        """Merge that introduced the artifact's current blob, if any.

        ``None`` means provenance is **absent**: the current blob was not
        introduced by a merge commit on the default branch's first-parent
        chain — this covers squash/rebase merges (no merge commit at all)
        and plain direct commits alike. It is a fact, not an unavailability
        marker (contrast with :meth:`approvals`): a caller asking "was this
        introduced by a merge?" gets a real yes/no from local git alone.
        What local git can never answer is *who* merged it — see
        :class:`MergeProvenance`'s ``actor``/``actor_source``.
        """
        ...


class InjectedGitFacts:
    """Deterministic facts loaded from JSON for ``--no-fs`` runs (REQ-207)."""

    def __init__(
        self,
        default_branch_files: frozenset[str],
        approvals: dict[str, tuple[Approval, ...]],
        blob_hashes: dict[str, str],
        ancestors: frozenset[str] | None = None,
        changed_paths_since_map: dict[str, tuple[str, ...]] | None = None,
        merge_provenance_map: dict[str, MergeProvenance] | None = None,
    ) -> None:
        self._files = default_branch_files
        self._approvals = approvals
        self._hashes = blob_hashes
        # None (not merely absent-for-this-commit) means the facts file never
        # declared the key at all — self-freshness cannot be proven (D9).
        self._ancestors = ancestors
        self._changed_paths_since = changed_paths_since_map
        # Same shape as `_ancestors`: None means the facts file never
        # declared the section at all (unprovable); a path missing from a
        # present mapping means provenance was checked and is absent.
        self._merge_provenance = merge_provenance_map

    @classmethod
    def from_file(cls, path: str | Path) -> InjectedGitFacts:
        """Load and validate a facts.json file."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise FactsError(f"cannot read facts file {path}: {err}") from err
        if not isinstance(data, dict):
            raise FactsError("facts file: top level must be a mapping")

        files = data.get("default_branch_files", [])
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            raise FactsError("facts file: 'default_branch_files' must be a list of strings")

        raw_approvals = data.get("approvals", {})
        if not isinstance(raw_approvals, dict):
            raise FactsError("facts file: 'approvals' must be a mapping")
        approvals: dict[str, tuple[Approval, ...]] = {}
        for artifact_path, entries in raw_approvals.items():
            if not isinstance(entries, list):
                raise FactsError(f"facts file: approvals[{artifact_path!r}] must be a list")
            parsed = []
            for entry in entries:
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("handle"), str)
                    or not isinstance(entry.get("role"), str)
                ):
                    raise FactsError(
                        f"facts file: approvals[{artifact_path!r}] entries need "
                        "string 'handle' and 'role'"
                    )
                parsed.append(Approval(handle=entry["handle"], role=entry["role"]))
            approvals[artifact_path] = tuple(parsed)

        raw_hashes = data.get("blob_hashes", {})
        if not isinstance(raw_hashes, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw_hashes.items()
        ):
            raise FactsError("facts file: 'blob_hashes' must map path -> hash string")

        ancestors: frozenset[str] | None = None
        raw_ancestors = data.get("ancestors")
        if raw_ancestors is not None:
            if not isinstance(raw_ancestors, list) or not all(
                isinstance(a, str) for a in raw_ancestors
            ):
                raise FactsError("facts file: 'ancestors' must be a list of strings")
            ancestors = frozenset(raw_ancestors)

        changed_paths_since_map: dict[str, tuple[str, ...]] | None = None
        raw_changed = data.get("changed_paths_since")
        if raw_changed is not None:
            if not isinstance(raw_changed, dict):
                raise FactsError("facts file: 'changed_paths_since' must be a mapping")
            changed_paths_since_map = {}
            for commit, paths in raw_changed.items():
                if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                    raise FactsError(
                        f"facts file: changed_paths_since[{commit!r}] must be a list of strings"
                    )
                changed_paths_since_map[commit] = tuple(paths)

        merge_provenance_map: dict[str, MergeProvenance] | None = None
        raw_merge_provenance = data.get("merge_provenance")
        if raw_merge_provenance is not None:
            if not isinstance(raw_merge_provenance, dict):
                raise FactsError("facts file: 'merge_provenance' must be a mapping")
            merge_provenance_map = {}
            for artifact_path, entry in raw_merge_provenance.items():
                if not isinstance(entry, dict):
                    raise FactsError(
                        f"facts file: merge_provenance[{artifact_path!r}] must be a mapping"
                    )
                required = ("sha", "subject", "current_blob_sha", "merge_method")
                if not all(isinstance(entry.get(k), str) for k in required):
                    raise FactsError(
                        f"facts file: merge_provenance[{artifact_path!r}] needs string {required}"
                    )
                actor = entry.get("actor")
                if actor is not None and not isinstance(actor, str):
                    raise FactsError(
                        f"facts file: merge_provenance[{artifact_path!r}]['actor'] "
                        "must be a string or null"
                    )
                actor_source = entry.get("actor_source", "unavailable")
                if not isinstance(actor_source, str):
                    raise FactsError(
                        f"facts file: merge_provenance[{artifact_path!r}]['actor_source'] "
                        "must be a string"
                    )
                merge_provenance_map[artifact_path] = MergeProvenance(
                    sha=entry["sha"],
                    subject=entry["subject"],
                    current_blob_sha=entry["current_blob_sha"],
                    merge_method=entry["merge_method"],
                    actor=actor,
                    actor_source=actor_source,
                )

        return cls(
            frozenset(files),
            approvals,
            dict(raw_hashes),
            ancestors=ancestors,
            changed_paths_since_map=changed_paths_since_map,
            merge_provenance_map=merge_provenance_map,
        )

    def on_default_branch(self, path: str) -> bool:
        return path in self._files

    def approvals(self, path: str) -> tuple[Approval, ...] | None:
        return self._approvals.get(path, ())

    def blob_hash(self, path: str) -> str | None:
        return self._hashes.get(path)

    def is_ancestor(self, commit: str) -> bool:
        if self._ancestors is None:
            raise FactsError(
                "facts file: 'ancestors' required for self-freshness checks but absent"
            )
        return commit in self._ancestors

    def changed_paths_since(self, commit: str) -> list[str]:
        if self._changed_paths_since is None:
            raise FactsError(
                "facts file: 'changed_paths_since' required for self-freshness checks but absent"
            )
        return list(self._changed_paths_since.get(commit, ()))

    def merge_provenance(self, path: str) -> MergeProvenance | None:
        if self._merge_provenance is None:
            raise FactsError(
                "facts file: 'merge_provenance' required for approval-policy checks but absent"
            )
        return self._merge_provenance.get(path)


class LiveGitFacts:
    """Local-dev facts over the git CLI; approvals are never available.

    That was always true; it is now expressed through the type — ``approvals``
    returns ``None`` (facts unavailable) rather than an empty tuple (facts
    confirm no approvals), so callers cannot mistake the two.
    """

    def __init__(self, repo_root: Path, bundle_root: Path) -> None:
        self._root = repo_root
        self._bundle = bundle_root
        self._default_files: frozenset[str] | None = None
        self._default_ref: str | None = None

    def _rel_to_repo(self, path: str) -> str:
        return (self._bundle / path).resolve().relative_to(self._root.resolve()).as_posix()

    def _default_branch_ref(self) -> str:
        """Best local guess at the default branch: ``origin/HEAD`` if a
        remote-tracking ref exists (mirrors real forge default-branch
        resolution), else local ``HEAD`` (dev/test repos with no remote)."""
        if self._default_ref is None:
            for rev in ("origin/HEAD", "HEAD"):
                proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
                    ["git", "rev-parse", "--verify", "-q", rev],
                    cwd=self._root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.returncode == 0:
                    self._default_ref = rev
                    break
            else:
                self._default_ref = "HEAD"
        return self._default_ref

    def _default_branch_files(self) -> frozenset[str]:
        if self._default_files is None:
            proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
                ["git", "ls-tree", "-r", "--name-only", self._default_branch_ref()],
                cwd=self._root,
                capture_output=True,
                text=True,
                check=False,
            )
            self._default_files = (
                frozenset(proc.stdout.splitlines()) if proc.returncode == 0 else frozenset()
            )
        return self._default_files

    def on_default_branch(self, path: str) -> bool:
        try:
            rel = self._rel_to_repo(path)
        except ValueError:
            return False
        return rel in self._default_branch_files()

    def approvals(self, path: str) -> tuple[Approval, ...] | None:  # noqa: ARG002
        return None  # forge approvals need facts injection (CI uses --no-fs)

    def blob_hash(self, path: str) -> str | None:
        try:
            rel = self._rel_to_repo(path)
        except ValueError:
            return None
        proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
            ["git", "rev-parse", f"HEAD:{rel}"],
            cwd=self._root,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None

    def is_ancestor(self, commit: str) -> bool:
        proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
            ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
            cwd=self._root,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0

    def changed_paths_since(self, commit: str) -> list[str]:
        proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
            ["git", "diff", "--name-only", f"{commit}..HEAD"],
            cwd=self._root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise FactsError(
                f"git diff --name-only {commit}..HEAD failed (exit {proc.returncode}): "
                f"{proc.stderr.strip()}"
            )
        return proc.stdout.splitlines()

    def _blob_at(self, rev: str, rel: str) -> str | None:
        proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
            ["git", "rev-parse", "-q", "--verify", f"{rev}:{rel}"],
            cwd=self._root,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None

    def _first_parent_chain(self, ref: str) -> list[str]:
        proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
            ["git", "rev-list", "--first-parent", ref],
            cwd=self._root,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout.splitlines() if proc.returncode == 0 else []

    def _parent_count(self, sha: str) -> int:
        proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
            ["git", "rev-list", "--parents", "-n", "1", sha],
            cwd=self._root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return 0
        return len(proc.stdout.split()) - 1  # first token is `sha` itself

    def _subject(self, sha: str) -> str:
        proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
            ["git", "log", "-1", "--format=%s", sha],
            cwd=self._root,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def merge_provenance(self, path: str) -> MergeProvenance | None:
        """First-parent-introduction search (owner ruling, AP-3).

        Walks the default branch's first-parent chain from its tip backward,
        tracking the run of commits whose blob at ``path`` equals the
        *current* blob. The oldest commit in that unbroken run is exactly
        the commit that made the path's content become what it is today —
        earlier commits in the chain are, by the first-parent-chain
        invariant (``chain[i+1]`` is ``chain[i]``'s first parent), reached
        only once the blob has changed, so the run boundary alone already
        proves the file's parent-side content differed right before that
        commit (v1's check (c) — folds into the boundary, not a separate
        pass). If that boundary commit has two parents, it is the merge
        that introduced the blob. Otherwise (a direct/squash-like commit,
        or a merge that happened to leave the blob untouched relative to
        its own first parent and so isn't the boundary) provenance is
        absent — ``None``.
        """
        try:
            rel = self._rel_to_repo(path)
        except ValueError:
            return None
        default_ref = self._default_branch_ref()
        current_blob = self._blob_at(default_ref, rel)
        if current_blob is None:
            return None

        boundary: str | None = None
        for sha in self._first_parent_chain(default_ref):
            if self._blob_at(sha, rel) != current_blob:
                break
            boundary = sha
        if boundary is None:
            return None

        if self._parent_count(boundary) != 2:
            return None  # boundary commit is a direct commit, not a merge

        return MergeProvenance(
            sha=boundary,
            subject=self._subject(boundary),
            current_blob_sha=current_blob,
            merge_method="merge_commit",
            actor=None,
            actor_source="unavailable",
        )
