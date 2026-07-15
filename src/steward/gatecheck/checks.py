"""gate-check checks: pure functions over a collected bundle (WS-002, DESIGN-203).

Each check is ``(graph, artifacts[, git]) -> list[Finding]`` — no I/O, no
ordering dependencies, independently unit-testable. The aggregator
(:func:`run_checks`) concatenates findings; the CLI maps them to exit codes.

Deferred by design (documented, not forgotten):

- REQ-209 OSS bridge (repolinter / codeowners-validator) is P2 and follows
  once the native checks prove themselves in CI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from steward.gatecheck.git_facts import GitFacts
from steward.graph import SpecGraph
from steward.meta import ArtifactMeta, MetaError, parse_artifact, parse_owner_roles

__all__ = ["Artifact", "Finding", "collect_bundle", "run_checks"]

_APPROVED = "approved"


@dataclass(frozen=True)
class Finding:
    """One governance violation (error blocks the PR, warn does not)."""

    severity: Literal["error", "warn"]
    rule_id: str
    artifact: str
    message: str


@dataclass(frozen=True)
class Artifact:
    """A managed artifact matched (or not) to a profile node."""

    path: str  # bundle-relative POSIX path
    node_id: str | None  # None: managed but unknown to the profile
    meta: ArtifactMeta
    text: str


def collect_bundle(graph: SpecGraph, spec_dir: Path) -> tuple[list[Artifact], list[Finding]]:
    """Scan a bundle dir: parse managed artifacts, match them to profile nodes.

    Unmanaged files (no frontmatter / no spec_stage) pass through untouched
    (REQ-208). A managed stage the profile does not know is a warning; two
    artifacts claiming the same node is an error.
    """
    artifacts: list[Artifact] = []
    findings: list[Finding] = []
    claimed: dict[str, str] = {}

    for path in sorted(spec_dir.rglob("*.md")):
        rel = path.relative_to(spec_dir).as_posix()
        text = path.read_text(encoding="utf-8")
        try:
            meta = parse_artifact(text)
        except MetaError as err:
            findings.append(Finding("error", "GC-META", rel, f"malformed frontmatter: {err}"))
            continue
        if meta is None:
            continue  # unmanaged passthrough (REQ-208)

        node_id = meta.spec_stage if meta.spec_stage in graph.nodes else None
        if node_id is None:
            findings.append(
                Finding(
                    "warn",
                    "GC-STAGE",
                    rel,
                    f"spec_stage {meta.spec_stage!r} is not a node of profile {graph.profile!r}",
                )
            )
        elif node_id in claimed:
            findings.append(
                Finding(
                    "error",
                    "GC-DUP",
                    rel,
                    f"node {node_id!r} already claimed by {claimed[node_id]}",
                )
            )
            node_id = None
        else:
            claimed[node_id] = rel
        artifacts.append(Artifact(path=rel, node_id=node_id, meta=meta, text=text))
    return artifacts, findings


def run_checks(graph: SpecGraph, artifacts: list[Artifact], git: GitFacts) -> list[Finding]:
    """Run every check and concatenate their findings."""
    findings: list[Finding] = []
    findings.extend(check_completeness(graph, artifacts))
    findings.extend(check_traceability(graph, artifacts))
    findings.extend(check_upstream_approved(graph, artifacts))
    findings.extend(check_status_git(graph, artifacts, git))
    findings.extend(check_stale_cascade(graph, artifacts, git))
    return findings


def _by_node(artifacts: list[Artifact]) -> dict[str, Artifact]:
    return {a.node_id: a for a in artifacts if a.node_id is not None}


def check_completeness(graph: SpecGraph, artifacts: list[Artifact]) -> list[Finding]:
    """REQ-202: every required, non-delegated node has an artifact."""
    present = _by_node(artifacts)
    findings = []
    for node in graph.nodes.values():
        if node.delegate is not None:
            continue  # delegated leaves live per-workstream, not in the bundle
        if node.required and node.id not in present:
            findings.append(
                Finding(
                    "error",
                    "GC-COMPLETENESS",
                    node.id,
                    f"required artifact {node.id!r} is missing from the bundle",
                )
            )
    return findings


def check_traceability(graph: SpecGraph, artifacts: list[Artifact]) -> list[Finding]:
    """REQ-203: every traces_to resolves upstream; downstream without any link warns.

    An entry resolves when it names an upstream node id, or when the id token
    (e.g. ``REQ-003``) occurs in the text of any upstream artifact.
    """
    present = _by_node(artifacts)
    findings = []
    for artifact in artifacts:
        if artifact.node_id is None:
            continue
        node = graph.nodes[artifact.node_id]
        if not node.upstream:
            continue
        if not artifact.meta.traces_to:
            findings.append(
                Finding(
                    "warn",
                    "GC-TRACE-EMPTY",
                    artifact.path,
                    f"downstream {node.id!r} carries no traces_to link",
                )
            )
            continue
        upstream_texts = [present[up].text for up in node.upstream if up in present]
        for ref in artifact.meta.traces_to:
            if ref in node.upstream:
                continue
            pattern = re.compile(rf"(?<![\w-]){re.escape(ref)}(?![\w-])")
            if any(pattern.search(text) for text in upstream_texts):
                continue
            findings.append(
                Finding(
                    "error",
                    "GC-TRACE",
                    artifact.path,
                    f"traces_to {ref!r} resolves to no upstream artifact of "
                    f"{node.id!r} (upstream: {', '.join(node.upstream)})",
                )
            )
    return findings


def check_upstream_approved(graph: SpecGraph, artifacts: list[Artifact]) -> list[Finding]:
    """REQ-204: downstream cannot be approved before every upstream is."""
    present = _by_node(artifacts)
    findings = []
    for artifact in artifacts:
        if artifact.node_id is None or artifact.meta.status != _APPROVED:
            continue
        for up in graph.nodes[artifact.node_id].upstream:
            upstream = present.get(up)
            if upstream is None or upstream.meta.status != _APPROVED:
                state = "missing" if upstream is None else upstream.meta.status
                findings.append(
                    Finding(
                        "error",
                        "GC-UPSTREAM",
                        artifact.path,
                        f"approved while upstream {up!r} is {state}",
                    )
                )
    return findings


def check_stale_cascade(
    graph: SpecGraph, artifacts: list[Artifact], git: GitFacts
) -> list[Finding]:
    """REQ-206 / DESIGN-207: an approved downstream pins its upstream blob hashes.

    Approval stamps ``upstream_hashes`` into the downstream's frontmatter; a
    pinned hash that no longer matches the upstream's current blob means the
    upstream changed after approval — the downstream is stale and must be
    re-approved (error). A downstream approved without a pin, or whose upstream
    hash cannot be resolved, only warns: pins arrive with C2-era approvals, so
    older approved artifacts must not start failing CI retroactively.
    """
    present = _by_node(artifacts)
    findings = []
    for artifact in artifacts:
        if artifact.node_id is None or artifact.meta.status != _APPROVED:
            continue
        node = graph.nodes[artifact.node_id]
        pinned = dict(artifact.meta.upstream_hashes)
        for up in node.upstream:
            upstream = present.get(up)
            if upstream is None:
                continue  # GC-UPSTREAM already errors on a missing approved upstream
            recorded = pinned.pop(up, None)
            if recorded is None:
                findings.append(
                    Finding(
                        "warn",
                        "GC-STALE-UNPINNED",
                        artifact.path,
                        f"approved without a pinned upstream_hashes entry for {up!r} — "
                        "stale-cascade cannot verify this edge",
                    )
                )
                continue
            current = git.blob_hash(upstream.path)
            if current is None:
                findings.append(
                    Finding(
                        "warn",
                        "GC-STALE-UNPINNED",
                        artifact.path,
                        f"cannot resolve the current blob hash of upstream {up!r} "
                        f"({upstream.path}) — stale-cascade cannot verify this edge",
                    )
                )
            elif current != recorded:
                findings.append(
                    Finding(
                        "error",
                        "GC-STALE",
                        artifact.path,
                        f"upstream {up!r} ({upstream.path}) changed since approval "
                        f"(pinned {recorded}, current {current}) — mark stale and re-approve",
                    )
                )
        for unknown in pinned:
            findings.append(
                Finding(
                    "warn",
                    "GC-STALE-KEY",
                    artifact.path,
                    f"upstream_hashes pins {unknown!r}, which is not an upstream of "
                    f"{node.id!r} (upstream: {', '.join(node.upstream) or '—'})",
                )
            )
    return findings


def check_status_git(graph: SpecGraph, artifacts: list[Artifact], git: GitFacts) -> list[Finding]:
    """REQ-205: ``status: approved`` must be mirrored by git facts.

    git is primary (steward NFR-003): the artifact must exist on the default
    branch, and — unless the profile is solo_auto_approve — carry a PR
    approval from one of the node's owner roles.
    """
    findings = []
    for artifact in artifacts:
        if artifact.node_id is None or artifact.meta.status != _APPROVED:
            continue
        if not git.on_default_branch(artifact.path):
            findings.append(
                Finding(
                    "error",
                    "GC-GIT-BRANCH",
                    artifact.path,
                    "approved in frontmatter but not confirmed on the default branch",
                )
            )
        if graph.solo_auto_approve:
            continue
        node_roles = set(parse_owner_roles(graph.nodes[artifact.node_id].owner_role))
        approving_roles = {a.role for a in git.approvals(artifact.path)}
        if not node_roles & approving_roles:
            findings.append(
                Finding(
                    "error",
                    "GC-GIT-ROLE",
                    artifact.path,
                    f"approved without a PR approval from an owner role "
                    f"(need one of: {', '.join(sorted(node_roles)) or '—'}; "
                    f"got: {', '.join(sorted(approving_roles)) or 'none'})",
                )
            )
    return findings
