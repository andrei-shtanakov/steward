"""gate-check checks: pure functions over a collected bundle (WS-002, DESIGN-203).

Each check is ``(graph, artifacts[, git]) -> list[Finding]`` — no I/O, no
ordering dependencies, independently unit-testable. The aggregator
(:func:`run_checks`) concatenates findings; the CLI maps them to exit codes.

The act layer (approval-facts v3 §7.2) also lives here, because every check
that reads approval depends on it: :func:`collect_bundle` takes the caller's
mandatory ``act_mode``; :func:`act_statuses` resolves the node status
provider's answers once, against a :class:`Snapshot` of the bytes on disk, and
the checks receive the resulting :class:`ActStatuses`. Order: collect →
statuses → checks. Like ``collect_bundle``, the snapshot and the
``GC-ACT-MODE-CONFLICT`` detector read the filesystem; the checks stay pure.

Deferred by design (documented, not forgotten):

- REQ-209 OSS bridge (repolinter / codeowners-validator) is P2 and follows
  once the native checks prove themselves in CI.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from steward.compile.decomposition import (
    CompileError,
    extract_compile_block,
    parse_decomposition,
)
from steward.gatecheck.candidate import blob_hash_of
from steward.gatecheck.git_facts import FactsError, GitFacts
from steward.graph import SpecGraph
from steward.meta import ArtifactMeta, MetaError, parse_artifact
from steward.roleassignments import RoleAssignments

__all__ = [
    "ABSENT_FROM_PROVIDER",
    "BUNDLE_ARTIFACT",
    "DELEGATED_RULES",
    "INVALID_ANSWER",
    "NO_PROVIDER",
    "PROVIDER_ERROR",
    "ActMode",
    "ActStatuses",
    "Artifact",
    "Finding",
    "NodeStatus",
    "Snapshot",
    "SnapshotError",
    "act_conflict",
    "act_statuses",
    "check_act_status",
    "collect_bundle",
    "delegated",
    "relaxable",
    "run_checks",
    "snapshot_digest",
    "snapshot_for",
]

_APPROVED = "approved"

#: The caller's admission result (approval-facts v3 §7.2.1, R-M0-1/R-M0-5):
#: steward never infers it — not from files, not from history.
ActMode = Literal["legacy", "review"]
_ACT_MODES: tuple[str, ...] = ("legacy", "review")
_REVIEW_ACT = "review/v1"

#: Provider answers steward substitutes itself (§7.2.2). Each is a valid answer
#: form, so a substituted reason travels exactly like a provider's own.
INVALID_ANSWER = "not_approved:invalid_provider_answer"
PROVIDER_ERROR = "unavailable:provider_error"
ABSENT_FROM_PROVIDER = "unavailable:absent_from_provider"
NO_PROVIDER = "unavailable:no_status_provider"

#: Codes ``check_stale_cascade`` hands over to the provider for an established
#: node in ``review`` (§7.2.3): the provider answers for this snapshot's bytes
#: and the envelope's pins, so the frontmatter pin cascade is redundant there.
DELEGATED_RULES: tuple[str, ...] = ("GC-STALE", "GC-STALE-UNPINNED", "GC-STALE-KEY")

#: ``Finding.artifact`` of a finding about the bundle as a whole.
BUNDLE_ARTIFACT = "<bundle>"


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


class SnapshotError(RuntimeError):
    """The snapshot's bytes cannot be read (config-level, exit 2 — never a skip)."""


@dataclass(frozen=True)
class Snapshot:
    """The bytes a run judges, which a node status provider must answer for (§7.2.3).

    ``root`` is the repo root for ``live``, the candidate directory for
    ``candidate`` and the bundle for ``injected``; ``commit`` is HEAD for
    ``live`` and ``None`` otherwise. Build it only through :func:`snapshot_for`.
    """

    kind: Literal["live", "candidate", "injected"]
    root: Path
    commit: str | None
    digest: str


#: A node status provider: ``(node_id, snapshot) -> answer``, where the answer
#: is ``approved`` | ``not_approved:<reason>`` | ``unavailable:<reason>``.
NodeStatus = Callable[[str, Snapshot], str]


