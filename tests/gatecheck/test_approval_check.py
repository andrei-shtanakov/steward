"""GC-APPROVAL-MISSING: release-stage merge-evidence combinator (AP-5).

``check_approval_evidence`` combines four independently-sourced facts —
frontmatter ``status``, default-branch presence, local merge provenance
(:mod:`steward.gatecheck.git_facts`), and materialized merge-actor facts
(:mod:`steward.approvalfacts`) classified against the closed policy
(:mod:`steward.gatecheck.approval`) — into five distinguishable outcomes.
Per the plan's Global Constraints, the combinator takes actor facts as its
own argument and never reads ``MergeProvenance.actor`` (that field is a
test-fixture-only injection path, not the authoritative source).
"""

from __future__ import annotations

from steward.approvalfacts import ApprovalFacts
from steward.gatecheck.approval import ActorFact, ApprovalPolicy, check_approval_evidence
from steward.gatecheck.checks import Artifact
from steward.gatecheck.git_facts import MergeProvenance
from steward.meta import parse_artifact

_POLICY = ApprovalPolicy(
    version=1,
    human_identities=frozenset({"github:andrei-shtanakov"}),
    agent_identities=frozenset({"github:dependabot[bot]"}),
)

_ALLOWING_POLICY = ApprovalPolicy(
    version=1,
    human_identities=frozenset({"github:andrei-shtanakov"}),
    agent_identities=frozenset({"github:dependabot[bot]"}),
    agent_merge_allowed=True,
)

_SHA = "a" * 40


def _artifact(status: str, node_id: str | None = "design") -> Artifact:
    text = f"---\nspec_stage: design\nstatus: {status}\nversion: 1\n---\nbody\n"
    meta = parse_artifact(text)
    assert meta is not None
    return Artifact(path="20-design.md", node_id=node_id, meta=meta, text=text)


def _provenance(sha: str = _SHA) -> MergeProvenance:
    return MergeProvenance(
        sha=sha,
        subject="Merge PR #7",
        current_blob_sha="deadbeef",
        merge_method="merge_commit",
        actor=None,
        actor_source="unavailable",
    )


class FakeGitFacts:
    """Only the two members ``check_approval_evidence`` actually calls."""

    def __init__(
        self,
        on_default: set[str] | None = None,
        provenance: dict[str, MergeProvenance] | None = None,
    ) -> None:
        self._on_default = on_default or set()
        self._provenance = provenance or {}

    def on_default_branch(self, path: str) -> bool:
        return path in self._on_default

    def merge_provenance(self, path: str) -> MergeProvenance | None:
        return self._provenance.get(path)


def _one_finding(findings):
    assert len(findings) == 1
    return findings[0]


def test_stage_authoring_skips_check_entirely() -> None:
    """Not merely "no findings" — the check must not run at all at authoring."""
    artifacts = [_artifact("approved")]
    git = FakeGitFacts(on_default={"20-design.md"}, provenance={})  # provenance absent
    findings = check_approval_evidence(artifacts, git, _POLICY, None, "authoring")
    assert findings == []


def test_artifact_not_on_default_branch_is_out_of_scope() -> None:
    artifacts = [_artifact("approved")]
    git = FakeGitFacts(on_default=set(), provenance={})
    findings = check_approval_evidence(artifacts, git, _POLICY, None, "release")
    assert findings == []


def test_draft_artifact_is_out_of_scope() -> None:
    artifacts = [_artifact("draft")]
    git = FakeGitFacts(on_default={"20-design.md"}, provenance={})
    findings = check_approval_evidence(artifacts, git, _POLICY, None, "release")
    assert findings == []


def test_unmanaged_artifact_is_out_of_scope_even_if_approved() -> None:
    artifacts = [_artifact("approved", node_id=None)]
    git = FakeGitFacts(on_default={"20-design.md"}, provenance={"20-design.md": _provenance()})
    findings = check_approval_evidence(artifacts, git, _POLICY, None, "release")
    assert findings == []


def test_provenance_absent_gives_absent_finding() -> None:
    artifacts = [_artifact("approved")]
    git = FakeGitFacts(on_default={"20-design.md"}, provenance={})
    findings = check_approval_evidence(artifacts, git, _POLICY, None, "release")
    finding = _one_finding(findings)
    assert finding.severity == "error"
    assert finding.rule_id == "GC-APPROVAL-MISSING"
    assert finding.artifact == "20-design.md"
    assert "no first-parent merge provenance for the current blob" in finding.message


def test_actor_facts_argument_none_gives_unavailable_finding() -> None:
    """No ``--approval-facts`` file at all: unavailable for every sha."""
    artifacts = [_artifact("approved")]
    git = FakeGitFacts(on_default={"20-design.md"}, provenance={"20-design.md": _provenance()})
    findings = check_approval_evidence(artifacts, git, _POLICY, None, "release")
    finding = _one_finding(findings)
    assert finding.rule_id == "GC-APPROVAL-MISSING"
    assert f"sha {_SHA}" in finding.message
    assert "merge actor facts are unavailable" in finding.message
    assert "steward approval-facts" in finding.message


