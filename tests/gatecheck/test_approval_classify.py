"""Merge-actor classification: closed policy, no default-human (AP-4).

Four required outcomes, per the plan's Global Constraints: exact match on
``human_identities`` -> human; exact match on ``agent_identities`` OR a
``Bot`` hint -> agent; identity ``None`` -> unknown; and — the case that
must never silently regress into a default-human fail-open path — an
*unrecognized* identity with a ``User`` hint -> unknown, not human.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.gatecheck.approval import (
    ApprovalPolicy,
    PolicyError,
    classify_actor,
    load_approval_policy,
)

CANONICAL = Path(__file__).parents[2] / "profiles" / "approval-policy.yaml"


@pytest.fixture
def policy() -> ApprovalPolicy:
    return ApprovalPolicy(
        version=1,
        human_identities=frozenset({"github:andrei-shtanakov"}),
        agent_identities=frozenset({"github:dependabot[bot]"}),
    )


def test_exact_human_match(policy: ApprovalPolicy) -> None:
    assert classify_actor("github:andrei-shtanakov", "User", policy) == "human"


def test_exact_agent_match(policy: ApprovalPolicy) -> None:
    assert classify_actor("github:dependabot[bot]", "Bot", policy) == "agent"


def test_bot_hint_without_allowlist_match_is_agent(policy: ApprovalPolicy) -> None:
    """A hint of "Bot" alone is sufficient — the identity need not also be
    on the closed agent_identities allowlist for the Bot-hint branch."""
    assert classify_actor("github:some-other-bot[bot]", "Bot", policy) == "agent"


def test_identity_none_is_unknown(policy: ApprovalPolicy) -> None:
    assert classify_actor(None, None, policy) == "unknown"


def test_unrecognized_identity_with_user_hint_is_unknown_not_human(
    policy: ApprovalPolicy,
) -> None:
    """The default-human trap: "doesn't look like a bot" must never be
    treated as proof of human. An unlisted identity with a User hint is
    unknown, exactly like one with no hint at all."""
    assert classify_actor("github:some-stranger", "User", policy) == "unknown"


def test_human_allowlist_wins_even_with_bot_hint(policy: ApprovalPolicy) -> None:
    """Precedence: human_identities is checked before the hint, so a listed
    human is never reclassified as an agent by a misleading hint."""
    assert classify_actor("github:andrei-shtanakov", "Bot", policy) == "human"


def test_load_canonical_policy() -> None:
    policy = load_approval_policy(CANONICAL)
    assert policy.version == 1
    assert "github:andrei-shtanakov" in policy.human_identities
    assert "github:merge-broker" in policy.agent_identities


def test_canonical_agent_identities_carry_no_bot_suffix() -> None:
    """Regression: identities in the policy must use the grammar the fact
    provider emits — `github:{login}` from GraphQL, where a Bot's login is the
    bare slug (`merge-broker`), not the REST form (`merge-broker[bot]`).
    A suffixed entry never matches `classify_actor`'s exact comparison, and the
    mismatch is invisible because the "Bot" hint classifies the actor as an agent
    anyway: the declared identity is dead while behaviour looks correct.
    Observed on the live merge of steward#75 (2026-08-20)."""
    policy = load_approval_policy(CANONICAL)
    suffixed = [i for i in policy.agent_identities | policy.human_identities if i.endswith("[bot]")]
    assert suffixed == [], f"identities in REST grammar will never match facts: {suffixed}"


def test_load_policy_empty_lists_are_legitimate(tmp_path: Path) -> None:
    """Empty allowlists mean "we don't know anyone yet" — a legal state,
    not a config error."""
    path = tmp_path / "approval-policy.yaml"
    path.write_text("version: 1\nhuman_identities: []\nagent_identities: []\n")
    policy = load_approval_policy(path)
    assert policy.human_identities == frozenset()
    assert policy.agent_identities == frozenset()


def test_load_policy_non_dict_document_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "approval-policy.yaml"
    path.write_text("- just\n- a\n- list\n")
    with pytest.raises(PolicyError):
        load_approval_policy(path)


def test_load_policy_missing_human_identities_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "approval-policy.yaml"
    path.write_text("version: 1\nagent_identities: []\n")
    with pytest.raises(PolicyError):
        load_approval_policy(path)


def test_load_policy_missing_agent_identities_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "approval-policy.yaml"
    path.write_text("version: 1\nhuman_identities: []\n")
    with pytest.raises(PolicyError):
        load_approval_policy(path)


def test_load_policy_non_string_entry_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "approval-policy.yaml"
    path.write_text("version: 1\nhuman_identities: [123]\nagent_identities: []\n")
    with pytest.raises(PolicyError):
        load_approval_policy(path)


def test_load_policy_missing_version_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "approval-policy.yaml"
    path.write_text("human_identities: []\nagent_identities: []\n")
    with pytest.raises(PolicyError):
        load_approval_policy(path)


def test_load_policy_unknown_key_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "approval-policy.yaml"
    path.write_text("version: 1\nhuman_identities: []\nagent_identities: []\nsurprise: true\n")
    with pytest.raises(PolicyError):
        load_approval_policy(path)


def test_load_policy_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(PolicyError):
        load_approval_policy(tmp_path / "nope.yaml")


def test_canonical_policy_denies_agent_merge_by_default() -> None:
    """The shipped policy must keep agent_merge denied: ADR-ECO-008 is
    `proposed`, and merging that document is not ratifying it."""
    assert load_approval_policy(CANONICAL).agent_merge_allowed is False


def test_absent_agent_merge_allowed_defaults_to_denied(tmp_path: Path) -> None:
    """Fail-closed default: a policy written before the field existed must
    not silently start permitting agent merges."""
    path = tmp_path / "approval-policy.yaml"
    path.write_text("version: 1\nhuman_identities: []\nagent_identities: []\n")
    assert load_approval_policy(path).agent_merge_allowed is False


def test_agent_merge_allowed_true_is_loaded(tmp_path: Path) -> None:
    path = tmp_path / "approval-policy.yaml"
    path.write_text(
        "version: 1\nhuman_identities: []\nagent_identities: []\nagent_merge_allowed: true\n"
    )
    assert load_approval_policy(path).agent_merge_allowed is True


@pytest.mark.parametrize("value", ["'yes'", "1", "[]", "null"])
def test_non_bool_agent_merge_allowed_fails_closed(tmp_path: Path, value: str) -> None:
    """Permission is granted by a real boolean only — a truthy YAML scalar
    must never be coerced into allowing agent merges."""
    path = tmp_path / "approval-policy.yaml"
    path.write_text(
        f"version: 1\nhuman_identities: []\nagent_identities: []\nagent_merge_allowed: {value}\n"
    )
    with pytest.raises(PolicyError):
        load_approval_policy(path)