def bundle_dir(spec_dir: Path) -> Path:
    """The bundle path at the boundary, guarded so ``..`` is its repo-tree parent.

    ``os.path.abspath`` collapses ``.`` and ``x/..`` without following links, so
    ``<spec_dir>/../approvals/`` is the sibling *as it sits in the tree* (git
    stores a symlink as a link). A bundle or ``approvals/`` directory that is
    itself a symlink is refused (:class:`SnapshotError`) rather than located by
    guess: following it would read approvals from beside the link's target,
    possibly outside the repository, and the digest would cover other envelopes
    than the tree shows. Symlinks among the ancestors (``/tmp``, ``/var`` on
    macOS, a symlinked clone directory) move the bundle and its sibling
    together and are allowed. The guarded path is returned physically
    resolved, so its ``.parent`` is the sibling's directory.
    """
    lexical = Path(os.path.abspath(spec_dir))
    for candidate in (lexical, lexical.parent / "approvals"):
        if candidate.is_symlink():
            raise SnapshotError(
                f"snapshot: {candidate} is a symlink; approvals/ would not be the "
                "bundle's repo-tree sibling — pass the real directory"
            )
    return lexical.resolve()


def approvals_dir(spec_dir: Path) -> Path:
    """``<spec_dir>/../approvals/`` — where envelopes and request manifests live.

    Through :func:`bundle_dir`: ``Path(".").parent`` is ``.`` itself, so a
    relative bundle path would put ``approvals/`` *inside* the bundle, and a
    symlinked bundle would put it beside the link's target.
    """
    return bundle_dir(spec_dir).parent / "approvals"


def snapshot_digest(spec_dir: Path) -> str:
    """``sha256:`` over sorted ``"<git blob id> <path>\\n"`` lines of the snapshot's files.

    Every file on disk under ``spec_dir`` and ``<spec_dir>/../approvals/`` —
    the bytes ``collect_bundle`` reads. Paths are ``bundle/<rel>`` and
    ``approvals/<rel>``, so the digest addresses content, not location: the
    same bytes give the same digest wherever the bundle sits. An unreadable
    file or directory raises :class:`SnapshotError`; skipping it would let a
    provider's answer cover bytes nobody hashed. A non-regular file (a FIFO, a
    dangling symlink) is the same error — reading it would block or fail.
    """
    spec_dir = bundle_dir(spec_dir)
    lines = []
    for label, root in (("bundle", spec_dir), ("approvals", approvals_dir(spec_dir))):
        if label == "approvals" and not root.exists():
            continue
        for path in _walk_files(root):
            if not path.is_file():
                raise SnapshotError(f"snapshot: {path} is not a regular file")
            try:
                data = path.read_bytes()
            except OSError as err:
                raise SnapshotError(f"snapshot: cannot read {path}: {err}") from err
            lines.append(f"{blob_hash_of(data)} {label}/{path.relative_to(root).as_posix()}\n")
    payload = "".join(sorted(lines)).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _walk_files(root: Path) -> list[Path]:
    def fail(err: OSError) -> None:
        raise SnapshotError(f"snapshot: cannot list {err.filename}: {err}") from err

    return [Path(top) / name for top, _dirs, files in os.walk(root, onerror=fail) for name in files]


def snapshot_for(
    run: Literal["live", "candidate", "injected"], spec_dir: Path, repo_root: Path | None
) -> Snapshot:
    """The one place a :class:`Snapshot` is built — from the run's own inputs.

    For ``candidate`` the root is the candidate directory, never the checkout
    of the base: an answer computed over the base would cover other bytes.
    Path inputs are resolved here, at the boundary.
    """
    spec_dir = bundle_dir(spec_dir)
    digest = snapshot_digest(spec_dir)
    if run != "live":
        return Snapshot(kind=run, root=spec_dir, commit=None, digest=digest)
    if repo_root is None:
        raise ValueError("snapshot_for('live', ...) needs the repo root")
    if not spec_dir.is_relative_to(repo_root.resolve()):
        raise SnapshotError(f"snapshot: bundle {spec_dir} is outside the repository {repo_root}")
    proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SnapshotError(f"snapshot: cannot resolve HEAD of {repo_root}")
    return Snapshot(
        kind="live", root=repo_root.resolve(), commit=proc.stdout.strip(), digest=digest
    )


