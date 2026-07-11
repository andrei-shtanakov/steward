"""Unit tests for the gate-check checks (WS-002 REQ-202..205, 208)."""

from __future__ import annotations

from pathlib import Path

from steward.gatecheck.checks import (
    check_completeness,
    check_status_git,
    check_traceability,
    check_upstream_approved,
    collect_bundle,
)
from steward.gatecheck.git_facts import Approval
from steward.graph import load_profile_data

_PROFILE = {
    "profile": "team-test",
    "solo_auto_approve": False,
    "artifacts": [
        {"id": "requirements", "owner_role": "@product", "upstream": []},
        {"id": "design", "owner_role": "@architects", "upstream": ["requirements"]},
        {
            "id": "task",
            "owner_role": "@stream-owner",
            "upstream": ["design"],
            "delegate": "spec-runner",
        },
    ],
}


class FakeGitFacts:
    def __init__(
        self,
        on_default: set[str] | None = None,
        approvals: dict[str, tuple[Approval, ...]] | None = None,
    ) -> None:
        self._on_default = on_default or set()
        self._approvals = approvals or {}

    def on_default_branch(self, path: str) -> bool:
        return path in self._on_default

    def approvals(self, path: str) -> tuple[Approval, ...]:
        return self._approvals.get(path, ())

    def blob_hash(self, path: str) -> str | None:  # noqa: ARG002
        return None


def _graph():
    return load_profile_data(_PROFILE)


def _write(
    tmp_path: Path, name: str, stage: str, status: str, traces: str = "[]", body: str = ""
) -> None:
    (tmp_path / name).write_text(
        f"---\nspec_stage: {stage}\nstatus: {status}\nversion: 1\ntraces_to: {traces}\n---\n{body}",
        encoding="utf-8",
    )


def test_unmanaged_passthrough_and_unknown_stage(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("just notes, no frontmatter\n")
    _write(tmp_path, "odd.md", "retrospective", "draft")
    artifacts, findings = collect_bundle(_graph(), tmp_path)
    assert [a.path for a in artifacts] == ["odd.md"]  # managed but unknown
    assert [f.rule_id for f in findings] == ["GC-STAGE"]
    assert findings[0].severity == "warn"


def test_duplicate_node_claim_is_error(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "requirements", "draft")
    _write(tmp_path, "b.md", "requirements", "draft")
    _, findings = collect_bundle(_graph(), tmp_path)
    assert any(f.rule_id == "GC-DUP" and f.severity == "error" for f in findings)


def test_completeness_missing_required_skips_delegated(tmp_path: Path) -> None:
    _write(tmp_path, "req.md", "requirements", "draft")
    artifacts, _ = collect_bundle(_graph(), tmp_path)
    findings = check_completeness(_graph(), artifacts)
    # design missing -> error; delegated 'task' missing -> NOT an error
    assert [f.artifact for f in findings if f.severity == "error"] == ["design"]


def test_traceability_resolution_and_dangling(tmp_path: Path) -> None:
    _write(tmp_path, "req.md", "requirements", "draft", body="## REQ-001 do a thing\n")
    _write(
        tmp_path,
        "des.md",
        "design",
        "draft",
        traces="[REQ-001, requirements, REQ-999]",
    )
    artifacts, _ = collect_bundle(_graph(), tmp_path)
    findings = check_traceability(_graph(), artifacts)
    dangling = [f for f in findings if f.rule_id == "GC-TRACE"]
    assert len(dangling) == 1 and "REQ-999" in dangling[0].message


def test_traceability_empty_downstream_warns(tmp_path: Path) -> None:
    _write(tmp_path, "req.md", "requirements", "draft")
    _write(tmp_path, "des.md", "design", "draft")
    artifacts, _ = collect_bundle(_graph(), tmp_path)
    findings = check_traceability(_graph(), artifacts)
    assert [f.rule_id for f in findings] == ["GC-TRACE-EMPTY"]
    assert findings[0].severity == "warn"


def test_token_matching_no_substring_false_positive(tmp_path: Path) -> None:
    # REQ-001 must NOT resolve via the token REQ-0011 in upstream text
    _write(tmp_path, "req.md", "requirements", "draft", body="only REQ-0011 here\n")
    _write(tmp_path, "des.md", "design", "draft", traces="[REQ-001]")
    artifacts, _ = collect_bundle(_graph(), tmp_path)
    findings = check_traceability(_graph(), artifacts)
    assert [f.rule_id for f in findings] == ["GC-TRACE"]


def test_upstream_approved_gate(tmp_path: Path) -> None:
    _write(tmp_path, "req.md", "requirements", "draft")
    _write(tmp_path, "des.md", "design", "approved", traces="[requirements]")
    artifacts, _ = collect_bundle(_graph(), tmp_path)
    findings = check_upstream_approved(_graph(), artifacts)
    assert [f.rule_id for f in findings] == ["GC-UPSTREAM"]
    assert "draft" in findings[0].message


def test_status_git_requires_branch_and_role(tmp_path: Path) -> None:
    _write(tmp_path, "req.md", "requirements", "approved")
    artifacts, _ = collect_bundle(_graph(), tmp_path)

    nothing = check_status_git(_graph(), artifacts, FakeGitFacts())
    assert {f.rule_id for f in nothing} == {"GC-GIT-BRANCH", "GC-GIT-ROLE"}

    wrong_role = check_status_git(
        _graph(),
        artifacts,
        FakeGitFacts(
            on_default={"req.md"},
            approvals={"req.md": (Approval("@bob", "@qa"),)},
        ),
    )
    assert {f.rule_id for f in wrong_role} == {"GC-GIT-ROLE"}

    clean = check_status_git(
        _graph(),
        artifacts,
        FakeGitFacts(
            on_default={"req.md"},
            approvals={"req.md": (Approval("@alice", "@product"),)},
        ),
    )
    assert clean == []


def test_solo_auto_approve_skips_role_check(tmp_path: Path) -> None:
    solo = load_profile_data({**_PROFILE, "solo_auto_approve": True})
    _write(tmp_path, "req.md", "requirements", "approved")
    artifacts, _ = collect_bundle(solo, tmp_path)
    findings = check_status_git(solo, artifacts, FakeGitFacts(on_default={"req.md"}))
    assert findings == []  # branch confirmed; no role approval needed
