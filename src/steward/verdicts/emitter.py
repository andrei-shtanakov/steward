"""gate_verdicts.jsonl emitter — the producer half of contract gate-verdicts/v1.

WS-A of the governance bundle ``workstreams/WS-005-gate-verdicts/spec/``
(capability: the operator's read-only bundle panel; canon of the schema and
fixtures: ``contracts/gate-verdicts/v1/``).

Design decisions this module implements:

- ARCH-D1: the file lives at ``<repo-root>/.steward/gate_verdicts.jsonl``,
  gitignored — a local machine artifact, never a second source of truth in git.
- ARCH-D2: steward writes **facts only** (header provenance, artifact inventory,
  findings). Classification into pass/blocked/no-data/unreadable/stale/
  unresolvable is the reader's job; the file never asserts its own freshness.
- ARCH-D4: the file is rewritten whole on every emitting run — no append across
  runs. The write is atomic (temp + ``os.replace``) so a crashed run never
  leaves a torn file *by our doing*; a torn file is still a reader-side
  'unreadable' case (BEH-08), not a trusted state.
- Fail-closed covers the instrument: the header pins ``source_commit`` and a
  ``dirty`` flag taken from git at emit time. No resolvable provenance — no
  file (:class:`ProvenanceError`); a stale leftover file is the reader's
  stale-classification problem, which is exactly the designed path.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from steward.gatecheck.checks import Artifact, Finding
from steward.graph import SpecGraph
from steward.meta import parse_owner_roles

__all__ = ["EmitError", "ProvenanceError", "emit_verdicts"]

SCHEMA_VERSION = "1"
VERDICTS_RELPATH = Path(".steward") / "gate_verdicts.jsonl"

_SEVERITY_TO_VERDICT = {"error": "fail", "warn": "warn"}


class EmitError(RuntimeError):
    """The verdicts file could not be produced (config-level, exit 2)."""


class ProvenanceError(EmitError):
    """Git provenance (HEAD commit / dirty state) is unavailable — refuse to emit."""


def emit_verdicts(
    graph: SpecGraph,
    artifacts: list[Artifact],
    findings: list[Finding],
    spec_dir: Path,
) -> Path:
    """Write the run's verdicts file; return its path.

    ``spec_dir`` anchors both the git provenance lookup and the output location
    (``<repo-root>/.steward/gate_verdicts.jsonl``). Raises
    :class:`ProvenanceError` when the bundle is not inside a git repository —
    verdicts without provenance would be exactly the untrustworthy 'clean' the
    capability forbids.
    """
    repo_root = _git(spec_dir, "rev-parse", "--show-toplevel")
    if repo_root is None:
        raise ProvenanceError(
            "cannot emit verdicts: bundle is not inside a git repository "
            "(header provenance requires HEAD + dirty state)"
        )
    root = Path(repo_root)
    source_commit = _git(spec_dir, "rev-parse", "HEAD")
    if source_commit is None:
        raise ProvenanceError("cannot emit verdicts: git HEAD is unresolvable (empty repo?)")
    porcelain = _git(spec_dir, "status", "--porcelain")
    if porcelain is None:
        raise ProvenanceError("cannot emit verdicts: git working-tree state is unreadable")

    try:
        bundle = spec_dir.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as err:
        raise EmitError(f"bundle {spec_dir} is outside the repo root {root}") from err

    records: list[dict] = [
        {
            "kind": "header",
            "schema_version": SCHEMA_VERSION,
            "source_commit": source_commit,
            "dirty": bool(porcelain),
            "generated_at": datetime.now(UTC).isoformat(),
            "profile": graph.profile,
            "bundle": bundle,
        }
    ]
    for artifact in sorted(artifacts, key=lambda a: a.path):
        records.append(
            {
                "kind": "artifact",
                "path": artifact.path,
                "node_id": artifact.node_id,
                "status": _status(artifact),
                "owner_roles": _role_slugs(graph, artifact),
            }
        )
    for finding in findings:
        records.append(
            {
                "kind": "finding",
                "gate_id": finding.rule_id,
                "verdict": _SEVERITY_TO_VERDICT[finding.severity],
                "artifact": finding.artifact,
                "message": finding.message,
            }
        )

    out_path = root / VERDICTS_RELPATH
    try:
        _atomic_write_jsonl(out_path, records)
    except OSError as err:
        raise EmitError(f"cannot write verdicts file {out_path}: {err}") from err
    return out_path


def _status(artifact: Artifact) -> str:
    status = artifact.meta.status
    return status if status in ("draft", "approved", "stale", "in_review") else "unknown"


def _role_slugs(graph: SpecGraph, artifact: Artifact) -> list[str]:
    """DEC-007 slugs (no ``@``) from the profile node's owner_role.

    The profile is the role authority for a node; the artifact's own
    frontmatter mirror is not consulted. Legacy multi-role strings produce a
    list — the DEC-007 migration collapses it to one accountable slug later,
    explicitly, not here.
    """
    if artifact.node_id is None:
        return sorted({role.lstrip("@") for role in artifact.meta.owner_roles})
    node = graph.nodes[artifact.node_id]
    return [role.lstrip("@") for role in parse_owner_roles(node.owner_role)]


def _atomic_write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".gate_verdicts-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _git(cwd: Path, *args: str) -> str | None:
    proc = subprocess.run(  # noqa: S603 S607 — fixed argv, no user input
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() if proc.returncode == 0 else None
