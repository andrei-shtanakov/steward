"""Merge-actor classification: closed policy, no default-human (AP-4).

Local git proves merge *provenance* only (``MergeProvenance`` in
:mod:`steward.gatecheck.git_facts`) — never the *actor* who performed or
approved a merge. Actor identity is a materialized fact from GitHub's
``mergedBy`` (see :mod:`steward.approvalfacts`), classified here against a
closed allowlist policy (``profiles/approval-policy.yaml``).

There is no default-human path: an identity absent from both
``human_identities`` and ``agent_identities`` — and not hinted ``Bot`` — is
``"unknown"``, never assumed human because it merely fails to look like a
bot ("doesn't look like a bot" = human is fail-open, rejected by the owner
2026-08-08).

Whether a correctly classified ``"agent"`` actor *satisfies* the release
policy is a separate question from classification, and it is a **policy
value**, not a constant in this code: ``agent_merge_allowed`` in
``profiles/approval-policy.yaml``. ADR-ECO-008 D1 puts merges in automatic
runs on an agent, so the gate must be able to permit exactly that; ADR-ECO-008
is still ``status: proposed``, so the default — including for a policy file
written before the field existed — is denied. Permission has to arrive as an
explicit ``true``; it never appears on its own. Consuming the value belongs
to ``check_approval_evidence``, not to ``classify_actor``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from steward.gatecheck.checks import Artifact, Finding
from steward.gatecheck.git_facts import GitFacts

__all__ = [
    "ActorFact",
    "ActorType",
    "ApprovalPolicy",
    "PolicyError",
    "check_approval_evidence",
    "classify_actor",
    "load_approval_policy",
]

ActorType = Literal["human", "agent", "unknown"]


class PolicyError(ValueError):
    """Malformed ``approval-policy.yaml`` — fail-closed, config-level error."""


@dataclass(frozen=True)
class ActorFact:
    """One merge-actor fact, as materialized from GitHub's ``mergedBy``."""

    identity: str  # "github:<login>"
    actor_type_hint: str  # "User" | "Bot" | ... (from GitHub) — a hint only


@dataclass(frozen=True)
class ApprovalPolicy:
    """Closed merge-actor classification policy.

    Empty allowlists are a legitimate state ("we don't know anyone yet"),
    not a config error — only a malformed *shape* is.
    """

    version: int
    human_identities: frozenset[str]
    agent_identities: frozenset[str]
    agent_merge_allowed: bool = False


def classify_actor(identity: str | None, hint: str | None, policy: ApprovalPolicy) -> ActorType:
    """Classify a merge actor against the closed policy.

    - ``identity`` is ``None`` (no evidence) -> ``"unknown"``.
    - exact match in ``policy.human_identities`` -> ``"human"`` (checked
      first, so a listed human is never reclassified by a misleading hint).
    - exact match in ``policy.agent_identities`` OR ``hint == "Bot"`` ->
      ``"agent"``.
    - anything else, including an unrecognized identity with a ``"User"``
      hint -> ``"unknown"``. There is no default-human fallback.
    """
    if identity is None:
        return "unknown"
    if identity in policy.human_identities:
        return "human"
    if identity in policy.agent_identities or hint == "Bot":
        return "agent"
    return "unknown"


def _check_identity_list(value: object, field: str, path: Path) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PolicyError(f"{path}: '{field}' must be a list of strings")
    return frozenset(value)


_ALLOWED_KEYS = {"version", "human_identities", "agent_identities", "agent_merge_allowed"}


def load_approval_policy(path: Path) -> ApprovalPolicy:
    """Load and validate ``approval-policy.yaml``, fail-closed.

    Follows the same shape-validation discipline as
    :mod:`steward.gatecatalog`: a non-mapping document, missing required
    lists, or non-string list entries all raise :class:`PolicyError` naming
    the file and field. Empty ``human_identities``/``agent_identities``
    lists are accepted — they mean "no known actors yet", not an error.

    ``agent_merge_allowed`` is optional and defaults to ``False``; when
    present it must be a real ``bool``. A truthy scalar (``1``, ``"yes"``)
    is a :class:`PolicyError`, never a coerced grant — permission is
    something a policy states, not something a parser infers.
    """
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"cannot read approval policy {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{path}: top level must be a mapping")

    unknown_keys = set(data.keys()) - _ALLOWED_KEYS
    if unknown_keys:
        raise PolicyError(f"{path}: unknown key(s) {sorted(unknown_keys)}")

    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PolicyError(f"{path}: 'version' must be an int >= 1")

    if "human_identities" not in data:
        raise PolicyError(f"{path}: missing required 'human_identities'")
    if "agent_identities" not in data:
        raise PolicyError(f"{path}: missing required 'agent_identities'")

    human_identities = _check_identity_list(data["human_identities"], "human_identities", path)
    agent_identities = _check_identity_list(data["agent_identities"], "agent_identities", path)

    agent_merge_allowed = data.get("agent_merge_allowed", False)
    if not isinstance(agent_merge_allowed, bool):
        raise PolicyError(f"{path}: 'agent_merge_allowed' must be a bool")

    return ApprovalPolicy(
        version=version,
        human_identities=human_identities,
        agent_identities=agent_identities,
        agent_merge_allowed=agent_merge_allowed,
    )