def collect_bundle(
    graph: SpecGraph, spec_dir: Path, *, act_mode: ActMode
) -> tuple[list[Artifact], list[Finding]]:
    """Scan a bundle dir: parse managed artifacts, match them to profile nodes.

    Unmanaged files (no frontmatter / no spec_stage) pass through untouched
    (REQ-208). A managed stage the profile does not know is a warning; two
    artifacts claiming the same node is an error.

    ``act_mode`` is the caller's admission result and has no default (R-M0-5).
    Under ``legacy`` the obvious-contradiction detector runs
    (:func:`act_conflict`, ``GC-ACT-MODE-CONFLICT``).
    """
    if act_mode not in _ACT_MODES:
        raise ValueError(f"act_mode must be one of {_ACT_MODES}, got {act_mode!r}")
    spec_dir = bundle_dir(spec_dir)  # the boundary: `.`/`..` must not shift approvals/
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
    if act_mode == "legacy":
        findings.extend(act_conflict(spec_dir))
    return artifacts, findings


def act_conflict(spec_dir: Path) -> list[Finding]:
    """``GC-ACT-MODE-CONFLICT``: ``legacy`` contradicted by the approvals tree (§7.2.1).

    A fail-closed finding, not authority: an envelope ``<node>.yaml`` or a
    request manifest ``requests/*.yaml`` claiming ``act: review/v1``, or a
    directory / any YAML in it that cannot be read, contradicts the caller's
    ``legacy``. A missing directory proves nothing and permits nothing.
    """
    root = approvals_dir(spec_dir)
    if not root.exists():
        return []
    try:
        files = _walk_files(root)
    except SnapshotError as err:
        return [_conflict(f"approvals directory is unreadable: {err}")]
    claimed: list[str] = []
    for path in sorted(files):
        if path.suffix not in (".yaml", ".yml"):
            continue
        rel = path.relative_to(root).as_posix()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as err:
            return [_conflict(f"approvals/{rel} is unreadable: {err}")]
        envelope_or_manifest = path.parent == root or path.parent == root / "requests"
        if envelope_or_manifest and isinstance(data, dict) and data.get("act") == _REVIEW_ACT:
            claimed.append(f"approvals/{rel}")
    if not claimed:
        return []
    return [_conflict(f"act: {_REVIEW_ACT} is claimed by {', '.join(claimed)}")]


def _conflict(detail: str) -> Finding:
    return Finding(
        "error",
        "GC-ACT-MODE-CONFLICT",
        BUNDLE_ARTIFACT,
        f"--act-mode legacy contradicts the approvals tree: {detail}",
    )


@dataclass(frozen=True)
class ActStatuses:
    """Admission result + provider answers: the one input that decides approval (§7.2.2).

    ``meta.status`` is never rewritten. ``claimed`` is what frontmatter says;
    ``established`` is what the act establishes — frontmatter under ``legacy``,
    only the provider's ``approved`` under ``review``; ``in_scope`` (either) is
    what every approval-reading check takes, so a provider's refusal never
    switches a check off. Build it with :func:`act_statuses`.
    """

    act_mode: ActMode
    provider_given: bool
    answers: Mapping[str, str]
    snapshot: Snapshot | None

    def claimed(self, a: Artifact) -> bool:
        """Frontmatter says ``status: approved``."""
        return a.meta.status == _APPROVED

    def established(self, a: Artifact) -> bool:
        """The act establishes the approval."""
        if self.act_mode == "legacy":
            return self.claimed(a)
        return a.node_id is not None and self.answers.get(a.node_id) == _APPROVED

    def in_scope(self, a: Artifact) -> bool:
        """Claimed or established: the artifact is taken into the approval checks."""
        return self.claimed(a) or self.established(a)

    def reason(self, a: Artifact) -> str:
        """Why ``a`` is not established under ``review`` (the provider's answer)."""
        if not self.provider_given:
            return NO_PROVIDER
        return self.answers.get(a.node_id or "", ABSENT_FROM_PROVIDER)