def test_actor_facts_present_but_sha_missing_gives_unavailable_finding() -> None:
    """``--approval-facts`` was materialized, but not for THIS merge sha."""
    artifacts = [_artifact("approved")]
    git = FakeGitFacts(on_default={"20-design.md"}, provenance={"20-design.md": _provenance()})
    actor_facts = ApprovalFacts(actors={})
    findings = check_approval_evidence(artifacts, git, _POLICY, actor_facts, "release")
    finding = _one_finding(findings)
    assert "merge actor facts are unavailable" in finding.message


def test_unknown_actor_gives_unknown_finding() -> None:
    artifacts = [_artifact("approved")]
    git = FakeGitFacts(on_default={"20-design.md"}, provenance={"20-design.md": _provenance()})
    actor_facts = ApprovalFacts(
        actors={_SHA: ActorFact(identity="github:some-stranger", actor_type_hint="User")}
    )
    findings = check_approval_evidence(artifacts, git, _POLICY, actor_facts, "release")
    finding = _one_finding(findings)
    assert finding.rule_id == "GC-APPROVAL-MISSING"
    assert "github:some-stranger" in finding.message
    assert "unknown" in finding.message


def _agent_case(policy: ApprovalPolicy) -> list:
    """One approved artifact merged by a correctly classified agent actor."""
    return check_approval_evidence(
        [_artifact("approved")],
        FakeGitFacts(on_default={"20-design.md"}, provenance={"20-design.md": _provenance()}),
        policy,
        ApprovalFacts(
            actors={_SHA: ActorFact(identity="github:dependabot[bot]", actor_type_hint="Bot")}
        ),
        "release",
    )


def test_agent_actor_gives_agent_finding_under_denying_policy() -> None:
    """The denial is a *policy value*, and the message must name the field
    an operator would change — not a decision baked into the code."""
    finding = _one_finding(_agent_case(_POLICY))
    assert finding.rule_id == "GC-APPROVAL-MISSING"
    assert "agent_merge" in finding.message
    assert "agent_merge_allowed" in finding.message


def test_agent_actor_satisfies_policy_when_explicitly_allowed() -> None:
    """ADR-ECO-008 D1: in automatic runs the agent merges. Once the policy
    says so, an agent merge satisfies the release policy exactly as a human
    one does — the gate must not keep blocking what the policy permits."""
    assert _agent_case(_ALLOWING_POLICY) == []


def test_unknown_actor_stays_fail_closed_under_allowing_policy() -> None:
    """Permitting agent_merge widens exactly one classification. Absence of
    evidence must never become "agent by default" and slip through."""
    findings = check_approval_evidence(
        [_artifact("approved")],
        FakeGitFacts(on_default={"20-design.md"}, provenance={"20-design.md": _provenance()}),
        _ALLOWING_POLICY,
        ApprovalFacts(actors={_SHA: ActorFact("github:stranger", "User")}),
        "release",
    )
    finding = _one_finding(findings)
    assert finding.rule_id == "GC-APPROVAL-MISSING"
    assert "unknown" in finding.message


def test_human_actor_gives_no_finding() -> None:
    artifacts = [_artifact("approved")]
    git = FakeGitFacts(on_default={"20-design.md"}, provenance={"20-design.md": _provenance()})
    actor_facts = ApprovalFacts(
        actors={_SHA: ActorFact(identity="github:andrei-shtanakov", actor_type_hint="User")}
    )
    findings = check_approval_evidence(artifacts, git, _POLICY, actor_facts, "release")
    assert findings == []


def test_four_distinct_outcomes_have_four_distinct_messages() -> None:
    """D5's operational point: absent / unavailable / unknown / agent must never
    collapse into the same string — each is independently diagnosable."""
    on_default = {"20-design.md"}

    absent = check_approval_evidence(
        [_artifact("approved")], FakeGitFacts(on_default, {}), _POLICY, None, "release"
    )
    unavailable = check_approval_evidence(
        [_artifact("approved")],
        FakeGitFacts(on_default, {"20-design.md": _provenance()}),
        _POLICY,
        None,
        "release",
    )
    unknown = check_approval_evidence(
        [_artifact("approved")],
        FakeGitFacts(on_default, {"20-design.md": _provenance()}),
        _POLICY,
        ApprovalFacts(actors={_SHA: ActorFact("github:stranger", "User")}),
        "release",
    )
    agent = check_approval_evidence(
        [_artifact("approved")],
        FakeGitFacts(on_default, {"20-design.md": _provenance()}),
        _POLICY,
        ApprovalFacts(actors={_SHA: ActorFact("github:dependabot[bot]", "Bot")}),
        "release",
    )

    messages = {
        _one_finding(absent).message,
        _one_finding(unavailable).message,
        _one_finding(unknown).message,
        _one_finding(agent).message,
    }
    assert len(messages) == 4
