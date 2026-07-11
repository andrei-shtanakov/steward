"""GitFacts adapters: the determinism key of gate-check (WS-002, DESIGN-204).

Checks depend only on the :class:`GitFacts` protocol. Two implementations:

- :class:`InjectedGitFacts` — facts loaded from a JSON file for ``--no-fs`` CI
  runs (REQ-207): fully deterministic, no git, no network.
- :class:`LiveGitFacts` — local-dev convenience over the ``git`` CLI. It can
  confirm presence on the default branch and blob hashes, but NOT PR approvals
  (that needs forge API access) — ``approvals`` returns an empty tuple, so an
  ``approved`` artifact under a non-solo profile will produce a finding telling
  the operator to supply facts. CI must use ``--no-fs``.

facts.json shape::

    {
      "default_branch_files": ["spec/10-requirements.md", ...],
      "approvals": {"spec/10-requirements.md": [{"handle": "@alice", "role": "@product"}]},
      "blob_hashes": {"spec/10-requirements.md": "abc123..."}
    }

Paths are bundle-relative POSIX strings.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

__all__ = ["Approval", "FactsError", "GitFacts", "InjectedGitFacts", "LiveGitFacts"]


class FactsError(ValueError):
    """Malformed facts file (config-level error, exit 2)."""


@dataclass(frozen=True)
class Approval:
    """One recorded PR approval for an artifact."""

    handle: str
    role: str


class GitFacts(Protocol):
    """The only git surface checks may touch (DESIGN-204)."""

    def on_default_branch(self, path: str) -> bool:
        """True when the artifact exists on the default branch."""
        ...

    def approvals(self, path: str) -> tuple[Approval, ...]:
        """PR approvals recorded for the artifact (may be empty)."""
        ...

    def blob_hash(self, path: str) -> str | None:
        """Current blob hash of the artifact, if known."""
        ...


class InjectedGitFacts:
    """Deterministic facts loaded from JSON for ``--no-fs`` runs (REQ-207)."""

    def __init__(
        self,
        default_branch_files: frozenset[str],
        approvals: dict[str, tuple[Approval, ...]],
        blob_hashes: dict[str, str],
    ) -> None:
        self._files = default_branch_files
        self._approvals = approvals
        self._hashes = blob_hashes

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

        return cls(frozenset(files), approvals, dict(raw_hashes))

    def on_default_branch(self, path: str) -> bool:
        return path in self._files

    def approvals(self, path: str) -> tuple[Approval, ...]:
        return self._approvals.get(path, ())

    def blob_hash(self, path: str) -> str | None:
        return self._hashes.get(path)


class LiveGitFacts:
    """Local-dev facts over the git CLI; approvals are never available."""

    def __init__(self, repo_root: Path, bundle_root: Path) -> None:
        self._root = repo_root
        self._bundle = bundle_root
        self._default_files: frozenset[str] | None = None

    def _rel_to_repo(self, path: str) -> str:
        return (self._bundle / path).resolve().relative_to(self._root.resolve()).as_posix()

    def _default_branch_files(self) -> frozenset[str]:
        if self._default_files is None:
            for rev in ("origin/HEAD", "HEAD"):
                proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
                    ["git", "ls-tree", "-r", "--name-only", rev],
                    cwd=self._root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.returncode == 0:
                    self._default_files = frozenset(proc.stdout.splitlines())
                    break
            else:
                self._default_files = frozenset()
        return self._default_files

    def on_default_branch(self, path: str) -> bool:
        try:
            rel = self._rel_to_repo(path)
        except ValueError:
            return False
        return rel in self._default_branch_files()

    def approvals(self, path: str) -> tuple[Approval, ...]:  # noqa: ARG002
        return ()  # forge approvals need facts injection (CI uses --no-fs)

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