def act_statuses(
    artifacts: list[Artifact],
    *,
    act_mode: ActMode,
    node_status: NodeStatus | None,
    snapshot: Snapshot | None,
) -> ActStatuses:
    """Resolve approval statuses once, before the checks — the only provider call site.

    A provider under ``legacy`` or without a ``snapshot`` is a call error
    (``ValueError``): ``legacy`` has no provider by definition, and an answer
    not bound to a snapshot is bound to nothing. A provider exception becomes
    :data:`PROVIDER_ERROR`; an answer outside the three forms becomes
    :data:`INVALID_ANSWER` — neither stops the gate.
    """
    if act_mode not in _ACT_MODES:
        raise ValueError(f"act_mode must be one of {_ACT_MODES}, got {act_mode!r}")
    if node_status is not None and act_mode == "legacy":
        raise ValueError("node_status is meaningless under act_mode='legacy'")
    if node_status is None:
        return ActStatuses(act_mode, provider_given=False, answers={}, snapshot=snapshot)
    if snapshot is None:
        raise ValueError("node_status needs the snapshot it answers for")
    answers: dict[str, str] = {}
    for artifact in artifacts:
        node_id = artifact.node_id
        if node_id is None or node_id in answers:
            continue
        try:
            answer = node_status(node_id, snapshot)
        except Exception:  # noqa: BLE001 — any provider failure is an unavailable answer
            answers[node_id] = PROVIDER_ERROR
            continue
        answers[node_id] = answer if _valid_answer(answer) else INVALID_ANSWER
    return ActStatuses(act_mode, provider_given=True, answers=answers, snapshot=snapshot)


def _valid_answer(answer: object) -> bool:
    if not isinstance(answer, str):
        return False
    if answer == _APPROVED:
        return True
    kind, sep, reason = answer.partition(":")
    return bool(sep) and kind in ("not_approved", "unavailable") and bool(reason)


def delegated(artifact: Artifact, statuses: ActStatuses) -> bool:
    """``artifact``'s :data:`DELEGATED_RULES` are the provider's, not the cascade's."""
    return (
        statuses.act_mode == "review"
        and artifact.node_id is not None
        and statuses.established(artifact)
    )


def check_act_status(artifacts: list[Artifact], statuses: ActStatuses) -> list[Finding]:
    """``GC-ACT-PROVIDER-MISSING`` / ``GC-ACT-NOT-APPROVED`` (``review`` only, §7.2.2).

    A bundle left without any established approval does not pass silently, and
    every node artifact taken into scope but not established is a finding that
    carries the provider's reason.
    """
    if statuses.act_mode != "review":
        return []
    findings = []
    if not statuses.provider_given:
        findings.append(
            Finding(
                "error",
                "GC-ACT-PROVIDER-MISSING",
                BUNDLE_ARTIFACT,
                "--act-mode review without a node status provider: no approval in "
                "this bundle can be established",
            )
        )
    for artifact in artifacts:
        if artifact.node_id is None or not statuses.in_scope(artifact):
            continue
        if statuses.established(artifact):
            continue
        findings.append(
            Finding(
                "error",
                "GC-ACT-NOT-APPROVED",
                artifact.path,
                f"node {artifact.node_id!r} is not approved by the act "
                f"({statuses.reason(artifact)}); frontmatter status is not authoritative "
                "under --act-mode review",
            )
        )
    return findings


def run_checks(
    graph: SpecGraph,
    artifacts: list[Artifact],
    git: GitFacts,
    assignments: RoleAssignments | None = None,
    *,
    statuses: ActStatuses,
    prospective: bool = False,
    not_required: frozenset[str] = frozenset(),
) -> list[Finding]:
    """Run every check and concatenate their findings.

    ``statuses`` (:func:`act_statuses`) is mandatory: the act mode and the
    provider's answers reach the checks only through it, so the checks cannot
    run without the caller's admission result (approval-facts v3 §7.2.2).

    ``prospective`` marks a **candidate revision** — bundle content that is not
    a git ref yet (``gate-check --candidate``, :mod:`steward.gatecheck.candidate`).
    It skips exactly the ref-bound checks, and the skip lives here, in the one
    place the check list is written, so that a check added later cannot join
    the prospective run by accident: whoever adds it has to decide which side
    of this branch it belongs on. What is skipped is not silently passed — the
    CLI prints :data:`steward.gatecheck.candidate.NOT_EVALUATED`.

    ``check_stale_cascade`` deliberately stays on the prospective side: it
    reaches only for ``blob_hash``, which is a content address a candidate can
    answer (and answer more accurately than a live checkout, which would read
    the last commit instead of the files in front of it).

    ``not_required`` (``gate-check --upto``) relaxes **only** completeness for
    the nodes above the boundary. The graph itself is never truncated: an
    artifact of such a node that is present stays a node of the graph and goes
    through every other check — a truncated graph would demote it to a
    ``GC-STAGE`` warning and let it skip traceability and the stale cascade.
    """
    # Local imports: both modules import Artifact/Finding from this one.
    from steward.gatecheck.acceptance import check_acceptance
    from steward.gatecheck.behaviour import check_behaviour_spec

    findings: list[Finding] = []
    findings.extend(check_act_status(artifacts, statuses))
    findings.extend(check_completeness(graph, artifacts, not_required))
    findings.extend(check_traceability(graph, artifacts))
    findings.extend(check_upstream_approved(graph, artifacts, statuses=statuses))
    if not prospective:
        findings.extend(check_status_git(graph, artifacts, git, assignments, statuses=statuses))
    findings.extend(check_stale_cascade(graph, artifacts, git, statuses=statuses))
    findings.extend(check_compile_block(artifacts))
    findings.extend(check_behaviour_spec(graph, artifacts))
    findings.extend(check_acceptance(graph, artifacts))
    return findings