_APPROVED = "approved"


def check_approval_evidence(
    artifacts: list[Artifact],
    git: GitFacts,
    policy: ApprovalPolicy,
    actor_facts: object | None,
    stage: str,
) -> list[Finding]:
    """GC-APPROVAL-MISSING: release-stage merge-evidence gate (D5).

    Only runs at ``stage == "release"`` — at ``"authoring"`` the check does
    not run at all (not "runs and finds nothing"). For each *managed*,
    ``status: approved`` artifact confirmed present on the default branch,
    the four independently-sourced facts below combine into exactly five
    outcomes, each with a distinguishable message (operational semantics,
    D5):

    - no first-parent merge provenance for the current blob (:meth:`GitFacts
      .merge_provenance` returns ``None``) -> **absent**.
    - provenance found, but ``actor_facts`` is ``None`` (no
      ``--approval-facts`` file at all) or has no entry for the merge
      ``sha`` -> **unavailable** — materialize with ``steward
      approval-facts``.
    - an actor identity present but :func:`classify_actor` returns
      ``"unknown"`` (not in the closed allowlist policy) -> **unknown**.
    - a correctly classified ``"agent"`` actor -> **agent** — a finding
      unless ``policy.agent_merge_allowed`` is set, which is the one
      classification the policy value widens. Permitting it does not touch
      the other outcomes: **unknown** stays fail-closed either way.
    - a correctly classified ``"human"`` actor -> no finding.

    This combinator takes ``actor_facts`` as its own argument and never
    reads ``MergeProvenance.actor``/``actor_source`` — those fields are a
    test-fixture-only direct-injection path (see
    :mod:`steward.gatecheck.git_facts`), never the authoritative source.

    .. note::
       ``actor_facts`` is typed ``object | None`` for the duration of the
       ``approval-facts/v2`` migration (steward TODO.md): the v1 evidence
       source (``steward.approvalfacts.ApprovalFacts``) that used to
       populate it is gone, and no caller passes anything but ``None`` yet.
       Every merge therefore reports **unavailable** below. Task 8 of that
       migration rewires this function onto ``ApprovalFactsV2`` — until
       then the gate is disconnected and untested by design, not a bug.
    """
    if stage != "release":
        return []

    findings: list[Finding] = []
    for artifact in artifacts:
        if artifact.node_id is None or artifact.meta.status != _APPROVED:
            continue
        if not git.on_default_branch(artifact.path):
            continue

        provenance = git.merge_provenance(artifact.path)
        if provenance is None:
            findings.append(
                Finding(
                    "error",
                    "GC-APPROVAL-MISSING",
                    artifact.path,
                    "required merge evidence is absent: no first-parent merge "
                    "provenance for the current blob",
                )
            )
            continue

        # actor_facts is always None until task 8 rewires this onto
        # ApprovalFactsV2 (see the note in this function's docstring).
        actor_fact = None
        if actor_fact is None:
            findings.append(
                Finding(
                    "error",
                    "GC-APPROVAL-MISSING",
                    artifact.path,
                    f"merge provenance found (sha {provenance.sha}) but merge actor "
                    "facts are unavailable — materialize with `steward approval-facts`",
                )
            )
            continue

        actor_type = classify_actor(actor_fact.identity, actor_fact.actor_type_hint, policy)
        if actor_type == "unknown":
            findings.append(
                Finding(
                    "error",
                    "GC-APPROVAL-MISSING",
                    artifact.path,
                    f"merge actor {actor_fact.identity!r} is not in the closed "
                    "classification (unknown)",
                )
            )
        elif actor_type == "agent" and not policy.agent_merge_allowed:
            findings.append(
                Finding(
                    "error",
                    "GC-APPROVAL-MISSING",
                    artifact.path,
                    f"merge actor {actor_fact.identity!r} is an agent, but "
                    "agent_merge does not satisfy the release policy: the "
                    "approval policy has 'agent_merge_allowed' false — set it "
                    "to true there to permit agent merges",
                )
            )
        # human, and agent under an allowing policy -> the release policy
        # is satisfied.
    return findings
