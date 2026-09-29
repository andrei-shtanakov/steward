"""Charter schema 2 and workstream-code uniqueness (steward#190 item 3, `charter-check`).

Mirrors devtools ``governance/charter_guard.py`` (bundle-criteria oracle spec
rev 10 §1.1–1.2, owner ruling 2026-09-29), so the two parsers of a bundle agree:

- ``workstreams/*/spec/00-charter.md`` with ``schema: 2`` must carry
  ``code: ^[A-Z]{2,6}$`` and ``plan_item: todo://<this repo>/<id>`` naming an
  ``@id:`` that exists in the repo's ``TODO.md``. No frontmatter means schema 1
  (not checked); frontmatter that does not parse is a finding, never schema 1 —
  a YAML typo must not switch the oracle off.
- The code registry is the schema-2 charters themselves. Two charters with one
  code: the violator is the one merged later on first-parent history — of
  ``--base`` when given, else of HEAD (an uncommitted one is always the
  violator). Judge collisions against ``--base``: on a branch that merged the
  default branch in, HEAD's first-parent chain lists master's charters at the
  merge commit, i.e. after the branch's own (devtools has the same property).
- Against a base: a schema-2 charter is a tombstone — deleting it, moving its
  workstream directory or dropping it to schema 1 is a finding; its code is
  immutable, except for a collision violator already present in the base.

Finding codes use the ``CHARTER-`` prefix: ``GC-*`` is a closed namespace minted
only by the gate catalog (steward#62), and this is a repo check, not a bundle
gate. Exit codes mirror gate-check: 0 clean, 1 findings, 2 config error.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from steward._vendor.spec_meta import split_frontmatter

__all__ = ["Charter", "CharterFinding", "check_repo", "read_charter"]

CODE_RE = re.compile(r"^[A-Z]{2,6}$")
PLAN_ITEM_RE = re.compile(r"^todo://([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
CHARTER_GLOB = "workstreams/*/spec/00-charter.md"
_TODO_ID_RE = re.compile(r"@id:([A-Za-z0-9_.-]+)")
_UNMERGED = 10**9


@dataclass(frozen=True)
class Charter:
    """The schema-relevant frontmatter of one charter."""

    schema: int  # 0: a value outside the digit vocabulary
    code: str | None
    plan_item: str | None
    malformed: bool = False
    raw_schema: object = None  # the value as written, for the finding message


@dataclass(frozen=True)
class CharterFinding:
    """One violation; ``path`` is repo-relative (empty for a repo-level finding)."""

    rule_id: str
    path: str
    message: str


def read_charter(text: str) -> Charter:
    """Read schema/code/plan_item; broken frontmatter is ``malformed``, not schema 1."""
    if not text.startswith("---"):
        return Charter(schema=1, code=None, plan_item=None)
    meta, _ = split_frontmatter(text)
    if meta is None:
        return Charter(schema=0, code=None, plan_item=None, malformed=True)
    schema = meta.get("schema", 1)
    code = meta.get("code")
    plan_item = meta.get("plan_item")
    return Charter(
        schema=int(schema) if str(schema).isdigit() else 0,
        code=None if code is None else str(code),
        plan_item=None if plan_item is None else str(plan_item),
        raw_schema=schema,
    )


def check_repo(
    repo: Path, base_ref: str | None, *, repo_name: str | None = None
) -> list[CharterFinding]:
    """All findings over the working tree, and against ``base_ref`` when given.

    A base that is given but does not resolve to a commit is a finding
    (fail-closed): "cannot read the base" is not "the base has no charters".
    """
    if base_ref is not None and not _is_commit(repo, base_ref):
        return [
            CharterFinding("CHARTER-BASE", "", f"base {base_ref!r} does not resolve to a commit")
        ]
    name = repo_name or repo.name
    todo = repo / "TODO.md"
    todo_ids = set(_TODO_ID_RE.findall(todo.read_text())) if todo.exists() else set()
    head = {
        path.relative_to(repo).as_posix(): read_charter(path.read_text())
        for path in sorted(repo.glob(CHARTER_GLOB))
    }
    findings: list[CharterFinding] = []
    for path, charter in head.items():
        findings.extend(_grammar_findings(path, charter, todo_ids, name))
    if base_ref is not None:
        base = _charters_at(repo, base_ref)
        findings.extend(_tombstone_findings(base, head))
        base_violators = {
            f.path for f in _collision_findings(base, _merge_order(repo, base_ref, list(base)))
        }
        for path, charter in head.items():
            findings.extend(
                _code_change_findings(path, base.get(path), charter, path in base_violators)
            )
    order = _merge_order(repo, base_ref or "HEAD", list(head))
    findings.extend(_collision_findings(head, order))
    return findings


def _grammar_findings(
    path: str, charter: Charter, todo_ids: set[str], repo_name: str
) -> list[CharterFinding]:
    if charter.malformed:
        return [CharterFinding("CHARTER-MALFORMED", path, "charter frontmatter does not parse")]
    if charter.schema == 1:
        return []
    if charter.schema != 2:
        return [
            CharterFinding(
                "CHARTER-SCHEMA",
                path,
                f"schema {charter.raw_schema!r} is outside the vocabulary 1|2",
            )
        ]
    findings = []
    if not charter.code:
        findings.append(CharterFinding("CHARTER-CODE", path, "schema 2 requires a code"))
    elif not CODE_RE.match(charter.code):
        findings.append(
            CharterFinding("CHARTER-CODE", path, f"code {charter.code!r} is not ^[A-Z]{{2,6}}$")
        )
    problem = _plan_item_problem(charter.plan_item, todo_ids, repo_name)
    if problem is not None:
        findings.append(CharterFinding("CHARTER-PLAN-ITEM", path, problem))
    return findings


def _plan_item_problem(plan_item: str | None, todo_ids: set[str], repo_name: str) -> str | None:
    if not plan_item:
        return "schema 2 requires a plan_item"
    match = PLAN_ITEM_RE.match(plan_item)
    if match is None:
        return f"plan_item {plan_item!r} is not todo://<repo>/<id>"
    if match.group(1) != repo_name:
        return f"plan_item points at another repo ({match.group(1)!r}, this is {repo_name!r})"
    if match.group(2) not in todo_ids:
        return f"plan_item @id:{match.group(2)} is not in TODO.md"
    return None


def _collision_findings(
    charters: dict[str, Charter], order: dict[str, int]
) -> list[CharterFinding]:
    """One code on two charters: every one merged after the first is a violator."""
    by_code: dict[str, list[str]] = {}
    for path, charter in charters.items():
        if charter.schema == 2 and charter.code:
            by_code.setdefault(charter.code, []).append(path)
    findings = []
    for code, paths in sorted(by_code.items()):
        ranked = sorted(paths, key=lambda p: (order.get(p, _UNMERGED), p))
        findings.extend(
            CharterFinding(
                "CHARTER-COLLISION",
                path,
                f"code {code} already belongs to {_ws(ranked[0])} (merged earlier) — "
                f"{_ws(path)} is the violator; change its code by reopening its charter",
            )
            for path in ranked[1:]
        )
    return findings


def _tombstone_findings(base: dict[str, Charter], head: dict[str, Charter]) -> list[CharterFinding]:
    return [
        CharterFinding(
            "CHARTER-TOMBSTONE",
            path,
            f"schema-2 charter deleted, moved or downgraded (code {charter.code} is a "
            "tombstone: its charter stays where it was merged)",
        )
        for path, charter in sorted(base.items())
        if charter.schema == 2 and (path not in head or head[path].schema != 2)
    ]


def _code_change_findings(
    path: str, base: Charter | None, head: Charter, violator_in_base: bool
) -> list[CharterFinding]:
    if base is None or base.schema != 2 or head.schema != 2 or base.code == head.code:
        return []
    if violator_in_base:
        return []
    return [
        CharterFinding(
            "CHARTER-CODE-CHANGE",
            path,
            f"code is immutable: {base.code} → {head.code} "
            "(only a collision violator may change it)",
        )
    ]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def _is_commit(repo: Path, ref: str) -> bool:
    return bool(ref) and _git(repo, "cat-file", "-e", f"{ref}^{{commit}}").returncode == 0


def _charters_at(repo: Path, ref: str) -> dict[str, Charter]:
    """path → Charter at ``ref`` (git objects, not the working tree)."""
    listed = _git(repo, "ls-tree", "-r", "--name-only", ref, "--", "workstreams")
    charters = {}
    for path in listed.stdout.splitlines():
        if Path(path).match(CHARTER_GLOB):
            shown = _git(repo, "show", f"{ref}:{path}")
            if shown.returncode == 0:
                charters[path] = read_charter(shown.stdout)
    return charters


def _merge_order(repo: Path, ref: str, paths: list[str]) -> dict[str, int]:
    """path → position of its first appearance in ``ref``'s first-parent history."""
    chain = _git(repo, "rev-list", "--first-parent", "--reverse", ref).stdout.split()
    index = {sha: i for i, sha in enumerate(chain)}
    order = {}
    for path in paths:
        shas = _git(repo, "log", "--first-parent", "--format=%H", ref, "--", path).stdout.split()
        if shas and shas[-1] in index:
            order[path] = index[shas[-1]]
    return order


def _ws(path: str) -> str:
    return Path(path).parent.parent.name