def _by_node(artifacts: list[Artifact]) -> dict[str, Artifact]:
    return {a.node_id: a for a in artifacts if a.node_id is not None}


def check_completeness(
    graph: SpecGraph, artifacts: list[Artifact], not_required: frozenset[str] = frozenset()
) -> list[Finding]:
    """REQ-202: every required, non-delegated node has an artifact.

    Nodes in ``not_required`` (above a ``--upto`` boundary) are out of scope —
    unless a present artifact depends on them. A bundle with a hole below a
    present artifact is not a level prefix, and later checks (the behaviour
    gates among them) rely on completeness having flagged a missing upstream
    rather than running without it.
    """
    present = _by_node(artifacts)
    not_required = relaxable(graph, artifacts, not_required)
    findings = []
    for node in graph.nodes.values():
        if node.delegate is not None:
            continue  # delegated leaves live per-workstream, not in the bundle
        if node.id in not_required:
            continue
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


def relaxable(graph: SpecGraph, artifacts: list[Artifact], above: Iterable[str]) -> frozenset[str]:
    """The part of ``above`` a ``--upto`` boundary actually relaxes.

    A node on which a present artifact (transitively) depends stays required.
    One function for both the check and the CLI's declaration of the boundary,
    so the declared scope can never disagree with the findings.
    """
    return frozenset(above) - _upstream_closure(graph, _by_node(artifacts))


def _upstream_closure(graph: SpecGraph, node_ids: Iterable[str]) -> frozenset[str]:
    """Every transitive upstream of ``node_ids`` (the nodes themselves excluded)."""
    seen: set[str] = set()
    frontier = [up for node_id in node_ids for up in graph.nodes[node_id].upstream]
    while frontier:
        node_id = frontier.pop()
        if node_id not in seen:
            seen.add(node_id)
            frontier.extend(graph.nodes[node_id].upstream)
    return frozenset(seen)


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


def check_upstream_approved(
    graph: SpecGraph, artifacts: list[Artifact], *, statuses: ActStatuses
) -> list[Finding]:
    """REQ-204: downstream cannot be approved before every upstream is.

    The downstream is taken when ``in_scope``; the upstream must be
    ``established`` — under ``review`` frontmatter alone approves nothing.
    """
    present = _by_node(artifacts)
    findings = []
    for artifact in artifacts:
        if artifact.node_id is None or not statuses.in_scope(artifact):
            continue
        for up in graph.nodes[artifact.node_id].upstream:
            upstream = present.get(up)
            if upstream is None or not statuses.established(upstream):
                state = _upstream_state(upstream, statuses)
                findings.append(
                    Finding(
                        "error",
                        "GC-UPSTREAM",
                        artifact.path,
                        f"approved while upstream {up!r} is {state}",
                    )
                )
    return findings


def _upstream_state(upstream: Artifact | None, statuses: ActStatuses) -> str:
    if upstream is None:
        return "missing"
    if statuses.act_mode == "legacy" or not statuses.claimed(upstream):
        return f"{upstream.meta.status}"
    return f"not established ({statuses.reason(upstream)})"


