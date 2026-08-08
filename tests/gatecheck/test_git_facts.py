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

import json
import subprocess
from pathlib import Path

import pytest

from steward.gatecheck.git_facts import Approval, FactsError, InjectedGitFacts, LiveGitFacts


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


def _facts_file(tmp_path: Path, approvals: list[dict]) -> Path:
    """Write a facts.json with the given approvals and return its (closed) path.

    The file is fully written and closed before the caller reads it —
    keeping a NamedTemporaryFile open across ``from_file`` works on POSIX
    but trips over file locking on Windows.
    """
    path = tmp_path / "facts.json"
    path.write_text(
        json.dumps(
            {
                "default_branch_files": ["spec/10-requirements.md"],
                "approvals": {"spec/10-requirements.md": approvals},
                "blob_hashes": {"spec/10-requirements.md": "abc123"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_injected_facts_parses_valid_approvals(tmp_path: Path) -> None:
    """Approvals with identity field parse successfully."""
    path = _facts_file(tmp_path, [{"identity": "github:alice"}, {"identity": "github:bob"}])
    facts = InjectedGitFacts.from_file(path)
    approvals = facts.approvals("spec/10-requirements.md")
    assert len(approvals) == 2
    assert approvals[0] == Approval(identity="github:alice")
    assert approvals[1] == Approval(identity="github:bob")


def test_injected_facts_rejects_approvals_without_identity(tmp_path: Path) -> None:
    """Approvals missing 'identity' field fail parsing."""
    path = _facts_file(tmp_path, [{"other_field": "value"}])
    with pytest.raises(FactsError, match="identity"):
        InjectedGitFacts.from_file(path)


def test_injected_facts_rejects_empty_identity(tmp_path: Path) -> None:
    """Approvals with empty identity string fail parsing."""
    path = _facts_file(tmp_path, [{"identity": ""}])
    with pytest.raises(FactsError, match="identity"):
        InjectedGitFacts.from_file(path)


def test_injected_facts_rejects_non_string_identity(tmp_path: Path) -> None:
    """Approvals with non-string identity fail parsing."""
    path = _facts_file(tmp_path, [{"identity": 123}])
    with pytest.raises(FactsError, match="identity"):
        InjectedGitFacts.from_file(path)


def test_injected_facts_rejects_old_approval_shape(tmp_path: Path) -> None:
    """Old-shape approvals with handle/role fields fail parsing with error naming identity."""
    path = _facts_file(tmp_path, [{"handle": "@alice", "role": "product"}])
    with pytest.raises(FactsError, match="identity"):
        InjectedGitFacts.from_file(path)


def test_injected_facts_rejects_approvals_with_unknown_keys(tmp_path: Path) -> None:
    """Approvals with unknown keys fail parsing."""
    path = _facts_file(tmp_path, [{"identity": "github:alice", "extra_field": "value"}])
    with pytest.raises(FactsError, match="unknown keys"):
        InjectedGitFacts.from_file(path)
