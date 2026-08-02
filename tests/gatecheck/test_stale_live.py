"""Live stale-cascade test on a real git repo with real blob hashes (FL-10).

The unit tests drive ``check_stale_cascade`` with ``FakeGitFacts`` and invented
hashes; the golden run pinned placeholders. This is the missing end-to-end leg:
a temporary git repository, ``LiveGitFacts``, and pins produced by git itself —
proving that approval-time pinning and the stale error fire on the hashes git
actually computes, not on the shape of our fakes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from steward.gatecheck.checks import collect_bundle, run_checks
from steward.gatecheck.git_facts import LiveGitFacts
from steward.graph import load_profile_data

_PROFILE = {
    "profile": "team-exp-live",
    "solo_auto_approve": True,  # keeps GC-GIT-ROLE out of a forge-less test repo
    "artifacts": [
        {"id": "requirements", "owner_role": "@product", "upstream": []},
        {
            "id": "behaviour-spec",
            "owner_role": "@qa",
            "upstream": ["requirements"],
        },
    ],
}

_REQUIREMENTS = """---
spec_stage: requirements
status: approved
---
#### FR-01: Panel shows bundle state
**Priority**: 🔴 Must
"""

_BEHAVIOUR_TEMPLATE = """---
spec_stage: behaviour-spec
status: approved
upstream_hashes:
  requirements: {pin}
---
#### BEH-01: Clean bundle renders as pass `traces: [FR-01]`
- **checked_by**: `status: planned` `kind: e2e` `owner: @qa` `target: tests/ui.py::t1`
"""


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _init_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    spec = repo / "spec"
    spec.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "steward-test")
    return repo, spec


def _run(repo: Path, spec: Path) -> list:
    graph = load_profile_data(_PROFILE)
    artifacts, findings = collect_bundle(graph, spec)
    findings.extend(run_checks(graph, artifacts, LiveGitFacts(repo, spec)))
    return findings


def test_real_pin_passes_then_upstream_edit_goes_stale(tmp_path: Path) -> None:
    repo, spec = _init_repo(tmp_path)

    # Commit requirements, pin its REAL blob hash into the approved downstream.
    (spec / "10-requirements.md").write_text(_REQUIREMENTS)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "requirements")
    pin = _git(repo, "rev-parse", "HEAD:spec/10-requirements.md")
    (spec / "15-behaviour.md").write_text(_BEHAVIOUR_TEMPLATE.format(pin=pin))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "behaviour approved against pin")

    clean = _run(repo, spec)
    assert [f for f in clean if f.severity == "error"] == []
    assert all(f.rule_id != "GC-STALE" for f in clean)

    # Edit the approved upstream; the committed pin must now mismatch.
    (spec / "10-requirements.md").write_text(
        _REQUIREMENTS + "\n#### FR-02: Late-breaking requirement\n**Priority**: 🟠 Should\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "requirements changed after approval")

    stale = [f for f in _run(repo, spec) if f.rule_id == "GC-STALE"]
    assert len(stale) == 1
    assert stale[0].severity == "error"
    assert stale[0].artifact == "15-behaviour.md"
    assert pin in stale[0].message  # names the real pinned hash…
    current = _git(repo, "rev-parse", "HEAD:spec/10-requirements.md")
    assert current in stale[0].message  # …and the real current one


def test_repin_to_current_hash_clears_stale(tmp_path: Path) -> None:
    repo, spec = _init_repo(tmp_path)
    (spec / "10-requirements.md").write_text(_REQUIREMENTS)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "requirements")
    # A pin git never produced for this blob. Letters matter: an all-digit
    # 40-char string would be YAML-parsed as an int and fail GC-META instead.
    stale_pin = "deadbeef" * 5
    (spec / "15-behaviour.md").write_text(_BEHAVIOUR_TEMPLATE.format(pin=stale_pin))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "behaviour with a wrong pin")

    assert any(f.rule_id == "GC-STALE" for f in _run(repo, spec))

    # Re-approval stamps the real current hash — the cascade must clear.
    real_pin = _git(repo, "rev-parse", "HEAD:spec/10-requirements.md")
    (spec / "15-behaviour.md").write_text(_BEHAVIOUR_TEMPLATE.format(pin=real_pin))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "re-approved with the current pin")

    findings = _run(repo, spec)
    assert all(f.rule_id != "GC-STALE" for f in findings)
    assert [f for f in findings if f.severity == "error"] == []
