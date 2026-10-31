"""The snapshot a node status provider answers for (approval-facts v3 §7.2.3).

``snapshot_digest`` binds a provider's answer to the bytes on disk that
``collect_bundle`` reads: the bundle plus ``<spec_dir>/../approvals/``. The CLI
builds the snapshot only through ``snapshot_for``, from the same inputs as the
run, and ``--print-snapshot-digest`` exposes it without an act mode.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from steward.gatecheck.checks import (
    SnapshotError,
    approvals_dir,
    snapshot_digest,
    snapshot_for,
)
from steward.gatecheck.cli import app

runner = CliRunner()

_PROFILE = """\
profile: test
solo_auto_approve: false
artifacts:
  - {id: requirements, owner_role: product, upstream: []}
"""

_REQ = "---\nspec_stage: requirements\nstatus: draft\nversion: 1\n---\n## REQ-001\n"


def _bundle(root: Path, name: str = "spec") -> Path:
    spec = root / name
    spec.mkdir(parents=True)
    (spec / "req.md").write_text(_REQ)
    (spec / "notes.txt").write_text("free text\n")
    return spec


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def test_digest_covers_bundle_and_approvals(tmp_path: Path) -> None:
    spec = _bundle(tmp_path)
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    (approvals / "requirements.yaml").write_text("act: review/v1\n")
    first = snapshot_digest(spec)

    (approvals / "requirements.yaml").write_text("act: review/v1\nnode: requirements\n")
    second = snapshot_digest(spec)
    assert second != first

    (approvals / "requests").mkdir()
    (approvals / "requests" / "r-1.yaml").write_text("act: review/v1\n")
    third = snapshot_digest(spec)
    assert third != second

    (spec / "notes.txt").write_text("changed\n")
    assert snapshot_digest(spec) != third


def test_digest_order_independent_and_stable(tmp_path: Path) -> None:
    one = tmp_path / "one" / "spec"
    one.mkdir(parents=True)
    (one / "a.md").write_text("A\n")
    (one / "sub").mkdir()
    (one / "sub" / "b.md").write_text("B\n")

    two = tmp_path / "two" / "elsewhere"
    (two / "sub").mkdir(parents=True)
    (two / "sub" / "b.md").write_text("B\n")
    (two / "a.md").write_text("A\n")

    digest = snapshot_digest(one)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
    assert snapshot_digest(one) == digest
    # Content-addressed: the same bytes under the same relative paths give the
    # same digest wherever the bundle lives and whatever its directory is named.
    assert snapshot_digest(two) == digest

    (two / "sub" / "b.md").rename(two / "b.md")
    assert snapshot_digest(two) != digest


def test_digest_unreadable_file_is_error(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    (tmp_path / "test.yaml").write_text(_PROFILE)
    spec = _bundle(tmp_path)
    locked = spec / "notes.txt"
    locked.chmod(0)
    try:
        with pytest.raises(SnapshotError, match="notes.txt"):
            snapshot_digest(spec)
        res = runner.invoke(
            app,
            [str(spec), "--profile", str(tmp_path / "test.yaml"), "--act-mode", "legacy"]
            + ["--candidate"],
        )
        assert res.exit_code == 2, res.output
        assert "config error" in res.output
    finally:
        locked.chmod(0o644)


def test_snapshot_for_each_run_kind(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    spec = _bundle(repo)
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "steward-test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "bundle")

    live = snapshot_for("live", spec, repo)
    assert (live.kind, live.root, live.commit) == (
        "live",
        repo.resolve(),
        _git(repo, "rev-parse", "HEAD"),
    )
    assert live.digest == snapshot_digest(spec)

    for kind in ("candidate", "injected"):
        snap = snapshot_for(kind, spec, None)
        assert (snap.kind, snap.root, snap.commit) == (kind, spec.resolve(), None)
        assert snap.digest == live.digest

    with pytest.raises(ValueError):
        snapshot_for("live", spec, None)


def test_print_snapshot_digest_cli(tmp_path: Path) -> None:
    spec = _bundle(tmp_path)
    res = runner.invoke(app, ["--print-snapshot-digest", "--candidate", str(spec)])
    assert res.exit_code == 0, res.output
    assert res.stdout.strip() == snapshot_for("candidate", spec, None).digest


def test_print_snapshot_digest_live_needs_a_checkout(tmp_path: Path) -> None:
    """Without --candidate the digest is the live run's snapshot, which — like
    the run itself — needs a git checkout."""
    spec = _bundle(tmp_path)
    res = runner.invoke(app, ["--print-snapshot-digest", str(spec)])
    assert res.exit_code == 2
    assert "not inside a git repository" in res.output


def test_print_snapshot_digest_needs_no_act_mode(tmp_path: Path) -> None:
    spec = _bundle(tmp_path)
    res = runner.invoke(app, ["--print-snapshot-digest", "--candidate", str(spec)])
    assert res.exit_code == 0 and res.stdout.strip().startswith("sha256:")


def test_gate_run_without_act_mode_still_exit_2(
    tmp_path: Path, write_roles: Path, write_role_assignments: Path
) -> None:
    (tmp_path / "test.yaml").write_text(_PROFILE)
    spec = _bundle(tmp_path)
    res = runner.invoke(app, [str(spec), "--profile", str(tmp_path / "test.yaml"), "--candidate"])
    assert res.exit_code == 2
    assert "--act-mode is required: pass the admission result (legacy|review)" in res.output


def test_symlinked_bundle_dir_is_refused(tmp_path: Path) -> None:
    """A bundle reached through a link is refused, not located by guess (§7.2.3):
    following it would hash approvals/ beside the target, not beside the link."""
    real = _bundle(tmp_path / "real")
    (tmp_path / "view").mkdir()
    link = tmp_path / "view" / "spec"
    link.symlink_to(real, target_is_directory=True)
    (tmp_path / "view" / "approvals").mkdir()
    (tmp_path / "view" / "approvals" / "requirements.yaml").write_text("act: review/v1\n")
    with pytest.raises(SnapshotError, match="symlink"):
        snapshot_digest(link)
    with pytest.raises(SnapshotError, match="symlink"):
        snapshot_for("candidate", link, None)
    res = runner.invoke(app, ["--print-snapshot-digest", "--candidate", str(link)])
    assert res.exit_code == 2
    assert "symlink" in res.output


def test_symlinked_approvals_dir_is_refused(tmp_path: Path) -> None:
    spec = _bundle(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "approvals").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(SnapshotError, match="symlink"):
        snapshot_digest(spec)


def test_approvals_dir_is_the_lexical_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _bundle(tmp_path / "repo")
    monkeypatch.chdir(spec)
    assert approvals_dir(Path(".")) == tmp_path / "repo" / "approvals"
    assert approvals_dir(Path("../spec/.")) == tmp_path / "repo" / "approvals"


def test_dangling_symlink_in_bundle_is_error(tmp_path: Path) -> None:
    """The non-regular-file guard: a dangling link is neither hashed nor skipped."""
    spec = _bundle(tmp_path)
    (spec / "x").symlink_to(tmp_path / "nonexistent")
    with pytest.raises(SnapshotError, match="not a regular file"):
        snapshot_digest(spec)
    res = runner.invoke(app, ["--print-snapshot-digest", "--candidate", str(spec)])
    assert res.exit_code == 2
