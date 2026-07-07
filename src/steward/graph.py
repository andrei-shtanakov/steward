"""Profile loader and spec-artifact DAG for steward governance (WS-001).

A *profile* (``profiles/lite.yaml``, ``profiles/team.yaml``) declares the
artifact DAG as data. This module loads it into a :class:`SpecGraph` and
validates structural integrity: unique ids, upstream references that resolve,
and no cycles. Any violation raises :class:`ProfileError`, which gate-check
maps to exit code 2 (config error, REQ-201).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "ProfileError",
    "SpecGraph",
    "SpecNode",
    "load_profile",
    "load_profile_data",
]


class ProfileError(ValueError):
    """Invalid profile: bad shape, duplicate id, dangling upstream, or cycle."""


@dataclass(frozen=True)
class SpecNode:
    """One artifact (gate) in the governance DAG."""

    id: str
    owner_role: str
    upstream: tuple[str, ...] = ()
    required: bool = True
    template: str | None = None
    delegate: str | None = None
    per: str | None = None


@dataclass(frozen=True)
class SpecGraph:
    """A loaded, structurally-validated artifact DAG for one profile."""

    profile: str
    solo_auto_approve: bool
    nodes: dict[str, SpecNode]

    def topo_order(self) -> list[str]:
        """Return node ids in dependency order (every upstream before its downstream)."""
        return _kahn_order(self.nodes)


def load_profile(path: str | Path) -> SpecGraph:
    """Load and validate a profile YAML file into a :class:`SpecGraph`."""
    text = Path(path).read_text(encoding="utf-8")
    return load_profile_data(yaml.safe_load(text))


def load_profile_data(data: Any) -> SpecGraph:
    """Build and validate a :class:`SpecGraph` from parsed profile data."""
    if not isinstance(data, dict):
        raise ProfileError("profile must be a mapping")

    profile = data.get("profile")
    if not isinstance(profile, str) or not profile:
        raise ProfileError("profile: missing or non-string 'profile' name")

    raw_artifacts = data.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ProfileError("profile: 'artifacts' must be a non-empty list")

    nodes: dict[str, SpecNode] = {}
    for entry in raw_artifacts:
        node = _node_from_entry(entry)
        if node.id in nodes:
            raise ProfileError(f"duplicate artifact id: {node.id!r}")
        nodes[node.id] = node

    _validate_edges(nodes)

    solo_auto_approve = data.get("solo_auto_approve", False)
    if not isinstance(solo_auto_approve, bool):
        raise ProfileError("profile: 'solo_auto_approve' must be a boolean")

    return SpecGraph(
        profile=profile,
        solo_auto_approve=solo_auto_approve,
        nodes=nodes,
    )


def _node_from_entry(entry: Any) -> SpecNode:
    if not isinstance(entry, dict):
        raise ProfileError(f"artifact entry must be a mapping, got {type(entry).__name__}")

    node_id = entry.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise ProfileError(f"artifact missing 'id': {entry!r}")

    owner_role = entry.get("owner_role")
    if not isinstance(owner_role, str) or not owner_role:
        raise ProfileError(f"artifact {node_id!r} missing 'owner_role'")

    upstream = entry.get("upstream")
    if upstream is None:
        upstream = []
    if not isinstance(upstream, list) or not all(isinstance(u, str) and u for u in upstream):
        raise ProfileError(f"artifact {node_id!r}: 'upstream' must be a list of ids")
    if len(set(upstream)) != len(upstream):
        raise ProfileError(f"artifact {node_id!r}: duplicate upstream ids")

    required = entry.get("required", True)
    if not isinstance(required, bool):
        raise ProfileError(f"artifact {node_id!r}: 'required' must be a boolean")

    return SpecNode(
        id=node_id,
        owner_role=owner_role,
        upstream=tuple(upstream),
        required=required,
        template=entry.get("template"),
        delegate=entry.get("delegate"),
        per=entry.get("per"),
    )


def _validate_edges(nodes: dict[str, SpecNode]) -> None:
    for node in nodes.values():
        for upstream_id in node.upstream:
            if upstream_id not in nodes:
                raise ProfileError(
                    f"artifact {node.id!r} references unknown upstream {upstream_id!r}"
                )
    if len(_kahn_order(nodes)) != len(nodes):
        raise ProfileError("profile DAG contains a cycle")


def _kahn_order(nodes: dict[str, SpecNode]) -> list[str]:
    """Topologically order nodes; a shorter-than-input result signals a cycle.

    O(V+E): downstream adjacency is precomputed and a deque feeds the frontier.
    Assumes upstream references resolve (checked by :func:`_validate_edges`).
    """
    downstream: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    indegree = {node_id: len(node.upstream) for node_id, node in nodes.items()}
    for node in nodes.values():
        for upstream_id in node.upstream:
            downstream[upstream_id].append(node.id)

    ready = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for node_id in downstream[current]:
            indegree[node_id] -= 1
            if indegree[node_id] == 0:
                ready.append(node_id)
    return order
