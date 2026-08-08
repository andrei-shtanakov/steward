"""LiveGitFacts unit tests: fail-closed behaviour on git-command failure.

``changed_paths_since`` used to return ``[]`` when the underlying ``git diff``
invocation failed — fail-open in isolation, shielded only by the caller's
``is_ancestor`` gate (see :mod:`steward.gatecheck.architecture`). It must
instead raise :class:`FactsError`, mirroring
:meth:`InjectedGitFacts.changed_paths_since`'s missing-key behaviour, so every
caller — present and future — inherits the fail-closed wrapper rather than a
silent "no changes".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from steward.gatecheck.git_facts import FactsError, LiveGitFacts


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "steward-test")
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_changed_paths_since_raises_facts_error_on_git_failure(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    facts = LiveGitFacts(repo, repo)
    with pytest.raises(FactsError):
        facts.changed_paths_since("not-a-real-commit-ish")


def test_changed_paths_since_returns_paths_on_success(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text("world\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "second")
    facts = LiveGitFacts(repo, repo)
    assert facts.changed_paths_since(base) == ["b.txt"]


def test_approvals_are_unavailable_not_empty(tmp_path: Path) -> None:
    """approvals() -> None (facts unavailable), not () (authoritative empty) —
    only an authoritative source may prove a role violation downstream."""
    repo = _init_repo(tmp_path)
    facts = LiveGitFacts(repo, repo)
    assert facts.approvals("a.txt") is None
