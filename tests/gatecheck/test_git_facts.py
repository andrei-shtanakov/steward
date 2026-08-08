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
import tempfile
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


def test_injected_facts_parses_valid_approvals() -> None:
    """Approvals with identity field parse successfully."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "default_branch_files": ["spec/10-requirements.md"],
                "approvals": {
                    "spec/10-requirements.md": [
                        {"identity": "github:alice"},
                        {"identity": "github:bob"},
                    ]
                },
                "blob_hashes": {"spec/10-requirements.md": "abc123"},
            },
            f,
        )
        f.flush()
        try:
            facts = InjectedGitFacts.from_file(f.name)
            approvals = facts.approvals("spec/10-requirements.md")
            assert len(approvals) == 2
            assert approvals[0] == Approval(identity="github:alice")
            assert approvals[1] == Approval(identity="github:bob")
        finally:
            Path(f.name).unlink()


def test_injected_facts_rejects_approvals_without_identity() -> None:
    """Approvals missing 'identity' field fail parsing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "default_branch_files": ["spec/10-requirements.md"],
                "approvals": {"spec/10-requirements.md": [{"other_field": "value"}]},
                "blob_hashes": {},
            },
            f,
        )
        f.flush()
        try:
            with pytest.raises(FactsError, match="identity"):
                InjectedGitFacts.from_file(f.name)
        finally:
            Path(f.name).unlink()


def test_injected_facts_rejects_empty_identity() -> None:
    """Approvals with empty identity string fail parsing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "default_branch_files": ["spec/10-requirements.md"],
                "approvals": {"spec/10-requirements.md": [{"identity": ""}]},
                "blob_hashes": {},
            },
            f,
        )
        f.flush()
        try:
            with pytest.raises(FactsError, match="identity"):
                InjectedGitFacts.from_file(f.name)
        finally:
            Path(f.name).unlink()


def test_injected_facts_rejects_non_string_identity() -> None:
    """Approvals with non-string identity fail parsing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "default_branch_files": ["spec/10-requirements.md"],
                "approvals": {"spec/10-requirements.md": [{"identity": 123}]},
                "blob_hashes": {},
            },
            f,
        )
        f.flush()
        try:
            with pytest.raises(FactsError, match="identity"):
                InjectedGitFacts.from_file(f.name)
        finally:
            Path(f.name).unlink()


def test_injected_facts_rejects_old_approval_shape() -> None:
    """Old-shape approvals with handle/role fields fail parsing with error naming identity."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "default_branch_files": ["spec/10-requirements.md"],
                "approvals": {"spec/10-requirements.md": [{"handle": "@alice", "role": "product"}]},
                "blob_hashes": {},
            },
            f,
        )
        f.flush()
        try:
            with pytest.raises(FactsError, match="identity"):
                InjectedGitFacts.from_file(f.name)
        finally:
            Path(f.name).unlink()


def test_injected_facts_rejects_approvals_with_unknown_keys() -> None:
    """Approvals with unknown keys fail parsing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "default_branch_files": ["spec/10-requirements.md"],
                "approvals": {
                    "spec/10-requirements.md": [
                        {"identity": "github:alice", "extra_field": "value"}
                    ]
                },
                "blob_hashes": {},
            },
            f,
        )
        f.flush()
        try:
            with pytest.raises(FactsError, match="unknown keys"):
                InjectedGitFacts.from_file(f.name)
        finally:
            Path(f.name).unlink()