def check_compile_block(artifacts: list[Artifact]) -> list[Finding]:
    """Dep-link integrity of the normalized decomposition, upstream of compilation.

    The known trap (``emitter-contract-check.md``): Maestro ``validate --no-fs``
    silently accepts a dangling ``depends_on``, so a broken workstream link must
    be caught HERE, before compile-down ever runs — never rely on Maestro
    preflight for this. Artifacts without a ``steward-compile`` block are out of
    scope; whichever managed artifact carries one gets validated.
    """
    findings = []
    for artifact in artifacts:
        try:
            if extract_compile_block(artifact.text) is None:
                continue
            parse_decomposition(artifact.text)
        except CompileError as err:
            findings.append(Finding("error", "GC-COMPILE", artifact.path, str(err)))
    return findings


def check_stale_cascade(
    graph: SpecGraph, artifacts: list[Artifact], git: GitFacts, *, statuses: ActStatuses
) -> list[Finding]:
    """REQ-206 / DESIGN-207: an approved downstream pins its upstream blob hashes.

    Approval stamps ``upstream_hashes`` into the downstream's frontmatter; a
    pinned hash that no longer matches the upstream's current blob means the
    upstream changed after approval — the downstream is stale and must be
    re-approved (error). A downstream approved without a pin, or whose upstream
    hash cannot be resolved, only warns: pins arrive with C2-era approvals, so
    older approved artifacts must not start failing CI retroactively.

    Under ``review`` an ``established`` artifact is skipped: its cascade is
    delegated to the provider, which answers for this snapshot's bytes and the
    envelope's pins (:data:`DELEGATED_RULES`, §7.2.3). Claimed but not
    established keeps the frontmatter cascade — an extra finding is
    acceptable, a skipped one is not.
    """
    present = _by_node(artifacts)
    findings = []
    for artifact in artifacts:
        if artifact.node_id is None or not statuses.in_scope(artifact):
            continue
        if delegated(artifact, statuses):
            continue
        node = graph.nodes[artifact.node_id]
        pinned = dict(artifact.meta.upstream_hashes)
        for up in node.upstream:
            upstream = present.get(up)
            recorded = pinned.pop(up, None)
            if upstream is None:
                continue  # GC-UPSTREAM already errors on a missing approved upstream
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


def check_status_git(
    graph: SpecGraph,
    artifacts: list[Artifact],
    git: GitFacts,
    assignments: RoleAssignments | None = None,
    *,
    statuses: ActStatuses,
) -> list[Finding]:
    """REQ-205: ``status: approved`` must be mirrored by git facts.

    git is primary (steward NFR-003): the artifact must exist on the default
    branch, and — unless the profile is solo_auto_approve — carry a PR
    approval from an identity authorized by ``allowed_approver_roles``
    (DEC-007 D7).
    """
    findings = []
    for artifact in artifacts:
        if artifact.node_id is None or not statuses.in_scope(artifact):
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
        artifact_approvals = git.approvals(artifact.path)
        if artifact_approvals is None:
            # No authoritative facts (e.g. LiveGitFacts, no forge access):
            # absence of evidence is not a proven violation.
            continue
        if assignments is None:
            raise FactsError(
                "role assignments are required to authorize approvals for "
                f"non-solo profile {graph.profile!r} — role-assignments.yaml "
                "is a mandatory sibling of the selected profile"
            )
        node = graph.nodes[artifact.node_id]
        # DEC-007 D7 (owner ruling): absent allowed_approver_roles → the
        # accountable owner may approve; an explicit list REPLACES that
        # default (separation of duties — the owner may be excluded).
        # Node-level only for now: the frontmatter-level field parses but
        # instance-vs-node precedence awaits its own owner ruling.
        allowed = (
            frozenset(node.allowed_approver_roles)
            if node.allowed_approver_roles is not None
            else frozenset({node.owner_role})
        )
        approver_roles: set[str] = set()
        for approval in artifact_approvals:
            approver_roles |= assignments.roles_for(approval.identity)
        if not allowed & approver_roles:
            findings.append(
                Finding(
                    "error",
                    "GC-GIT-ROLE",
                    artifact.path,
                    "approved without an approval from an allowed approver role "
                    f"(need one of: {', '.join(sorted(allowed))}; approvers "
                    f"{', '.join(sorted(a.identity for a in artifact_approvals)) or '—'} "
                    f"map to: {', '.join(sorted(approver_roles)) or 'no roles'})",
                )
            )
    return findings
