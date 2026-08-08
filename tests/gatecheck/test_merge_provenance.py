"""LiveGitFacts.merge_provenance: first-parent-introduction search (AP-3).

Local git can prove *provenance* (which merge commit on the default
branch's first-parent chain introduced the artifact's current blob) but
never the *actor* who performed or approved that merge — that needs forge
API access, which the local provider does not have. These tests pin the
four required cases from the task brief: a real merge introduction, a
squash-like direct commit (no merge commit at all), a merge later
overwritten by a direct commit, and a merge that did not actually change
the blob relative to its own first parent (so it is not the introduction
and must be skipped).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from steward.gatecheck.git_facts import LiveGitFacts, MergeProvenance


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "steward-test")
    return repo


def _write_and_commit(repo: Path, name: str, content: str, message: str) -> None:
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)


def test_merge_introduction_found(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_and_commit(repo, "base.txt", "base\n", "init")

    _git(repo, "switch", "-c", "feature")
    _write_and_commit(repo, "artifact.md", "feature content\n", "add artifact on feature")
    _git(repo, "switch", "master")
    _git(repo, "merge", "--no-ff", "-m", "Merge PR #7: add artifact", "feature")

    facts = LiveGitFacts(repo, repo)
    prov = facts.merge_provenance("artifact.md")

    assert prov is not None
    assert isinstance(prov, MergeProvenance)
    expected_sha = _git(repo, "rev-parse", "HEAD")
    expected_blob = _git(repo, "rev-parse", "HEAD:artifact.md")
    assert prov.sha == expected_sha
    assert prov.subject == "Merge PR #7: add artifact"
    assert prov.current_blob_sha == expected_blob
    assert prov.merge_method == "merge_commit"
    assert prov.actor is None
    assert prov.actor_source == "unavailable"


def test_direct_commit_no_merge_gives_none(tmp_path: Path) -> None:
    """Squash-like history: the artifact lands via a plain commit on the
    default branch, with no merge commit anywhere in its introduction."""
    repo = _init_repo(tmp_path)
    _write_and_commit(repo, "base.txt", "base\n", "init")
    _write_and_commit(repo, "artifact.md", "squashed content\n", "add artifact directly")

    facts = LiveGitFacts(repo, repo)
    assert facts.merge_provenance("artifact.md") is None


def test_merge_then_direct_edit_gives_none(tmp_path: Path) -> None:
    """A real merge introduced the path, but a later direct commit changed
    it — the current blob's provenance is that direct commit, not the
    merge, so overall provenance must be absent."""
    repo = _init_repo(tmp_path)
    _write_and_commit(repo, "base.txt", "base\n", "init")

    _git(repo, "switch", "-c", "feature")
    _write_and_commit(repo, "artifact.md", "feature content\n", "add artifact on feature")
    _git(repo, "switch", "master")
    _git(repo, "merge", "--no-ff", "-m", "Merge PR #8: add artifact", "feature")

    _write_and_commit(repo, "artifact.md", "direct edit after merge\n", "direct edit")

    facts = LiveGitFacts(repo, repo)
    assert facts.merge_provenance("artifact.md") is None


def test_merge_not_changing_blob_is_skipped(tmp_path: Path) -> None:
    """The artifact was introduced by a direct commit; a later merge
    happens but does not touch the path at all (its blob in the merge
    commit equals the blob in its own first parent). That merge is not
    the introduction and must not be reported as provenance."""
    repo = _init_repo(tmp_path)
    _write_and_commit(repo, "base.txt", "base\n", "init")
    _write_and_commit(repo, "artifact.md", "direct content\n", "add artifact directly")

    _git(repo, "switch", "-c", "unrelated")
    _write_and_commit(repo, "other.txt", "unrelated change\n", "unrelated change on branch")
    _git(repo, "switch", "master")
    _git(repo, "merge", "--no-ff", "-m", "Merge PR #9: unrelated change", "unrelated")

    facts = LiveGitFacts(repo, repo)
    assert facts.merge_provenance("artifact.md") is None


def test_diverged_local_head_gives_none_not_origin_content(tmp_path: Path) -> None:
    """Fix-round-1, Important 1: ``current_blob`` must come from local
    ``HEAD`` (same source as ``blob_hash``), never from the default-branch
    ref. Set up a real ``origin/HEAD`` (bare remote + fetch + set-head), get
    a merge introduced and pushed, confirm provenance is found against it —
    then diverge the local checkout with an unpushed direct edit of the
    same path. The default branch's first-parent history (origin/HEAD)
    still only knows the merged content; the artifact currently on disk is
    the unpushed edit. Reporting the merge as its provenance would be
    describing content that isn't there anymore — must fail closed to
    ``None`` instead."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", "master", str(origin)],
        check=True,
        capture_output=True,
        text=True,
    )
    repo = _init_repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(origin))
    _write_and_commit(repo, "base.txt", "base\n", "init")

    _git(repo, "switch", "-c", "feature")
    _write_and_commit(repo, "artifact.md", "feature content\n", "add artifact on feature")
    _git(repo, "switch", "master")
    _git(repo, "merge", "--no-ff", "-m", "Merge PR #10: add artifact", "feature")
    _git(repo, "push", "-q", "origin", "master")
    _git(repo, "fetch", "-q", "origin")
    _git(repo, "remote", "set-head", "origin", "master")

    # Sanity: with no divergence, provenance is found against origin/HEAD
    # (proves the default-branch-ref resolution actually exercises
    # `origin/HEAD`, not just the no-remote `HEAD` fallback).
    assert LiveGitFacts(repo, repo).merge_provenance("artifact.md") is not None

    # Diverge: a local, unpushed direct edit of the same path.
    _write_and_commit(repo, "artifact.md", "unpushed local edit\n", "unpushed divergent edit")

    assert LiveGitFacts(repo, repo).merge_provenance("artifact.md") is None


def test_bounded_git_calls_regardless_of_history_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix-round-1, Important 2: the search must not spawn a git process
    per commit. 50 unrelated commits after the introducing merge must not
    move the call count — it should stay a small, fixed budget."""
    repo = _init_repo(tmp_path)
    _write_and_commit(repo, "base.txt", "base\n", "init")

    _git(repo, "switch", "-c", "feature")
    _write_and_commit(repo, "artifact.md", "feature content\n", "add artifact on feature")
    _git(repo, "switch", "master")
    _git(repo, "merge", "--no-ff", "-m", "Merge PR #11: add artifact", "feature")

    for i in range(50):
        _write_and_commit(repo, "noise.txt", f"noise {i}\n", f"unrelated noise commit {i}")

    real_run = subprocess.run
    call_count = 0

    def counting_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal call_count
        call_count += 1
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", counting_run)

    facts = LiveGitFacts(repo, repo)
    prov = facts.merge_provenance("artifact.md")

    assert prov is not None
    assert call_count <= 10, (
        f"expected a fixed small budget of git calls regardless of history depth, got {call_count}"
    )
