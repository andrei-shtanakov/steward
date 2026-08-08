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
2026-08-08). ``agent_merge`` is disabled by policy in v1 (ADR-ECO-004): a
correctly classified ``"agent"`` actor still does not satisfy the release
policy — that consuming decision belongs to ``check_approval_evidence``
(a later task in this workstream), not to this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

__all__ = [
    "ActorFact",
    "ActorType",
    "ApprovalPolicy",
    "PolicyError",
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


_ALLOWED_KEYS = {"version", "human_identities", "agent_identities"}


def load_approval_policy(path: Path) -> ApprovalPolicy:
    """Load and validate ``approval-policy.yaml``, fail-closed.

    Follows the same shape-validation discipline as
    :mod:`steward.gatecatalog`: a non-mapping document, missing required
    lists, or non-string list entries all raise :class:`PolicyError` naming
    the file and field. Empty ``human_identities``/``agent_identities``
    lists are accepted — they mean "no known actors yet", not an error.
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

    return ApprovalPolicy(
        version=version,
        human_identities=human_identities,
        agent_identities=agent_identities,
    )
